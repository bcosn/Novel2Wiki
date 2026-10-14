import base64
import copy
import hashlib
import importlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from core_store import Store, digest, file_digest, write_json
import convert
import task_protocol as tasks
import local_uploads as uploads
import merge_append
import render_wiki
import usage_metrics
import review_plan
from argparse import Namespace
from test_v02fix import entry

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3YQAAAAASUVORK5CYII=')

class CoreTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".validation"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="v03-", dir=scratch)
        self.root = pathlib.Path(self.temp.name).resolve()
        self.db = self.root / "workspace.sqlite"
        self.store = Store(self.db)
        self.store.ensure_work("书")
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def manifest(self, identity="task-1", stage="extract", text="第1章\n甲在酒店工作。", config=None):
        source = self.root / "input.txt"
        source.write_text(text, encoding="utf-8")
        return {"protocol": 1, "id": identity, "stage": stage,
            "inputs": [{"path": str(source), "sha256": file_digest(source), "offset": 0, "length": len(text)}],
            "output": str(self.root / (identity + ".json")), "rules_hash": digest("rules-v1"), "config": config or {}}
    def finish(self, manifest, result):
        tasks.prepare(self.store, "书", manifest)
        claim = tasks.claim(self.store, manifest["id"])
        write_json(manifest["output"], result)
        return tasks.submit(self.store, manifest["id"], claim["token"])
    def test_roundtrip_versions_and_stable_ids(self):
        original = {"meta": {"book": "书"}, "entries": [entry(), entry("e-2", "乙")]}
        self.store.import_documents("书", {"entries": original})
        identity = self.store.stable_id("书", "entries", "e-1")
        changed = copy.deepcopy(original)
        changed["entries"].reverse()
        changed["entries"][1]["name"] = "改名甲"
        self.store.import_documents("书", {"entries": changed})
        self.assertEqual(identity, self.store.stable_id("书", "entries", "e-1"))
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM versions").fetchone()[0], 2)
        self.store.export_documents("书", self.root / "export")
        result = json.loads((self.root / "export/书.entries.json").read_text(encoding="utf-8"))
        self.assertEqual(result, changed)
    def test_duplicate_chapter_labels_preserve_source_order(self):
        doc = {"meta": {}, "chapters": [{"index": 1, "content": "甲"}, {"index": 1, "content": "乙"}]}
        self.store.import_documents("书", {"chapters": doc})
        self.assertEqual(self.store.get_document("书", "chapters"), doc)
    def test_invalid_multi_document_import_rolls_back(self):
        with self.assertRaises(ValueError):
            self.store.import_documents("书", {"characters": {"items": [{"name": "甲"}]}, "objects": {"items": [{"name": "刀"}, {"name": "刀"}]}})
        self.assertIsNone(self.store.get_document("书", "characters"))
    def test_fault_mid_append_rolls_back_data_and_receipt(self):
        original = self.store.put_document
        calls = 0
        def fault(*args):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("injected failure")
            return original(*args)
        with mock.patch.object(self.store, "put_document", side_effect=fault):
            with self.assertRaises(OSError):
                merge_append.apply_append(self.store, "书", {"new_entities": {"characters": [{"name": "甲"}]}}, "batch-1")
        self.assertIsNone(self.store.get_document("书", "characters"))
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM batches").fetchone()[0], 0)
        merge_append.apply_append(self.store, "书", {"new_entities": {"characters": [{"name": "甲"}]}}, "batch-1")
        self.assertEqual(len(self.store.get_document("书", "characters")["items"]), 1)
    def test_append_replay_and_changed_batch_conflict(self):
        doc = {"new_entities": {"characters": [{"name": "甲"}]}, "unresolved": ["问题"]}
        merge_append.apply_append(self.store, "书", doc, "batch-1")
        self.assertEqual(merge_append.apply_append(self.store, "书", doc, "batch-1")["status"], "reused")
        with self.assertRaises(ValueError):
            merge_append.apply_append(self.store, "书", dict(doc, unresolved=["改动"]), "batch-1")
    def test_concurrent_append_preserves_all_workers(self):
        def worker(n):
            with Store(self.db) as store:
                return merge_append.apply_append(store, "书", {"new_entities": {"characters": [{"name": "人物%d" % n}]}}, "batch-%d" % n)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, range(8)))
        self.assertEqual(len(results), 8)
        self.assertEqual(len(self.store.get_document("书", "characters")["items"]), 8)
    def test_process_exit_inside_transaction_recovers(self):
        code = "import sys,os;sys.path.insert(0,sys.argv[1]);from core_store import Store;s=Store(sys.argv[2]);s.connection.execute('BEGIN IMMEDIATE');s.put_document('书','characters',{'items':[{'name':'未提交'}]});os._exit(23)"
        process = subprocess.run([sys.executable, "-c", code, str(ROOT / "scripts"), str(self.db)], capture_output=True, timeout=20)
        self.assertEqual(process.returncode, 23)
        self.assertIsNone(self.store.get_document("书", "characters"))
        self.assertEqual(self.store.connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
    def test_backup_uses_sqlite_snapshot(self):
        self.store.import_documents("书", {"characters": {"items": [{"name": "甲"}]}})
        backup = self.root / "backup.sqlite"
        self.store.backup(backup)
        with Store(backup) as restored:
            self.assertEqual(restored.get_document("书", "characters")["items"][0]["name"], "甲")
    def test_protocol_is_bounded_and_validates_hash(self):
        manifest = self.manifest()
        tasks.prepare(self.store, "书", manifest)
        bad = copy.deepcopy(manifest); bad["chat_history"] = "全书"
        with self.assertRaises(ValueError): tasks.check_manifest(bad)
        bad = copy.deepcopy(manifest); bad["inputs"][0]["length"] = 12001
        with self.assertRaises(ValueError): tasks.check_manifest(bad)
        pathlib.Path(manifest["inputs"][0]["path"]).write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError): tasks.check_manifest(manifest)
    def test_slice_reads_only_requested_character_range(self):
        manifest = self.manifest(text="前文甲在酒店工作。后文")
        manifest["inputs"][0].update(offset=2, length=8)
        self.assertEqual(tasks.input_slice(manifest), "甲在酒店工作。后")
    def test_task_single_claim_and_expired_token(self):
        manifest = self.manifest()
        tasks.prepare(self.store, "书", manifest)
        first = tasks.claim(self.store, manifest["id"])
        with self.assertRaises(ValueError): tasks.claim(self.store, manifest["id"])
        self.store.connection.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (manifest["id"],))
        second = tasks.claim(self.store, manifest["id"])
        write_json(manifest["output"], {"documents": {"characters": {"items": [{"name": "甲"}]}}})
        with self.assertRaises(ValueError): tasks.submit(self.store, manifest["id"], first["token"])
        self.assertEqual(tasks.submit(self.store, manifest["id"], second["token"])["status"], "completed")
    def test_parallel_extract_batches_merge_instead_of_replace(self):
        self.finish(self.manifest("task-a"), {"documents": {"characters": {"items": [{"name": "甲"}]}}})
        self.finish(self.manifest("task-b"), {"documents": {"characters": {"items": [{"name": "乙"}]}}})
        self.assertEqual(len(self.store.get_document("书", "characters")["items"]), 2)
    def test_task_conflict_does_not_complete_or_write_partial_data(self):
        self.store.import_documents("书", {"characters": {"items": [{"name": "甲", "age": 18}]}})
        m = self.manifest()
        tasks.prepare(self.store, "书", m); claim = tasks.claim(self.store, m["id"])
        write_json(m["output"], {"documents": {"objects": {"items": [{"name": "刀"}]}, "characters": {"items": [{"name": "甲", "age": 19}]}}})
        with self.assertRaises(ValueError): tasks.submit(self.store, m["id"], claim["token"])
        self.assertIsNone(self.store.get_document("书", "objects"))
        self.assertEqual(self.store.connection.execute("SELECT status FROM jobs").fetchone()[0], "running")
    def test_draft_rejects_stale_revision(self):
        doc = {"entries": [entry()]}
        self.store.import_documents("书", {"entries": doc})
        m = self.manifest(stage="draft", config={"expected_entries_hash": digest(doc)})
        tasks.prepare(self.store, "书", m); claim = tasks.claim(self.store, m["id"])
        newer = copy.deepcopy(doc); newer["entries"][0]["lead"] = "新稿"
        self.store.import_documents("书", {"entries": newer})
        write_json(m["output"], doc)
        with self.assertRaises(ValueError): tasks.submit(self.store, m["id"], claim["token"])
        self.assertEqual(self.store.get_document("书", "entries"), newer)
    def test_cache_reuses_content_and_invalidates_changed_rules_or_result(self):
        m = self.manifest()
        self.finish(m, {"documents": {"characters": {"items": [{"name": "甲"}]}}})
        other = copy.deepcopy(m); other["id"] = "task-2"; other["output"] = str(self.root / "other.json")
        self.assertEqual(tasks.cache_lookup(self.store, "书", other)["id"], m["id"])
        other["rules_hash"] = digest("different rules")
        self.assertEqual(tasks.cache_lookup(self.store, "书", other)["status"], "miss")
        pathlib.Path(m["output"]).write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError): tasks.submit(self.store, m["id"], "old")
        self.assertEqual(tasks.cache_lookup(self.store, "书", m)["status"], "miss")
    def test_uploads_survive_entry_regeneration_and_original_file_move(self):
        doc = {"entries": [entry()]}
        self.store.import_documents("书", {"entries": doc})
        target = self.store.stable_id("书", "entries", "e-1")
        image = self.root / "original.png"; image.write_bytes(PNG)
        uploads.add(self.store, "书", "image", {"caption": "<图>", "cover": True}, target, image, "image-1")
        uploads.add(self.store, "书", "review", {"body": "<script>书评</script>", "author": "读者"}, target, upload_id="review-1")
        image.unlink()
        changed = copy.deepcopy(doc); changed["entries"][0]["lead"] = "修订正文"
        self.store.import_documents("书", {"entries": changed})
        site = self.root / "site"
        overlay = uploads.export_overlay(self.store, "书", site)
        pages = render_wiki.render(changed, site, overlay=overlay)
        page = (site / pages["甲"]).read_text(encoding="utf-8")
        self.assertIn("用户书评", page)
        self.assertIn("&lt;script&gt;书评&lt;/script&gt;", page)
        self.assertNotIn("<script>", page)
        self.assertEqual(len(list((site / "assets/uploads").glob("*.png"))), 1)
        self.assertEqual(self.store.get_document("书", "entries"), changed)
        uploads.update(self.store, "review-1", {"body": "更新书评"})
        uploads.remove(self.store, "review-1")
        self.assertEqual(len(uploads.list_uploads(self.store, "书")), 1)
    def test_image_dedup_and_cross_work_target_rejected(self):
        self.store.import_documents("书", {"entries": {"entries": [entry()]}})
        image = self.root / "same.png"; image.write_bytes(PNG)
        uploads.add(self.store, "书", "image", {}, image_path=image)
        uploads.add(self.store, "书", "image", {}, image_path=image)
        self.assertEqual(len(list((self.root / "uploads").glob("*.png"))), 1)
        with self.assertRaises(ValueError):
            uploads.add(self.store, "另一部", "review", {"body": "书评"}, self.store.stable_id("书", "entries", "e-1"))
    def test_usage_is_deduplicated_and_actual_ratio_only(self):
        event = {"id": "call-1", "cached_input_tokens": 90, "uncached_input_tokens": 10, "output_tokens": 20}
        usage_metrics.record_usage(self.store, "书", event)
        usage_metrics.record_usage(self.store, "书", event)
        result = usage_metrics.usage_summary(self.store, "书")
        self.assertEqual(result["events"], 1)
        self.assertEqual(result["cache_read_ratio"], .9)
    def test_ingest_snapshots_reuses_and_versions_changed_input(self):
        from ingest import ingest
        source = self.root / "book.txt"
        source.write_text("第1章 开始\n甲在酒店工作。", encoding="utf-8")
        first = ingest(self.store, "书", source)
        self.assertEqual(ingest(self.store, "书", source)["status"], "reused")
        original = self.store.get_document("书", "chapters")
        snapshot = self.root / original["meta"]["source_snapshot"]
        self.assertTrue(snapshot.is_file())
        source.write_text("第1章 开始\n甲离开酒店。", encoding="utf-8")
        second = ingest(self.store, "书", source)
        self.assertNotEqual(first["ingest_id"], second["ingest_id"])
        self.assertIn("工作", snapshot.read_text(encoding="utf-8"))
    def test_three_small_online_fixtures_parse_within_budget(self):
        fixture = ROOT / "tests/fixtures/small"
        manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["samples"]), 3)
        for sample in manifest["samples"]:
            self.assertLessEqual(sample["chars"], 1500)
            self.assertEqual(file_digest(fixture / sample["file"]), sample["sha256"])
            doc = convert.build_result(str(fixture / sample["file"]), sample["title"], "")
            self.assertEqual(len(doc["chapters"]), 1)
            self.assertNotIn("\ufffd", doc["chapters"][0]["content"])
    def test_batch_merge_preserves_gaps_and_rejects_bad_links_without_writing(self):
        first, second = entry("e-0010", "甲"), entry("e-0090", "乙")
        first["links"] = ["e-0090"]
        source = self.root / "batch.json"
        write_json(source, {"entries": [first, second]})
        out = self.root / "merged.json"
        command = [sys.executable, str(ROOT / "scripts/merge_batches.py"), str(source), "-o", str(out)]
        run = subprocess.run(command, capture_output=True, timeout=20)
        self.assertEqual(run.returncode, 0)
        doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual([e["id"] for e in doc["entries"]], ["e-0010", "e-0090"])
        before = out.read_bytes()
        first["links"] = ["missing"]
        write_json(source, {"entries": [first, second]})
        self.assertNotEqual(subprocess.run(command, capture_output=True, timeout=20).returncode, 0)
        self.assertEqual(out.read_bytes(), before)
    def test_draft_quote_error_cannot_commit(self):
        self.store.import_documents("书", {"chapters": {"chapters": [{"index": 1, "content": "甲离开酒店。"}]}})
        m = self.manifest(stage="draft")
        tasks.prepare(self.store, "书", m); claim = tasks.claim(self.store, m["id"])
        write_json(m["output"], {"entries": [entry()]})
        with self.assertRaises(ValueError): tasks.submit(self.store, m["id"], claim["token"])
        self.assertIsNone(self.store.get_document("书", "entries"))
    def review_args(self, **overrides):
        values = dict(entries=str(self.root / "entries.json"), state=str(self.root / "review-state.json"),
            out=str(self.root / "packet.json"), db=str(self.db), mode=None, max_reviews=None, sample_size=None,
            context_chars=None, include=None, claim=False, record_result=None)
        values.update(overrides)
        return Namespace(**values)
    def test_sqlite_review_ledger_prevents_parallel_double_claim(self):
        write_json(self.root / "entries.json", {"meta": {"book": "书"}, "entries": [entry()]})
        review_plan.operate_sqlite(self.review_args())
        def worker(_):
            try:
                return review_plan.operate_sqlite(self.review_args(out=None, claim=True))["status"]
            except ValueError:
                return "rejected"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, range(2)))
        self.assertEqual(sorted(results), ["claimed", "rejected"])
        self.assertEqual(self.store.get_document("书", "_review_state")["calls"], 1)
    def test_review_ledger_survives_missing_json_export(self):
        write_json(self.root / "entries.json", {"meta": {"book": "书"}, "entries": [entry()]})
        review_plan.operate_sqlite(self.review_args())
        review_plan.operate_sqlite(self.review_args(out=None, claim=True))
        (self.root / "review-state.json").unlink()
        with self.assertRaises(ValueError): review_plan.operate_sqlite(self.review_args(out=None, claim=True))
        self.assertEqual(self.store.get_document("书", "_review_state")["calls"], 1)

    def test_failed_review_remains_failed_after_export_loss(self):
        write_json(self.root / "entries.json", {"meta": {"book": "书"}, "entries": [entry()]})
        review_plan.operate_sqlite(self.review_args(mode="strict"))
        review_plan.operate_sqlite(self.review_args(out=None, claim=True))
        result = review_plan.operate_sqlite(self.review_args(out=None, record_failure="单次输出上限"))
        self.assertEqual(result["review_status"], "model_failed")
        state = self.store.get_document("书", "_review_state")
        self.assertEqual(state["calls"], 1)
        self.assertEqual(state["history"][0]["status"], "model_failed")
        self.assertEqual(state["history"][0]["failure_reason"], "单次输出上限")
        (self.root / "review-state.json").unlink()
        result = review_plan.operate_sqlite(self.review_args())
        self.assertEqual(result["status"], "stop")
        self.assertEqual(result["review_status"], "model_failed")
        self.assertEqual(result["calls"], 1)
        with self.assertRaises(ValueError):
            review_plan.operate_sqlite(self.review_args(out=None, claim=True))
    def test_failure_result_file_is_not_recorded_as_pass(self):
        write_json(self.root / "entries.json", {"meta": {"book": "书"}, "entries": [entry()]})
        review_plan.operate_sqlite(self.review_args())
        review_plan.operate_sqlite(self.review_args(out=None, claim=True))
        p = self.root / "result.json"
        write_json(p, {"status": "model_failed", "failure_reason": "没有取得结论", "issues": [], "needs_followup": False})
        review_plan.operate_sqlite(self.review_args(out=None, record_result=str(p)))
        self.assertEqual(self.store.get_document("书", "_review_state")["history"][0]["status"], "model_failed")
    def test_failed_review_cannot_also_claim_a_content_conclusion(self):
        write_json(self.root / "entries.json", {"meta": {"book": "书"}, "entries": [entry()]})
        review_plan.operate_sqlite(self.review_args())
        review_plan.operate_sqlite(self.review_args(out=None, claim=True))
        p = self.root / "result.json"
        for value in [
            {"status": "model_failed", "failure_reason": "", "issues": [], "needs_followup": False},
            {"status": "model_failed", "failure_reason": "失败", "issues": [], "needs_followup": True},
            {"status": "model_failed", "failure_reason": "失败", "issues": [{"entry_id": "e-1", "kind": "style", "sentence": "句", "fix": "改"}], "needs_followup": False},
        ]:
            write_json(p, value)
            with self.assertRaises(ValueError):
                review_plan.operate_sqlite(self.review_args(out=None, record_result=str(p)))
            state = self.store.get_document("书", "_review_state")
            self.assertEqual(state["calls"], 1)
            self.assertEqual(state["pending"]["status"], "claimed")
            self.assertEqual(state["history"], [])

class ParserTests(unittest.TestCase):
    def test_prefixed_xhtml_paragraphs_remain_separate(self):
        text = convert.html_to_text('<x:html><x:head><x:title>标题</x:title></x:head><x:body><x:p>甲。</x:p><x:p>乙。</x:p></x:body></x:html>')
        self.assertEqual(text, "甲。\n\n乙。")
    def test_html_structure_entities_and_literal_angles(self):
        result = convert.html_to_text('<html><head><title>元数据</title></head><body><p>甲说：&lt;并非标签&gt;。</p><p>乙<br>丙<script>丢弃脚本</script></p></body></html>')
        self.assertIn("甲说：<并非标签>。", result)
        self.assertIn("乙\n丙", result)
        self.assertNotIn("元数据", result)
        self.assertNotIn("丢弃脚本", result)
    def test_epub_namespace_quotes_and_spine_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "test.epub"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("META-INF/container.xml", "<container><rootfiles><rootfile full-path='OPS/book.opf'/></rootfiles></container>")
                z.writestr("OPS/book.opf", "<package xmlns='http://www.idpf.org/2007/opf'><metadata xmlns:x='http://purl.org/dc/elements/1.1/'><x:title>书</x:title></metadata><manifest><item id='b' href='b.xhtml' media-type='application/xhtml+xml'/><item id='a' href='a.xhtml' media-type='application/xhtml+xml'/></manifest><spine><itemref idref='b'/><itemref idref='a'/></spine></package>")
                z.writestr("OPS/a.xhtml", "<html><body><p>第二段。</p></body></html>")
                z.writestr("OPS/b.xhtml", "<html><body><p>第一段。</p></body></html>")
            meta, text, _ = convert.read_epub(path)
            self.assertEqual(meta["title"], "书")
            self.assertLess(text.index("第一段"), text.index("第二段"))
    def test_missing_spine_document_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "test.epub"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("META-INF/container.xml", "<container><rootfiles><rootfile full-path='book.opf'/></rootfiles></container>")
                z.writestr("book.opf", "<package><manifest><item id='a' href='missing.xhtml' media-type='application/xhtml+xml'/></manifest><spine><itemref idref='a'/></spine></package>")
            with self.assertRaises(ValueError): convert.read_epub(path)
    def test_explicit_encoding_failure_does_not_insert_replacement_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "bad.txt"; path.write_bytes(b'\xff\xff')
            with self.assertRaises(UnicodeDecodeError): convert.build_result(str(path), "书", "", "utf-8")

if __name__ == "__main__": unittest.main()
