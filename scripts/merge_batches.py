#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并多批条目，保留稳定 ID 与链接。

编号缺口合法；重复 ID、重名和悬空链接在写入前拒绝，不删除链接或重编号。

用法：
    python merge_batches.py <批1.json> <批2.json> ... -o <out.json> [--base <原文件.json>]
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


def main() -> int:
    ap = argparse.ArgumentParser(description="合并多批条目，保留稳定 ID")
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--base", default="", help="取 meta 的原文件")
    args = ap.parse_args()

    entries, meta = [], {}
    for p in args.inputs:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        if not meta:
            meta = dict(d.get("meta", {}))
        entries += d.get("entries", [])
        print("%-24s %3d 条" % (os.path.basename(p), len(d.get("entries", []))))

    if args.base and os.path.isfile(args.base):
        with open(args.base, encoding="utf-8") as fh:
            meta = dict(json.load(fh).get("meta", {}))

    from core_store import validate_document, write_json
    validate_document("entries", {"entries": entries})
    names = [e["name"] for e in entries]
    if len(names) != len(set(names)):
        raise ValueError("重名条目，需明确合并")
    entries.sort(key=lambda e: e["id"])

    meta["entry_count"] = len(entries)
    meta["by_kind"] = dict(Counter(e.get("kind") for e in entries))
    out = {"meta": meta, "entries": entries}
    write_json(args.out, out)

    print()
    print("合并 → %s" % args.out)
    print("  条目 %d 条  %s" % (len(entries),
                              " / ".join("%s %d" % (k, v) for k, v in meta["by_kind"].items())))
    print("  稳定 ID 保留，结构与链接检查通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("合并失败: %s" % exc, file=sys.stderr)
        sys.exit(1)
