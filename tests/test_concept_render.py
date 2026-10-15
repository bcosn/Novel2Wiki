import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from core_store import Store, validate_document
from draft_format import expand, template
from draft_validation import shape_errors
import render as single_render
import render_wiki
import review_plan


def entry(eid="e-person", name="某人", kind="character"):
    return {"id": eid, "name": name, "kind": kind, "lead": name + "见于原文。",
            "lead_refs": [1], "sections": [], "sources": [{"chapter": 1, "quote": "原文。"}],
            "reception": {"in_work": "", "user": "", "external": ""},
            "links": [], "aliases": [], "stub": True}


def relation(target="e-concept", label="所属品阶", refs=None):
    value = {"target": target, "label": label, "evidence_ids": ["c1p1"]}
    if refs is not None:
        value["refs"] = refs
    return value


class ConceptRenderTests(unittest.TestCase):
    def test_concept_and_race_roundtrip_without_new_tables(self):
        rows = [entry(), entry("e-concept", "暮潮级", "concept"), entry("e-race", "潮民", "race")]
        rows[0]["relations"] = [relation()]
        doc = {"meta": {"book": "测试书"}, "entries": rows}
        self.assertEqual(shape_errors(doc), [])
        validate_document("entries", doc)
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "workspace.sqlite")
            try:
                store.ensure_work("测试书")
                store.import_documents("测试书", {"entries": doc})
                self.assertEqual(store.get_document("测试书", "entries"), doc)
            finally:
                store.close()

    def test_concept_compact_input_preserves_explicit_relations(self):
        doc = template("书", "", "第1章", "hash", [("concept", "e-concept", "暮潮级")])
        doc["entries"][0].update(lead_facts="原文用来称呼某种品阶。", relations=[relation("e-person", "已知成员")])
        result = expand(doc, {"meta": {"title": "书"}}, "书")
        self.assertIn("中的设定", result["entries"][0]["lead"])
        self.assertEqual(result["entries"][0]["relations"], doc["entries"][0]["relations"])

    def test_invalid_relations_have_structural_diagnostics(self):
        value = entry()
        value["relations"] = [relation(label="品阶\n猜测", refs=[True]),
                              {"target": [], "label": "", "evidence_ids": []}, None]
        errors = shape_errors({"entries": [value]})
        for path in ("relations[1]/label", "relations[1]/refs", "relations[2]/target",
                     "relations[2]/label", "relations[2]/evidence_ids", "relations[3]"):
            self.assertTrue(any(path in error for error in errors), path)
        with self.assertRaises(ValueError):
            validate_document("entries", {"entries": [value]})

    def test_pending_target_is_kept_without_dangling_html_or_alias(self):
        person = entry()
        person["relations"] = [relation("e-unpublished", "所属品阶")]
        doc = {"entries": [person]}
        self.assertEqual(shape_errors(doc), [])
        validate_document("entries", doc)
        before = copy.deepcopy(doc)
        with tempfile.TemporaryDirectory() as temporary:
            render_wiki.render(doc, temporary)
            page = (Path(temporary) / "某人.html").read_text(encoding="utf-8")
            self.assertNotIn('id="relations"', page)
            self.assertEqual(len(list(Path(temporary).glob("*.html"))), 2)
        self.assertNotIn("e-unpublished", single_render.render(doc, "书"))
        self.assertEqual(doc, before)
        self.assertNotIn("所属品阶", single_render.build_link_map(doc["entries"]))

    def test_multi_page_relations_escape_labels_and_titles_and_cite(self):
        person, concept = entry(), entry("e-concept", '暮潮<&"级', "concept")
        person["relations"] = [relation(label='所属<&"品阶', refs=[1, 99, "c1p1", True])]
        with tempfile.TemporaryDirectory() as temporary:
            mapping = render_wiki.render({"entries": [person, concept]}, temporary)
            page = (Path(temporary) / mapping[person["name"]]).read_text(encoding="utf-8")
            index = (Path(temporary) / "index.html").read_text(encoding="utf-8")
            self.assertIn('id="category-concept">设定</h3>', index)
            self.assertIn('所属&lt;&amp;&quot;品阶：<a href=', page)
            self.assertIn('>暮潮&lt;&amp;&quot;级</a><sup class="ref"><a href="#ref1">[1]</a></sup>', page)
            self.assertNotIn('href="#ref99"', page)
            self.assertNotIn('所属<&"品阶', page)

    def test_single_page_relations_use_local_entry_and_source_anchors(self):
        person, concept = entry(), entry('e-"concept', "暮潮级", "concept")
        person["relations"] = [relation(concept["id"], '所属<&"品阶', [1, 99])]
        page = single_render.render({"entries": [person, concept]}, "书")
        self.assertIn('<h2>设定</h2>', page)
        self.assertIn('所属&lt;&amp;&quot;品阶：<a href="#e-&quot;concept">暮潮级</a>', page)
        self.assertIn('href="#e-person-ref1">[1]</a>', page)
        self.assertIn('<li id="e-person-ref1">', page)
        self.assertNotIn('href="#e-person-ref99"', page)

    def test_review_packet_includes_explicit_concept_relations(self):
        concept = entry("e-concept", "暮潮级", "concept")
        concept["relations"] = [relation("e-person", "已知成员")]
        state = {"history": [], "work": "书", "calls": 0,
                 "settings": {"sample_size": 1, "context_chars": 4000}}
        packet = review_plan.plan({"entries": [concept]}, state)
        self.assertEqual(packet["entries"][0]["relations"], concept["relations"])
        self.assertEqual(packet["entries"][0]["kind"], "concept")


if __name__ == "__main__":
    unittest.main()
