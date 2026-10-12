#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并多个条目文件，并校验。

多个写作方各写一份 entries_*.json，按 id 段分配，本脚本合并成一份并检查：
    * id 冲突
    * 重名条目
    * 字段齐备（lead / sections / sources / reception / links / stub）
    * links 悬空（指向不存在的 id）
    * kind 合法

用法：
    python merge_entries.py <a.json> <b.json> ... -o <out.json> [--report r.md]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

KINDS = {"character", "place", "object", "event", "race"}
REQUIRED = ("id", "kind", "name", "lead", "sections", "sources", "links", "stub")


def main() -> int:
    ap = argparse.ArgumentParser(description="合并条目文件并校验")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    entries, metas = [], []
    for p in args.inputs:
        if not os.path.isfile(p):
            print("跳过不存在的文件: %s" % p)
            continue
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        metas.append(d.get("meta", {}))
        entries += d.get("entries", [])
        print("%-28s %3d 条" % (os.path.basename(p), len(d.get("entries", []))))

    errors, warns = [], []

    # id 冲突
    for i, c in Counter(e.get("id") for e in entries).items():
        if c > 1:
            errors.append("id 冲突：%s 出现 %d 次" % (i, c))
    # 重名
    for n, c in Counter(e.get("name") for e in entries).items():
        if c > 1:
            errors.append("重名条目：%s 出现 %d 次" % (n, c))

    ids = {e.get("id") for e in entries}
    for e in entries:
        eid = e.get("id", "?")
        for k in REQUIRED:
            if k not in e:
                errors.append("%s 缺字段 %s" % (eid, k))
        if e.get("kind") not in KINDS:
            errors.append("%s 的 kind 非法：%r" % (eid, e.get("kind")))
        if not str(e.get("lead", "")).strip():
            errors.append("%s 的 lead 为空" % eid)
        for l in e.get("links") or []:
            if l not in ids:
                warns.append("%s 的 links 指向不存在的 %s" % (eid, l))
        rec = e.get("reception")
        if not isinstance(rec, dict):
            errors.append("%s 的 reception 不是对象" % eid)
        else:
            for k in ("in_work", "user", "external"):
                if k not in rec:
                    warns.append("%s 的 reception 缺 %s" % (eid, k))
        if not (e.get("sources") or []):
            warns.append("%s 没有 sources（无考据）" % eid)

    meta = dict(metas[0]) if metas else {}
    meta["entry_count"] = len(entries)
    meta["by_kind"] = dict(Counter(e.get("kind") for e in entries))
    out = {"meta": meta, "entries": sorted(entries, key=lambda e: e.get("id", ""))}
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print()
    print("合并 → %s" % args.out)
    print("  条目 %d 条  %s" % (len(entries),
                              " / ".join("%s %d" % (k, v) for k, v in meta["by_kind"].items())))
    print("  错误 %d  警告 %d" % (len(errors), len(warns)))
    for e in errors:
        print("  [ERROR] " + e)
    for w in warns[:20]:
        print("  [WARN ] " + w)
    if len(warns) > 20:
        print("  ... 另有 %d 条警告" % (len(warns) - 20))

    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# 条目合并报告\n\n条目 %d 条\n\n" % len(entries))
            fh.write("## 错误\n\n" + ("\n".join("- " + e for e in errors) or "无") + "\n\n")
            fh.write("## 警告\n\n" + ("\n".join("- " + w for w in warns) or "无") + "\n")
        print("报告：" + args.report)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
