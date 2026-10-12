#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨条目重复检查（含跨批次）。

`check_style.py` 只查**同一条目文件内**的 lead 重复，而第二批是分批并行写的，
同一段原文材料可能被两批各自写成条目（第二批的 K 批就报告过：诺诺在拉面馆那段自述
同时出现在四条条目里）。本工具在**合并后**的条目上查两件事：

  1. 不同条目的正文之间，有没有较长（默认 ≥20 字）的同形片段
  2. 有没有两条的 lead 高度相似

比对前会**去掉引号内的内容**——引文按设计就该与原文一致，两条都引同一句不算重复；
剩下来的重合才是编者叙述的重复。

用法：
    python tools/check_duplication.py data/龙族5.entries.json [--min 20]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

RE_QUOTED = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|‘[^’]*’")
RE_KEEP = re.compile(r"[^\u3400-\u4dbf\u4e00-\u9fff]")


def norm(s: str) -> str:
    """去掉引号内内容，只留汉字。"""
    return RE_KEEP.sub("", RE_QUOTED.sub("", s or ""))


def texts(e):
    yield "lead", e.get("lead") or ""
    for i, s in enumerate(e.get("sections") or [], 1):
        yield "body%d" % i, s.get("body") or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("entries")
    ap.add_argument("--min", type=int, default=20, help="同形片段的最小字数，默认 20")
    args = ap.parse_args()

    doc = json.load(open(args.entries, encoding="utf-8"))
    ents = doc.get("entries", [])

    # 分片索引：每 N 字一片，记下出现过它的条目
    idx = defaultdict(set)
    frag_of = {}
    for e in ents:
        eid = e.get("id")
        for tag, raw in texts(e):
            t = norm(raw)
            for i in range(0, max(0, len(t) - args.min + 1)):
                frag = t[i:i + args.min]
                idx[frag].add((eid, tag))
                frag_of.setdefault(frag, (eid, tag, raw))

    # 只保留「出现在两个以上不同条目」的分片，并合并成较长的片段
    hits = []
    for frag, owners in idx.items():
        ids = {o[0] for o in owners}
        if len(ids) < 2:
            continue
        hits.append((frag, sorted(ids), sorted(owners)))

    # 合并相邻分片，避免同一处重复报几十遍
    merged = []
    hits.sort()
    used = set()
    for frag, ids, owners in hits:
        if frag in used:
            continue
        # 向右扩展
        cur = frag
        while True:
            nxt = cur + "x"
            cand = [f for f in idx if f.startswith(cur) and len(f) == len(cur) + 1]
            ext = None
            for c in cand:
                if {o[0] for o in idx[c]} == set(ids):
                    ext = c
                    break
            if not ext:
                break
            cur = ext
            used.add(ext)
        merged.append((cur, ids))

    print("条目 %d 条，跨条目同形片段（去引号后 ≥%d 字）%d 处"
          % (len(ents), args.min, len(merged)))
    if not merged:
        print("没有发现跨条目重复。")
        return 0
    for frag, ids in merged[:40]:
        print("   %2d 条共用「%s」  ← %s" % (len(ids), frag[:34], "、".join(ids)))
    if len(merged) > 40:
        print("   ... 另有 %d 处" % (len(merged) - 40))
    return 1


if __name__ == "__main__":
    sys.exit(main())
