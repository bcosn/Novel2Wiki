"""Deterministic draft finalization, bounded diagnostics and recoverable output."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
from core_store import digest, file_digest, validate_document, write_json
from citation_catalog import materialize, CitationErrors
from quote_source_check import check as check_quotes
from check_voice import check as check_voice
from verify_example import TABLES, table_errors, site_errors


def shape(doc):
    for e in doc["entries"]:
        if not isinstance(e, dict) or any(not isinstance(e.get(k), str) or not e[k].strip() for k in ("id","name","lead")):
            raise ValueError("entry requires nonempty id, name and lead")
        if type(e.get("stub")) is not bool or not isinstance(e.get("reception"), dict) or any(not isinstance(v,str) for v in e["reception"].values()):
            raise ValueError("entry requires boolean stub and reception text object")
        for k in ("sections","sources","links","aliases"):
            if not isinstance(e.get(k, []), list):
                raise ValueError("invalid entry array: " + k)
        if "sections" not in e or "sources" not in e or "links" not in e:
            raise ValueError("missing required entry arrays")
        if any(not isinstance(s,dict) or not isinstance(s.get("body",""),str) or not isinstance(s.get("heading",""),str) for s in e["sections"]):
            raise ValueError("sections must contain text bodies and headings")
        if any(not isinstance(s,dict) or not isinstance(s.get("quote",""),str) for s in e["sources"]):
            raise ValueError("sources must contain objects with text quotes")
        if any(not isinstance(v,str) for v in e.get("aliases",[]) + e["links"]):
            raise ValueError("aliases and links must be strings")
    validate_document("entries", doc)
    if len({e["name"] for e in doc["entries"]}) != len(doc["entries"]):
        raise ValueError("duplicate entry names")


def rules_hash():
    root = Path(__file__).resolve().parent
    return digest({n:file_digest(root/n) for n in ("finalize_entries.py","citation_catalog.py","core_store.py","quote_source_check.py","check_voice.py","verify_example.py","audit_append.py","render_wiki.py","local_uploads.py")} | {
        "css":file_digest(root.parent/"assets/wiki.css")})


def output_paths(directory, work, doc, uploads):
    import render_wiki
    files = {directory/"data"/(work+"."+k+".json") for k in TABLES+["chapters","entries"]}
    files |= {directory/"site"/n for n in ["index.html","assets/wiki.css",".novel2wiki-manifest.json",*render_wiki.filenames(doc["entries"]).values()]}
    files |= {directory/"site/assets/uploads"/u["asset"] for u in uploads if u["kind"]=="image" and (u["target_id"] is None or u["target_kind"]=="entries" and u["target_active"])}
    return files


def upload_state(store, work):
    import local_uploads
    rows=local_uploads.list_uploads(store,work)
    for row in rows:
        if row["kind"]=="image":
            path=local_uploads.managed_path(store,row["asset"])
            row["managed_content_hash"]=file_digest(path) if path.is_file() else None
    return rows


def cached(report, signature, files, directory):
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
        expected = {str(p.relative_to(directory)) for p in files}
        return (value.get("status")=="completed" and value.get("stage")=="completed" and value.get("signature")==signature
                and set(value.get("files",{}))==expected
                and all(p.is_file() and file_digest(p)==value["files"][str(p.relative_to(directory))] for p in files))
    except (ValueError,OSError,KeyError,TypeError):
        return False


def repair_packet(report, doc, errors):
    ids = {e.get("id") for e in doc["entries"] if isinstance(e,dict) and isinstance(e.get("id"),str)}
    affected = sorted({s.split("/",1)[0] for s in errors if s.split("/",1)[0] in ids})
    packet = {"affected_entry_ids":[v[:128] for v in affected[:12]], "affected_entry_count":len(affected),
              "errors":[s[:300] for s in errors[:8]], "entries":[], "omitted_entry_count":max(0,len(affected)-12), "truncated_errors":len(errors)>8}
    for e in doc["entries"]:
        if isinstance(e,dict) and e.get("id") in affected[:12]:
            candidate = dict(packet, entries=packet["entries"]+[e])
            if len(json.dumps(candidate,ensure_ascii=False,indent=2))+1<=6000:
                packet = candidate
            else:
                packet["omitted_entry_count"]+=1
    path = report.with_suffix(".repair.json")
    write_json(path, packet)
    return path, affected


def summary(result, report, directory):
    return {k:result[k] for k in ("status","stage","work","entries","entries_hash","error_count","hint_count")} | {
        "report":str(report), "output":str(directory), "repair_packet":result.get("repair_packet"),
        "affected_entry_ids":[v[:128] for v in result.get("affected_entry_ids",[])[:12]],
        "error_preview":[s[:160] for s in result.get("errors",[])[:3]]}


def finalize(store, work, inputs, directory, report, expected_hash=None, resume=False):
    directory, report = Path(directory).resolve(), Path(report).resolve()
    inputs = [Path(p).resolve() for p in inputs]
    protected = {store.path, *inputs}
    exports = {directory/"data"/(work+"."+k+".json") for k in TABLES+["chapters","entries"]}
    site = directory/"site"
    if any(p in protected|exports or p==site or site in p.parents for p in (report,report.with_suffix(".repair.json"))) or directory==store.path or exports & protected or any(site==p or site in p.parents for p in protected):
        raise ValueError("outputs cannot overwrite database or batch inputs")
    batches=[]
    for p in inputs:
        if p.stat().st_size>16*1024*1024:
            raise ValueError("batch exceeds 16 MiB")
        value=json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(value,dict) or not isinstance(value.get("entries"),list) or not isinstance(value.get("meta",{}),dict):
            raise ValueError("batch requires meta object and entries array")
        if value.get("meta",{}).get("book",work)!=work:
            raise ValueError("batch belongs to another work")
        batches.append(value)
    errors,hints=[],[]
    import local_uploads
    with store.transaction():
        previous=store.get_document(work,"entries")
        actual=digest(previous) if previous is not None else None
        if expected_hash is not None and expected_hash!=actual:
            raise ValueError("entry version changed; refresh expected hash")
        if resume:
            if batches or previous is None or expected_hash is None:
                raise ValueError("resume requires a committed draft and its --expected-hash, without batches")
            batches=[previous]
        if not batches or not any(b["entries"] for b in batches):
            raise ValueError("empty draft")
        for key in ("scope","author"):
            if len({json.dumps(b.get("meta",{}).get(key),sort_keys=True) for b in batches})>1:
                raise ValueError("batch metadata differs: "+key)
        doc={"meta":dict(batches[0].get("meta",{})),"entries":[e for b in batches for e in b["entries"]]}
        doc["meta"].update(book=work,entry_count=len(doc["entries"]))
        chapters=store.get_document(work,"chapters")
        tables={k:store.get_document(work,k) for k in TABLES}
        try:
            if chapters is None or any(v is None for v in tables.values()):
                raise ValueError("frozen chapters and all eight tables required")
            shape(doc)
            filled=[materialize(b,chapters) for b in batches]
            doc["entries"]=[e for b in filled for e in b["entries"]]
            if any("evidence_hash" in b.get("meta",{}) for b in filled):
                doc["meta"]["evidence_hash"]=digest(chapters)
            doc["entries"].sort(key=lambda e:e["id"])
            doc["meta"]["by_kind"]=dict(Counter(e["kind"] for e in doc["entries"]))
            errors.extend(table_errors(tables))
            quote_errors,_=check_quotes(doc,chapters)
            voice_errors,hints=check_voice(doc)
            errors.extend(quote_errors+voice_errors)
        except CitationErrors as exc:
            errors.extend(exc.errors)
        except (ValueError,TypeError,KeyError) as exc:
            errors.append(str(exc)[:400])
        uploads=upload_state(store,work)
        fingerprint=digest({"entries":doc,"chapters":chapters,"tables":tables,"uploads":uploads,"rules":rules_hash(),"output":str(directory)})
        result={"status":"invalid" if errors else "running","stage":"validation_failed" if errors else "committed","work":work,
                "entries":len(doc["entries"]),"entries_hash":digest(doc),"signature":fingerprint,"error_count":len(errors),"hint_count":len(hints),"errors":errors,"hints":hints}
        if not errors:
            if previous is not None and expected_hash is None and actual!=digest(doc):
                raise ValueError("changed draft requires --expected-hash from workspace status")
            files=output_paths(directory,work,doc,uploads)
            if actual==digest(doc) and cached(report,fingerprint,files,directory):
                result.update(status="reused",stage="completed")
                return summary(result,report,directory)
            store.put_document(work,"entries",doc)
            uploads=upload_state(store,work)
            result["signature"]=digest({"entries":doc,"chapters":chapters,"tables":tables,"uploads":uploads,"rules":rules_hash(),"output":str(directory)})
            files=output_paths(directory,work,doc,uploads)
    packet,affected=repair_packet(report,doc,errors)
    result.update(repair_packet=str(packet),affected_entry_ids=affected)
    write_json(report,result)
    if errors:
        return summary(result,report,directory)
    try:
        result["stage"]="exporting"; write_json(report,result)
        for kind,value in dict(tables,chapters=chapters,entries=doc).items():
            write_json(directory/"data"/(work+"."+kind+".json"),value)
        result["stage"]="rendering"; write_json(report,result)
        import render_wiki
        overlay=local_uploads.export_overlay(store,work,site)
        render_wiki.render(doc,site,work=work,unit="章",overlay=overlay)
        errors.extend(site_errors(site))
        with store.transaction():
            if digest(store.get_document(work,"entries"))!=result["entries_hash"] or digest(store.get_document(work,"chapters"))!=digest(chapters) or any(digest(store.get_document(work,k))!=digest(v) for k,v in tables.items()):
                errors.append("workspace changed during rendering; resume the current version")
        if errors:
            result.update(status="retryable",stage="site_check_failed",error_count=len(errors))
        else:
            result.update(status="completed",stage="completed",files={str(p.relative_to(directory)):file_digest(p) for p in files})
    except (OSError,ValueError) as exc:
        result.update(status="retryable",stage=result["stage"]+"_failed",error_count=1,errors=[str(exc)[:400]])
        write_json(report,result)
        raise
    write_json(report,result)
    return summary(result,report,directory)
