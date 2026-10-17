"""Bounded, path-based tasks. No model calls, chat history, or text echo."""
from __future__ import annotations
import json
import re
import time
import uuid
from pathlib import Path
from core_store import canonical, digest, file_digest, now, validate_document, write_json

STAGES = {"extract", "append", "draft", "review"}
MAX_MANIFEST_CHARS = 4000
MAX_INPUT_CHARS = 12000
MAX_RESULT_BYTES = 16 * 1024 * 1024

def check_manifest(manifest):
    allowed = {"protocol", "id", "stage", "inputs", "output", "rules_hash", "config", "reason", "parent_id"}
    if not isinstance(manifest, dict) or set(manifest) - allowed:
        raise ValueError("unknown manifest fields; do not embed material or chat history")
    if len(canonical(manifest)) > MAX_MANIFEST_CHARS:
        raise ValueError("manifest exceeds bounded metadata limit")
    if manifest.get("protocol") != 1 or manifest.get("stage") not in STAGES:
        raise ValueError("unsupported protocol or stage")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", manifest.get("id", "")):
        raise ValueError("invalid task ID")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.get("rules_hash", "")):
        raise ValueError("rules_hash must identify the frozen instructions")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 8:
        raise ValueError("one to eight input references required")
    count = 0
    for ref in inputs:
        if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "offset", "length"}:
            raise ValueError("inputs must contain only path/hash/character range")
        path = Path(ref["path"])
        if not path.is_absolute() or not path.is_file():
            raise ValueError("input reference must be an existing absolute file path")
        if file_digest(path) != ref["sha256"]:
            raise ValueError("input changed since manifest creation")
        if type(ref["offset"]) is not int or ref["offset"] < 0 or type(ref["length"]) is not int or ref["length"] < 1:
            raise ValueError("invalid character range")
        count += ref["length"]
    if count > MAX_INPUT_CHARS:
        raise ValueError("task exceeds input context budget")
    output = Path(manifest.get("output", ""))
    if not output.is_absolute() or output.suffix != ".json":
        raise ValueError("output must be an absolute JSON file path")
    if output.resolve() in {Path(r["path"]).resolve() for r in inputs}:
        raise ValueError("output cannot overwrite an input")
    if not isinstance(manifest.get("config", {}), dict):
        raise ValueError("config must be an object")
    return digest({"stage": manifest["stage"], "inputs": [{k: r[k] for k in ("sha256", "offset", "length")} for r in inputs],
                   "rules_hash": manifest["rules_hash"], "config": manifest.get("config", {})})

def prepare(store, work, manifest):
    signature = check_manifest(manifest)
    with store.transaction():
        store.ensure_work(work)
        existing = store.connection.execute("SELECT * FROM jobs WHERE id=?", (manifest["id"],)).fetchone()
        if existing:
            if existing["work_id"] != work or existing["manifest"] != canonical(manifest):
                raise ValueError("task ID already has a different manifest")
            return summary(existing)
        store.connection.execute("INSERT INTO jobs(id,work_id,stage,manifest,signature,status,updated) VALUES(?,?,?,?,?,'pending',?)",
            (manifest["id"], work, manifest["stage"], canonical(manifest), signature, now()))
    return {"id": manifest["id"], "status": "pending", "signature": signature}

def summary(row):
    return {k: row[k] for k in ("id", "stage", "status", "signature", "result_path", "result_hash", "error")}

def claim(store, task_id, lease_seconds=1800):
    if not 1 <= lease_seconds <= 86400:
        raise ValueError("lease must be between 1 and 86400 seconds")
    with store.transaction():
        row = store.connection.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError("unknown task")
        if row["status"] == "completed":
            return summary(row)
        if row["status"] == "running" and row["lease_until"] > time.time():
            raise ValueError("task is already claimed")
        token = uuid.uuid4().hex
        store.connection.execute("UPDATE jobs SET status='running',token=?,lease_until=?,error=NULL,updated=? WHERE id=?",
            (token, time.time() + lease_seconds, now(), task_id))
    return {"id": task_id, "status": "running", "token": token, "lease_seconds": lease_seconds}

def input_slice(manifest, index=0):
    check_manifest(manifest)
    if type(index) is not int or not 0 <= index < len(manifest["inputs"]):
        raise ValueError("input index is out of range")
    ref = manifest["inputs"][index]
    with open(ref["path"], encoding="utf-8", errors="strict") as f:
        # Character offsets, not byte offsets. Never print the unselected document.
        remaining = ref["offset"]
        while remaining:
            chunk = f.read(min(remaining, 8192))
            if not chunk:
                raise ValueError("input offset exceeds file")
            remaining -= len(chunk)
        text = f.read(ref["length"])
    if not text:
        raise ValueError("empty input range")
    return text

def submit(store, task_id, token):
    row = store.connection.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone()
    if not row:
        raise ValueError("unknown task")
    manifest = json.loads(row["manifest"])
    check_manifest(manifest)
    result_path = Path(manifest["output"])
    if not result_path.is_file() or result_path.stat().st_size > MAX_RESULT_BYTES:
        raise ValueError("missing or oversized result file")
    # Read the file once so digest and parsed payload identify exactly the same bytes.
    import hashlib
    raw = result_path.read_bytes()
    result_hash = hashlib.sha256(raw).hexdigest()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("result must be an object")
    with store.transaction():
        current = store.connection.execute("SELECT * FROM jobs WHERE id=?", (task_id,)).fetchone()
        if current["status"] == "completed":
            if current["result_hash"] != result_hash:
                raise ValueError("completed result has changed")
            return summary(current)
        if current["status"] != "running" or current["token"] != token or current["lease_until"] <= time.time():
            raise ValueError("claim is invalid or expired")
        work = current["work_id"]
        if current["stage"] == "append":
            from merge_append import apply_append
            apply_append(store, work, value, task_id, manifest.get("config", {}).get("scope", ""), in_transaction=True)
        elif current["stage"] == "draft":
            existing = store.get_document(work, "entries")
            expected = manifest.get("config", {}).get("expected_entries_hash")
            actual = digest(existing) if existing is not None else None
            if expected != actual:
                raise ValueError("entry document changed; draft cannot overwrite newer data")
            chapters = store.get_document(work, "chapters")
            if chapters is None:
                raise ValueError("draft submission requires the frozen chapter document")
            from citation_catalog import materialize
            from draft_format import expand
            from draft_validation import shape_errors
            value = expand(value, chapters, work)
            shape_issues = shape_errors(value)
            if shape_issues:
                raise ValueError("draft shape failed: " + "; ".join(shape_issues[:3]))
            value = materialize(value, chapters)
            from quote_source_check import check as check_quotes
            from check_voice import check as check_voice
            errors, _ = check_quotes(value, chapters)
            voice_errors, _ = check_voice(value)
            if errors or voice_errors:
                raise ValueError("draft validation failed (%d issues); inspect only affected entries" % (len(errors) + len(voice_errors)))
            store.put_document(work, "entries", value)
        elif current["stage"] == "extract":
            docs = value.get("documents")
            if not isinstance(docs, dict) or not docs or set(docs) - {"characters", "places", "objects", "races", "character_timeline", "object_timeline", "interactions", "custom_attributes"}:
                raise ValueError("extract result requires structured table documents")
            for kind, doc in docs.items():
                validate_document(kind, doc)
                previous = store.get_document(work, kind)
                if previous is None:
                    merged = doc
                else:
                    from merge_append import merge_list
                    from core_store import record_key
                    merged = json.loads(canonical(previous))
                    merged["items"], _, _ = merge_list(merged["items"], doc["items"], lambda item: record_key(kind, item))
                    if kind == "custom_attributes":
                        merged["attribute_defs"], _, _ = merge_list(merged.get("attribute_defs", []), doc.get("attribute_defs", []), lambda item: item["name"])
                store.put_document(work, kind, merged)
        else:
            if not isinstance(value.get("issues"), list) or type(value.get("needs_followup")) is not bool:
                raise ValueError("invalid review result")
        store.connection.execute("UPDATE jobs SET status='completed',result_path=?,result_hash=?,token=NULL,lease_until=NULL,updated=? WHERE id=?",
            (str(result_path.resolve()), result_hash, now(), task_id))
    return {"id": task_id, "status": "completed", "result_path": str(result_path.resolve()), "result_hash": result_hash}

def fail(store, task_id, token, reason):
    if not isinstance(reason, str) or not 1 <= len(reason) <= 240:
        raise ValueError("failure reason must be a short summary")
    with store.transaction():
        result = store.connection.execute("UPDATE jobs SET status='failed',error=?,token=NULL,lease_until=NULL,updated=? WHERE id=? AND status='running' AND token=?",
            (reason, now(), task_id, token))
        if result.rowcount != 1:
            raise ValueError("invalid task claim")
    return {"id": task_id, "status": "failed", "error": reason}

def cache_lookup(store, work, manifest):
    signature = check_manifest(manifest)
    rows = store.connection.execute("SELECT * FROM jobs WHERE work_id=? AND signature=? AND status='completed' ORDER BY updated DESC", (work, signature)).fetchall()
    for row in rows:
        p = Path(row["result_path"])
        if p.is_file() and file_digest(p) == row["result_hash"]:
            return summary(row)
    return {"status": "miss", "signature": signature}
