#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 entries.json 渲染成**多页**维基式站点。

版式：
    index.html          首页，作品介绍 + 分类方框表跳转
    <条目>.html          每个词条单独一页
    assets/wiki.css     共用样式

词条页为三栏布局：
    左栏  目录（本页章节）
    中栏  正文
    右栏  信息框 + 相关条目
上方有站点名、面包屑位置提示、条目/讨论/编辑/历史 标签页。
白灰配色、少圆角。零外部请求。

用法：
    python render_wiki.py <entries.json> -o <输出目录> [--site 站点名] [--work 作品名]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

KIND_LABEL = {"character": "人物", "place": "地点", "object": "物品",
              "event": "事件", "race": "种族"}
KIND_ORDER = ["character", "place", "object", "event", "race"]

# 章节单位按作品而定：章回体写「回」，现代长篇写「章」。
# 默认保持「回」，章回体作品的既有产物不受影响。
UNIT = {"unit": "回", "infobox": "回目"}


def chapter_label(n) -> str:
    return "第 %s %s" % (n, UNIT["unit"])

# 样式放在 skill 的 assets/wiki.css，渲染时复制到站点目录。
# 改版式改那个文件即可，不必动本脚本。
CSS_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "wiki.css")


def load_css() -> str:
    with open(CSS_SRC, encoding="utf-8") as fh:
        return fh.read()



def slug(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return (s or "entry") + ".html"


def linkify(text: str, link_map: dict, self_name: str, min_len: int = 2,
            once: set | None = None) -> str:
    if not text:
        return ""
    names = sorted((n for n in link_map if len(n) >= min_len), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(n) for n in names)) if names else None
    out, pos = [], 0
    if pattern:
        for m in pattern.finditer(text):
            out.append(html.escape(text[pos:m.start()]))
            name = m.group(0)
            tid = link_map[name]
            if name == self_name or (once is not None and tid in once):
                out.append(html.escape(name))
            else:
                if once is not None:
                    once.add(tid)
                out.append('<a href="%s">%s</a>' % (html.escape(tid), html.escape(name)))
            pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


RE_URL = re.compile(r"https?://[^\s；，。、）】\"']+")


def link_urls(t: str) -> str:
    return RE_URL.sub(lambda m: '<a class="ext" href="%s" target="_blank" '
                                'rel="noopener noreferrer">%s</a>' % (m.group(0), m.group(0)), t)


def page(site: str, work: str, crumbs: list, tabs: list, title: str, body: str) -> str:
    crumb_html = ' <span style="color:#a2a9b1">&lt;</span> '.join(
        ('<a href="%s">%s</a>' % (c[1], html.escape(c[0]))) if c[1] else html.escape(c[0])
        for c in crumbs)
    tab_html = "".join('<span class="%s">%s</span>' % (t[1], html.escape(t[0])) for t in tabs)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="assets/wiki.css"></head><body>
<div id="top"><div class="site"><a href="index.html">{html.escape(site)}</a></div>
<div class="tagline">{html.escape(work)}</div></div>
<div id="crumbs">{crumb_html}</div>
<div id="tabs">{tab_html}</div>
{body}
<div id="foot">本站为《{html.escape(work)}》的整理条目，内容均可回溯到原文出处。
词条页面的讨论、编辑、历史为版式占位，不含交互功能。</div>
</body></html>"""


def tabs_for(cur: str) -> list:
    return [("条目", "cur" if cur == "条目" else "disabled"),
            ("讨论", "disabled"), ("编辑", "disabled"), ("历史", "disabled")]


def entry_page(e: dict, link_map: dict, work: str, site: str) -> str:
    name = e["name"]
    kind = KIND_LABEL.get(e.get("kind"), e.get("kind", ""))
    once: set = set()
    heads = [s["heading"] for s in (e.get("sections") or []) if s.get("heading")]

    # 左栏：目录
    left = ""
    if heads:
        left = ('<div class="tocbox"><div class="t">目录</div><ol>'
                + "".join("<li>%s</li>" % html.escape(h) for h in heads)
                + "</ol></div>")

    # 右栏：信息框 + 相关条目
    rows = [("类型", kind)]
    al = [a for a in (e.get("aliases") or []) if a != name]
    if al:
        rows.append(("别称", "、".join(al)))
    if e.get("chapter"):
        rows.append((UNIT["infobox"], chapter_label(e["chapter"])))
    if e.get("participants"):
        rows.append(("参与", "、".join(
            '<a href="%s">%s</a>' % (html.escape(link_map.get(p, "#")), html.escape(p))
            if p in link_map else html.escape(p) for p in e["participants"])))
    if e.get("place"):
        p = str(e["place"])
        rows.append(("地点", '<a href="%s">%s</a>' % (html.escape(link_map[p]), html.escape(p))
                     if p in link_map else html.escape(p)))
    if e.get("stub"):
        rows.append(("状态", "材料不足，待补"))
    right = ('<table class="infobox"><caption>%s</caption>' % html.escape(name)
             + "".join("<tr><th>%s</th><td>%s</td></tr>" % (html.escape(k), v) for k, v in rows)
             + "</table>")

    rel = [TITLE_BY_ID.get(r) for r in (e.get("links") or [])]
    rel = [t for t in rel if t and t != name]
    if rel:
        right += ('<div class="relbox"><div class="t">相关条目</div><ul>'
                  + "".join('<li><a href="%s">%s</a></li>'
                            % (html.escape(slug(t)), html.escape(t)) for t in rel)
                  + "</ul></div>")

    # 中栏：正文
    mid = ['<h1 class="firstHeading">%s</h1>' % html.escape(name)]
    if al:
        mid.append('<p class="subtitle">别称 %s</p>' % html.escape("、".join(al)))

    def refs_mark(nums) -> str:
        """把考据编号渲染成上标内联标记。"""
        if not nums:
            return ""
        return '<sup class="ref">' + "".join(
            '<a href="#ref%d">[%d]</a>' % (n, n) for n in nums) + '</sup>'

    src_list = e.get("sources") or []
    mid.append('<p>%s%s</p>' % (linkify(e.get("lead", ""), link_map, name, once=once),
                                refs_mark(e.get("lead_refs") or [])))

    for s in e.get("sections") or []:
        if s.get("heading"):
            mid.append("<h2>%s</h2>" % html.escape(s["heading"]))
        paras = [p for p in str(s.get("body", "")).split("\n\n") if p.strip()]
        marks = s.get("refs") or []
        for i, para in enumerate(paras):
            # 该节的注脚标在末段末尾
            tail = refs_mark(marks) if (i == len(paras) - 1 and marks) else ""
            mid.append("<p>%s%s</p>" % (linkify(para.strip(), link_map, name, once=once), tail))

    rec = e.get("reception") or {}
    if any(rec.get(k) for k in ("in_work", "user", "external")):
        mid.append("<h2>评价</h2>")
        for k, label in (("in_work", "作品内的评价"), ("user", "读者评价"), ("external", "外部评价")):
            if rec.get(k):
                mid.append('<p class="rec"><b>%s</b>　%s</p>'
                           % (label, linkify(str(rec[k]), link_map, name, once=once)))

    if src_list:
        mid.append("<h2>考据</h2>")
        mid.append('<div class="refs">')
        for i, s in enumerate(src_list, 1):
            cite = s.get("cite") or chapter_label(s.get("chapter", "?"))
            mid.append('<div class="refrow" id="ref%d"><span class="num"></span>'
                       '<span><span class="ch">%s</span><span class="quote">%s</span></span></div>'
                       % (i, html.escape(str(cite)), html.escape(str(s.get("quote", "")))))
            if s.get("note"):
                mid.append('<div class="refrow" style="border-left-color:#e3ded4">'
                           '<span class="num" style="visibility:hidden"></span>'
                           '<span class="quote">%s</span></div>' % html.escape(str(s["note"])))
        mid.append("</div>")

    if e.get("note"):
        mid.append('<div class="note"><b>备注</b>　%s</div>'
                   % link_urls(linkify(str(e["note"]), link_map, name, once=once)))

    body = ('<div id="layout"><div id="left">%s</div><div id="mid">%s</div>'
            '<div id="right">%s</div></div>' % (left, "\n".join(mid), right))
    crumbs = [(site, "index.html"), (work, "index.html"), (name, "")]
    return page(site, work, crumbs, tabs_for("条目"), "%s - %s" % (name, site), body)


def index_page(entries: list, work: str, site: str, meta: dict, home_note: str = "") -> str:
    groups: dict[str, list] = {}
    for e in entries:
        groups.setdefault(e.get("kind", "other"), []).append(e)

    author = meta.get("author", "")
    scope = meta.get("scope", "")
    intro = (f"<p><b>《{html.escape(work)}》</b>"
             + (f"，{html.escape(author)}著" if author else "")
             + f"。本页是这部作品的整理条目索引"
             + (f"，覆盖范围{html.escape(scope)}" if scope else "")
             + f"，共 {len(entries)} 条，按人物、地点、物品、事件分类。</p>"
             + "<p>各条内容均可回溯到原文出处，考据列在每页末尾。"
               "正文里的人物、地点、物品名可直接点击跳转。</p>")
    if home_note:
        intro += '<p class="homenote">%s</p>' % link_urls(html.escape(home_note))

    boxes = ['<div class="boxes">']
    for kind in KIND_ORDER:
        if kind not in groups:
            continue
        items = sorted(groups[kind], key=lambda x: x["name"])
        boxes.append('<div class="box"><h3>%s（%d）</h3><ul>' % (KIND_LABEL.get(kind, kind), len(items)))
        for e in items:
            boxes.append('<li><a href="%s">%s</a></li>'
                         % (html.escape(slug(e["name"])), html.escape(e["name"])))
        boxes.append("</ul></div>")
    boxes.append("</div>")

    body = (f'<div class="home"><h1 class="firstHeading">{html.escape(work)}</h1>'
            + intro + "<h2>分类索引</h2>" + "".join(boxes) + "</div>")
    crumbs = [(site, "index.html"), (work, "")]
    return page(site, work, crumbs, tabs_for("条目"), "%s - %s" % (work, site), body)


TITLE_BY_ID: dict[str, str] = {}


def main() -> int:
    ap = argparse.ArgumentParser(description="entries.json → 多页维基式站点")
    ap.add_argument("entries")
    ap.add_argument("-o", "--out", required=True, help="输出目录")
    ap.add_argument("--site", default="小说维基", help="左上角站点名")
    ap.add_argument("--work", default="", help="作品名")
    ap.add_argument("--title", default="", help="同 --work")
    ap.add_argument("--unit", default="回", help="章节单位，章回体用「回」，现代长篇用「章」")
    ap.add_argument("--home-note", default="",
                    help="附加在首页作品介绍之后的说明文字，用来交代底本与整理情况")
    args = ap.parse_args()

    UNIT["unit"] = args.unit
    UNIT["infobox"] = "回目" if args.unit == "回" else "章节"

    with open(args.entries, encoding="utf-8") as fh:
        doc = json.load(fh)
    entries = doc.get("entries", [])
    meta = doc.get("meta", {})
    work = args.work or args.title or meta.get("book", "未命名")

    global TITLE_BY_ID
    TITLE_BY_ID = {e["id"]: e["name"] for e in entries}

    link_map = {}
    for e in entries:
        link_map[e["name"]] = slug(e["name"])
        for a in e.get("aliases") or []:
            link_map.setdefault(a, slug(e["name"]))

    out = args.out
    # 不整个删目录再重建：Windows 上目录可能被浏览器或服务占用（WinError 32）。
    # 只清掉上次生成的 html 与 assets 内容，目录本身保留。
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        p = os.path.join(out, f)
        if os.path.isfile(p) and f.endswith(".html"):
            os.remove(p)
    assets = os.path.join(out, "assets")
    os.makedirs(assets, exist_ok=True)
    for f in os.listdir(assets):
        os.remove(os.path.join(assets, f))
    with open(os.path.join(assets, "wiki.css"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(load_css())
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(index_page(entries, work, args.site, meta, args.home_note))
    for e in entries:
        with open(os.path.join(out, slug(e["name"])), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(entry_page(e, link_map, work, args.site))

    print("%s → %s/" % (os.path.basename(args.entries), out))
    print("  首页 index.html + %d 个词条页 + assets/wiki.css" % len(entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
