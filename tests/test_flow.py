import copy
import base64
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from core_store import Store,digest,write_json,file_digest
from test_v02fix import entry
from verify_example import TABLES
import citation_catalog
import draft_context
import finalize_entries
import local_uploads
import task_protocol


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/".validation",prefix="flow-")
        self.root=pathlib.Path(self.temp.name).resolve()
        self.store=Store(self.root/"state.sqlite")
        self.chapters={"meta":{"title":"书","creator":"作者"},"chapters":[{"index":1,"content":"甲在酒店工作。\n\n乙说：‘病便好了。’"}]}
        tables={k:{"meta":{"book":"书","scope":"第1章"},"items":[]} for k in TABLES}
        tables["characters"]["items"]=[{"name":"甲","first_chapter":1},{"name":"乙","first_chapter":1}]
        tables["custom_attributes"]["attribute_defs"]=[]
        self.store.import_documents("书",dict(tables,chapters=self.chapters))
        self.batch=self.root/"batch.json"
        self.doc={"meta":{"book":"书","scope":"第1章"},"entries":[entry()]}
        write_json(self.batch,self.doc)
        self.report=self.root/"finish.json"
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def finish(self,**kw):
        return finalize_entries.finalize(self.store,"书",[self.batch],self.root,self.report,**kw)
    def test_prepare_draft_is_bounded_and_has_no_raw_source_stdout(self):
        result=draft_context.prepare(self.store,"书",self.root/"context")
        self.assertLess(result["context_chars"],12000)
        self.assertNotIn("甲在酒店",json.dumps(result,ensure_ascii=False))
        value=json.loads(pathlib.Path(result["evidence"]).read_text(encoding="utf-8"))
        self.assertEqual(value["evidence_hash"],digest(self.chapters))
        self.assertEqual(value["paragraphs"][1]["text"],"乙说：‘病便好了。’")
    def test_prepare_draft_over_budget_does_not_silently_truncate(self):
        big=copy.deepcopy(self.chapters);big["chapters"][0]["content"]="正文。"*6000
        self.store.import_documents("书",{"chapters":big})
        target=self.root/"context"
        with self.assertRaises(ValueError):draft_context.prepare(self.store,"书",target)
        self.assertFalse(target.exists())
    def test_reference_is_copied_exactly_and_draft_is_not_mutated(self):
        value=copy.deepcopy(self.doc);value["meta"]["evidence_hash"]=digest(self.chapters)
        value["entries"][0]["sources"]=[{"evidence_id":"c1p1"}]
        filled=citation_catalog.materialize(value,self.chapters)
        self.assertEqual(filled["entries"][0]["sources"][0]["quote"],"甲在酒店工作。")
        self.assertNotIn("quote",value["entries"][0]["sources"][0])
        write_json(self.batch,value)
        self.assertEqual(self.finish()["status"],"completed")
        self.assertEqual(self.store.get_document("书","entries")["entries"][0]["sources"][0]["chapter"],1)
    def test_reference_rejects_stale_or_conflicting_evidence(self):
        value=copy.deepcopy(self.doc);value["entries"][0]["sources"]=[{"evidence_id":"c1p1"}]
        for metadata,source in [({},{}),({"evidence_hash":"old"},{}),({"evidence_hash":digest(self.chapters)},{"chapter":2}),({"evidence_hash":digest(self.chapters)},{"quote":"乙在工作。"}),({"evidence_hash":digest(self.chapters)},{"start":-1}),({"evidence_hash":digest(self.chapters)},{"evidence_id":"missing"})]:
            candidate=copy.deepcopy(value);candidate["meta"].update(metadata);candidate["entries"][0]["sources"][0].update(source)
            with self.assertRaises(ValueError):citation_catalog.materialize(candidate,self.chapters)
    def test_unchanged_output_reuses_and_modified_page_rebuilds(self):
        self.finish()
        with mock.patch("render_wiki.render",side_effect=AssertionError("must reuse")):
            self.assertEqual(self.finish()["status"],"reused")
        page=self.root/"site/index.html";page.write_text("modified",encoding="utf-8")
        self.assertEqual(self.finish()["status"],"completed")
        self.assertNotEqual(page.read_text(encoding="utf-8"),"modified")
    def test_upload_change_invalidates_output_cache(self):
        self.finish()
        local_uploads.add(self.store,"书","review",{"body":"新书评"},upload_id="review-1")
        self.assertEqual(self.finish()["status"],"completed")
        self.assertIn("新书评",(self.root/"site/index.html").read_text(encoding="utf-8"))
    def test_render_failure_records_checkpoint_and_resume_uses_committed_draft(self):
        with mock.patch("render_wiki.render",side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):self.finish()
        saved=json.loads(self.report.read_text(encoding="utf-8"))
        self.assertEqual(saved["stage"],"rendering_failed")
        self.assertEqual(saved["status"],"retryable")
        self.batch.unlink()
        result=finalize_entries.finalize(self.store,"书",[],self.root,self.report,saved["entries_hash"],resume=True)
        self.assertEqual(result["status"],"completed")
        with self.assertRaises(ValueError):
            finalize_entries.finalize(self.store,"书",[],self.root,self.report,"stale",resume=True)
    def test_repair_packet_has_only_affected_entry_and_stays_bounded(self):
        bad=entry();bad["sources"][0]["chapter"]=99
        value=copy.deepcopy(self.doc);value["entries"]=[bad,entry("e-2","乙")]
        write_json(self.batch,value)
        result=self.finish()
        self.assertEqual(result["affected_entry_ids"],["e-1"])
        packet=json.loads(pathlib.Path(result["repair_packet"]).read_text(encoding="utf-8"))
        self.assertEqual([e["id"] for e in packet["entries"]],["e-1"])
        path,_=finalize_entries.repair_packet(self.report,value,["e-1/source[1] "+"错"*10000]*1000)
        self.assertLess(len(path.read_text(encoding="utf-8")),6000)
        self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["truncated_errors"])
        self.assertIsNone(self.store.get_document("书","entries"))
    def test_concurrent_revision_during_render_is_not_reported_complete(self):
        import render_wiki
        original=render_wiki.render
        def change(*args,**kwargs):
            result=original(*args,**kwargs)
            newer=self.store.get_document("书","entries");newer["entries"][0]["lead"]="甲是已修订的人物。"
            self.store.import_documents("书",{"entries":newer})
            return result
        with mock.patch("render_wiki.render",side_effect=change):
            result=self.finish()
        self.assertEqual(result["status"],"retryable")
        self.assertIn("workspace changed",result["error_preview"][0])
        self.assertEqual(self.store.get_document("书","entries")["entries"][0]["lead"],"甲是已修订的人物。")
    def test_bad_reference_has_local_repair_context(self):
        value=copy.deepcopy(self.doc);value["meta"]["evidence_hash"]=digest(self.chapters)
        value["entries"][0]["sources"]=[{"evidence_id":"c1p999"}]
        value["entries"].append(entry("e-2","乙"));write_json(self.batch,value)
        result=self.finish()
        self.assertEqual(result["status"],"invalid")
        self.assertEqual(result["affected_entry_ids"],["e-1"])
        packet=json.loads(pathlib.Path(result["repair_packet"]).read_text(encoding="utf-8"))
        self.assertEqual([e["id"] for e in packet["entries"]],["e-1"])
    def test_reference_offsets_are_characters_and_cannot_cross_paragraph(self):
        chapters={"chapters":[{"index":2,"content":"甲🙂乙。\n\n丙。"}]}
        value=copy.deepcopy(self.doc);value["meta"]["evidence_hash"]=digest(chapters)
        value["entries"][0]["sources"]=[{"evidence_id":"c1p1","start":1,"end":3}]
        filled=citation_catalog.materialize(value,chapters)
        self.assertEqual(filled["entries"][0]["sources"][0]["quote"],"🙂乙")
        value["entries"][0]["sources"][0]["end"]=6
        with self.assertRaises(ValueError):citation_catalog.materialize(value,chapters)
    def test_duplicate_chapter_indices_cannot_create_ambiguous_catalog(self):
        chapters={"chapters":[{"index":1,"content":"甲。"},{"index":1,"content":"乙。"}]}
        with self.assertRaises(ValueError):citation_catalog.catalog(chapters)
    def test_modified_managed_image_is_not_hidden_by_output_cache(self):
        raw=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3YQAAAAASUVORK5CYII=')
        source=self.root/"image.png";source.write_bytes(raw)
        local_uploads.add(self.store,"书","image",{},image_path=source,upload_id="image-1")
        self.finish()
        row=local_uploads.list_uploads(self.store,"书")[0]
        local_uploads.managed_path(self.store,row["asset"]).write_bytes(raw+b'changed')
        with self.assertRaises(ValueError):self.finish()
        self.assertEqual(json.loads(self.report.read_text(encoding="utf-8"))["status"],"retryable")
    def test_named_refs_build_local_source_numbers_without_hand_numbering(self):
        value=copy.deepcopy(self.doc);value["meta"]["evidence_hash"]=digest(self.chapters)
        e=value["entries"][0];e["sources"]=[];e["lead_refs"]=["c1p2","c1p1"];e["sections"][0]["refs"]=["c1p1"]
        filled=citation_catalog.materialize(value,self.chapters)
        self.assertEqual(filled["entries"][0]["lead_refs"],[1,2])
        self.assertEqual(filled["entries"][0]["sections"][0]["refs"],[2])
        self.assertEqual([s["chapter"] for s in filled["entries"][0]["sources"]],[1,1])
        self.assertEqual(value["entries"][0]["sources"],[])
        write_json(self.batch,value)
        self.assertEqual(self.finish()["status"],"completed")
    def test_named_refs_and_legacy_numeric_refs_can_mix(self):
        value=copy.deepcopy(self.doc);value["meta"]["evidence_hash"]=digest(self.chapters)
        e=value["entries"][0];e["lead_refs"]=[1];e["sections"][0]["refs"]=["c1p2",1]
        filled=citation_catalog.materialize(value,self.chapters)
        self.assertEqual(filled["entries"][0]["lead_refs"],[1])
        self.assertEqual(filled["entries"][0]["sections"][0]["refs"],[2,1])
        self.assertEqual(len(filled["entries"][0]["sources"]),2)
    def test_worker_draft_submission_accepts_the_same_evidence_interface(self):
        value=copy.deepcopy(self.doc);value["meta"]["evidence_hash"]=digest(self.chapters)
        e=value["entries"][0];e["sources"]=[];e["lead_refs"]=["c1p1"];e["sections"][0]["refs"]=["c1p1"]
        frozen=self.root/"input.json";write_json(frozen,self.chapters)
        manifest={"protocol":1,"id":"draft-worker","stage":"draft","inputs":[{"path":str(frozen),"sha256":file_digest(frozen),"offset":0,"length":len(frozen.read_text(encoding="utf-8"))}],"output":str(self.batch),"rules_hash":digest("rules"),"config":{"expected_entries_hash":None}}
        task_protocol.prepare(self.store,"书",manifest);claim=task_protocol.claim(self.store,"draft-worker")
        write_json(self.batch,value)
        task_protocol.submit(self.store,"draft-worker",claim["token"])
        self.assertEqual(self.store.get_document("书","entries")["entries"][0]["lead_refs"],[1])
    def test_all_bad_evidence_ids_are_reported_in_one_repair_batch(self):
        value=copy.deepcopy(self.doc);value["meta"]["evidence_hash"]=digest(self.chapters)
        value["entries"].append(entry("e-2","乙"))
        for i,e in enumerate(value["entries"],100):
            e["sources"]=[];e["lead_refs"]=["c1p%d" % i];e["sections"][0]["refs"]=["c1p1"]
        write_json(self.batch,value)
        result=self.finish()
        self.assertEqual(result["error_count"],2)
        self.assertEqual(result["affected_entry_ids"],["e-1","e-2"])
        self.assertIsNone(self.store.get_document("书","entries"))


if __name__=="__main__":unittest.main()
