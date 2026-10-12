#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐条核对：正文里每处引号是否都能在 sources 里找到对应引文。

规范 1c：正文引原文必须加引号，并在该节 refs 里挂对应注脚。反过来说，
正文出现的每一对引号都要在 sources 里查得到出处；查不到的要么补考据，
要么改回现代汉语转述。

比对分两级：先按原样做子串匹配，再去掉标点做匹配；两级都落空才判为对不上。

用法：
    python quote_source_check.py <entries.json> [--min 2]

退出码：有对不上的引号时为 1。
"""

from __future__ import annotations

import argparse
import json
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

RE_QUOTED = re.compile(r"「([^」]*)」|『([^』]*)』|“([^”]*)”")
RE_PUNCT = re.compile(r"[^\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9]")


def squeeze(s: str) -> str:
    return RE_PUNCT.sub("", s or "")


def fields_of(e: dict):
    yield "lead", e.get("lead", "")
    for s in e.get("sections", []) or []:
        if s.get("heading"):
            yield "heading", s["heading"]
        yield "body", s.get("body", "")
    rec = e.get("reception") or {}
    for k in ("in_work", "user", "external"):
        if rec.get(k):
            yield "reception." + k, rec[k]


def main() -> int:
    ap = argparse.ArgumentParser(description="正文引号与 sources 的对应检查")
    ap.add_argument("entries")
    ap.add_argument("--min", type=int, default=2, help="短于此长度的引号视为名称标记，单列")
    args = ap.parse_args()

    with open(args.entries, encoding="utf-8") as fh:
        doc = json.load(fh)

    bad, names, total = [], [], 0
    for e in doc.get("entries", []):
        quotes = [q for q in (s.get("quote", "") for s in (e.get("sources") or []))]
        joined = "".join(quotes)
        joined_sq = squeeze(joined)
        for tag, raw in fields_of(e):
            for m in RE_QUOTED.finditer(raw or ""):
                inner = next(g for g in m.groups() if g is not None)
                total += 1
                if len(inner) < args.min:
                    names.append((e.get("id"), tag, inner))
                    continue
                if inner in joined or squeeze(inner) in joined_sq:
                    continue
                bad.append((e.get("id"), e.get("name"), tag, inner))

    print("正文引号 %d 处，其中短引号（名称标记）%d 处" % (total, len(names)))
    if names:
        marks = sorted({n for _, _, n in names})
        print("  名称标记：%s" % "、".join("「%s」" % n for n in marks))
    print()
    if bad:
        print("对不上 sources 的引号 %d 处：" % len(bad))
        for eid, name, tag, inner in bad:
            print("    %s %s [%s] 「%s」" % (eid, name, tag, inner))
    else:
        print("每处引号都能在 sources 里找到对应引文。全部通过。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
