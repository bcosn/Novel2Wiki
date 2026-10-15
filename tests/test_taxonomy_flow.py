"""Tiny fictional source: classify first, then publish explicit shared pages."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from core_store import Store, digest, write_json
from verify_example import TABLES, site_errors
import draft_context
import entry_plan
import finalize_entries


class TaxonomyFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state.sqlite")
        self.work = "雾港记"
        self.chapters = {"meta": {"title": self.work, "creator": "某作者", "scope": "第1至3章"},
            "chapters": [
                {"index": 1, "content": "洛禾和岑岚都是雾裔。雾裔在夜航中以潮纹辨路。\n\n"
                    "港民把标记方式称为弦序。弦序有回枝和无环两种称呼，原文没有比较高低。\n\n"
                    "洛禾最初登记为回枝，岑岚登记为无环。\n\n岑岚携着石扣到港。"},
                {"index": 2, "content": "洛禾后来改记为无环，但记簿未说明其他人的后续状态。"},
                {"index": 3, "content": "另章的纸上还写有落钟，仅此一次。"}]}
        tables = {kind: {"meta": {"scope": "第1至3章"}, "items": []} for kind in TABLES}
        tables["characters"]["items"] = [{"name": "洛禾", "importance": "minor", "race": "雾裔"},
                                                  {"name": "岑岚", "importance": "minor", "race": "雾裔"}]
        tables["races"]["items"] = [{"name": "雾裔", "level": 0, "parent": None,
                                            "members": ["洛禾", "岑岚"], "traits": ["以潮纹辨路"]}]
        tables["objects"]["items"] = [{"name": "石扣"}]
        # Neither the attribute name nor its observed values are reserved keywords.
        tables["custom_attributes"]["attribute_defs"] = [
            {"name": "弦序", "target_kind": "character", "definition": "港民的标记方式"}]
        tables["custom_attributes"]["items"] = [
            {"name": "弦序", "target_kind": "character", "target": "洛禾", "value": "回枝", "chapter": 1,
             "evidence": "洛禾最初登记为回枝，岑岚登记为无环。"},
            {"name": "弦序", "target_kind": "character", "target": "岑岚", "value": "无环", "chapter": 1,
             "evidence": "洛禾最初登记为回枝，岑岚登记为无环。"},
            {"name": "弦序", "target_kind": "character", "target": "洛禾", "value": "无环", "chapter": 2,
             "evidence": "洛禾后来改记为无环，但记簿未说明其他人的后续状态。"}]
        self.store.import_documents(self.work, dict(tables, chapters=self.chapters))
        self.base = entry_plan.build(self.store, self.work, selected=[1, 2])
        self.ids = {row["name"]: row["id"] for row in self.base["items"]}
        self.decisions = {"items": [
            {"kind": "race", "name": "雾裔", "reason": "原文明确是两个人物共同所属的族群，材料足以建短页。",
             "disposition": "stub", "depth": "brief", "evidence_ids": ["c1p1"]},
            {"kind": "concept", "name": "弦序", "reason": "依据原文判断为共同的标记体系，汇总名称与有时点的记录。",
             "disposition": "standalone", "depth": "standard", "evidence_ids": ["c1p2", "c1p3", "c2p1"],
             "topics": [
                 {"id": "marks", "heading": "标记名称", "focus": "只介绍原文明确的两种称呼，不猜排序。", "evidence_ids": ["c1p2"]},
                 {"id": "records", "heading": "原文记录", "focus": "保留先后记录，不推断其他人的当前状态。", "evidence_ids": ["c1p3", "c2p1"]}]}]}
        initial = entry_plan.build(self.store, self.work, selected=[1, 2], decisions=self.decisions)
        self.ids = {row["name"]: row["id"] for row in initial["items"]}
        self.decisions["relations"] = [
            self.edge("洛禾", "雾裔", "所属族群", ["c1p1"]),
            self.edge("岑岚", "雾裔", "所属族群", ["c1p1"]),
            self.edge("雾裔", "洛禾", "原文成员", ["c1p1"]),
            self.edge("雾裔", "岑岚", "原文成员", ["c1p1"]),
            self.edge("洛禾", "弦序", "标记记录", ["c1p3", "c2p1"]),
            self.edge("岑岚", "弦序", "标记记录", ["c1p3"]),
            self.edge("弦序", "洛禾", "记载对象", ["c1p3", "c2p1"]),
            self.edge("弦序", "岑岚", "记载对象", ["c1p3"])]
        self.plan = entry_plan.build(self.store, self.work, selected=[1, 2], decisions=self.decisions)
        self.planfile = self.root / "plan.json"
        write_json(self.planfile, self.plan)
        self.output = self.root / "output"
        self.report = self.root / "report.json"

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def edge(self, source, target, label, refs):
        return {"source": self.ids[source], "target": self.ids[target], "label": label, "evidence_ids": refs}

    def draft(self, names, directory="context", selected=None):
        result = draft_context.prepare(self.store, self.work, self.root / directory,
            selected=selected, plan=self.planfile, plan_entries=[self.ids[name] for name in names])
        self.assertLessEqual(result["context_chars"], 12000)
        packet = json.loads(Path(result["context"]).read_text(encoding="utf-8"))
        doc = packet["draft_template"]
        for row in doc["entries"]:
            name = row["name"]
            if name in {"洛禾", "岑岚"}:
                row.update(lead=name + "是某作者《雾港记》中的人物，属于雾裔。", lead_refs=["c1p1"])
            elif name == "雾裔":
                row.update(lead="雾裔是某作者《雾港记》中的族群，在夜航中以潮纹辨路。", lead_refs=["c1p1"])
            else:
                row.update(lead="弦序是某作者《雾港记》中的标记方式。", lead_refs=["c1p2"])
                row["sections"][0].update(body="弦序有回枝和无环两种称呼。", refs=["c1p2"])
                row["sections"][1].update(body="洛禾最初登记为回枝，岑岚登记为无环。洛禾后来改记为无环，其他人的后续状态未在记簿中说明。", refs=["c1p3", "c2p1"])
        return doc, packet

    def finish(self, doc, update=False):
        batch = self.root / "draft.json"
        write_json(batch, doc)
        previous = self.store.get_document(self.work, "entries")
        return finalize_entries.finalize(self.store, self.work, [batch], self.output,
            self.report, plan=self.planfile, update=update,
            expected_hash=digest(previous) if previous is not None else None)

    def publish_initial(self):
        doc, packet = self.draft(["洛禾", "岑岚", "雾裔"])
        result = self.finish(doc)
        self.assertEqual(result["status"], "completed", result)
        return doc, packet, result

    def site_snapshot(self):
        return {str(path.relative_to(self.output / "site")): path.read_bytes()
                for path in (self.output / "site").rglob("*") if path.is_file()}

    def test_candidates_wait_for_judgment_and_values_do_not_become_pages(self):
        base = {row["name"]: row for row in self.base["items"]}
        self.assertEqual(base["雾裔"]["disposition"], "review")
        self.assertEqual(base["石扣"]["disposition"], "review")
        self.assertNotIn("弦序", base)
        self.assertNotIn("回枝", base)
        self.assertNotIn("无环", base)
        plan = {row["name"]: row for row in self.plan["items"]}
        self.assertEqual(plan["雾裔"]["disposition"], "stub")
        self.assertEqual(plan["弦序"]["kind"], "concept")
        self.assertEqual([row["name"] for row in self.plan["items"] if row["kind"] == "concept"], ["弦序"])
        self.assertEqual(plan["石扣"]["disposition"], "review")

    def test_published_shared_pages_link_both_directions_with_their_own_sources(self):
        _, _, result = self.publish_initial()
        self.assertEqual(result["entry_plan"]["missing_page_count"], 1)
        self.assertEqual(result["entry_plan"]["unresolved_count"], 1)
        self.assertFalse(result["entry_plan"]["mechanical_coverage_complete"])
        self.assertEqual(result["entry_plan"]["unverified_page_count"], 0)
        self.assertFalse((self.output / "site/弦序.html").exists())
        person_page = (self.output / "site/洛禾.html").read_text(encoding="utf-8")
        race_page = (self.output / "site/雾裔.html").read_text(encoding="utf-8")
        self.assertIn('所属族群：<a href="雾裔.html">雾裔</a><sup', person_page)
        self.assertIn('原文成员：<a href="洛禾.html">洛禾</a><sup', race_page)
        self.assertIn('原文成员：<a href="岑岚.html">岑岚</a><sup', race_page)
        self.assertNotIn('href="弦序.html"', person_page)
        self.assertEqual(site_errors(self.output / "site"), [])
        saved = self.store.get_document(self.work, "entries")
        for row in saved["entries"]:
            for edge in row["relations"]:
                self.assertEqual({row["sources"][number - 1]["evidence_id"] for number in edge["refs"]}, set(edge["evidence_ids"]))
                self.assertTrue(all(type(number) is int for number in edge["refs"]))
            self.assertEqual(row["aliases"], [])

    def test_adding_concept_page_activates_links_without_rewriting_person_prose(self):
        self.publish_initial()
        before = self.store.get_document(self.work, "entries")
        prose = {row["id"]: (row["lead"], copy.deepcopy(row["sections"])) for row in before["entries"]}
        doc, packet = self.draft(["弦序"], directory="concept-context", selected=[1, 2])
        self.assertEqual({row["id"] for row in packet["evidence"]["paragraphs"]}, {"c1p2", "c1p3", "c2p1"})
        result = self.finish(doc, update=True)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["entry_plan"]["missing_page_count"], 0)
        self.assertEqual(result["entry_plan"]["unverified_page_count"], 0)
        self.assertEqual(result["entry_plan"]["unresolved_count"], 1)
        after = self.store.get_document(self.work, "entries")
        self.assertEqual(len(after["entries"]), 4)
        for row in after["entries"]:
            if row["id"] in prose:
                self.assertEqual((row["lead"], row["sections"]), prose[row["id"]])
        person_page = (self.output / "site/洛禾.html").read_text(encoding="utf-8")
        concept_page = (self.output / "site/弦序.html").read_text(encoding="utf-8")
        self.assertIn('标记记录：<a href="弦序.html">弦序</a><sup', person_page)
        self.assertIn('记载对象：<a href="洛禾.html">洛禾</a><sup', concept_page)
        self.assertIn('记载对象：<a href="岑岚.html">岑岚</a><sup', concept_page)
        for forbidden in ("高于", "低于", "当前成员"):
            self.assertNotIn(forbidden, concept_page)
        for name in ("回枝", "无环", "石扣"):
            self.assertFalse((self.output / "site" / (name + ".html")).exists())
        self.assertEqual(site_errors(self.output / "site"), [])

    def test_reverse_relations_are_not_inferred_from_race_members_or_attributes(self):
        decisions = copy.deepcopy(self.decisions)
        decisions["relations"] = [edge for edge in decisions["relations"]
                                  if edge["source"] not in {self.ids["雾裔"], self.ids["弦序"]}]
        self.plan = entry_plan.build(self.store, self.work, selected=[1, 2], decisions=decisions)
        write_json(self.planfile, self.plan)
        self.publish_initial()
        race_page = (self.output / "site/雾裔.html").read_text(encoding="utf-8")
        self.assertNotIn('id="relations"', race_page)
        self.assertNotIn('href="洛禾.html"', race_page)
        self.assertNotIn('href="岑岚.html"', race_page)

    def test_relation_mismatch_rejects_update_and_preserves_database_and_site(self):
        self.publish_initial()
        saved = self.store.get_document(self.work, "entries")
        site = self.site_snapshot()
        for field, value in (("label", "当前所属"), ("evidence_ids", ["c1p2"])):
            with self.subTest(field=field):
                doc, _ = self.draft(["洛禾"], directory="bad-" + field)
                row = doc["entries"][0]
                expected = [edge for edge in self.plan["relations"] if edge["source"] == row["id"]]
                row["relations"] = [{key: copy.deepcopy(edge[key]) for key in ("target", "label", "evidence_ids")} for edge in expected]
                row["relations"][0][field] = value
                result = self.finish(doc, update=True)
                self.assertEqual(result["status"], "invalid")
                self.assertTrue(any("relations conflict" in error for error in result["error_preview"]), result)
                self.assertEqual(self.store.get_document(self.work, "entries"), saved)
                self.assertEqual(self.site_snapshot(), site)

    def test_out_of_scope_relation_is_rejected_and_existing_site_is_untouched(self):
        self.publish_initial()
        saved = self.store.get_document(self.work, "entries")
        site = self.site_snapshot()
        decisions = copy.deepcopy(self.decisions)
        decisions["relations"][0]["evidence_ids"] = ["c3p1"]
        with self.assertRaisesRegex(ValueError, "relation evidence_ids"):
            entry_plan.build(self.store, self.work, selected=[1, 2], decisions=decisions)
        # Rehashing a malformed plan does not bypass the selected chapter boundary.
        forged = copy.deepcopy(self.plan)
        forged["relations"][0]["evidence_ids"] = ["c3p1"]
        forged["plan_hash"] = digest({key: value for key, value in forged.items() if key != "plan_hash"})
        write_json(self.planfile, forged)
        doc = {"meta": dict(saved["meta"], entry_plan_hash=forged["plan_hash"]), "entries": [saved["entries"][0]]}
        with self.assertRaisesRegex(ValueError, "relation evidence_ids"):
            self.finish(doc, update=True)
        self.assertEqual(self.store.get_document(self.work, "entries"), saved)
        self.assertEqual(self.site_snapshot(), site)
        write_json(self.planfile, self.plan)
        with self.assertRaisesRegex(ValueError, "outside selected chapters"):
            self.draft(["洛禾"], directory="wrong-chapters", selected=[1])
        self.assertEqual(self.site_snapshot(), site)


if __name__ == "__main__":
    unittest.main()
