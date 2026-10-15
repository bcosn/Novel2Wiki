"""Transactional local workspace. JSON remains the model interchange format."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from entry_relations import relation_errors

KINDS = {"characters", "places", "objects", "races", "character_timeline",
         "object_timeline", "interactions", "custom_attributes", "entries", "_pending", "chapters", "_review_state", "_entry_plan"}

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()

def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def now():
    return datetime.now(timezone.utc).isoformat()

def write_json(path, value, *, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(value, f, ensure_ascii=False, indent=None if compact else 2,
                      separators=(",", ":") if compact else None, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

def validate_document(kind, value):
    if kind not in KINDS or not isinstance(value, dict):
        raise ValueError("unknown document kind or non-object document")
    list_key = "entries" if kind == "entries" else "chapters" if kind == "chapters" else "items"
    if kind == "_review_state":
        if value.get("version") != 1 or type(value.get("calls")) is not int or value["calls"] < 0 or not isinstance(value.get("history"), list):
            raise ValueError("invalid review ledger")
        if value["calls"] > value.get("settings", {}).get("max_reviews", -1):
            raise ValueError("review quota exceeded")
        return
    if kind == "_pending":
        for field in ("corrections", "unresolved", "conflicts"):
            if not isinstance(value.get(field, []), list):
                raise ValueError("pending fields must be arrays")
        return
    if kind == "_entry_plan":
        if (type(value.get("version")) is not int or value["version"] != 1
                or not isinstance(value.get("work"),str)
                or not isinstance(value.get("items"),list)
                or not isinstance(value.get("relations",[]),list)
                or value.get("plan_hash") != digest({k:v for k,v in value.items() if k!="plan_hash"})):
            raise ValueError("invalid saved entry plan")
    if not isinstance(value.get(list_key), list):
        raise ValueError("%s requires %s array" % (kind, list_key))
    keys = set()
    for row in value[list_key]:
        if not isinstance(row, dict):
            raise ValueError("record must be an object")
        key = record_key(kind, row)
        if key in keys:
            raise ValueError("duplicate record identity: %s" % key)
        keys.add(key)
    if kind == "entries":
        ids = {r["id"] for r in value["entries"]}
        for row in value["entries"]:
            if not isinstance(row.get("id"), str) or not isinstance(row.get("name"), str) or not row["name"].strip():
                raise ValueError("entry IDs and names must be nonempty strings")
            if not isinstance(row.get("links", []), list) or not isinstance(row.get("sections", []), list) or not isinstance(row.get("sources", []), list):
                raise ValueError("invalid entry arrays")
            if row.get("kind") not in {"character", "place", "object", "race", "event", "concept"}:
                raise ValueError("invalid entry kind")
            errors = relation_errors(row["relations"], row["id"]) if "relations" in row else []
            if errors:
                raise ValueError("; ".join(errors))
            if any(link not in ids for link in row.get("links", [])):
                raise ValueError("dangling entry link")
    if kind.endswith("timeline"):
        for row in value["items"]:
            start, end = row.get("from_chapter"), row.get("to_chapter")
            if start is not None and (type(start) is not int or start < 1):
                raise ValueError("invalid timeline start")
            if end is not None and (type(end) is not int or end < 1 or (start is not None and end < start)):
                raise ValueError("invalid timeline end")

def record_key(kind, row):
    if kind in ("entries", "interactions", "_entry_plan"):
        key = row.get("id")
    elif kind == "chapters":
        # Order is independent of the source chapter label (which may repeat).
        key = row.get("source_id") or row.get("_storage_order")
    elif kind.endswith("timeline"):
        key = canonical([row.get("name"), row.get("field"), row.get("track") or "", row.get("from_chapter")])
    elif kind == "custom_attributes":
        key = canonical([row.get("name"), row.get("target_kind"), row.get("target"), row.get("chapter")])
    else:
        key = row.get("name")
    if key is None or key == "":
        raise ValueError("missing record identity in %s" % kind)
    return str(key)

SCHEMA = """
CREATE TABLE works(id TEXT PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE versions(work_id TEXT NOT NULL REFERENCES works(id), kind TEXT NOT NULL,
 hash TEXT NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(work_id,kind,hash));
CREATE TABLE documents(work_id TEXT NOT NULL, kind TEXT NOT NULL, hash TEXT NOT NULL,
 PRIMARY KEY(work_id,kind), FOREIGN KEY(work_id,kind,hash) REFERENCES versions(work_id,kind,hash));
CREATE TABLE records(id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
 kind TEXT NOT NULL, record_key TEXT NOT NULL, active INTEGER NOT NULL CHECK(active IN(0,1)),
 UNIQUE(work_id,kind,record_key));
CREATE TABLE batches(work_id TEXT NOT NULL REFERENCES works(id), id TEXT NOT NULL,
 hash TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(work_id,id));
CREATE TABLE jobs(id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
 stage TEXT NOT NULL, manifest TEXT NOT NULL, signature TEXT NOT NULL, status TEXT NOT NULL
 CHECK(status IN('pending','running','completed','failed')), token TEXT, lease_until REAL,
 result_path TEXT, result_hash TEXT, error TEXT, updated TEXT NOT NULL);
CREATE TABLE uploads(id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
 target_id TEXT REFERENCES records(id), kind TEXT NOT NULL CHECK(kind IN('image','review')),
 payload TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE usage(id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
 payload TEXT NOT NULL, created TEXT NOT NULL);
"""

class Store:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=15000")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            # Serialize initial creation; recheck after the lock is acquired.
            with self.transaction():
                if self.connection.execute("PRAGMA user_version").fetchone()[0] == 0:
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            self.connection.execute(statement)
                    self.connection.execute("PRAGMA user_version=1")
        elif version != 1:
            self.close()
            raise ValueError("unsupported workspace schema version: %s" % version)

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def ensure_work(self, work, title=None):
        if not isinstance(work, str) or not work.strip() or any(c in work for c in '/\\:*?"<>|') or work in (".", ".."):
            raise ValueError("work identifier must be a safe nonempty filename prefix")
        self.connection.execute("INSERT OR IGNORE INTO works VALUES(?,?)", (work, title or work))

    def get_document(self, work, kind, default=None):
        row = self.connection.execute("SELECT v.payload FROM documents d JOIN versions v USING(work_id,kind,hash) WHERE d.work_id=? AND d.kind=?", (work, kind)).fetchone()
        return json.loads(row[0]) if row else default

    def put_document(self, work, kind, value):
        value = json.loads(canonical(value))
        if kind == "_entry_plan" and value.get("work") != work:
            raise ValueError("saved entry plan belongs to another work")
        if kind == "chapters":
            # Preserve the interchange document while deriving stable storage identities separately.
            for order, row in enumerate(value.get("chapters", [])):
                row["_storage_order"] = str(order)
        validate_document(kind, value)
        if kind == "chapters":
            for row in value["chapters"]:
                row.pop("_storage_order", None)
        signature = digest(value)
        self.ensure_work(work)
        self.connection.execute("INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?)", (work, kind, signature, canonical(value), now()))
        self.connection.execute("INSERT INTO documents VALUES(?,?,?) ON CONFLICT(work_id,kind) DO UPDATE SET hash=excluded.hash", (work, kind, signature))
        self.connection.execute("UPDATE records SET active=0 WHERE work_id=? AND kind=?", (work, kind))
        rows = value.get("entries" if kind == "entries" else "chapters" if kind == "chapters" else "items", [])
        for order, row in enumerate(rows):
            key = str(row.get("source_id") or order) if kind == "chapters" else record_key(kind, row)
            identity = "r-" + uuid.uuid5(uuid.NAMESPACE_URL, canonical([work, kind, key])).hex
            self.connection.execute("INSERT INTO records VALUES(?,?,?,?,1) ON CONFLICT(work_id,kind,record_key) DO UPDATE SET active=1", (identity, work, kind, key))
        return signature

    def import_documents(self, work, docs):
        with self.transaction():
            for kind, value in docs.items():
                self.put_document(work, kind, value)
        return {"status": "imported", "documents": len(docs)}

    def export_documents(self, work, directory, kinds=None):
        # One read snapshot for all documents; interrupted exports can be rerun.
        self.connection.execute("BEGIN")
        try:
            rows = self.connection.execute("SELECT d.kind,v.payload FROM documents d JOIN versions v USING(work_id,kind,hash) WHERE d.work_id=? ORDER BY d.kind", (work,)).fetchall()
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        for row in rows:
            if kinds and row["kind"] not in kinds:
                continue
            write_json(Path(directory) / (work + "." + row["kind"] + ".json"), json.loads(row["payload"]))
        return {"status": "exported", "documents": sum(not kinds or r["kind"] in kinds for r in rows), "directory": str(Path(directory).resolve())}

    def stable_id(self, work, kind, key):
        row = self.connection.execute("SELECT id FROM records WHERE work_id=? AND kind=? AND record_key=? AND active=1", (work, kind, str(key))).fetchone()
        if not row:
            raise ValueError("target record does not exist")
        return row[0]

    def backup(self, destination):
        from contextlib import closing
        destination = Path(destination).resolve()
        if destination == self.path:
            raise ValueError("backup must use a different path")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(destination)) as target:
            self.connection.backup(target)
        return {"status": "backed_up", "path": str(destination)}
