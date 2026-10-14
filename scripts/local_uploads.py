"""User-owned additions, kept outside generated entry documents."""
from __future__ import annotations
import hashlib
import json
import os
import uuid
from pathlib import Path
from core_store import canonical, file_digest, now

def managed_path(store, relative):
    root = (store.path.parent / "uploads").resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("invalid managed asset path")
    return path

def image_extension(raw):
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if raw.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("supported images: PNG, JPEG, GIF, WebP")

def validate_payload(kind, payload):
    allowed = {"caption", "source", "author", "title", "date", "order", "cover", "body", "asset", "sha256"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("unknown upload metadata fields")
    limits = {"caption": 500, "source": 1000, "author": 160, "title": 240, "date": 40}
    for field, limit in limits.items():
        if field in payload and (not isinstance(payload[field], str) or len(payload[field]) > limit):
            raise ValueError("invalid or oversized %s" % field)
    if type(payload.get("order", 0)) is not int or type(payload.get("cover", False)) is not bool:
        raise ValueError("invalid ordering or cover flag")
    if kind == "review" and (not isinstance(payload.get("body"), str) or not payload["body"].strip() or len(payload["body"]) > 100000):
        raise ValueError("review requires nonempty body within 100000 characters")

def add(store, work, kind, payload, target_id=None, image_path=None, upload_id=None):
    if kind not in {"image", "review"}:
        raise ValueError("unknown upload kind")
    payload = dict(payload)
    if "asset" in payload or "sha256" in payload:
        raise ValueError("asset references are managed by the import program")
    validate_payload(kind, payload)
    if kind == "image":
        source = Path(image_path)
        if not source.is_file() or source.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("missing or oversized image")
        raw = source.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        relative = sha + image_extension(raw)
        path = managed_path(store, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        payload.update(asset=relative, sha256=sha)
    identity = upload_id or "u-" + uuid.uuid4().hex
    if not isinstance(identity, str) or not 1 <= len(identity) <= 100:
        raise ValueError("invalid upload ID")
    with store.transaction():
        store.ensure_work(work)
        if target_id:
            target = store.connection.execute("SELECT work_id FROM records WHERE id=?", (target_id,)).fetchone()
            if not target or target[0] != work:
                raise ValueError("upload target must belong to this work")
        existing = store.connection.execute("SELECT * FROM uploads WHERE id=?", (identity,)).fetchone()
        if existing:
            if existing["work_id"] == work and existing["target_id"] == target_id and existing["kind"] == kind and existing["payload"] == canonical(payload):
                return {"status": "reused", "id": identity}
            raise ValueError("upload ID conflicts; use update for explicit revisions")
        store.connection.execute("INSERT INTO uploads VALUES(?,?,?,?,?,?,?)", (identity, work, target_id, kind, canonical(payload), now(), now()))
    return {"status": "imported", "id": identity, "kind": kind}

def update(store, identity, fields):
    if not isinstance(fields, dict) or set(fields) - {"caption", "source", "author", "title", "date", "order", "cover", "body"}:
        raise ValueError("only display metadata and review text may be updated")
    with store.transaction():
        row = store.connection.execute("SELECT * FROM uploads WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("unknown upload")
        payload = dict(json.loads(row["payload"]), **fields)
        validate_payload(row["kind"], payload)
        store.connection.execute("UPDATE uploads SET payload=?,updated=? WHERE id=?", (canonical(payload), now(), identity))
    return {"status": "updated", "id": identity}

def remove(store, identity):
    # Files can be shared by multiple uploads. Keep blobs until an explicit GC/backup policy exists.
    with store.transaction():
        result = store.connection.execute("DELETE FROM uploads WHERE id=?", (identity,))
        if result.rowcount != 1:
            raise ValueError("unknown upload")
    return {"status": "deleted", "id": identity}

def list_uploads(store, work):
    rows = store.connection.execute("SELECT u.*,r.kind AS target_kind,r.record_key,r.active FROM uploads u LEFT JOIN records r ON u.target_id=r.id WHERE u.work_id=? ORDER BY u.created,u.id", (work,)).fetchall()
    return [dict(id=r["id"], kind=r["kind"], target_id=r["target_id"], target_kind=r["target_kind"],
                 target_key=r["record_key"], target_active=r["active"], **json.loads(r["payload"])) for r in rows]

def export_overlay(store, work, site):
    """Data for deterministic rendering; no AI receives uploaded review text."""
    site = Path(site).resolve()
    overlay = {"entries": {}, "work": {"images": [], "reviews": []}, "unmatched": []}
    for item in list_uploads(store, work):
        if item["target_id"] is None:
            target = overlay["work"]
        elif item["target_kind"] == "entries" and item["target_active"]:
            target = overlay["entries"].setdefault(item["target_key"], {"images": [], "reviews": []})
        else:
            overlay["unmatched"].append({"id": item["id"], "target_id": item["target_id"]})
            continue
        if item["kind"] == "image":
            path = managed_path(store, item["asset"])
            if not path.is_file() or file_digest(path) != item["sha256"]:
                raise ValueError("managed image missing or modified: %s" % item["id"])
            destination = site / "assets" / "uploads" / item["asset"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            raw = path.read_bytes()
            temp = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temp.write_bytes(raw)
                os.replace(temp, destination)
            finally:
                temp.unlink(missing_ok=True)
            item["url"] = "assets/uploads/" + item["asset"]
            target["images"].append(item)
        else:
            target["reviews"].append(item)
    for target in [overlay["work"], *overlay["entries"].values()]:
        target["images"].sort(key=lambda x: (not x.get("cover", False), x.get("order", 0), x["id"]))
    return overlay
