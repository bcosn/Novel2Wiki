import copy
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from core_store import Store, validate_document
from draft_format import expand, template
from draft_validation import shape_errors
import render as single_render
import render_wiki
import review_plan
from verify_example import PageLinks, site_errors


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
        self.assertIn('所属&lt;&amp;&quot;品阶：<a href="#e-%22concept">暮潮级</a>', page)
        self.assertIn('href="#e-person-ref1">[1]</a>', page)
        self.assertIn('<li id="e-person-ref1">', page)
        self.assertNotIn('href="#e-person-ref99"', page)

    def test_multi_page_local_urls_resolve_reserved_characters_and_unicode_once(self):
        names = ["设定#一", "设定%20一", "设定 一", "暮潮é级", "中文品阶", "设定&一"]
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                person, concept = entry(), entry("e-concept", name, "concept")
                person["lead"] += "记载涉及" + name + "。"
                person["relations"] = [relation(refs=[1])]
                person["links"] = [concept["id"]]
                mapping = render_wiki.render({"entries": [person, concept]}, temporary)
                filename = mapping[name]
                self.assertEqual(filename, render_wiki.slug(name))
                self.assertTrue((Path(temporary) / filename).is_file())
                expected_href = render_wiki.local_component(filename)
                self.assertEqual(unquote(urlsplit(expected_href).path), filename)
                self.assertEqual(urlsplit(expected_href).fragment, "")
                index, article = PageLinks(), PageLinks()
                index.feed((Path(temporary) / "index.html").read_text(encoding="utf-8"))
                article.feed((Path(temporary) / mapping[person["name"]]).read_text(encoding="utf-8"))
                self.assertIn(expected_href, index.links)
                # Prose auto-link, explicit relationship, and related-entry link.
                self.assertEqual(article.links.count(expected_href), 3)
                self.assertEqual(site_errors(Path(temporary)), [])
                if "%20" in name:
                    self.assertIn("%2520", expected_href)
                if name == "中文品阶":
                    self.assertEqual(expected_href, "中文品阶.html")

    def test_single_page_encoded_fragments_match_raw_ids_and_source_ids(self):
        for identity in ("e#一", "e%20一", "e 一", "eé一", "e?一", "中文编号"):
            with self.subTest(identity=identity):
                person, concept = entry("p#%20 空格"), entry(identity, "暮潮级", "concept")
                person["lead"] += "记载涉及暮潮级。"
                person["relations"] = [relation(identity, refs=[1])]
                person["links"] = [identity]
                page = single_render.render({"entries": [person, concept]}, "书")
                parsed = PageLinks()
                parsed.feed(page)
                self.assertIn(identity, parsed.ids)
                self.assertIn(person["id"] + "-ref1", parsed.ids)
                target_href = "#" + single_render.local_component(identity)
                self.assertEqual(parsed.links.count(target_href), 4)
                self.assertIn("#" + single_render.local_component(person["id"] + "-ref1"), parsed.links)
                for href in parsed.links:
                    parts = urlsplit(href)
                    if not parts.scheme and not parts.netloc:
                        self.assertEqual(parts.path, "")
                        self.assertIn(unquote(parts.fragment), parsed.ids)
                if "%20" in identity:
                    self.assertIn("%2520", target_href)

    def test_site_check_reports_missing_non_html_paths_and_invalid_local_anchors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            render_wiki.render({"entries": [entry()]}, root)
            home = root / "index.html"
            original = home.read_text(encoding="utf-8")
            for href in ("missing#part.html", "missing.bin", "index.html#absent%23anchor", "#absent%20anchor",
                         "invalid%00.html", "../out.html", "//["):
                with self.subTest(href=href):
                    home.write_text(original + '<a href="%s">broken</a>' % href, encoding="utf-8")
                    self.assertTrue(any(href in error for error in site_errors(root)))
            home.write_text(original + '<img src="invalid%00.png">', encoding="utf-8")
            self.assertTrue(any("invalid%00.png" in error for error in site_errors(root)))

    def test_single_page_source_anchor_avoids_real_entry_ids(self):
        person = entry("e-person")
        concept = entry("e-person-ref1", "暮潮级", "concept")
        other = entry("e-person-ref1-2", "雾纹级", "concept")
        person["relations"] = [relation(concept["id"], refs=[1])]
        page = single_render.render({"entries": [person, concept, other]}, "书")
        parsed = PageLinks()
        parsed.feed(page)
        self.assertEqual(len(parsed.ids), len(set(parsed.ids)))
        self.assertIn('href="#e-person-ref1">暮潮级</a>', page)
        self.assertIn('href="#e-person-ref1-3">[1]</a>', page)
        self.assertIn('<li id="e-person-ref1-3">', page)
        for href in parsed.links:
            self.assertIn(unquote(urlsplit(href).fragment), parsed.ids)

    def test_encoded_filename_collision_cleanup_retains_only_current_pages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = entry("a", "潮#纹:一", "concept")
            second = entry("b", "潮#纹?一", "concept")
            person = entry("p", "某人")
            person["relations"] = [relation("b", refs=[1])]
            mapping = render_wiki.render({"entries": [first, second, person]}, root)
            old_name = mapping[second["name"]]
            self.assertTrue(old_name.endswith("-2.html"))
            mapping = render_wiki.render({"entries": [second, person]}, root)
            self.assertNotEqual(mapping[second["name"]], old_name)
            self.assertFalse((root / old_name).exists())
            self.assertTrue((root / mapping[second["name"]]).is_file())
            self.assertEqual(site_errors(root), [])

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
