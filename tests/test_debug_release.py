"""Small reproductions for the post-planning debug release; no model calls."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
import citation_catalog
import draft_context
import entry_plan
import finalize_entries
import test_taxonomy_flow as taxonomy
from core_store import digest,validate_document,write_json
from verify_example import TABLES


class DebugReleaseTests(unittest.TestCase):
    def setUp(self):
        self.case=taxonomy.TaxonomyFlowTests()
        self.case.setUp()
    def tearDown(self):self.case.tearDown()

    def test_empty_or_invalid_chapter_selection_cannot_expand_to_the_whole_book(self):
        t=self.case
        for scope in ([],(),set(),[True],[False],[0],[-1],["1"],True,"1"):
            with self.subTest(scope=scope):
                with self.assertRaises(ValueError):citation_catalog.catalog(t.chapters,scope)
                with self.assertRaises(ValueError):draft_context.prepare(t.store,t.work,t.root/"bad-scope",selected=scope)
                self.assertFalse((t.root/"bad-scope").exists())
        self.assertEqual({r["chapter"] for r in citation_catalog.catalog(t.chapters,[2,2])["paragraphs"]},{2})

    def test_committing_prose_also_saves_the_metadata_plan_and_pending_identities(self):
        t=self.case;t.publish_initial()
        self.assertEqual(t.store.get_document(t.work,"_entry_plan"),t.plan)
        self.assertEqual(len(TABLES),8)
        rebuilt=entry_plan.build(t.store,t.work)
        pending=next(r for r in rebuilt["items"] if r["name"]=="弦序")
        self.assertEqual(pending,next(r for r in t.plan["items"] if r["name"]=="弦序"))
        self.assertEqual(rebuilt["relations"],t.plan["relations"])
        t.plan=rebuilt;write_json(t.planfile,t.plan)
        doc,_=t.draft(["弦序"],directory="resume-pending")
        result=t.finish(doc,update=True)
        self.assertEqual(result["status"],"completed",result)
        self.assertEqual(result["entry_plan"]["missing_page_count"],0)

    def test_invalid_draft_cannot_advance_the_saved_plan(self):
        t=self.case;t.publish_initial()
        saved=t.store.get_document(t.work,"_entry_plan")
        previous=t.store.get_document(t.work,"entries");site=t.site_snapshot()
        changed=copy.deepcopy(t.decisions)
        changed["items"].append({"kind":"object","name":"石扣","reason":"本轮仅保留已有抽取记录。","disposition":"table_only","depth":"none"})
        t.plan=entry_plan.build(t.store,t.work,selected=[1,2],decisions=changed)
        write_json(t.planfile,t.plan)
        doc,_=t.draft(["弦序"],directory="invalid-pending");doc["entries"][0]["lead"]=""
        self.assertEqual(t.finish(doc,update=True)["status"],"invalid")
        self.assertEqual(t.store.get_document(t.work,"_entry_plan"),saved)
        self.assertEqual(t.store.get_document(t.work,"entries"),previous)
        self.assertEqual(t.site_snapshot(),site)

    def test_plan_write_rolls_back_if_the_following_prose_commit_fails(self):
        t=self.case;doc,_=t.draft(["洛禾","岑岚","雾裔"])
        original=t.store.put_document
        def fail_prose(work,kind,value):
            if kind=="entries":raise ValueError("injected prose commit failure")
            return original(work,kind,value)
        with patch.object(t.store,"put_document",side_effect=fail_prose):
            with self.assertRaisesRegex(ValueError,"injected prose commit failure"):t.finish(doc)
        self.assertIsNone(t.store.get_document(t.work,"_entry_plan"))
        self.assertIsNone(t.store.get_document(t.work,"entries"))
        self.assertFalse(t.store.connection.in_transaction)

    def test_legacy_workspace_can_explicitly_recover_from_the_original_plan(self):
        t=self.case;t.publish_initial()
        with t.store.transaction():t.store.connection.execute("DELETE FROM documents WHERE work_id=? AND kind='_entry_plan'",(t.work,))
        with self.assertRaisesRegex(ValueError,"base-plan"):entry_plan.build(t.store,t.work)
        before=t.store.connection.total_changes
        recovered=entry_plan.build(t.store,t.work,selected=[1,2],base_plan=t.plan)
        self.assertEqual(recovered,t.plan)
        self.assertEqual(t.store.connection.total_changes,before)
        self.assertIsNone(t.store.get_document(t.work,"_entry_plan"))

    def test_unplanned_edit_does_not_discard_unpublished_identity_metadata(self):
        t=self.case;t.publish_initial()
        previous=t.store.get_document(t.work,"entries")
        doc={"meta":copy.deepcopy(previous["meta"]),"entries":[copy.deepcopy(next(e for e in previous["entries"] if e["name"]=="洛禾"))]}
        doc["meta"].pop("entry_plan_hash")
        batch=t.root/"unplanned.json";write_json(batch,doc)
        result=finalize_entries.finalize(t.store,t.work,[batch],t.output,t.report,expected_hash=digest(previous),update=True)
        self.assertEqual(result["status"],"completed",result)
        self.assertNotIn("entry_plan_hash",t.store.get_document(t.work,"entries")["meta"])
        rebuilt=entry_plan.build(t.store,t.work,selected=[1,2])
        self.assertIn(t.ids["弦序"],{r["id"] for r in rebuilt["items"]})
        self.assertEqual(rebuilt["relations"],t.plan["relations"])

    def test_stale_plan_cannot_restore_pending_decisions_against_new_source(self):
        t=self.case;t.publish_initial()
        changed=copy.deepcopy(t.chapters);changed["chapters"][0]["content"]+="\n\n另增记录。"
        t.store.import_documents(t.work,{"chapters":changed})
        with self.assertRaisesRegex(ValueError,"stale"):entry_plan.build(t.store,t.work,base_plan=t.plan)
        with self.assertRaisesRegex(ValueError,"stale"):entry_plan.build(t.store,t.work)
        fresh=entry_plan.build(t.store,t.work,decisions={"relations":[]})
        self.assertNotIn("弦序",{r["name"] for r in fresh["items"]})
        self.assertEqual(t.store.get_document(t.work,"_entry_plan"),t.plan)

    def test_approved_duplicate_titles_are_rejected_before_writing_a_packet(self):
        t=self.case
        decisions=copy.deepcopy(t.decisions)
        decisions["items"].append({"kind":"concept","name":"雾裔","reason":"测试同名标题的前置诊断。","disposition":"stub","depth":"brief","evidence_ids":["c1p1"]})
        planned=entry_plan.build(t.store,t.work,selected=[1,2],decisions=decisions)
        write_json(t.planfile,planned)
        duplicate=next(r for r in planned["items"] if r["kind"]=="concept" and r["name"]=="雾裔")
        with self.assertRaisesRegex(ValueError,"disambiguate before drafting"):
            draft_context.prepare(t.store,t.work,t.root/"duplicate",plan=t.planfile,plan_entries=[duplicate["id"]])
        self.assertFalse((t.root/"duplicate").exists())
        self.assertIsNone(t.store.get_document(t.work,"entries"))

    def test_published_title_conflicts_are_rejected_even_when_the_old_page_is_demoted(self):
        t=self.case;t.publish_initial()
        decisions={"items":[
            {"kind":"race","name":"雾裔","reason":"测试保留旧页时的标题冲突。","disposition":"table_only","depth":"none"},
            {"kind":"concept","name":"雾裔","reason":"测试新对象沿用旧页面标题。","disposition":"stub","depth":"brief","evidence_ids":["c1p1"]}],"relations":[]}
        planned=entry_plan.build(t.store,t.work,selected=[1,2],decisions=decisions);write_json(t.planfile,planned)
        key=next(r["id"] for r in planned["items"] if r["kind"]=="concept" and r["name"]=="雾裔")
        with self.assertRaisesRegex(ValueError,"retained published entry"):
            draft_context.prepare(t.store,t.work,t.root/"duplicate-old",plan=t.planfile,plan_entries=[key])
        self.assertFalse((t.root/"duplicate-old").exists())

    def test_saved_plan_hash_and_work_are_validated_by_storage(self):
        t=self.case
        for value in (dict(t.plan,plan_hash="wrong"),dict(t.plan,version=True)):
            with self.assertRaises(ValueError):validate_document("_entry_plan",value)
        other=copy.deepcopy(t.plan);other["work"]="另一书";other["plan_hash"]=digest({k:v for k,v in other.items() if k!="plan_hash"})
        with self.assertRaisesRegex(ValueError,"another work"):
            t.store.import_documents(t.work,{"_entry_plan":other})
        self.assertIsNone(t.store.get_document(t.work,"_entry_plan"))

    def test_deleted_published_page_and_its_edges_are_not_restored_as_pending(self):
        t=self.case;t.publish_initial()
        doc,_=t.draft(["弦序"],directory="publish-concept");self.assertEqual(t.finish(doc,update=True)["status"],"completed")
        previous=t.store.get_document(t.work,"entries")
        removed=t.ids["弦序"]
        doc=copy.deepcopy(previous);doc["meta"].pop("entry_plan_hash")
        doc["entries"]=[e for e in doc["entries"] if e["id"]!=removed]
        for row in doc["entries"]:row["relations"]=[edge for edge in row.get("relations",[]) if edge["target"]!=removed]
        batch=t.root/"remove-concept.json";write_json(batch,doc)
        result=finalize_entries.finalize(t.store,t.work,[batch],t.output,t.report,expected_hash=digest(previous),replace=True)
        self.assertEqual(result["status"],"completed",result)
        rebuilt=entry_plan.build(t.store,t.work,selected=[1,2])
        self.assertNotIn(removed,{r["id"] for r in rebuilt["items"]})
        self.assertFalse(any(edge["source"]==removed or edge["target"]==removed for edge in rebuilt["relations"]))


if __name__=="__main__":unittest.main()
