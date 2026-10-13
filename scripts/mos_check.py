#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mechanical typography checks. Meaning and writing quality require contextual judgment."""
from __future__ import annotations
import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

CJK = r"\u4e00-\u9fff"
RE_SPACE_MIX = re.compile(r"[%s] [A-Za-z0-9]|[A-Za-z0-9] [%s]" % (CJK, CJK))
RE_HALF_PUNCT = re.compile(r"[%s][,;?!]|[,;?!][%s]" % (CJK, CJK))
RE_FULLWIDTH_DIGIT = re.compile(r"[０-９]")
QUOTED = re.compile(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"]*"')

def texts_of(e):
    yield "lead", e.get("lead", "")
    for s in e.get("sections") or []:
        yield "heading", s.get("heading", "")
        yield "body", s.get("body", "")
    for key, value in (e.get("reception") or {}).items():
        if value:
            yield "reception." + key, value

class HeadingLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.level = 0
        self.bad = False
    def handle_starttag(self, tag, attrs):
        if re.fullmatch("h[1-6]", tag):
            self.level += 1
        if tag == "a" and self.level:
            self.bad = True
    def handle_endtag(self, tag):
        if re.fullmatch("h[1-6]", tag):
            self.level = max(0, self.level-1)

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="条目排版检查；不强制文风词表、禁标点或重合清零")
    ap.add_argument("entries")
    ap.add_argument("--html", default="")
    ap.add_argument("--report", default="")
    args = ap.parse_args()
    issues = []
    try:
        doc = json.loads(Path(args.entries).read_text(encoding="utf-8"))
        entries = doc.get("entries") or []
        if not entries:
            raise ValueError("条目文件为空")
        for e in entries:
            for tag, text in texts_of(e):
                if not isinstance(text, str):
                    continue
                plain = QUOTED.sub("", text)
                for label, regex in (("中英数字间多余空格", RE_SPACE_MIX), ("中文用半角标点", RE_HALF_PUNCT), ("全角数字", RE_FULLWIDTH_DIGIT)):
                    for match in regex.finditer(plain):
                        issues.append("%s/%s %s: %s" % (e.get("id", "?"), tag, label, match.group()))
        if args.html:
            raw = Path(args.html).read_text(encoding="utf-8")  # Missing requested HTML is an error.
            parser = HeadingLinks()
            parser.feed(raw)
            if parser.bad:
                issues.append("渲染标题内含链接")
    except (OSError, ValueError) as exc:
        issues.append("无法检查: %s" % exc)
    print("排版问题 %d" % len(issues))
    for issue in issues[:30]:
        print("  " + issue)
    if args.report:
        Path(args.report).write_text("# 排版检查\n\n" + ("\n".join("- " + i for i in issues) or "通过") + "\n", encoding="utf-8")
    return 1 if issues else 0

if __name__ == "__main__":
    sys.exit(main())
