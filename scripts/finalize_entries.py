"""Deterministic draft finalization, bounded diagnostics and recoverable output."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
from core_store import digest, file_digest, write_json
from citation_catalog import materialize, CitationErrors
from draft_format import expand, DraftFormatErrors
from draft_validation import shape_errors
from quote_source_check import check as check_quotes
from check_voice import check as check_voice
from verify_example import TABLES, table_errors, site_errors


def shape(doc):
    issues=shape_errors(doc)
    if issues:
        raise ValueError("; ".join(issues))


def rules_hash():
    root = Path(__file__).resolve().parent
    return digest({n:file_digest(root/n) for n in ("finalize_entries.py","draft_format.py","draft_validation.py","citation_catalog.py","core_store.py","quote_source_check.py","check_voice.py","verify_example.py","audit_append.py","render_wiki.py","local_uploads.py")} | {
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
    prefixes={s.split("/",1)[0] for s in errors}
    indexes=[i for i,e in enumerate(doc["entries"],1) if (not isinstance(e,dict) or not isinstance(e.get("id"),str) or not e["id"].strip()) and "entry[%d]" % i in prefixes]
    packet = {"affected_entry_ids":[v[:128] for v in affected[:12]], "affected_entry_count":len(affected),
              "affected_entry_indexes":indexes[:12],
              "errors":[s[:300] for s in errors[:8]], "entries":[], "omitted_entry_count":max(0,len(affected)-12), "truncated_errors":len(errors)>8}
    meta={k:v for k,v in doc.get("meta",{}).items() if isinstance(v,(str,int,bool)) and len(str(v))<=256}
    if len(json.dumps(meta,ensure_ascii=False))<=1000:
        packet["meta"]=meta
    for index,e in enumerate(doc["entries"],1):
        if isinstance(e,dict) and e.get("id") in affected[:12] or index in indexes[:12]:
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


def finalize(store, work, inputs, directory, report, expected_hash=None, resume=False, update=False, replace=False):
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
        if update and (previous is None or expected_hash is None or resume):
            raise ValueError("--update requires a committed draft and its --expected-hash; cannot combine with resume")
        if replace and (update or resume):
            raise ValueError("--replace cannot combine with --update or resume")
        if resume:
            if batches or previous is None or expected_hash is None:
                raise ValueError("resume requires a committed draft and its --expected-hash, without batches")
            batches=[previous]
        if not batches or not any(b["entries"] for b in batches):
            raise ValueError("empty draft")
        for key in ("scope","author"):
            if len({json.dumps(b.get("meta",{}).get(key),sort_keys=True) for b in batches})>1:
                raise ValueError("batch metadata differs: "+key)
        chapters=store.get_document(work,"chapters")
        tables={k:store.get_document(work,k) for k in TABLES}
        if chapters is None or any(v is None for v in tables.values()):
            raise ValueError("frozen chapters and all eight tables required")
        normalized=[]
        for batch in batches:
            try:
                value=expand(batch,chapters,work)
                batch_errors=shape_errors(value,check_links=False)
                errors.extend(batch_errors)
                if not batch_errors:
                    value=materialize(value,chapters)
                normalized.append(value)
            except (CitationErrors,DraftFormatErrors) as exc:
                errors.extend(exc.errors);normalized.append(batch)
            except (ValueError,TypeError,KeyError) as exc:
                errors.append(str(exc)[:400]);normalized.append(batch)
        incoming=[e for b in normalized for e in b["entries"]]
        # Batches are complete by default for backward compatibility. Partial
        # changes must explicitly select update; that path never deletes entries.
        meta=dict((previous if update else normalized[0]).get("meta",{}))
        if update:
            for key in ("scope","author"):
                for batch in normalized:
                    if not meta.get(key) and batch.get("meta",{}).get(key):
                        meta[key]=batch["meta"][key]
                    if key in batch.get("meta",{}) and batch["meta"][key]!=meta.get(key):
                        errors.append("update metadata differs from committed draft: "+key)
            pool={e["id"]:e for e in previous["entries"]}
            seen=set()
            for e in incoming:
                if not isinstance(e,dict) or not isinstance(e.get("id"),str):
                    continue
                key=e["id"]
                if key in seen:
                    errors.append(key+"/id repeated across update batches")
                seen.add(key)
                if key in pool and any(e.get(k)!=pool[key].get(k) for k in ("kind","name")):
                    errors.append(key+"/identity update cannot change kind or name; use an explicit complete replacement")
                pool[key]=e
            rows=list(pool.values())
        else:
            rows=incoming
            if previous is not None and not replace and not errors:
                old_ids={e["id"] for e in previous["entries"]}
                new_ids={e["id"] for e in rows}
                if old_ids-new_ids:
                    raise ValueError("complete draft would remove %d committed entries; use --update for partial edits, or --replace for intentional removal" % len(old_ids-new_ids))
        doc={"meta":meta,"entries":rows}
        doc["meta"].update(book=work,entry_count=len(doc["entries"]))
        try:
            if not errors:
                errors.extend(shape_errors(doc))
            if errors:
                raise ValueError("draft validation failed; repair all reported entry-local errors")
            if any("evidence_hash" in b.get("meta",{}) for b in normalized):
                doc["meta"]["evidence_hash"]=digest(chapters)
            doc=materialize(doc,chapters)
            doc["entries"].sort(key=lambda e:e["id"])
            doc["meta"]["by_kind"]=dict(Counter(e["kind"] for e in doc["entries"]))
            errors.extend(table_errors(tables))
            quote_errors,_=check_quotes(doc,chapters)
            voice_errors,hints=check_voice(doc)
            errors.extend(quote_errors+voice_errors)
        except CitationErrors as exc:
            errors.extend(exc.errors)
        except (ValueError,TypeError,KeyError) as exc:
            if not errors:
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
