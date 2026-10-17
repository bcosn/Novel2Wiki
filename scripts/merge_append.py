#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把第四步的 append.json 合并进既有产物。

合并内容：new_entities / new_timeline / new_interactions / new_attributes / range_advance。
**不合并** corrections 与 unresolved —— 前者需人工复核，后者尚未定论，只登记进 _pending.json。

用法：
    python merge_append.py <append.json> --base-dir <产物目录> [--to-scope 第1-20回]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

TABLES = {
    "characters": "characters.json",
    "places": "places.json",
    "objects": "objects.json",
    "races": "races.json",
    "character_timeline": "character_timeline.json",
    "object_timeline": "object_timeline.json",
    "interactions": "interactions.json",
    "custom_attributes": "custom_attributes.json",
}


def load(p, default=None):
    if not os.path.isfile(p):
        return default
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def save(p, doc):
    temp = p + ".tmp"
    with open(temp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    os.replace(temp, p)


def merge_list(base_items, new_items, key):
    """按 key 去重合并，返回 (合并后列表, 新增数, 跳过数)。"""
    existing = {key(i): i for i in base_items}
    seen = set(existing)
    added = skipped = 0
    for it in new_items:
        k = key(it)
        if k in seen:
            if existing[k] != it:
                raise ValueError("同一 key 出现不同内容，必须研判后合并: %r" % (k,))
            skipped += 1
            continue
        base_items.append(it)
        seen.add(k)
        existing[k] = it
        added += 1
    return base_items, added, skipped


def prepare_append(doc, prefix, to_scope, read_document, batch_name):
    """Pure merge preparation; storage owns the commit boundary."""
    report = []
    prepared = []

    def path(kind):
        return kind

    # ---- 四表 ----
    for kind in ("characters", "places", "objects", "races"):
        p = path(kind)
        base = read_document(p, {"meta": {}, "items": []})
        base["items"], added, skipped = merge_list(
            base["items"], doc.get("new_entities", {}).get(kind, []), lambda i: i["name"])
        if to_scope:
            base["meta"]["scope"] = to_scope
        prepared.append((p, base))
        report.append(("新实体 %s" % kind, added, skipped))

    # ---- 时间线 ----
    for grp, kind in (("characters", "character_timeline"), ("objects", "object_timeline")):
        p = path(kind)
        base = read_document(p, {"meta": {}, "items": []})
        base["items"], added, skipped = merge_list(
            base["items"], doc.get("new_timeline", {}).get(grp, []),
            lambda i: (i["name"], i["field"], i.get("track") or "", i.get("from_chapter")))
        # 范围收口：把 old_to_chapter 为 null 的旧条目收口到 new_to_chapter
        for ra in doc.get("range_advance", []):
            want = "character_timeline" if ra.get("kind", "character") == "character" else "object_timeline"
            if want != kind:
                continue
            close = ra.get("new_to_chapter")
            if type(close) is not int or close < 1:
                raise ValueError("range_advance 的 new_to_chapter 必须是正整章号")
            matched = False
            for it in base["items"]:
                if (it["name"] == ra["name"] and it["field"] == ra["field"]
                        and (it.get("track") or "") == (ra.get("track") or "")
                        and it.get("from_chapter") == ra.get("from_chapter")):
                    matched = True
                    if type(it.get("from_chapter")) is int and close < it["from_chapter"]:
                        raise ValueError("range_advance 不能结束在开始章之前")
                    if it.get("to_chapter") not in (None, close):
                        raise ValueError("range_advance 与已收口范围冲突")
                    it["to_chapter"] = close
            if not matched:
                raise ValueError("range_advance 找不到对应旧时间线: %s/%s" % (ra["name"], ra["field"]))
        if to_scope:
            base["meta"]["scope"] = to_scope
        prepared.append((p, base))
        report.append(("新时间线 %s" % kind, added, skipped))

    # ---- 互动 ----
    p = path("interactions")
    base = read_document(p, {"meta": {}, "items": []})
    base["items"], added, skipped = merge_list(
        base["items"], doc.get("new_interactions", []), lambda i: i["id"])
    if to_scope:
        base["meta"]["scope"] = to_scope
    prepared.append((p, base))
    report.append(("新互动", added, skipped))

    # ---- 动态属性 ----
    p = path("custom_attributes")
    base = read_document(p, {"meta": {}, "attribute_defs": [], "items": []})
    na = doc.get("new_attributes", {})
    have = {d["name"] for d in base.get("attribute_defs", [])}
    for d in na.get("attribute_defs", []):
        if d["name"] not in have:
            base.setdefault("attribute_defs", []).append(d)
            have.add(d["name"])
    base["items"], added, skipped = merge_list(
        base.get("items", []), na.get("items", []),
        lambda i: (i["name"], i["target_kind"], i["target"], i.get("chapter")))
    if to_scope:
        base["meta"]["scope"] = to_scope
    prepared.append((p, base))
    report.append(("新动态属性", added, skipped))

    # ---- 待人工处理：corrections / unresolved 只登记，不应用 ----
    pending = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "from": batch_name,
        "note": "corrections 需人工复核后才可应用；unresolved 尚未定论。两者都不自动改数据。",
        "corrections": doc.get("corrections", []),
        "unresolved": doc.get("unresolved", []),
        "conflicts": doc.get("conflicts", []),
    }
    pp = "_pending"
    previous = read_document(pp, {})
    for field in ("corrections", "unresolved", "conflicts"):
        merged = []
        fingerprints = set()
        for item in previous.get(field, []) + pending[field]:
            fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if fingerprint not in fingerprints:
                merged.append(item)
                fingerprints.add(fingerprint)
        pending[field] = merged
    pending["batches"] = list(dict.fromkeys(previous.get("batches", [])
        + ([previous["from"]] if previous.get("from") else []) + [pending["from"]]))
    prepared.append((pp, pending))
    return dict(prepared), report


def apply_append(store, prefix, doc, batch_id, to_scope="", base_documents=None, in_transaction=False):
    from core_store import canonical, digest, now
    from contextlib import nullcontext
    signature = digest({"append": doc, "scope": to_scope})
    with nullcontext() if in_transaction else store.transaction():
        store.ensure_work(prefix)
        previous = store.connection.execute("SELECT hash FROM batches WHERE work_id=? AND id=?", (prefix, batch_id)).fetchone()
        if previous:
            if previous[0] != signature:
                raise ValueError("同一批次 ID 内容变化；请明确创建新批次或修订")
            return {"status": "reused", "batch_id": batch_id, "documents": 0}
        for kind, value in (base_documents or {}).items():
            if store.get_document(prefix, kind) is None:
                store.put_document(prefix, kind, value)
        docs, report = prepare_append(doc, prefix, to_scope,
            lambda kind, default: store.get_document(prefix, kind, default), batch_id)
        for kind, value in docs.items():
            store.put_document(prefix, kind, value)
        store.connection.execute("INSERT INTO batches VALUES(?,?,?,?)", (prefix, batch_id, signature, now()))
    return {"status": "committed", "batch_id": batch_id, "documents": len(docs),
            "added": sum(row[1] for row in report), "duplicates": sum(row[2] for row in report)}


def main() -> int:
    from pathlib import Path
    from core_store import Store, digest
    ap = argparse.ArgumentParser(description="SQLite 事务提交追加数据；JSON 为可重建导出")
    ap.add_argument("append")
    ap.add_argument("--base-dir", required=True)
    ap.add_argument("--prefix", default="红楼梦")
    ap.add_argument("--to-scope", default="")
    ap.add_argument("--db", help="默认 base-dir/novel2wiki.sqlite")
    ap.add_argument("--batch-id", help="工作任务 ID；未指定时按内容寻址")
    ap.add_argument("--no-export", action="store_true", help="仅提交，之后可统一导出")
    args = ap.parse_args()
    doc = load(args.append)
    base = Path(args.base_dir)
    imported = {}
    for kind in list(TABLES) + ["_pending"]:
        path = base / (args.prefix + "." + kind + ".json")
        if path.exists():
            imported[kind] = load(path)
    with Store(args.db or base / "novel2wiki.sqlite") as store:
        result = apply_append(store, args.prefix, doc,
            args.batch_id or "append-" + digest({"append": doc, "scope": args.to_scope}), args.to_scope, imported)
        if not args.no_export:
            result["export"] = store.export_documents(args.prefix, base)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print("合并失败: %s" % exc, file=sys.stderr)
        sys.exit(1)
