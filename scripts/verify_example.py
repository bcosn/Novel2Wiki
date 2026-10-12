#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 example1 里的成品是否自洽。

一条命令查完示例目录：各表条数、重名、悬空引用、时间线收口、互动主体间完整性、
条目考据覆盖、渲染产物是否零外部依赖。

用法：
    python verify_example.py <example1 目录> [--book 红楼梦]
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

TABLES = ["characters", "places", "objects", "races",
          "character_timeline", "object_timeline", "interactions", "custom_attributes"]

OK, BAD = "  [OK]   ", "  [FAIL] "


def L(d, name):
    p = os.path.join(d, name)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    ap = argparse.ArgumentParser(description="校验示例成品")
    ap.add_argument("dir", help="example1 目录")
    ap.add_argument("--book", default="红楼梦")
    args = ap.parse_args()
    d, book = args.dir, args.book
    fails = 0

    def check(cond, msg):
        nonlocal fails
        print((OK if cond else BAD) + msg)
        if not cond:
            fails += 1

    print("=== %s 各表 ===" % book)
    counts = {}
    for t in TABLES:
        doc = L(d, "%s.%s.json" % (book, t))
        if doc is None:
            print("  [MISS] %-20s 不存在" % t)
            continue
        n = len(doc.get("items", []))
        counts[t] = n
        print("  %-20s %4d 条   scope=%s" % (t, n, doc.get("meta", {}).get("scope", "-")))

    ch = L(d, "%s.characters.json" % book)
    pl = L(d, "%s.places.json" % book)
    ob = L(d, "%s.objects.json" % book)
    ctl = L(d, "%s.character_timeline.json" % book)
    otl = L(d, "%s.object_timeline.json" % book)
    it = L(d, "%s.interactions.json" % book)
    ca = L(d, "%s.custom_attributes.json" % book)

    print()
    print("=== 一致性 ===")
    for nm, doc in (("characters", ch), ("places", pl), ("objects", ob)):
        if doc:
            dup = [k for k, c in Counter(i["name"] for i in doc["items"]).items() if c > 1]
            check(not dup, "%s 无重名%s" % (nm, ("（重名：%s）" % dup) if dup else ""))

    if ch and ctl:
        miss = [i["name"] for i in ctl["items"] if i["name"] not in {x["name"] for x in ch["items"]}]
        check(not miss, "character_timeline 无悬空引用%s" % (("（%s）" % miss[:5]) if miss else ""))
    if ob and otl:
        miss = [i["name"] for i in otl["items"] if i["name"] not in {x["name"] for x in ob["items"]}]
        check(not miss, "object_timeline 无悬空引用%s" % (("（%s）" % miss[:5]) if miss else ""))

    if ch and it:
        names = {x["name"] for x in ch["items"]}
        no_persp = [i["id"] for i in it["items"]
                    if any(p not in (i.get("perspectives") or {}) for p in i.get("participants", []))]
        check(not no_persp, "互动表每个参与者都有主体间视角%s"
              % (("（缺：%s）" % no_persp[:5]) if no_persp else ""))
        dangling = [i["id"] for i in it["items"]
                    if any(p not in names for p in i.get("participants", []))]
        check(not dangling, "互动参与者无悬空%s" % (("（%s）" % dangling[:5]) if dangling else ""))
        empty_asym = [i["id"] for i in it["items"] if not str(i.get("asymmetry", "")).strip()]
        check(not empty_asym, "互动 asymmetry 无空缺%s"
              % (("（%s）" % empty_asym[:5]) if empty_asym else ""))

    if ctl:
        open_ended = sum(1 for i in ctl["items"] if i.get("to_chapter") is None)
        print("  [INFO] character_timeline 中 to_chapter=null 的有 %d 条（表示截至范围末尾仍如此）" % open_ended)
    if ca:
        tg = {x["name"] for x in (ch or {}).get("items", [])}
        bad_t = [i["target"] for i in ca.get("items", [])
                 if i.get("target_kind") == "character" and i["target"] not in tg]
        check(not bad_t, "动态属性的人物 target 无悬空%s" % (("（%s）" % bad_t[:5]) if bad_t else ""))

    print()
    print("=== 条目 ===")
    en = L(d, "%s.entries.json" % book)
    if en is None:
        print("  [MISS] 条目文件不存在")
    else:
        items = en.get("entries", [])
        print("  条目 %d 条  %s" % (len(items),
                                  " / ".join("%s %d" % (k, v)
                                             for k, v in Counter(i.get("kind") for i in items).items())))
        no_src = [i["name"] for i in items if not (i.get("sources") or [])]
        check(not no_src, "每条都有考据%s" % (("（无考据：%s）" % no_src[:5]) if no_src else ""))
        stubs = [i["name"] for i in items if i.get("stub")]
        print("  [INFO] 标 stub 的 %d 条%s" % (len(stubs), ("：" + "、".join(stubs[:6])) if stubs else ""))
        # 写作规范
        viol = {"冒号": 0, "破折号": 0, "不是而是": 0}
        for i in items:
            txts = [i.get("lead", "")] + [s.get("body", "") for s in i.get("sections", [])]
            for t in txts:
                if "：" in t:
                    viol["冒号"] += 1
                if "——" in t:
                    viol["破折号"] += 1
                if re.search(r"不是.{0,12}而是", t):
                    viol["不是而是"] += 1
        check(all(v == 0 for v in viol.values()),
              "正文未用冒号/破折号/「不是…而是」%s" % (viol if any(viol.values()) else ""))

    print()
    print("=== 渲染产物 ===")
    for h in sorted(f for f in os.listdir(d) if f.endswith(".html")):
        raw = open(os.path.join(d, h), encoding="utf-8").read()
        scripts = len(re.findall(r"<script", raw))
        imgs = len(re.findall(r"<img", raw))
        anchors = len(re.findall(r'<article id="', raw))
        links = len(re.findall(r'href="#', raw))
        check(scripts == 0 and imgs == 0, "%s 无脚本无图片引用" % h)
        print("  [INFO] %s  条目 %d  锚点链接 %d  大小 %.1f KB"
              % (h, anchors, links, len(raw.encode()) / 1024))

    # 多页站点
    for site in sorted(f for f in os.listdir(d)
                       if f.endswith("_wiki") and os.path.isdir(os.path.join(d, f))):
        sp = os.path.join(d, site)
        files = sorted(f for f in os.listdir(sp) if f.endswith(".html"))
        have = set(files)
        dangling, ext, scripts = [], 0, 0
        for f in files:
            raw = open(os.path.join(sp, f), encoding="utf-8").read()
            scripts += len(re.findall(r"<script", raw))
            ext += len(re.findall(r'src="https?://|href="https?://(?!#)', raw))
            for m in re.finditer(r'href="([^"#:]+\.html)"', raw):
                if m.group(1) not in have:
                    dangling.append("%s → %s" % (f, m.group(1)))
        check(not dangling, "%s 站内链接无悬空%s"
              % (site, ("（%s）" % dangling[:4]) if dangling else ""))
        check(scripts == 0, "%s 无脚本" % site)
        check(os.path.isfile(os.path.join(sp, "index.html")), "%s 有首页" % site)
        check(os.path.isfile(os.path.join(sp, "assets", "wiki.css")), "%s 有样式文件" % site)
        print("  [INFO] %s  %d 页（首页 + %d 词条）  外部请求 %d"
              % (site, len(files), len(files) - 1, ext))

    print()
    print("失败项 %d" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
