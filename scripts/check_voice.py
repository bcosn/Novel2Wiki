#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Detect narrator voice leakage; surface ambiguous pronouns as advisory leads."""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

QUOTED = re.compile(r'「[^」]*」|『[^』]*』|“[^”]*”|‘[^’]*’|"[^"]*"')
PERSON = re.compile(r"我们|咱们|你们|本人|笔者|您|我|你")
NARRATOR_LABEL = re.compile(r'[「“]我[」”](?=为|是|从|在|认为|暗想|觉得|没有|与|和|的|送|收)')
LEXICAL = re.compile(r"自我|忘我|无我|你死我活|尔虞我诈|我行我素")

def check(doc):
    errors, warnings = [], []
    names = sorted({e.get("name", "") for e in doc.get("entries") or []
                    if e.get("kind") == "character" and len(e.get("name", "")) >= 2}, key=lambda n: (-len(n), n))
    protected = sorted({n for e in doc.get("entries") or [] for n in [e.get("name", "")] + (e.get("aliases") or []) if len(n) >= 2}, key=lambda n: (-len(n), n))
    for e in doc.get("entries") or []:
        fields = [("lead", e.get("lead", ""))]
        fields += [("sections[%d]" % n, s.get("body", "")) for n, s in enumerate(e.get("sections") or [], 1)]
        for tag, raw in fields:
            where = "%s/%s" % (e.get("id", "?"), tag)
            if NARRATOR_LABEL.search(raw or ""):
                errors.append("%s 把原文“我”当正文叙述主体，改为叙述者或角色名" % where)
            plain = re.sub(r"《[^》]*》", "", QUOTED.sub("", raw or ""))
            for name in protected:
                plain = plain.replace(name, "")
            plain = LEXICAL.sub("", plain)
            if PERSON.search(plain):
                errors.append("%s 第一、第二人称泄漏: %s" % (where, PERSON.search(plain).group()))
            for paragraph in (raw or "").split("\n\n"):
                visible = re.sub(r"《[^》]*》", "", QUOTED.sub("", paragraph))
                for sentence in re.split(r"[。！？]", visible):
                    pronoun = re.search(r"他|她|自己", sentence)
                    if not pronoun:
                        continue
                    prefix = sentence[:pronoun.start()]
                    mentioned = [n for n in names if n in prefix]
                    if len(mentioned) >= 2:
                        warnings.append("%s 同句多个候选人后使用代词，可选核对: %s" % (where, "、".join(mentioned)))
                        break
    return errors, warnings

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="第三人称正文与指代风险检查")
    ap.add_argument("entries")
    ap.add_argument("--strict", action="store_true", help="也将歧义线索计为失败；默认只提示")
    ap.add_argument("--report")
    args = ap.parse_args()
    try:
        doc = json.loads(Path(args.entries).read_text(encoding="utf-8"))
        if not doc.get("entries"):
            raise ValueError("条目文件为空")
        errors, warnings = check(doc)
    except (ValueError, OSError) as exc:
        print("无法校验: %s" % exc)
        return 1
    print("人称错误 %d；指代线索 %d（可选核对，不要求清零）" % (len(errors), len(warnings)))
    for line in errors:
        print("  [ERROR] " + line)
    for line in warnings[:5]:
        print("  [HINT] " + line)
    if args.report:
        Path(args.report).write_text(json.dumps({"errors": errors, "hints": warnings}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if errors or args.strict and warnings else 0
if __name__ == "__main__":
    sys.exit(main())
