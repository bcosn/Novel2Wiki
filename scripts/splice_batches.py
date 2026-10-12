#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按号段拼装多批产物。

各批拿到的是**完整副本**，只改了自己号段内的条目。本脚本以基线为底，
逐批把自己号段内的条目覆盖回去——号段外一律用基线。

用法：
    python splice_batches.py <基线.json> -o <out.json> --seg A:1-21 B:22-41 ...
"""

from __future__ import annotations

import argparse
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def num(eid: str) -> int:
    return int(str(eid).split("-")[-1])


def main() -> int:
    ap = argparse.ArgumentParser(description="按号段拼装多批产物")
    ap.add_argument("base")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--seg", nargs="+", required=True,
                    help="形如 A:1-21（批名:起-止），对应文件 _style_<批名>.json")
    ap.add_argument("--dir", default=".", help="_style_*.json 所在目录")
    args = ap.parse_args()

    base = json.load(open(args.base, encoding="utf-8"))
    by_id = {e["id"]: e for e in base["entries"]}
    order = [e["id"] for e in base["entries"]]

    for seg in args.seg:
        tag, rng = seg.split(":")
        lo, hi = (int(x) for x in rng.split("-"))
        p = os.path.join(args.dir, "_style_%s.json" % tag)
        if not os.path.isfile(p):
            print("  [跳过] %s 不存在" % p)
            continue
        d = json.load(open(p, encoding="utf-8"))
        pool = {e["id"]: e for e in d["entries"]}
        n = 0
        for eid in order:
            if lo <= num(eid) <= hi:
                if eid not in pool:
                    print("  [警告] %s 缺 %s" % (tag, eid))
                    continue
                by_id[eid] = pool[eid]
                n += 1
        print("  %-3s e-%04d~e-%04d  取用 %d 条" % (tag, lo, hi, n))

    out = {"meta": base.get("meta", {}), "entries": [by_id[i] for i in order]}
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print()
    print("拼装 → %s" % args.out)
    print("  条目 %d 条" % len(out["entries"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
