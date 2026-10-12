#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并多批条目并按 id 顺序重新连续编号。

多批分头改稿时，id 段可能留缺口（如 e-0011~e-0019 空着）。本脚本合并后重编号，
**并同步改写 links 里的引用**，避免悬空。

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
    ap = argparse.ArgumentParser(description="合并多批条目并重新连续编号")
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

    # 按原 id 数值排序后连续重编号
    entries.sort(key=lambda e: int(str(e["id"]).split("-")[-1]))
    remap = {}
    for i, e in enumerate(entries, 1):
        remap[e["id"]] = "e-%04d" % i

    dangling = []
    for e in entries:
        old = e["id"]
        new_links = []
        for l in e.get("links") or []:
            if l in remap:
                new_links.append(remap[l])
            else:
                dangling.append("%s → %s" % (old, l))
        e["id"] = remap[old]
        e["links"] = sorted(set(new_links), key=lambda x: int(x.split("-")[-1]))

    meta["entry_count"] = len(entries)
    meta["by_kind"] = dict(Counter(e.get("kind") for e in entries))
    out = {"meta": meta, "entries": entries}
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)

    print()
    print("合并 → %s" % args.out)
    print("  条目 %d 条  %s" % (len(entries),
                              " / ".join("%s %d" % (k, v) for k, v in meta["by_kind"].items())))
    print("  重编号 %s ~ %s" % (entries[0]["id"], entries[-1]["id"]))
    ids = {e["id"] for e in entries}
    gaps = [i for i in range(1, len(entries) + 1) if "e-%04d" % i not in ids]
    print("  编号缺口：%s" % (gaps or "无"))
    print("  links 悬空：%s" % (dangling or "无"))
    return 1 if (gaps or dangling) else 0


if __name__ == "__main__":
    sys.exit(main())
