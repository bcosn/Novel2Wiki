#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check section-level citations and, optionally, quotes against chapter text."""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

RE_QUOTED = re.compile(r"「([^」]*)」|『([^』]*)』|“([^”]*)”")
RE_PUNCT = re.compile(r"[^\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9]")
def squeeze(s):
    return RE_PUNCT.sub("", s or "")
def compact(s):
    text = str(s or "").translate(str.maketrans("「」『』", "“”‘’"))
    text = re.sub(r"[⑴-⒇①-⑳]", "", text)
    return re.sub(r"\s+", "", text)

def quote_in_source(quote, source):
    # Each source is compared separately. Concatenating sources creates false matches.
    return bool(quote) and (quote in source or bool(squeeze(quote)) and squeeze(quote) in squeeze(source))

def quote_in_chapter(quote, content):
    def paragraphs(text):
        return [compact(p) for p in re.split(r"\n\s*\n", text) if compact(p)]
    # Wrapped lines are not paragraph boundaries. Preserve real blank-line boundaries.
    if "\u2029".join(paragraphs(quote)) in "\u2029".join(paragraphs(content)) and compact(quote):
        return True
    parts = [compact(p) for p in re.split(r"…+|\.{3,}", quote) if compact(p)]
    if not parts:
        return False
    # Omitted passages must remain in order inside one source paragraph.
    for paragraph in re.split(r"\n\s*\n", content):
        text = compact(paragraph)
        pos = 0
        for part in parts:
            found = text.find(part, pos)
            if found < 0:
                break
            pos = found + len(part)
        else:
            return True
    return False

def check(doc, chapters=None, min_quote=2):
    errors, total = [], 0
    entries = doc.get("entries") or []
    if not entries:
        errors.append("条目文件为空")
    chapter_map = None
    if chapters is not None:
        chapter_map = {c.get("index"): c.get("content", "") for c in chapters.get("chapters") or []}
        if not chapter_map:
            errors.append("章节文件为空")
    for e in entries:
        eid = e.get("id", "?")
        sources = e.get("sources") or []
        if not sources:
            errors.append("%s 没有 sources" % eid)
        fields = [("lead", e.get("lead", ""), e.get("lead_refs") or [])]
        fields.extend(("sections[%d]" % n, s.get("body", ""), s.get("refs") or [])
                      for n, s in enumerate(e.get("sections") or [], 1))
        fields.extend(("relations[%d]" % n, r.get("label", ""), r.get("refs") or [])
                      for n, r in enumerate(e.get("relations") or [], 1))
        used = set()
        for tag, text, refs in fields:
            if not isinstance(refs, list):
                errors.append("%s/%s 的 refs 必须是数组" % (eid, tag))
                continue
            valid = []
            for n in refs:
                if type(n) is not int or not 1 <= n <= len(sources):
                    errors.append("%s/%s 的 refs=%r 越界或类型错误" % (eid, tag, n))
                else:
                    valid.append(n)
                    used.add(n)
            if str(text or "").strip() and not valid:
                errors.append("%s/%s 有正文却无有效 refs" % (eid, tag))
            for match in RE_QUOTED.finditer(text or ""):
                quote = next(g for g in match.groups() if g is not None)
                if len(quote) < min_quote:
                    continue
                total += 1
                if not any(quote_in_source(quote, sources[n-1].get("quote", "")) for n in valid):
                    errors.append("%s/%s 的引文「%s」不在本节 refs 对应的任一 source 内" % (eid, tag, quote))
        for relation in e.get("relations") or []:
            actual={sources[n-1].get("evidence_id") for n in relation.get("refs",[]) if type(n) is int and 1<=n<=len(sources)}
            if actual!=set(relation.get("evidence_ids",[])):
                errors.append("%s/relations 引用与声明的证据ID不一致" % eid)
        # Reception is legacy prose; explicit reception_refs are supported, while
        # older entries may match quotes against their individual sources.
        for key, text in (e.get("reception") or {}).items():
            if key != "in_work" or not isinstance(text, str):
                continue
            refs = (e.get("reception_refs") or {}).get(key)
            pool = sources if refs is None else [sources[n-1] for n in refs if type(n) is int and 1 <= n <= len(sources)]
            if refs is not None and len(pool) != len(refs):
                errors.append("%s/reception.%s 的 refs 越界" % (eid, key))
            for match in RE_QUOTED.finditer(text):
                quote = next(g for g in match.groups() if g is not None)
                if len(quote) >= min_quote and not any(quote_in_source(quote, s.get("quote", "")) for s in pool):
                    errors.append("%s/reception.%s 引文无对应来源: %s" % (eid, key, quote))
        for n, source in enumerate(sources, 1):
            quote = source.get("quote", "")
            if not isinstance(quote, str) or not quote.strip():
                errors.append("%s/source[%d] 引文为空" % (eid, n))
            if chapter_map is not None:
                ch = source.get("chapter")
                if type(ch) is not int or ch not in chapter_map:
                    errors.append("%s/source[%d] 章节 %r 不存在" % (eid, n, ch))
                elif not quote_in_chapter(quote, chapter_map[ch]):
                    errors.append("%s/source[%d] 引文不在所标章节的同一段落内" % (eid, n))
    return errors, total

def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="引用、节级 refs 与原文章节检查")
    ap.add_argument("entries")
    ap.add_argument("--chapters", help="清洗后的 chapters.json；提供时核验 source 原文")
    ap.add_argument("--min", type=int, default=2)
    args = ap.parse_args()
    try:
        doc = json.loads(Path(args.entries).read_text(encoding="utf-8"))
        chapters = json.loads(Path(args.chapters).read_text(encoding="utf-8")) if args.chapters else None
        errors, count = check(doc, chapters, args.min)
    except (ValueError, OSError) as exc:
        print("无法校验: %s" % exc, file=sys.stderr)
        return 1
    print("正文引文 %d 处；错误 %d 处%s" % (count, len(errors), "；已核对原文章节" if chapters is not None else ""))
    for error in errors:
        print("  [ERROR] " + error)
    return 1 if errors else 0
if __name__ == "__main__":
    sys.exit(main())
