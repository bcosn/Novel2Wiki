import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from core_store import Store, write_json
import entry_plan
import plan_context
from verify_example import TABLES


class PlanContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state.sqlite")
        self.chapters = {
            "meta": {"title": "测试书", "creator": "作者", "scope": "第2、7、11章"},
            "chapters": [
                {"index": 2, "content": "甲见到乙。乙持有复位标记。"},
                {"index": 7, "content": "甲的紫线数记作折羽，藏匣位置是东架。"},
                {"index": 11, "content": "乙在后来的章节离开。"},
            ],
        }
        self.tables = {key: {"meta": {"scope": "第2、7、11章"}, "items": []}
                       for key in TABLES}
        self.tables["characters"]["items"] = [
            {"name": "甲", "importance": "main", "first_chapter": 2},
            {"name": "乙", "importance": "minor", "first_chapter": 2},
        ]
        self.tables["custom_attributes"].update(
            attribute_defs=[
                {"name": "紫线数", "target_kind": "character", "definition": "原文定义尚待判断。"},
                {"name": "藏匣位置", "target_kind": "character", "definition": "一项普通位置记录。"},
                {"name": "复位标记", "target_kind": "character", "definition": "另一项自定义记录。"},
            ],
            items=[
                {"name": "紫线数", "target_kind": "character", "target": "甲", "value": "折羽",
                 "chapter": 7, "evidence": "甲的紫线数记作折羽"},
                {"name": "藏匣位置", "target_kind": "character", "target": "甲", "value": "东架",
                 "chapter": 7, "evidence": "藏匣位置是东架"},
                {"name": "复位标记", "target_kind": "character", "target": "乙", "value": "悬纹",
                 "chapter": 2, "evidence": "乙持有复位标记"},
            ],
        )
        self.store.import_documents("测试书", dict(self.tables, chapters=self.chapters))
        self.planfile = self.root / "plan.json"
        self.rebuild_plan()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def rebuild_plan(self, selected=None):
        self.plan = entry_plan.build(self.store, "测试书", selected=selected)
        self.ids = {row["name"]: row["id"] for row in self.plan["items"]}
        write_json(self.planfile, self.plan)

    def prepare(self, name="context", **kwargs):
        return plan_context.prepare(self.store, "测试书", self.planfile,
                                    self.root / name, **kwargs)

    def packet(self, result):
        return json.loads(Path(result["context"]).read_text(encoding="utf-8"))

    def test_candidate_ids_select_only_requested_entities_and_matching_facts(self):
        before = self.store.connection.total_changes
        result = self.prepare(candidates=[self.ids["甲"]], selected=[7])
        packet = self.packet(result)
        self.assertEqual([row["id"] for row in packet["candidates"]], [self.ids["甲"]])
        self.assertEqual([row["name"] for row in packet["facts"]["characters"]["items"]], ["甲"])
        self.assertEqual({row["target"] for row in packet["facts"]["custom_attributes"]["items"]}, {"甲"})
        self.assertEqual([row["name"] for row in packet["facts"]["custom_attributes"]["attribute_defs"]],
                         ["紫线数", "藏匣位置"])
        self.assertEqual(self.store.connection.total_changes, before)

    def test_scope_keeps_opaque_evidence_ids_and_actual_chapter_numbers(self):
        result = self.prepare(tables=["custom_attributes"], selected=[7])
        packet = self.packet(result)
        self.assertEqual(result["selected_chapters"], [7])
        self.assertEqual([(row["id"], row["chapter"]) for row in packet["evidence"]["paragraphs"]],
                         [("c2p1", 7)])
        self.assertEqual({row["chapter"] for row in packet["facts"]["custom_attributes"]["items"]}, {7})
        self.assertNotIn("后来的章节", json.dumps(packet, ensure_ascii=False))

    def test_scope_cannot_expand_beyond_frozen_plan(self):
        self.rebuild_plan(selected=[2, 7])
        with self.assertRaisesRegex(ValueError, "inside the plan scope"):
            self.prepare(tables=["custom_attributes"], selected=[11])
        self.assertFalse((self.root / "context").exists())

    def test_empty_and_invalid_explicit_scopes_are_rejected_without_writes(self):
        for selected in ([], (), [True], [0], [-1]):
            with self.subTest(selected=selected):
                with self.assertRaises(ValueError):
                    self.prepare(tables=["custom_attributes"], selected=selected)
                self.assertFalse((self.root / "context").exists())

    def test_unknown_duplicate_and_oversized_candidate_selections_are_rejected(self):
        for candidates in (["missing"], [self.ids["甲"], self.ids["甲"]],
                           ["candidate-%d" % index for index in range(7)]):
            with self.subTest(candidates=candidates):
                with self.assertRaises(ValueError):
                    self.prepare(candidates=candidates)
                self.assertFalse((self.root / "context").exists())
        with self.assertRaises(ValueError):
            self.prepare()
        with self.assertRaises(ValueError):
            self.prepare(tables=["not_a_table"])

    def test_explicit_custom_attributes_need_no_predeclared_taxonomy_words(self):
        original_plan = self.planfile.read_bytes()
        result = self.prepare(tables=["custom_attributes"], selected=[7])
        packet = self.packet(result)
        self.assertEqual(packet["candidates"], [])
        self.assertEqual(set(packet["facts"]), {"custom_attributes"})
        self.assertEqual([(row["name"], row["value"]) for row in packet["facts"]["custom_attributes"]["items"]],
                         [("紫线数", "折羽"), ("藏匣位置", "东架")])
        self.assertEqual(packet["facts"]["custom_attributes"]["attribute_defs"],
                         self.tables["custom_attributes"]["attribute_defs"])
        self.assertEqual(self.planfile.read_bytes(), original_plan)
        self.assertIsNone(self.store.get_document("测试书", "entries"))
    def test_decision_template_is_separate_from_context_metadata_and_requires_judgement(self):
        packet=self.packet(self.prepare(candidates=[self.ids["甲"]],selected=[7]))
        template=packet["decision_template"]
        self.assertEqual(set(template),{"items"})
        self.assertEqual(template["items"][0]["name"],"甲")
        self.assertEqual(template["items"][0]["reason"],"")
        with self.assertRaises(ValueError):entry_plan.build(self.store,"测试书",decisions=template)

    def test_exact_12000_character_budget_and_overflow_leave_no_partial_files(self):
        result = self.prepare(name="probe", tables=["custom_attributes"], selected=[7])
        base_size = result["context_chars"]
        self.assertLess(base_size, 12000)
        self.chapters["chapters"][1]["content"] += "界" * (12000 - base_size)
        self.store.import_documents("测试书", {"chapters": self.chapters})
        self.rebuild_plan()
        exact = self.prepare(name="exact", tables=["custom_attributes"], selected=[7])
        text = Path(exact["context"]).read_text(encoding="utf-8")
        self.assertEqual(exact["context_chars"], 12000)
        self.assertEqual(len(text), 12000)
        self.assertGreater(len(text.encode("utf-8")), 12000)
        self.chapters["chapters"][1]["content"] += "界"
        self.store.import_documents("测试书", {"chapters": self.chapters})
        self.rebuild_plan()
        with self.assertRaisesRegex(ValueError, r"12001/12000"):
            self.prepare(name="overflow", tables=["custom_attributes"], selected=[7])
        self.assertFalse((self.root / "overflow").exists())

    def test_stale_chapter_snapshot_is_rejected_before_writing(self):
        changed = copy.deepcopy(self.chapters)
        changed["chapters"][0]["content"] += "甲又开口。"
        self.store.import_documents("测试书", {"chapters": changed})
        before = self.store.connection.total_changes
        with self.assertRaisesRegex(ValueError, "stale"):
            self.prepare(candidates=[self.ids["甲"]], selected=[7])
        self.assertFalse((self.root / "context").exists())
        self.assertEqual(self.store.connection.total_changes, before)
    def test_explicit_paragraph_selection_handles_long_chapters_without_silent_truncation(self):
        self.chapters["chapters"][1]["content"]+="\n\n"+"长背景"*5000
        self.store.import_documents("测试书",{"chapters":self.chapters});self.rebuild_plan()
        with self.assertRaisesRegex(ValueError,"exceeds budget"):
            self.prepare(candidates=[self.ids["甲"]],selected=[7])
        result=self.prepare(name="small",candidates=[self.ids["甲"]],selected=[7],evidence_ids=["c2p1"])
        packet=self.packet(result)
        self.assertEqual([p["id"] for p in packet["evidence"]["paragraphs"]],["c2p1"])
        self.assertEqual(packet["evidence"]["omitted_paragraphs"],1)
        self.assertTrue(packet["evidence"]["partial_selection"])
        self.assertGreater(packet["candidates"][0]["candidate_evidence_omitted"],0)
        for refs in ([],["c1p1"],["missing"],["c2p1","c2p1"]):
            with self.assertRaises(ValueError):self.prepare(name="bad",tables=["custom_attributes"],selected=[7],evidence_ids=refs)
        self.assertFalse((self.root/"bad").exists())

    def test_stale_table_snapshot_is_rejected_before_writing(self):
        changed = copy.deepcopy(self.tables["custom_attributes"])
        changed["items"][0]["note"] = "新增待判断记录。"
        self.store.import_documents("测试书", {"custom_attributes": changed})
        with self.assertRaisesRegex(ValueError, "stale"):
            self.prepare(tables=["custom_attributes"], selected=[7])
        self.assertFalse((self.root / "context").exists())


if __name__ == "__main__":
    unittest.main()
