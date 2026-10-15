import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from core_store import Store,digest,write_json
from verify_example import TABLES
import entry_plan,draft_context,finalize_entries,structure_entries


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/"state.sqlite")
        self.chapters={"meta":{"title":"书","creator":"作者","scope":"第1章"},"chapters":[{"index":1,"content":"甲在驿站接待乙。乙送来银匙。\n\n甲负责登记来客，后来将银匙交还给乙。\n\n乙离开驿站。"}]}
        docs={k:{"meta":{"scope":"第1章"},"items":[]} for k in TABLES}
        docs["characters"]["items"]=[{"name":"甲","importance":"main"},{"name":"乙","importance":"minor"}]
        docs["objects"]["items"]=[{"name":"银匙","importance":"minor"}]
        docs["places"]["items"]=[{"name":"驿站"}]
        docs["custom_attributes"]["attribute_defs"]=[]
        self.store.import_documents("书",dict(docs,chapters=self.chapters))
        base=entry_plan.build(self.store,"书");self.ids={r["name"]:r["id"] for r in base["items"]}
        self.decisions={"items":[
          {"kind":"character","name":"甲","reason":"两个独立主题分别整理职责与往来。","depth":"standard","topics":[
            {"id":"duty","heading":"在驿站的职责","focus":"只写登记职责。","evidence_ids":["c1p2"]},
            {"id":"exchange","heading":"与乙的往来","focus":"送来和交还银匙，归属各自行为。","evidence_ids":["c1p1","c1p2","c1p3"]}]},
          {"kind":"object","name":"银匙","reason":"仅为往来涉及的物品，事实并入该主题。","disposition":"merge","depth":"none","merge_target":self.ids["甲"],"evidence_ids":["c1p1","c1p2"]}]}
        self.plan=entry_plan.build(self.store,"书",decisions=self.decisions)
        self.planfile=self.root/"plan.json";write_json(self.planfile,self.plan)
        self.doc={"meta":{"book":"书","author":"作者","scope":"第1章","evidence_hash":digest(self.chapters),"entry_plan_hash":self.plan["plan_hash"]},"entries":[
         {"id":self.ids["甲"],"kind":"character","name":"甲","aliases":[],"lead":"甲是作者《书》中的人物，在驿站接待乙。","lead_refs":["c1p1"],
          "sections":[{"topic_id":"duty","body":"甲负责登记来客。","refs":["c1p2"]},{"topic_id":"exchange","body":"乙送来银匙，甲后来把银匙交还给乙。","refs":["c1p1","c1p2"]}],
          "sources":[],"reception":{"in_work":"","user":"","external":""},"links":[],"stub":False}]}
        self.batch=self.root/"draft.json";self.report=self.root/"report.json"
    def tearDown(self):self.store.close();self.temp.cleanup()
    def finish(self,doc=None,**kwargs):
        write_json(self.batch,doc or self.doc)
        return finalize_entries.finalize(self.store,"书",[self.batch],self.root/"output",self.report,plan=self.planfile,**kwargs)
    def test_prepare_includes_only_selected_blueprint_and_frozen_evidence(self):
        result=draft_context.prepare(self.store,"书",self.root/"ctx",plan=self.planfile,plan_entries=[self.ids["甲"]])
        packet=json.loads(Path(result["context"]).read_text(encoding="utf-8"))
        self.assertEqual(len(packet["editorial"]["articles"]),1)
        self.assertEqual(packet["editorial"]["articles"][0]["merge_mentions"][0]["name"],"银匙")
        self.assertEqual(packet["draft_template"]["entries"][0]["sections"][0]["topic_id"],"duty")
        self.assertEqual(result["context_chars"],len(Path(result["context"]).read_text(encoding="utf-8")))
        self.assertTrue(result["prompt_version"].endswith("+structure-v1"))
        self.assertIsInstance(packet["guide"],list)
        self.assertEqual(result["prompt_hash"],digest(packet["guide"]))
        self.assertLess(max(map(len,Path(result["context"]).read_text(encoding="utf-8").splitlines())),2000)
    def test_preparation_requires_approved_and_outlined_entries(self):
        for ids in ([self.ids["驿站"]],[self.ids["银匙"]],[],[self.ids["甲"]]*2):
            with self.assertRaises(ValueError):draft_context.prepare(self.store,"书",self.root/"bad",plan=self.planfile,plan_entries=ids)
        base=entry_plan.build(self.store,"书");write_json(self.planfile,base)
        with self.assertRaisesRegex(ValueError,"needs evidence-backed topics"):
            draft_context.prepare(self.store,"书",self.root/"bad",plan=self.planfile,plan_entries=[self.ids["甲"]])
    def test_finalize_fills_headings_without_rewriting_prose_and_tracks_pending(self):
        before=copy.deepcopy(self.doc);result=self.finish()
        self.assertEqual(result["status"],"completed")
        saved=self.store.get_document("书","entries")["entries"][0]
        self.assertEqual(saved["sections"][0]["heading"],"在驿站的职责")
        self.assertEqual(saved["sections"][0]["body"],before["entries"][0]["sections"][0]["body"])
        self.assertEqual(self.doc,before)
        self.assertEqual(result["entry_plan"]["missing_page_count"],1)
        self.assertEqual(result["entry_plan"]["unresolved_count"],1)
        self.assertFalse(result["entry_plan"]["mechanical_coverage_complete"])
    def test_extra_missing_and_renamed_topics_are_rejected_in_one_batch(self):
        value=copy.deepcopy(self.doc);value["entries"][0]["sections"][0].update(topic_id="invented",heading="清晨到夜里")
        result=self.finish(value)
        self.assertEqual(result["status"],"invalid")
        self.assertGreaterEqual(result["error_count"],2)
        self.assertIsNone(self.store.get_document("书","entries"))
        value=copy.deepcopy(self.doc);value["entries"][0]["sections"][0]["heading"]="错误标题"
        self.assertEqual(self.finish(value)["status"],"invalid")
    def test_topic_cannot_borrow_other_topic_evidence(self):
        value=copy.deepcopy(self.doc);value["entries"][0]["sections"][0]["refs"]=["c1p1"]
        result=self.finish(value)
        self.assertEqual(result["status"],"invalid")
        self.assertIn("outside planned topic",result["error_preview"][0])
    def test_planned_topic_cannot_be_empty_even_with_valid_refs(self):
        value=copy.deepcopy(self.doc);value["entries"][0]["sections"][0]["body"]=" \n "
        result=self.finish(value)
        self.assertEqual(result["status"],"invalid")
        self.assertIn("nonempty body",result["error_preview"][0])
        self.assertIsNone(self.store.get_document("书","entries"))
    def test_merged_subject_needs_name_and_own_evidence_and_is_not_alias(self):
        for mode in ("omit","alias","wrong-evidence"):
            value=copy.deepcopy(self.doc);e=value["entries"][0]
            if mode=="omit":e["sections"][1]["body"]="乙来到驿站，甲接待乙。"
            elif mode=="alias":e["aliases"]=["银匙"]
            else:e["sections"][1]["refs"]=["c1p3"]
            self.assertEqual(self.finish(value)["status"],"invalid",mode)
    def test_full_repeated_paragraph_is_rejected_but_identity_repeats_are_allowed(self):
        value=copy.deepcopy(self.doc);paragraph="甲在驿站登记来客，乙把银匙送来，甲随后交还银匙，乙才离开驿站。"*2
        for s in value["entries"][0]["sections"]:s["body"]=paragraph
        self.assertEqual(self.finish(value)["status"],"invalid")
        self.assertEqual(self.finish()["status"],"completed")
    def test_stub_can_be_lead_only_and_cannot_grow_unplanned_sections(self):
        stub={"meta":self.doc["meta"],"entries":[{"id":self.ids["乙"],"kind":"character","name":"乙","aliases":[],"lead":"乙是作者《书》中的人物，到驿站送来银匙。","lead_refs":["c1p1"],"sections":[],"sources":[],"links":[],"reception":{"in_work":"","user":"","external":""},"stub":True}]}
        self.assertEqual(self.finish(stub)["status"],"completed")
        stub["entries"][0]["sections"]=[{"topic_id":"made-up","body":"乙离开驿站。","refs":["c1p3"]}]
        self.assertEqual(self.finish(stub)["status"],"invalid")
    def test_planned_batch_cannot_silently_skip_plan_validation(self):
        write_json(self.batch,self.doc)
        with self.assertRaisesRegex(ValueError,"requires finalize --plan"):
            finalize_entries.finalize(self.store,"书",[self.batch],self.root/"output",self.report)
        bad=copy.deepcopy(self.doc);bad["meta"]["entry_plan_hash"]="wrong"
        self.assertEqual(self.finish(bad)["status"],"invalid")
    def test_stale_plan_rejection_preserves_committed_draft_and_site(self):
        self.finish();saved=self.store.get_document("书","entries");page=(self.root/"output/site/index.html").read_bytes()
        chapters=copy.deepcopy(self.chapters);chapters["chapters"][0]["content"]+="\n\n新段落。"
        self.store.import_documents("书",{"chapters":chapters})
        with self.assertRaisesRegex(ValueError,"stale"):
            self.finish(expected_hash=digest(saved))
        self.assertEqual(self.store.get_document("书","entries"),saved)
        self.assertEqual((self.root/"output/site/index.html").read_bytes(),page)
    def test_resume_needs_no_outline_file_and_reuses_validated_snapshot(self):
        first=self.finish();saved=self.store.get_document("书","entries");self.planfile.unlink()
        result=finalize_entries.finalize(self.store,"书",[],self.root/"output",self.report,digest(saved),resume=True)
        self.assertEqual(result["status"],"reused")
        self.assertEqual(result["entry_plan"],first["entry_plan"])
        (self.root/"output/site/index.html").unlink()
        result=finalize_entries.finalize(self.store,"书",[],self.root/"output",self.report,digest(saved),resume=True)
        self.assertEqual(result["status"],"completed")
        self.assertEqual(json.loads(self.report.read_text(encoding="utf-8"))["entry_plan"],first["entry_plan"])
    def test_resume_does_not_present_stale_coverage_as_current(self):
        self.finish();saved=self.store.get_document("书","entries")
        chapters=copy.deepcopy(self.chapters);chapters["chapters"][0]["content"]+="\n\n另增资料。"
        self.store.import_documents("书",{"chapters":chapters})
        # Materialized quotes are still present, but the frozen evidence hash
        # correctly rejects an old snapshot before export.
        result=finalize_entries.finalize(self.store,"书",[],self.root/"output",self.report,digest(saved),resume=True)
        self.assertEqual(result["status"],"invalid")
        self.assertEqual(result["entry_plan"]["status"],"unavailable")
    def test_replanning_and_updating_another_page_preserves_published_relations(self):
        self.decisions["relations"]=[{"source":self.ids["甲"],"target":self.ids["乙"],"label":"接待过","evidence_ids":["c1p1"]}]
        self.plan=entry_plan.build(self.store,"书",decisions=self.decisions);write_json(self.planfile,self.plan)
        self.doc["meta"]["entry_plan_hash"]=self.plan["plan_hash"]
        self.assertEqual(self.finish()["status"],"completed")
        before=self.store.get_document("书","entries")["entries"][0]
        # Omitting relations while changing other editorial decisions must not
        # silently clear graph edges on a retained entry.
        self.plan=entry_plan.build(self.store,"书",decisions={"items":self.decisions["items"]});write_json(self.planfile,self.plan)
        stub={"meta":dict(self.doc["meta"],entry_plan_hash=self.plan["plan_hash"]),"entries":[{
            "id":self.ids["乙"],"name":"乙","kind":"character","aliases":[],"lead":"乙是作者《书》中的人物，到驿站送来银匙。",
            "lead_refs":["c1p1"],"sections":[],"sources":[],"reception":{"in_work":"","user":"","external":""},"links":[],"stub":True}]}
        previous=self.store.get_document("书","entries")
        self.assertEqual(self.finish(stub,update=True,expected_hash=digest(previous))["status"],"completed")
        after=next(e for e in self.store.get_document("书","entries")["entries"] if e["id"]==self.ids["甲"])
        self.assertEqual(after["lead"],before["lead"])
        self.assertEqual(after["sections"],before["sections"])
        self.assertEqual(after["relations"],before["relations"])
        self.assertIn('接待过：<a href="乙.html">乙</a>',(self.root/"output/site/甲.html").read_text(encoding="utf-8"))


if __name__=="__main__":unittest.main()
