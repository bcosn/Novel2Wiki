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


def main() -> int:
    ap = argparse.ArgumentParser(description="合并第四步追加产物")
    ap.add_argument("append")
    ap.add_argument("--base-dir", required=True)
    ap.add_argument("--to-scope", default="", help="合并后的 scope，如「第1-20回」")
    ap.add_argument("--prefix", default="红楼梦", help="文件名前缀，如「红楼梦」")
    args = ap.parse_args()

    doc = load(args.append)
    prefix = args.prefix
    report = []
    prepared = []

    def path(kind):
        return os.path.join(args.base_dir, "%s.%s" % (prefix, TABLES[kind]))

    # ---- 四表 ----
    for kind in ("characters", "places", "objects", "races"):
        p = path(kind)
        base = load(p, {"meta": {}, "items": []})
        base["items"], added, skipped = merge_list(
            base["items"], doc.get("new_entities", {}).get(kind, []), lambda i: i["name"])
        if args.to_scope:
            base["meta"]["scope"] = args.to_scope
        prepared.append((p, base))
        report.append(("新实体 %s" % kind, added, skipped))

    # ---- 时间线 ----
    for grp, kind in (("characters", "character_timeline"), ("objects", "object_timeline")):
        p = path(kind)
        base = load(p, {"meta": {}, "items": []})
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
        if args.to_scope:
            base["meta"]["scope"] = args.to_scope
        prepared.append((p, base))
        report.append(("新时间线 %s" % kind, added, skipped))

    # ---- 互动 ----
    p = path("interactions")
    base = load(p, {"meta": {}, "items": []})
    base["items"], added, skipped = merge_list(
        base["items"], doc.get("new_interactions", []), lambda i: i["id"])
    if args.to_scope:
        base["meta"]["scope"] = args.to_scope
    prepared.append((p, base))
    report.append(("新互动", added, skipped))

    # ---- 动态属性 ----
    p = path("custom_attributes")
    base = load(p, {"meta": {}, "attribute_defs": [], "items": []})
    na = doc.get("new_attributes", {})
    have = {d["name"] for d in base.get("attribute_defs", [])}
    for d in na.get("attribute_defs", []):
        if d["name"] not in have:
            base.setdefault("attribute_defs", []).append(d)
            have.add(d["name"])
    base["items"], added, skipped = merge_list(
        base.get("items", []), na.get("items", []),
        lambda i: (i["name"], i["target_kind"], i["target"], i.get("chapter")))
    if args.to_scope:
        base["meta"]["scope"] = args.to_scope
    prepared.append((p, base))
    report.append(("新动态属性", added, skipped))

    # ---- 待人工处理：corrections / unresolved 只登记，不应用 ----
    pending = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "from": os.path.basename(args.append),
        "note": "corrections 需人工复核后才可应用；unresolved 尚未定论。两者都不自动改数据。",
        "corrections": doc.get("corrections", []),
        "unresolved": doc.get("unresolved", []),
        "conflicts": doc.get("conflicts", []),
    }
    pp = os.path.join(args.base_dir, "%s._pending.json" % prefix)
    previous = load(pp, {})
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
    # Validate all tables before any writes; conflicting keys must not partly advance scope.
    for target, value in prepared:
        save(target, value)

    print("合并完成：%s → %s" % (os.path.basename(args.append), args.base_dir))
    for name, added, skipped in report:
        print("  %-22s 新增 %3d  跳过重复 %2d" % (name, added, skipped))
    print("  待人工处理（corrections %d / unresolved %d / conflicts %d）→ %s"
          % (len(pending["corrections"]), len(pending["unresolved"]),
             len(pending["conflicts"]), os.path.basename(pp)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError) as exc:
        print("合并失败: %s" % exc, file=sys.stderr)
        sys.exit(1)
