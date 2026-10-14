"""Small CLI surface for deterministic storage, task and upload operations."""
from __future__ import annotations
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from core_store import Store, KINDS, file_digest, write_json
import task_protocol as tasks
import local_uploads as uploads

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def main():
    p = argparse.ArgumentParser(description="Novel2Wiki workspace; outputs bounded metadata by default")
    p.add_argument("--db", required=True)
    p.add_argument("--work", required=True)
    sub = p.add_subparsers(dest="action", required=True)
    command = sub.add_parser("ingest")
    command.add_argument("file")
    command.add_argument("--author", default="")
    command.add_argument("--encoding", default="")
    command.add_argument("--denoise", action="store_true")
    command = sub.add_parser("import")
    command.add_argument("directory")
    command = sub.add_parser("put")
    command.add_argument("kind", choices=sorted(KINDS))
    command.add_argument("file")
    command = sub.add_parser("export")
    command.add_argument("directory")
    command.add_argument("--kind", action="append", choices=sorted(KINDS))
    sub.add_parser("status")
    command = sub.add_parser("backup")
    command.add_argument("destination")
    command = sub.add_parser("target-id")
    command.add_argument("kind", choices=sorted(KINDS))
    command.add_argument("key")
    command = sub.add_parser("task")
    command.add_argument("operation", choices=["prepare", "claim", "submit", "fail", "status", "cache"])
    command.add_argument("reference")
    command.add_argument("--token")
    command.add_argument("--reason", default="")
    command.add_argument("--lease-seconds", type=int, default=1800)
    command = sub.add_parser("slice")
    command.add_argument("manifest")
    command.add_argument("--index", type=int, default=0)
    command.add_argument("--out", required=True)
    command = sub.add_parser("upload")
    command.add_argument("kind", choices=["image", "review"])
    command.add_argument("file", help="image or UTF-8 review text")
    command.add_argument("--metadata", help="JSON file with display metadata")
    command.add_argument("--target-id")
    command.add_argument("--id")
    command = sub.add_parser("uploads")
    command.add_argument("--limit", type=int, default=20)
    command.add_argument("--offset", type=int, default=0)
    command = sub.add_parser("update-upload")
    command.add_argument("id")
    command.add_argument("metadata")
    command = sub.add_parser("delete-upload")
    command.add_argument("id")
    command = sub.add_parser("render")
    command.add_argument("directory")
    command.add_argument("--unit", default="章")
    command = sub.add_parser("finalize")
    command.add_argument("batches", nargs="+")
    command.add_argument("--directory", required=True, help="exports into data/ and site/ within this directory")
    command.add_argument("--report", required=True)
    command.add_argument("--expected-hash", help="current entries hash, required when changing an existing draft")
    command = sub.add_parser("usage")
    command.add_argument("file", help="normalized usage JSON; IDs prevent double counting")
    sub.add_parser("usage-summary")
    a = p.parse_args()
    with Store(a.db) as store:
        store.ensure_work(a.work)
        if a.action == "ingest":
            from ingest import ingest
            result = ingest(store, a.work, a.file, a.author, a.encoding, a.denoise)
        elif a.action == "import":
            directory = Path(a.directory)
            docs = {kind: load(directory / (a.work + "." + kind + ".json")) for kind in KINDS if (directory / (a.work + "." + kind + ".json")).is_file()}
            if not docs:
                raise ValueError("no workspace documents found")
            result = store.import_documents(a.work, docs)
        elif a.action == "put":
            result = store.import_documents(a.work, {a.kind: load(a.file)})
        elif a.action == "export":
            result = store.export_documents(a.work, a.directory, a.kind)
        elif a.action == "backup":
            result = store.backup(a.destination)
        elif a.action == "target-id":
            result = {"id": store.stable_id(a.work, a.kind, a.key)}
        elif a.action == "task":
            if a.operation == "prepare":
                result = tasks.prepare(store, a.work, load(a.reference))
            elif a.operation == "cache":
                result = tasks.cache_lookup(store, a.work, load(a.reference))
            else:
                row = store.connection.execute("SELECT * FROM jobs WHERE id=? AND work_id=?", (a.reference, a.work)).fetchone()
                if not row:
                    raise ValueError("task not found in selected work")
                if a.operation == "claim":
                    result = tasks.claim(store, a.reference, a.lease_seconds)
                elif a.operation == "submit":
                    result = tasks.submit(store, a.reference, a.token)
                elif a.operation == "fail":
                    result = tasks.fail(store, a.reference, a.token, a.reason)
                else:
                    result = tasks.summary(row)
        elif a.action == "slice":
            text = tasks.input_slice(load(a.manifest), a.index)
            out = Path(a.out).resolve()
            manifest = load(a.manifest)
            protected = {Path(ref["path"]).resolve() for ref in manifest["inputs"]} | {Path(a.manifest).resolve(), store.path}
            if out in protected:
                raise ValueError("slice output cannot overwrite inputs, manifest, or database")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            result = {"status": "written", "path": str(out), "chars": len(text), "sha256": file_digest(out)}
        elif a.action == "upload":
            metadata = load(a.metadata) if a.metadata else {}
            if a.kind == "review":
                source = Path(a.file)
                if source.stat().st_size > 400000:
                    raise ValueError("review file too large")
                metadata["body"] = source.read_text(encoding="utf-8", errors="strict")
            result = uploads.add(store, a.work, a.kind, metadata, a.target_id, a.file if a.kind == "image" else None, a.id)
        elif a.action == "uploads":
            # The full text stays in storage; listing reports only identifiers and metadata.
            if not 1 <= a.limit <= 100 or a.offset < 0:
                raise ValueError("invalid listing range")
            items = uploads.list_uploads(store, a.work)
            result = {"total": len(items), "offset": a.offset, "uploads": [{k: item[k] for k in ("id", "kind", "target_id", "target_active")} for item in items[a.offset:a.offset+a.limit]]}
        elif a.action == "update-upload":
            _assert_upload_work(store, a.id, a.work)
            result = uploads.update(store, a.id, load(a.metadata))
        elif a.action == "delete-upload":
            _assert_upload_work(store, a.id, a.work)
            result = uploads.remove(store, a.id)
        elif a.action == "finalize":
            from finalize_entries import finalize
            result = finalize(store, a.work, a.batches, a.directory, a.report, a.expected_hash)
        elif a.action == "render":
            import render_wiki
            doc = store.get_document(a.work, "entries")
            if not doc:
                raise ValueError("no entry document")
            overlay = uploads.export_overlay(store, a.work, a.directory)
            pages = render_wiki.render(doc, a.directory, work=a.work, unit=a.unit, overlay=overlay)
            result = {"status": "rendered", "entries": len(pages), "unmatched_upload_count": len(overlay["unmatched"]), "unmatched_uploads": overlay["unmatched"][:20], "path": str(Path(a.directory).resolve())}
        elif a.action in {"usage", "usage-summary"}:
            from usage_metrics import record_usage, usage_summary
            result = record_usage(store, a.work, load(a.file)) if a.action == "usage" else usage_summary(store, a.work)
        else:
            docs = store.connection.execute("SELECT kind,hash FROM documents WHERE work_id=? ORDER BY kind", (a.work,)).fetchall()
            counts = store.connection.execute("SELECT status,count(*) AS n FROM jobs WHERE work_id=? GROUP BY status", (a.work,)).fetchall()
            result = {"work": a.work, "documents": [dict(r) for r in docs], "jobs": {r["status"]: r["n"] for r in counts}}
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result.get("status") == "invalid" else 0

def _assert_upload_work(store, identity, work):
    if not store.connection.execute("SELECT 1 FROM uploads WHERE id=? AND work_id=?", (identity, work)).fetchone():
        raise ValueError("upload not found in selected work")

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as e:
        print(json.dumps({"status": "error", "message": str(e)[:400]}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
