"""Content-addressed input snapshots and reproducible chapter ingestion."""
from __future__ import annotations
import hashlib
import json
import os
import uuid
from pathlib import Path
import lxml
import charset_normalizer
import convert
import audit
from core_store import digest, file_digest, now

def ingest(store, work, source, author="", encoding="", denoise=False):
    source = Path(source).resolve()
    raw_hash = file_digest(source)
    rules_hash = digest({"convert": file_digest(Path(convert.__file__)), "audit": file_digest(Path(audit.__file__)),
                         "lxml": lxml.__version__, "charset_normalizer": charset_normalizer.__version__})
    options = {"work": work, "author": author, "encoding": encoding, "denoise": denoise, "format": source.suffix.lower()}
    batch_id = "ingest-" + digest({"input": raw_hash, "rules": rules_hash, "options": options})
    # Reuse only the still-active revision; an old receipt must not claim a newer input is current.
    current = store.get_document(work, "chapters")
    if current and current.get("meta", {}).get("ingest_id") == batch_id:
        return {"status": "reused", "ingest_id": batch_id, "chapters": len(current["chapters"]), "chars": current["meta"]["total_chars"]}
    snapshot = store.path.parent / "sources" / (raw_hash + source.suffix.lower())
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if not snapshot.is_file() or file_digest(snapshot) != raw_hash:
        temporary = snapshot.with_name(snapshot.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with source.open("rb") as original, temporary.open("wb") as saved:
                for block in iter(lambda: original.read(1024 * 1024), b""):
                    saved.write(block)
                saved.flush()
                os.fsync(saved.fileno())
            if file_digest(temporary) != raw_hash:
                raise ValueError("source changed while snapshotting")
            os.replace(temporary, snapshot)
        finally:
            temporary.unlink(missing_ok=True)
    doc = convert.build_result(str(snapshot), work, author, encoding)
    doc, findings = audit.audit(doc, denoise)
    if any(f["kind"] == "artifact" and f["severity"] == "error" and "U+FFFD" in f["detail"] for f in findings):
        raise ValueError("decoded source contains replacement characters; ingestion not committed")
    doc["meta"].update(source=source.name, source_sha256=raw_hash, parser_rules_hash=rules_hash,
                       ingest_id=batch_id, source_snapshot="sources/" + snapshot.name)
    with store.transaction():
        store.ensure_work(work)
        store.put_document(work, "chapters", doc)
        store.connection.execute("INSERT OR IGNORE INTO batches VALUES(?,?,?,?)", (work, batch_id, digest(doc), now()))
    return {"status": "ingested", "ingest_id": batch_id, "chapters": len(doc["chapters"]),
            "chars": doc["meta"]["total_chars"], "findings": len(findings)}
