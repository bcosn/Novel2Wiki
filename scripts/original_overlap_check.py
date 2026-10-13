#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出条目正文与原文连续重合达到阈值的片段。

规范 1c 要求：正文里凡用了原文的字句，都必须加引号标出；没加引号的必须是
现代汉语的编者语言。本脚本把两边的标点与引号内内容都去掉，只留汉字，做最长
公共子串扫描，把所有 ≥N 字的连续重合片段列出来，作为可选线索而非清零门槛。

引号内内容按设计就是要与原文重合的引用，故先剔除；剩余重合也可能来自专名或正常
事实短语，需结合语境判断，不能直接断定照搬。

用法：
    python original_overlap_check.py <entries.json> <chapters.json> [--min 10]

默认只提示；--strict 时命中返回 1。重合本身不能证明照搬或事实错误。
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

# 引号内内容：成对的直角引号与弯引号
RE_QUOTED = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|‘[^’]*’|\"[^\"]*\"")
RE_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def strip_quoted(s: str) -> str:
    return RE_QUOTED.sub("", s or "")


def norm(s: str) -> str:
    """只留汉字：去标点、去空白、去阿拉伯数字与注号。"""
    return "".join(RE_CJK.findall(s or ""))


def fields_of(e: dict):
    """逐字段产出可用于比对的文本。"""
    yield "lead", e.get("lead", "")
    for s in e.get("sections", []) or []:
        if s.get("heading"):
            yield "heading", s["heading"]
        yield "body", s.get("body", "")
    rec = e.get("reception") or {}
    for k in ("in_work", "user", "external"):
        if rec.get(k):
            yield "reception." + k, rec[k]
    if e.get("note"):
        yield "note", e["note"]


def maximal_matches(text: str, source: str, min_len: int):
    """贪心扫出 text 中所有与 source 连续重合 ≥min_len 的极大片段。"""
    out = []
    i, n = 0, len(text)
    while i + min_len <= n:
        j = i
        while j < n and text[i:j + 1] in source:
            j += 1
        length = j - i
        if length >= min_len:
            out.append((i, length, text[i:j]))
            i += length
        else:
            i += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="条目正文与原文的重合片段检查")
    ap.add_argument("entries")
    ap.add_argument("chapters")
    ap.add_argument("--min", type=int, default=10, help="阈值字数，默认 10")
    ap.add_argument("--strict", action="store_true", help="命中时返回失败；默认线索检查不要求清零")
    ap.add_argument("--allow", default="",
                    help="允许的片段，逗号分隔。专名与固定名词短语属合规，"
                         "不该为躲检查器而改写（如「大荒山无稽崖」「义忠亲王老千岁」）")
    args = ap.parse_args()

    allow = [a.strip() for a in args.allow.split(",") if a.strip()]
    # 豁免项要按同一套归一化处理：正文比对的片段已经过 norm()（只留汉字），
    # 而豁免项若带阿拉伯数字或连字符（如「米-35武装直升机」），原样比对永远匹配不上，
    # 于是专名被误判成违规。这里把豁免项也归一化后再用。
    allow_norm = sorted({norm(a) for a in allow if norm(a)}, key=len, reverse=True)

    with open(args.entries, encoding="utf-8") as fh:
        doc = json.load(fh)
    with open(args.chapters, encoding="utf-8") as fh:
        chap = json.load(fh)

    source = norm("\n".join(c.get("content", "") for c in chap.get("chapters", [])))
    entries = doc.get("entries", [])

    total = 0
    print("原文归一化 %d 字；重合阈值 %d 字" % (len(source), args.min))
    if allow:
        print("已豁免 %d 个片段：%s" % (len(allow), "、".join(allow)))
    print()

    def residual(frag: str) -> str:
        """剥掉豁免的专名/名词短语，返回剩余部分。frag 与豁免项都已归一化。"""
        r = frag
        for a in allow_norm:
            r = r.replace(a, "")
        return r

    for e in entries:
        hits = []
        for tag, raw in fields_of(e):
            text = norm(strip_quoted(raw))
            for pos, length, frag in maximal_matches(text, source, args.min):
                # 剥掉专名后若已不足阈值，说明重合全部由专名构成，合规
                if len(residual(frag)) < args.min:
                    continue
                hits.append((tag, pos, length, frag))
        if hits:
            total += len(hits)
            print("%s %s  重合 %d 处" % (e.get("id"), e.get("name"), len(hits)))
            for tag, pos, length, frag in hits:
                print("    [%s] %d 字  %s" % (tag, length, frag))
    print()
    if total == 0:
        print("正文与原文连续重合 ≥%d 字的片段：0 处。全部通过。" % args.min)
    else:
        print("正文与原文连续重合 ≥%d 字的线索：%d 处，结合语境判断，不要求清零。"
              % (args.min, total))
    return 1 if total and args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
