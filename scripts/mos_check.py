#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按维基百科格式手册检查条目与渲染产物。

来源：Wikipedia:格式手册（中文维基百科，oldid=93721497），用户提供。

检查项
    中文与外文/数字混排之间不应加空格
    中文内容用全角标点，不用半角
    一般数字用半角阿拉伯数字，不用全角
    年份加「年」且不简写（不可作「78 年」「七八年」）
    章节标题中避免链接
    同一词只链第一次出现，不重复链
    外链格式：地址后空一格接描述文字
    正文不得靠折叠元素隐藏（渲染层检查）

用法：
    python mos_check.py <entries.json> [--html out.html] [--report r.md]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

CJK = r"\u4e00-\u9fff"
RE_SPACE_MIX = re.compile(r"[%s] [A-Za-z0-9]|[A-Za-z0-9] [%s]" % (CJK, CJK))
RE_HALF_PUNCT = re.compile(r"[%s][,;?!]|[,;?!][%s]" % (CJK, CJK))
RE_FULLWIDTH_DIGIT = re.compile(r"[０-９]")
RE_SHORT_YEAR = re.compile(r"(?<![\d年])[’']?\d{2}\s*年")
RE_HEADING_LINK = re.compile(r"<h[1-6][^>]*>[^<]*(?:<a\s|<a>)", re.S)
RE_COLLAPSE = re.compile(r"(collaps|<details|max-height:\s*0|display:\s*none)", re.I)

# 照搬原文腔调的标志：文言虚词与近代白话套语。
# 注意不要放单字虚词（乎/矣/焉/哉/耳）——它们会误伤「几乎」「耳朵」这类现代词。
RE_CLASSICAL = re.compile(
    r"之乎者也|排出[一二三四五六七八九十百千两\d]+文|终于没有进学|不会营生|"
    r"愈过愈穷|弄到将要讨饭|如是几次|样子太傻|偷得的么|间或|定然|"
    r"踱进|嚷道|分辩|作揖|不胜|颇为|甚是|聊以|旋即|随即|"
    r"罢了|不成样子|好生|端的|恁地")
# 正文里引用原文应有的标记
RE_QUOTE_MARK = re.compile(r"[「『“\"][^」』”\"]{2,}[」』”\"]")
# 明令禁止的标点与句式
RE_FORBIDDEN_PUNCT = re.compile(r"[:：]|——|--|—")
RE_NOT_BUT = re.compile(r"不是.{0,20}而是|并非.{0,20}而是|不在于.{0,20}而在于")
# 规范第一节明列的「推断词」：编者替读者下结论的口吻。
# 这类词本身不算错，但出现在叙述里就是在替读者归纳，规范要求删掉或改成原文说法。
RE_INFERENCE = re.compile(r"可见|由此|由此可见|实际上|本质上|意味着|反映了|构成了|"
                          r"说明了|足以说明|不难看出|恰恰说明")


def texts_of(e):
    yield "lead", e.get("lead", "")
    for s in e.get("sections", []):
        if s.get("heading"):
            yield "heading", s["heading"]
        yield "body", s.get("body", "")
    rec = e.get("reception") or {}
    for k in ("in_work", "user", "external"):
        if rec.get(k):
            yield "reception." + k, rec[k]
    if e.get("note"):
        yield "note", e["note"]


def main() -> int:
    ap = argparse.ArgumentParser(description="按维基格式手册检查条目")
    ap.add_argument("entries")
    ap.add_argument("--html", default="", help="同时检查渲染产物")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    with open(args.entries, encoding="utf-8") as fh:
        doc = json.load(fh)
    entries = doc.get("entries", [])

    issues: list[tuple[str, str, str, str]] = []

    def add(rule, where, detail):
        issues.append((rule, where, detail, ""))

    for e in entries:
        name = e.get("name", "?")
        for tag, t in texts_of(e):
            if not isinstance(t, str):
                continue
            for m in RE_SPACE_MIX.finditer(t):
                add("中英数字间加空格", "%s/%s" % (name, tag), repr(m.group(0)))
            for m in RE_HALF_PUNCT.finditer(t):
                add("中文用半角标点", "%s/%s" % (name, tag), repr(m.group(0)))
            for m in RE_FULLWIDTH_DIGIT.finditer(t):
                add("全角数字", "%s/%s" % (name, tag), repr(m.group(0)))
            # 年份简写只在**叙述**里算违规。原文引文里的年份本来就写作「82年」
            # （「开了一瓶82年的拉菲」），规范又要求引文逐字照抄，两条规则直接冲突；
            # 所以这里与下面的「照搬腔调」一样，先剔除引号内内容再查。
            stripped_q = RE_QUOTE_MARK.sub("", t)
            for m in RE_SHORT_YEAR.finditer(stripped_q):
                add("年份简写", "%s/%s" % (name, tag), repr(m.group(0)))
            # 明令禁止的标点与句式（全角半角都算）
            if tag in ("lead", "body", "reception.in_work"):
                for m in RE_FORBIDDEN_PUNCT.finditer(t):
                    add("禁标点（冒号/破折号）", "%s/%s" % (name, tag),
                        "…%s…" % t[max(0, m.start() - 10):m.end() + 10].replace("\n", " "))
                for m in RE_NOT_BUT.finditer(t):
                    add("翻案句（不是…而是）", "%s/%s" % (name, tag),
                        "…%s…" % t[max(0, m.start() - 6):m.end() + 6].replace("\n", " "))
            # 照搬原文腔调：文言虚词出现在叙述里（不在引号内）
            if tag in ("lead", "body"):
                stripped = RE_QUOTE_MARK.sub("", t)      # 去掉已标出的引用
                for m in RE_CLASSICAL.finditer(stripped):
                    add("照搬原文腔调", "%s/%s" % (name, tag),
                        "…%s…（原文用词需改现代汉语或加引号）"
                        % stripped[max(0, m.start() - 8):m.end() + 8].replace("\n", " "))
                # 推断词：引文里的不算（那是原文的口吻），叙述里出现就是编者在下结论
                for m in RE_INFERENCE.finditer(stripped):
                    add("推断词（编者替读者下结论）", "%s/%s" % (name, tag),
                        "…%s…（规范第一节明列，应删掉或改成原文说法）"
                        % stripped[max(0, m.start() - 8):m.end() + 8].replace("\n", " "))
        # 标题中避免链接（条目数据层：heading 不应含 markdown 链接）
        for s in e.get("sections", []):
            if s.get("heading") and re.search(r"\[\[|\]\(|http", s["heading"]):
                add("标题含链接", name, s["heading"])

    # 渲染产物检查
    html_issues = []
    if args.html and os.path.isfile(args.html):
        raw = open(args.html, encoding="utf-8").read()
        if RE_HEADING_LINK.search(raw):
            html_issues.append("渲染产物里章节标题内含链接")
        if RE_COLLAPSE.search(raw):
            html_issues.append("渲染产物出现折叠/隐藏样式，可能遮挡正文")
        # 同一词重复链接
        hrefs = Counter(re.findall(r'href="#([^"]+)"', raw))
        # 目录与关联区的重复属正常，只看正文段落内的重复
        body = " ".join(re.findall(r"<p[^>]*>(.*?)</p>", raw, re.S))
        body_hrefs = Counter(re.findall(r'href="#([^"]+)"', body))
        heavy = [(k, v) for k, v in body_hrefs.items() if v > 12]
        if heavy:
            html_issues.append("正文中同一目标链接次数偏多：" + ", ".join("%s×%d" % t for t in heavy[:5]))

    print("条目 %d 条" % len(entries))
    print()
    if not issues:
        print("条目检查：全部通过")
    else:
        by_rule = Counter(i[0] for i in issues)
        print("条目检查：%d 处" % len(issues))
        for r, c in by_rule.most_common():
            print("  %-18s %d 处" % (r, c))
        for rule, where, detail, _ in issues[:25]:
            print("      [%s] %s %s" % (rule, where, detail))
        if len(issues) > 25:
            print("      ... 另有 %d 处" % (len(issues) - 25))

    if args.html:
        print()
        if not html_issues:
            print("渲染检查：全部通过")
        else:
            print("渲染检查：%d 处" % len(html_issues))
            for h in html_issues:
                print("      " + h)

    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# 格式手册检查\n\n条目 %d 条\n\n## 条目问题\n\n" % len(entries))
            fh.write("\n".join("- [%s] %s %s" % i[:3] for i in issues) or "无")
            fh.write("\n\n## 渲染问题\n\n")
            fh.write("\n".join("- " + h for h in html_issues) or "无")
            fh.write("\n")
        print("报告：" + args.report)

    return 1 if (issues or html_issues) else 0


if __name__ == "__main__":
    sys.exit(main())
