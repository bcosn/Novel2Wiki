import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from core_store import Store, digest
import entry_plan
from verify_example import TABLES


class EntryPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="entry-plan-")
        self.store = Store(Path(self.temp.name) / "workspace.sqlite")
        self.documents = {table: {"meta": {}, "items": []} for table in TABLES}
        self.documents["chapters"] = {"meta": {"title": "书", "scope": "前两章"}, "chapters": [
            {"index": 1, "content": "主角与配角到旧宅。\n\n配角遇见路人。\n\n配角看见银匙。"},
            {"index": 2, "content": "配角返回旧宅。\n\n无名客参与转折。"}]}
        self.documents["characters"]["items"] = [
            {"name": "主角", "importance": "main"},
            {"name": "配角", "importance": "minor"},
            {"name": "路人", "importance": "mention"},
            {"name": "无名客"}]
        self.documents["places"]["items"] = [{"name": "天下", "level": 0}, {"name": "旧宅", "level": 3}]
        self.documents["objects"]["items"] = [{"name": "银匙", "level": 3}]
        self.documents["races"]["items"] = [{"name": "人类", "level": 1}]
        self.store.import_documents("书", self.documents)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def rows(self, plan):
        return {(row["kind"], row["name"]): row for row in plan["items"]}

    def decision(self, kind, name, **changes):
        return {"kind": kind, "name": name, "reason": "按关键作用确定材料分工。", **changes}

    def rehash(self, plan):
        plan["plan_hash"] = digest({key: value for key, value in plan.items() if key != "plan_hash"})
        return plan

    def test_importance_is_not_frequency_and_unknown_is_retained(self):
        plan = entry_plan.build(self.store, "书")
        rows = self.rows(plan)
        main, minor = rows["character", "主角"], rows["character", "配角"]
        self.assertLess(len(main["evidence_ids"]), len(minor["evidence_ids"]))
        self.assertEqual((main["importance"], main["depth"]), ("main", "expanded"))
        self.assertEqual((minor["importance"], minor["disposition"], minor["depth"]), ("minor", "stub", "brief"))
        self.assertEqual(rows["character", "路人"]["disposition"], "table_only")
        for key in (("character", "无名客"), ("place", "旧宅"), ("object", "银匙"), ("race", "人类")):
            self.assertEqual((rows[key]["importance"], rows[key]["importance_source"], rows[key]["disposition"]),
                             ("unknown", "missing", "review"))
        self.assertEqual(rows["place", "天下"]["disposition"], "table_only")
        self.assertEqual(len(plan["items"]), 8)
        self.assertEqual(plan["source_hash"], digest({"chapters": self.documents["chapters"],
                         "tables": {key: self.documents[key] for key in TABLES}}))
        self.assertIsNone(entry_plan.validate(plan, self.store, "书"))

    def test_minor_upgrade_and_mention_independent_are_explicit(self):
        decisions = {"items": [self.decision("character", "配角", disposition="standalone", depth="standard"),
                               self.decision("character", "路人", disposition="standalone", depth="brief",
                                             importance="major")]}
        original = copy.deepcopy(decisions)
        rows = self.rows(entry_plan.build(self.store, "书", decisions=decisions))
        self.assertEqual((rows["character", "配角"]["importance"], rows["character", "配角"]["depth"]),
                         ("minor", "standard"))
        self.assertEqual(rows["character", "路人"]["disposition"], "standalone")
        self.assertEqual(rows["character", "路人"]["importance_source"], "decision")
        self.assertEqual(decisions, original)

    def test_stable_generated_ids_and_existing_ids_events_and_aliases(self):
        first = entry_plan.build(self.store, "书")
        original_id = self.rows(first)["character", "配角"]["id"]
        self.assertEqual(original_id, "e-" + digest(["书", "character", "配角"])[:16])
        self.store.import_documents("书", {"entries": {"entries": [
            {"id": "legacy-main", "kind": "character", "name": "主角", "aliases": ["配角"]},
            {"id": "legacy-event", "kind": "event", "name": "关键转折"},
            {"id": "legacy-narrator", "kind": "character", "name": "叙述者"}]}})
        second = entry_plan.build(self.store, "书")
        rows = self.rows(second)
        self.assertEqual(rows["character", "主角"]["id"], "legacy-main")
        self.assertEqual(rows["character", "配角"]["id"], original_id)
        self.assertEqual(rows["event", "关键转折"]["id"], "legacy-event")
        self.assertEqual(rows["character", "叙述者"]["disposition"], "standalone")
        characters = copy.deepcopy(self.documents["characters"])
        characters["items"].reverse()
        self.store.import_documents("书", {"characters": characters})
        self.assertEqual(self.rows(entry_plan.build(self.store, "书"))["character", "配角"]["id"], original_id)

    def test_hash_tampering_and_source_changes_are_rejected(self):
        plan = entry_plan.build(self.store, "书")
        changed = copy.deepcopy(plan)
        changed["items"][0]["depth"] = "brief"
        with self.assertRaisesRegex(ValueError, "plan_hash"):
            entry_plan.validate(changed, self.store, "书")
        chapters = copy.deepcopy(self.documents["chapters"])
        chapters["chapters"][1]["content"] += "\n\n后续新材料。"
        self.store.import_documents("书", {"chapters": chapters})
        with self.assertRaisesRegex(ValueError, "stale"):
            entry_plan.validate(plan, self.store, "书")
        plan = entry_plan.build(self.store, "书")
        objects = copy.deepcopy(self.documents["objects"])
        objects["items"][0]["note"] = "新增结构信息"
        self.store.import_documents("书", {"objects": objects})
        with self.assertRaisesRegex(ValueError, "stale"):
            entry_plan.validate(plan, self.store, "书")

    def test_current_entry_mapping_conflicts_are_checked_separately(self):
        plan = entry_plan.build(self.store, "书")
        self.store.import_documents("书", {"entries": {"entries": [
            {"id": "new-existing-id", "kind": "character", "name": "主角"}]}})
        with self.assertRaisesRegex(ValueError, "mapping"):
            entry_plan.validate(plan, self.store, "书")
        plan = entry_plan.build(self.store, "书")
        self.store.import_documents("书", {"entries": {"entries": [
            {"id": "id-one", "kind": "character", "name": "主角"},
            {"id": "id-two", "kind": "character", "name": "主角"}]}})
        with self.assertRaisesRegex(ValueError, "mapping"):
            entry_plan.validate(plan, self.store, "书")

    def test_merge_preserves_entity_and_never_creates_redirect_or_alias(self):
        base = entry_plan.build(self.store, "书")
        target = self.rows(base)["character", "主角"]["id"]
        source = self.rows(base)["character", "配角"]["id"]
        decision = self.decision("character", "配角", disposition="merge", depth="none",
                                 merge_target=target, evidence_ids=["c1p1"])
        plan = entry_plan.build(self.store, "书", decisions={"items": [decision]})
        rows = self.rows(plan)
        merged = rows["character", "配角"]
        self.assertEqual(merged["id"], source)
        self.assertEqual(merged["merge_target"], target)
        self.assertNotIn("aliases", merged)
        self.assertNotIn("redirect", merged)
        self.assertEqual(rows["character", "主角"], self.rows(base)["character", "主角"])
        entry_plan.validate(plan, self.store, "书")

    def test_invalid_merge_targets_self_chains_cycles_and_missing_evidence(self):
        rows = self.rows(entry_plan.build(self.store, "书"))
        source, target = rows["character", "配角"]["id"], rows["character", "主角"]["id"]
        for bad_target in ("absent", source, rows["character", "路人"]["id"]):
            with self.subTest(target=bad_target), self.assertRaises(ValueError):
                entry_plan.build(self.store, "书", decisions={"items": [self.decision(
                    "character", "配角", disposition="merge", depth="none", merge_target=bad_target,
                    evidence_ids=["c1p1"])]})
        merge = self.decision("character", "配角", disposition="merge", depth="none",
                              merge_target=target, evidence_ids=[])
        with self.assertRaises(ValueError):
            entry_plan.build(self.store, "书", decisions={"items": [merge]})
        first = self.decision("character", "配角", disposition="merge", depth="none",
                              merge_target=target, evidence_ids=["c1p1"])
        second = self.decision("character", "主角", disposition="merge", depth="none",
                               merge_target=source, evidence_ids=["c1p1"])
        with self.assertRaises(ValueError):
            entry_plan.build(self.store, "书", decisions={"items": [first, second]})
        second["merge_target"] = rows["character", "路人"]["id"]
        third = self.decision("character", "路人", disposition="standalone", depth="brief")
        with self.assertRaises(ValueError):
            entry_plan.build(self.store, "书", decisions={"items": [first, second, third]})

    def test_selected_catalog_and_event_candidates_require_current_evidence(self):
        event = self.decision("event", "关键转折", evidence_ids=["c2p2"],
                              disposition="standalone", depth="standard")
        plan = entry_plan.build(self.store, "书", selected=[2], decisions={"items": [event]})
        self.assertEqual(plan["selected_chapters"], [2])
        self.assertEqual(plan["scope"], "章节：2")
        self.assertEqual(self.rows(plan)["event", "关键转折"]["evidence_ids"], ["c2p2"])
        entry_plan.validate(plan, self.store, "书")
        for bad_refs in (["c1p1"], ["other-work:c2p2"], ["c99p1"], [False], [], "c2p2"):
            bad = dict(event, evidence_ids=bad_refs)
            with self.subTest(refs=bad_refs), self.assertRaises(ValueError):
                entry_plan.build(self.store, "书", selected=[2], decisions={"items": [bad]})
        with self.assertRaises(ValueError):
            entry_plan.validate(plan, self.store, "别书")
        for selected in ([], [0], [True], [3], "2"):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                entry_plan.build(self.store, "书", selected=selected)

    def test_topics_are_bounded_unique_metadata_with_valid_refs(self):
        topic = {"id": "main-theme", "heading": "作用与关系", "focus": "整理推动事件的作用及关系变化。",
                 "evidence_ids": ["c1p1"]}
        decision = self.decision("character", "主角", topics=[topic])
        plan = entry_plan.build(self.store, "书", decisions={"items": [decision]})
        entry_plan.validate(plan, self.store, "书")
        bad_topics = [dict(topic, id=""), dict(topic, heading=""), dict(topic, heading="字" * 41),
                      dict(topic, focus="字" * 161), dict(topic, evidence_ids=["missing"]),
                      dict(topic, body="正文不应放入计划")]
        for bad in bad_topics:
            with self.subTest(keys=list(bad)), self.assertRaises(ValueError):
                entry_plan.build(self.store, "书", decisions={"items": [dict(decision, topics=[bad])]})
        with self.assertRaises(ValueError):
            entry_plan.build(self.store, "书", decisions={"items": [decision,
                self.decision("character", "配角", topics=[topic])]})

    def test_long_work_topics_narrow_literal_name_candidates_without_truncation(self):
        chapters = {"meta": {}, "chapters": [
            {"index": index, "content": "主角与配角再次到旧宅。"}
            for index in range(1, 49)]}
        self.store.import_documents("书", {"chapters": chapters})
        baseline = entry_plan.build(self.store, "书")
        self.assertEqual(len(self.rows(baseline)["character", "主角"]["evidence_ids"]), 48)
        topics = [
            {"id": "early", "heading": "早期作用", "focus": "整理本主题所需材料。",
             "evidence_ids": ["c4p1", "c36p1"]},
            {"id": "later", "heading": "后续关系", "focus": "只使用选择的证据。",
             "evidence_ids": ["c36p1"]}]
        decisions = {"items": [self.decision("character", "主角", topics=topics)]}
        original = copy.deepcopy(decisions)
        narrowed = entry_plan.build(self.store, "书", decisions=decisions)
        self.assertEqual(self.rows(narrowed)["character", "主角"]["evidence_ids"], ["c4p1", "c36p1"])
        self.assertEqual(narrowed["source_hash"], baseline["source_hash"])
        self.assertNotEqual(narrowed["plan_hash"], baseline["plan_hash"])
        self.assertEqual(narrowed["selected_chapters"], list(range(1, 49)))
        self.assertEqual(decisions, original)
        entry_plan.validate(narrowed, self.store, "书")
        # A short entry without selected topics retains the complete locator pool.
        minor = self.decision("character", "配角", depth="brief")
        no_topic = entry_plan.build(self.store, "书", decisions={"items": [minor]})
        self.assertEqual(self.rows(no_topic)["character", "配角"]["evidence_ids"],
                         self.rows(baseline)["character", "配角"]["evidence_ids"])

    def test_explicit_item_evidence_overrides_topic_union(self):
        topic = {"id": "main", "heading": "作用", "focus": "按主题选择材料。", "evidence_ids": ["c1p1"]}
        for refs in (["c2p2"], []):
            decision = self.decision("character", "主角", topics=[topic], evidence_ids=refs)
            plan = entry_plan.build(self.store, "书", decisions={"items": [decision]})
            self.assertEqual(self.rows(plan)["character", "主角"]["evidence_ids"], refs)
            entry_plan.validate(plan, self.store, "书")
        # New explicitly named settings can supply their evidence through topics.
        decision = self.decision("concept", "设定甲", topics=[topic],
                                 disposition="standalone", depth="standard")
        plan = entry_plan.build(self.store, "书", decisions={"items": [decision]})
        self.assertEqual(self.rows(plan)["concept", "设定甲"]["evidence_ids"], ["c1p1"])
        entry_plan.validate(plan, self.store, "书")

    def test_schema_coverage_reason_and_fake_importance_are_rejected(self):
        plan = entry_plan.build(self.store, "书")
        for change in (lambda p: p["items"].pop(),
                       lambda p: p["items"][0].update(importance="mention"),
                       lambda p: p["items"][0].update(body="正文"),
                       lambda p: p["items"][0].update(reason=""),
                       lambda p: p["items"][0].update(evidence_ids=[{}]),
                       lambda p: p.update(selected_chapters=[2, 1])):
            bad = copy.deepcopy(plan)
            change(bad)
            self.rehash(bad)
            with self.assertRaises(ValueError):
                entry_plan.validate(bad, self.store, "书")
        for decision in ({"kind": "character", "name": "配角", "depth": "standard"},
                         self.decision("character", "配角", reason="字" * 201),
                         self.decision("character", "配角", body="正文")):
            with self.assertRaises(ValueError):
                entry_plan.build(self.store, "书", decisions={"items": [decision]})

    def test_interactions_do_not_create_event_titles(self):
        self.store.import_documents("书", {"interactions": {"items": [
            {"id": "interaction-1", "action": "不可自动成为条目名", "from": "主角", "to": "配角"}]}})
        plan = entry_plan.build(self.store, "书")
        self.assertFalse(any(row["kind"] == "event" for row in plan["items"]))

    def test_concepts_require_explicit_evidence_without_automatic_categories(self):
        self.store.import_documents("书", {"races": {"items": [
            {"name": "人类", "level": 1}, {"name": "生灵", "level": 0}]}})
        base = entry_plan.build(self.store, "书")
        self.assertFalse(any(row["kind"] == "concept" for row in base["items"]))
        for name in ("人类", "生灵"):
            self.assertEqual(self.rows(base)["race", name]["disposition"], "review")
        # A named setting may share a label with a concrete entity; identity uses kind.
        decision = self.decision("concept", "银匙", evidence_ids=["c1p3"],
                                 disposition="standalone", depth="standard")
        plan = entry_plan.build(self.store, "书", decisions={"items": [decision]})
        rows = self.rows(plan)
        self.assertIn(("object", "银匙"), rows)
        self.assertIn(("concept", "银匙"), rows)
        self.assertNotEqual(rows["object", "银匙"]["id"], rows["concept", "银匙"]["id"])
        self.assertEqual(rows["concept", "银匙"]["id"], "e-" + digest(["书", "concept", "银匙"])[:16])
        self.assertEqual(rows["concept", "银匙"]["importance"], "unknown")
        self.assertEqual(set(rows["concept", "银匙"]), entry_plan.ITEM_FIELDS)
        entry_plan.validate(plan, self.store, "书")
        for changes in ({"evidence_ids": []}, {"evidence_ids": ["missing"]},
                        {"rank": "invented"}, {"kind": "rank"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                entry_plan.build(self.store, "书", decisions={"items": [dict(decision, **changes)]})

    def test_existing_concept_reuses_id_and_same_name_of_other_kind_stays_separate(self):
        documents = copy.deepcopy(self.documents)
        documents["entries"] = {"entries": [
            {"id": "legacy-concept", "kind": "concept", "name": "银匙"},
            {"id": "legacy-object", "kind": "object", "name": "银匙"}]}

        class Snapshot:
            def get_document(self, work, kind):
                return copy.deepcopy(documents.get(kind)) if work == "书" else None

        snapshot = Snapshot()
        plan = entry_plan.build(snapshot, "书")
        rows = self.rows(plan)
        self.assertEqual(rows["concept", "银匙"]["id"], "legacy-concept")
        self.assertEqual(rows["object", "银匙"]["id"], "legacy-object")
        self.assertEqual(rows["concept", "银匙"]["disposition"], "standalone")
        entry_plan.validate(plan, snapshot, "书")
        decision = self.decision("concept", "银匙", evidence_ids=["c1p3"],
                                 importance="major", topics=[])
        rebuilt = entry_plan.build(snapshot, "书", decisions={"items": [decision]})
        self.assertEqual(self.rows(rebuilt)["concept", "银匙"]["id"], "legacy-concept")
        self.assertEqual(documents["entries"]["entries"][0]["id"], "legacy-concept")

    def test_relations_are_explicit_directional_and_preserve_unpublished_sources(self):
        base = entry_plan.build(self.store, "书")
        self.assertEqual(base["relations"], [])
        rows = self.rows(base)
        target = rows["character", "主角"]["id"]
        unpublished = rows["race", "人类"]["id"]
        table_only = rows["character", "路人"]["id"]
        relations = [
            {"source": unpublished, "target": target, "label": "相关人物", "evidence_ids": ["c1p1"]},
            {"source": table_only, "target": target, "label": "相关人物", "evidence_ids": ["c1p2"]}]
        decisions = {"relations": relations}
        original = copy.deepcopy(decisions)
        before = list(self.store.connection.iterdump())
        plan = entry_plan.build(self.store, "书", decisions=decisions)
        self.assertEqual(plan["relations"], relations)
        self.assertEqual(self.rows(plan)["race", "人类"]["disposition"], "review")
        self.assertEqual(self.rows(plan)["character", "路人"]["disposition"], "table_only")
        self.assertEqual(decisions, original)
        self.assertFalse(any("alias" in field or "redirect" in field for row in plan["items"] for field in row))
        entry_plan.validate(plan, self.store, "书")
        self.assertEqual(list(self.store.connection.iterdump()), before)
        self.assertEqual(entry_plan.inspect(plan)["relationship_count"], 2)
        first = {"source": rows["character", "配角"]["id"], "target": target,
                 "label": "影响对象", "evidence_ids": ["c1p1"]}
        forward = entry_plan.build(self.store, "书", decisions={"relations": [first]})
        self.assertEqual(len(forward["relations"]), 1)
        reverse = {"source": target, "target": first["source"], "label": "影响来源", "evidence_ids": ["c1p1"]}
        two_way = entry_plan.build(self.store, "书", decisions={"relations": [first, reverse]})
        self.assertEqual(two_way["relations"], [first, reverse])

    def committed_graph(self):
        rows = self.rows(entry_plan.build(self.store, "书"))
        main, minor = rows["character", "主角"]["id"], rows["character", "配角"]["id"]
        graph = [
            {"source": main, "target": minor, "label": "同行者", "evidence_ids": ["c1p1"]},
            {"source": minor, "target": main, "label": "同行者", "evidence_ids": ["c1p1"]},
        ]
        document = {"meta": {"evidence_hash": digest(self.documents["chapters"])}, "entries": [
            {"id": main, "kind": "character", "name": "主角", "relations": [
                {key: copy.deepcopy(value) for key, value in graph[0].items() if key != "source"} | {"refs": [3]}]},
            {"id": minor, "kind": "character", "name": "配角", "relations": [
                {key: copy.deepcopy(value) for key, value in graph[1].items() if key != "source"} | {"refs": [7]}]},
        ]}
        self.store.import_documents("书", {"entries": document})
        return graph, document

    def test_rebuilding_without_relation_decisions_preserves_committed_graph(self):
        graph, document = self.committed_graph()
        original = copy.deepcopy(document)
        before = list(self.store.connection.iterdump())
        for decisions in (None, {"items": []}, {"items": [
                self.decision("character", "主角", depth="brief")]}):
            with self.subTest(decisions=decisions):
                plan = entry_plan.build(self.store, "书", decisions=decisions)
                self.assertEqual(plan["relations"], graph)
                self.assertTrue(all("refs" not in edge for edge in plan["relations"]))
                entry_plan.validate(plan, self.store, "书")
        self.assertEqual(document, original)
        self.assertEqual(list(self.store.connection.iterdump()), before)

    def test_explicit_relation_decisions_replace_the_complete_graph(self):
        graph, _ = self.committed_graph()
        empty = entry_plan.build(self.store, "书", decisions={"relations": []})
        self.assertEqual(empty["relations"], [])
        replacement = [dict(graph[0], label="相关人物", evidence_ids=["c1p2"])]
        plan = entry_plan.build(self.store, "书", decisions={"relations": replacement})
        self.assertEqual(plan["relations"], replacement)
        self.assertEqual(self.store.get_document("书", "entries")["entries"][0]["relations"][0]["label"], "同行者")

    def test_inherited_relations_require_a_matching_frozen_evidence_hash(self):
        graph, document = self.committed_graph()
        changed = copy.deepcopy(self.documents["chapters"])
        changed["chapters"][0]["content"] = "新增开场。\n\n" + changed["chapters"][0]["content"]
        self.store.import_documents("书", {"chapters": changed})
        for decisions in (None, {"items": []}):
            with self.subTest(decisions=decisions), self.assertRaisesRegex(ValueError, "explicit decisions.relations"):
                entry_plan.build(self.store, "书", decisions=decisions)
        # Explicit re-review may clear or replace the graph against the new source.
        self.assertEqual(entry_plan.build(self.store, "书", decisions={"relations": []})["relations"], [])
        replacement = [dict(graph[0], evidence_ids=["c1p2"])]
        self.assertEqual(entry_plan.build(self.store, "书", decisions={"relations": replacement})["relations"], replacement)
        self.assertEqual(self.store.get_document("书", "entries"), document)

    def test_missing_evidence_hash_cannot_silently_rebind_committed_relations(self):
        _, document = self.committed_graph()
        for meta in ({}, {"evidence_hash": None}, {"evidence_hash": "wrong"}):
            with self.subTest(meta=meta):
                document["meta"] = meta
                self.store.import_documents("书", {"entries": document})
                with self.assertRaisesRegex(ValueError, "stale or unconfirmed"):
                    entry_plan.build(self.store, "书")
                self.assertEqual(entry_plan.build(self.store, "书", decisions={"relations": []})["relations"], [])

    def test_inherited_exact_duplicates_are_deduplicated_and_conflicts_rejected(self):
        graph, document = self.committed_graph()
        document["entries"][0]["relations"].append(dict(document["entries"][0]["relations"][0], refs=[9]))
        self.store.import_documents("书", {"entries": document})
        self.assertEqual(entry_plan.build(self.store, "书")["relations"], graph)
        document["entries"][0]["relations"][-1]["evidence_ids"] = ["c1p2"]
        self.store.import_documents("书", {"entries": document})
        with self.assertRaisesRegex(ValueError, "duplicate directed relation"):
            entry_plan.build(self.store, "书")
        # Inherited references still obey the selected plan's evidence scope.
        self.store.import_documents("书", {"entries": dict(document, entries=[document["entries"][1]])})
        with self.assertRaisesRegex(ValueError, "evidence_ids"):
            entry_plan.build(self.store, "书", selected=[2])

    def test_relation_validation_rejects_invalid_ids_labels_evidence_and_targets(self):
        rows = self.rows(entry_plan.build(self.store, "书"))
        source, target = rows["character", "配角"]["id"], rows["character", "主角"]["id"]
        valid = {"source": source, "target": target, "label": "关联", "evidence_ids": ["c1p1"]}
        invalid = [dict(valid, source="absent"), dict(valid, target="absent"), dict(valid, target=source),
                   dict(valid, target=rows["race", "人类"]["id"]),
                   dict(valid, target=rows["character", "路人"]["id"]),
                   dict(valid, label=""), dict(valid, label="字" * 31), dict(valid, label="两行\n标签"),
                   dict(valid, evidence_ids=[]), dict(valid, evidence_ids=["c99p1"]),
                   dict(valid, evidence_ids=["other-work:c1p1"]), dict(valid, evidence_ids=[{}]),
                   dict(valid, aliases=["不应合并"])]
        for relation in invalid:
            with self.subTest(fields=list(relation)), self.assertRaises(ValueError):
                entry_plan.build(self.store, "书", decisions={"relations": [relation]})
        for relations in (None, {}, "not-an-array", [None], [valid, valid]):
            with self.subTest(relations=relations), self.assertRaises(ValueError):
                entry_plan.build(self.store, "书", decisions={"relations": relations})
        with self.assertRaises(ValueError):
            entry_plan.build(self.store, "书", selected=[2], decisions={"relations": [valid]})

    def test_relations_are_hash_bound_and_legacy_plans_remain_valid(self):
        plan = entry_plan.build(self.store, "书")
        old = copy.deepcopy(plan)
        del old["relations"]
        self.rehash(old)
        entry_plan.validate(old, self.store, "书")
        self.assertEqual(entry_plan.inspect(old)["relationship_count"], 0)
        rows = self.rows(plan)
        relation = {"source": rows["character", "配角"]["id"],
                    "target": rows["character", "主角"]["id"],
                    "label": "关联", "evidence_ids": ["c1p1"]}
        plan = entry_plan.build(self.store, "书", decisions={"relations": [relation]})
        changed = copy.deepcopy(plan)
        changed["relations"][0]["label"] = "改标签"
        with self.assertRaisesRegex(ValueError, "plan_hash"):
            entry_plan.validate(changed, self.store, "书")
        changed["relations"][0]["evidence_ids"] = ["missing"]
        self.rehash(changed)
        with self.assertRaisesRegex(ValueError, "evidence_ids"):
            entry_plan.validate(changed, self.store, "书")
        chapters = copy.deepcopy(self.documents["chapters"])
        chapters["chapters"][1]["content"] += "\n\n后续材料。"
        self.store.import_documents("书", {"chapters": chapters})
        with self.assertRaisesRegex(ValueError, "stale"):
            entry_plan.validate(plan, self.store, "书")

    def test_read_operations_do_not_modify_database_or_input_plans(self):
        before = list(self.store.connection.iterdump())
        changes = self.store.connection.total_changes
        plan = entry_plan.build(self.store, "书")
        original = copy.deepcopy(plan)
        entry_plan.validate(plan, self.store, "书")
        entry_plan.inspect(plan)
        self.assertEqual(plan, original)
        self.assertEqual(self.store.connection.total_changes, changes)
        self.assertEqual(list(self.store.connection.iterdump()), before)
        broken = copy.deepcopy(plan)
        broken["plan_hash"] = "invalid"
        with self.assertRaises(ValueError):
            entry_plan.validate(broken, self.store, "书")
        self.assertEqual(list(self.store.connection.iterdump()), before)

    def test_summary_is_paginated_bounded_and_omits_source_prose(self):
        plan = entry_plan.build(self.store, "书")
        plan["items"] = [dict(plan["items"][0], id="id" + str(index), name="名字" * 500,
                              reason="private planning text", evidence_ids=["private quote"])
                         for index in range(200)]
        result = entry_plan.inspect(plan, offset=3, limit=12)
        self.assertEqual((result["total"], len(result["items"]), result["next_offset"]), (200, 12, 15))
        self.assertEqual(sum(result["counts"].values()), 200)
        self.assertLessEqual(len(json.dumps(result)), 6000)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 6000)
        self.assertNotIn("private", json.dumps(result))
        self.assertIsNone(entry_plan.inspect(plan, offset=199)["next_offset"])
        for row in plan["items"]:
            row.update(id="😀" * 40, name="😀" * 24)
        result = entry_plan.inspect(plan, limit=12)
        self.assertLessEqual(len(json.dumps(result)), 6000)
        self.assertGreater(len(result["items"]), 0)
        self.assertEqual(result["next_offset"], len(result["items"]))
        for offset, limit in ((-1, 8), (0, 0), (0, 13), (True, 8)):
            with self.assertRaises(ValueError):
                entry_plan.inspect(plan, offset, limit)


if __name__ == "__main__":
    unittest.main()
