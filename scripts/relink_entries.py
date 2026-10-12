#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按名字重建条目里的 links。

用途：当 links 里的 id 因故丢失、而 note 里记着应连条目的名字时，
用全量条目的「名字 → id」映射把 links 复原。

只按**精确名字**匹配，不做模糊推断；匹配不到的名字保留在 note 里，不猜 id。

用法：
    python relink_entries.py <目标 entries.json> --index <其它 entries.json...> [--in-place]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# note 里列名字的常见引导语
RE_NAME_LIST = re.compile(r"(?:相关条目另有|关联条目|参见|相关条目)\s*([^。；\n]+)")
SPLIT = re.compile(r"[、，,]\s*")


def main() -> int:
    ap = argparse.ArgumentParser(description="按名字重建 links")
    ap.add_argument("target")
    ap.add_argument("--index", nargs="+", required=True, help="提供名字→id 映射的条目文件")
    ap.add_argument("--in-place", action="store_true")
    args = ap.parse_args()

    # 建索引
    name2id = {}
    for p in args.index:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        for e in d.get("entries", []):
            name2id.setdefault(e["name"], e["id"])
            for a in e.get("aliases") or []:
                name2id.setdefault(a, e["id"])

    with open(args.target, encoding="utf-8") as fh:
        doc = json.load(fh)

    rebuilt = unresolved = 0
    for e in doc.get("entries", []):
        note = str(e.get("note", ""))
        m = RE_NAME_LIST.search(note)
        if not m:
            continue
        names = [n.strip() for n in SPLIT.split(m.group(1)) if n.strip()]
        ids = []
        for n in names:
            tid = name2id.get(n)
            if tid and tid != e["id"]:
                ids.append(tid)
            else:
                unresolved += 1
        # 去重保序
        seen, out = set(), []
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        if out:
            e["links"] = out
            rebuilt += 1

    target = args.target if args.in_place else args.target.replace(".json", ".relinked.json")
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    print("重建 %d 条的 links；未能匹配的名字 %d 个（保留在 note，不猜）" % (rebuilt, unresolved))
    print("输出：" + target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
