#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""条目体例与一致性检查：查审批方点出、但四条常规检查覆盖不到的那几类。

这些是「AI 读出来之后才写成规则」的项，顺序不能反——先由审批方发现，再机械化：

  * 句首单调：一节里连续多句以同一主体名或「第N章」起头
  * 节标题用章号：`heading` 写成「第8章」这种
  * 空节标题：`heading` 为空字符串，渲染目录会出现空白项
  * 跨条目重复：两条 lead 共用较长同形片段
  * 首句同语反复：「X 是……一台 X」
  * 一句里重复主体名：读成结巴
  * refs 挂了考据却整节没用上（粗查：该节 body 的归一化文本与 refs 指向的 quote 无任何重合）

用法：
    python scripts/check_style.py <entries.json> [--report r.md]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

RE_SENT = re.compile(r"[^。！？…]+[。！？…]+|[^。！？…]+$")
RE_CHAPTER = re.compile(r"第\s*\d+\s*(?:章|回|节|卷)")
# 节首就用章号起句（如「第21章写……」）
RE_CHAPTER_HEAD = re.compile(r"^第\s*\d+\s*(?:章|回|节|卷)")
# 引号内的内容：引文按设计就该是原文，查体例时不算编者的话
RE_QUOTED = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|‘[^’]*’")
CJK = re.compile(r"[^\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9]")
# 句首若是引号、标点或数字，这一句就不该参与「句首单调」的比对——
# 以对话起句不是「同一个主体连着起头」。
RE_NOT_A_HEAD = re.compile(r"^[「『“‘」』”’（()）【】\[\]]|\d")


def norm(s):
    return CJK.sub("", s or "")


def strip_quoted(s):
    """去掉引号内内容，只留编者自己的话。"""
    return RE_QUOTED.sub("", s or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("entries")
    ap.add_argument("--report", default="")
    ap.add_argument("--strict", action="store_true", help="将文风线索视为失败；默认只提示")
    args = ap.parse_args()

    doc = json.load(open(args.entries, encoding="utf-8"))
    ents = doc.get("entries", [])
    issues = []

    for e in ents:
        eid, name = e.get("id", "?"), e.get("name", "?")

        # 节首用章号起句
        #
        # 章号是考据的坐标，不是叙述的一部分（规范第 8 条）。这一项**全量查**，
        # 不区分新旧节——曾经按「refs 是否落在第21章之后」来反推新增节，
        # 那个判据把范围写死在 21 章上，对只做前 20 回的作品一条也查不出来。
        src = e.get("sources") or []
        for i, s in enumerate(e.get("sections") or [], 1):
            body = (s.get("body") or "").strip()
            if not body:
                continue
            # 按**段落**逐段匹配，不是只看整个 body 的第一个字符。
            # body 里用空行分段时，后面某段以「第N回」开头的同样违规——
            # 早先只 match(body) 会整片漏掉（实测漏了 124 处）。
            for pi, para in enumerate((p.strip() for p in body.split("\n")), 1):
                if para and RE_CHAPTER_HEAD.match(para):
                    issues.append(("节首用章号起句",
                                   "%s/%s sections[%d] 段%d" % (eid, name, i, pi),
                                   "%r…。整句重写，从物、动作或时间起头，章号只留 refs 与考据块"
                                   % para[:28]))
                    break

        # 节标题
        for i, s in enumerate(e.get("sections") or [], 1):
            h = (s.get("heading") or "").strip()
            # 空 heading 不是问题：规范允许用空字符串表示「本节不分节」，
            # 渲染器也会在目录与正文里跳过它（render_wiki.py 两处都判 if s.get("heading")）。
            if h and RE_CHAPTER.search(h):
                issues.append(("节标题用章号（建议改）", "%s/%s sections[%d]" % (eid, name, i),
                               "heading=%r。规范没有禁止，但同批其余条目多用主题式标题；"
                               "章号放 refs 与考据块更整齐" % h))

        # 句首单调
        for i, s in enumerate(e.get("sections") or [], 1):
            sents = [x.strip() for x in RE_SENT.findall(s.get("body") or "") if x.strip()]
            heads = []
            for x in sents:
                # 以引号/标点/数字起句的不参与比对：那是对话或列举，不是「同一主体连着起头」
                if RE_NOT_A_HEAD.match(x):
                    heads.append(None)
                    continue
                m = RE_CHAPTER.match(x)
                heads.append("第N章" if m else x[:2])
            run, best = 1, 1
            for a, b in zip(heads, heads[1:]):
                run = run + 1 if (a is not None and a == b) else 1
                best = max(best, run)
            if best >= 3:
                cand = [h for h in heads if h]
                issues.append(("句首单调", "%s/%s sections[%d]" % (eid, name, i),
                               "连续 %d 句以「%s」起头"
                               % (best, Counter(cand).most_common(1)[0][0] if cand else "?")))

        # 一句里重复主体名（引号内的不算：那是原文的说法，不是编者写结巴了）
        for i, s in enumerate(e.get("sections") or [], 1):
            for sent in RE_SENT.findall(s.get("body") or ""):
                plain = strip_quoted(sent)
                if len(name) >= 2 and plain.count(name) >= 2:
                    issues.append(("一句重复主体名", "%s/%s sections[%d]" % (eid, name, i),
                                   "「%s」在一句里出现 %d 次：%s"
                                   % (name, plain.count(name), plain.strip()[:40])))
                    break

        # 首句同语反复
        lead = e.get("lead") or ""
        first = lead.split("。")[0]
        if len(name) >= 2 and first.count(name) >= 2:
            issues.append(("首句同语反复", "%s/%s lead" % (eid, name), first[:48]))

    # 跨条目重复 lead
    leads = [(e.get("id"), e.get("name"), norm(e.get("lead") or "")) for e in ents]
    for i in range(len(leads)):
        for j in range(i + 1, len(leads)):
            a, b = leads[i][2], leads[j][2]
            if len(a) < 10 or len(b) < 10:
                continue
            best = ""
            for k in range(len(a) - 9):
                frag = a[k:k + 10]
                if frag in b and len(frag) > len(best):
                    best = frag
            if best:
                issues.append(("跨条目重复", "%s/%s 与 %s/%s" % (leads[i][0], leads[i][1],
                                                              leads[j][0], leads[j][1]),
                               "lead 共用同形片段「%s」" % best))

    by = Counter(x[0] for x in issues)
    print("条目 %d 条，文风线索 %d 处（可选核对，不要求清零）" % (len(ents), len(issues)))
    for k, v in by.most_common():
        print("  %-14s %d 处" % (k, v))
    print()
    for rule, where, detail in issues[:60]:
        print("   [%s] %s  %s" % (rule, where, detail))
    if len(issues) > 60:
        print("   ... 另有 %d 处" % (len(issues) - 60))
    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("# 体例与一致性检查\n\n条目 %d 条，问题 %d 处\n\n" % (len(ents), len(issues)))
            for rule, where, detail in issues:
                fh.write("- [%s] %s  %s\n" % (rule, where, detail))
    return 1 if issues and args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
