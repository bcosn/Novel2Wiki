#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""entries.json → local multi-page wiki with a Vector 2022 inspired layout."""
from __future__ import annotations
import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from entry_relations import published_relations, published_refs

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

KIND_LABEL = {"character": "人物", "place": "地点", "object": "物品", "event": "事件", "race": "种族", "concept": "设定"}
KIND_ORDER = list(KIND_LABEL)
UNIT = {"unit": "回", "infobox": "回目"}
TITLE_BY_ID = {}
CSS_SRC = Path(__file__).resolve().parent.parent / "assets" / "wiki.css"
ESC = html.escape

def chapter_label(n):
    return "第%s%s" % (n, UNIT["unit"])

def load_css():
    return CSS_SRC.read_text(encoding="utf-8")

def slug(name):
    s = re.sub(r'[\\/:*?"<>|]', "_", name).strip().rstrip(". ")
    if not s:
        s = "entry"
    if s.casefold() == "index" or re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", s):
        s = "条目-" + s
    return s + ".html"

def filenames(entries):
    """Windows uses case-insensitive paths; sanitized titles may collide."""
    result, used = {}, {"index.html"}
    for e in sorted(entries, key=lambda x: x["id"]):
        candidate = slug(e["name"])
        base = candidate[:-5]
        n = 2
        while candidate.casefold() in used:
            candidate = "%s-%d.html" % (base, n)
            n += 1
        result[e["name"]] = candidate
        used.add(candidate.casefold())
    return result

def linkify(text, link_map, self_name, min_len=2, once=None):
    text = str(text or "")
    own = link_map.get(self_name)
    names = sorted((n for n in link_map if len(n) >= min_len), key=len, reverse=True)
    out, pos = [], 0
    if names:
        for match in re.finditer("|".join(re.escape(n) for n in names), text):
            out.append(ESC(text[pos:match.start()]))
            name = match.group()
            target = link_map[name]
            if target == own or (once is not None and target in once):
                out.append(ESC(name))
            else:
                if once is not None:
                    once.add(target)
                out.append('<a href="%s">%s</a>' % (ESC(target, quote=True), ESC(name)))
            pos = match.end()
    out.append(ESC(text[pos:]))
    return "".join(out)

def link_urls(text):
    # Escape each URL and text independently, never rewrite already-generated markup.
    out, pos = [], 0
    for match in re.finditer(r"https?://[^\s；，。、）】<>\"']+", str(text)):
        out.append(ESC(text[pos:match.start()]))
        url = match.group()
        out.append('<a class="external" href="%s" rel="noopener noreferrer">%s</a>' % (ESC(url, quote=True), ESC(url)))
        pos = match.end()
    out.append(ESC(text[pos:]))
    return "".join(out)

def refs_mark(nums):
    return ('<sup class="ref">' + "".join('<a href="#ref%d">[%d]</a>' % (n, n) for n in nums) + "</sup>") if nums else ""

def tools():
    return """<aside id="right" aria-label="页面工具">
<div class="side-heading">工具</div>
<ul><li><a href="index.html">作品首页</a></li><li><a href="index.html#categories">条目索引</a></li>
<li><a href="#firstHeading">回到开头</a></li></ul>
<div class="side-heading">外观</div>
<fieldset><legend>文字</legend>
<label><input id="text-small" type="radio" name="text-size">小</label>
<label><input type="radio" name="text-size" checked>标准</label>
<label><input id="text-large" type="radio" name="text-size">大</label></fieldset>
<fieldset><legend>宽度</legend>
<label><input type="radio" name="page-width" checked>标准</label>
<label><input id="width-wide" type="radio" name="page-width">宽</label></fieldset>
</aside>"""

def page(site, work, name, toc, article):
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="Novel2Wiki v0.3">
<title>%s - %s</title><link rel="stylesheet" href="assets/wiki.css">
</head><body>
<a class="skip-link" href="#content">跳至内容</a>
<header class="site-header">
<details class="main-menu"><summary aria-label="主菜单">☰</summary><nav><a href="index.html">作品首页</a><a href="index.html#categories">条目索引</a></nav></details>
<a class="brand" href="index.html"><span class="brand-mark" aria-hidden="true">文</span><span><strong>%s</strong><small>%s</small></span></a>
<a class="browse-link" href="index.html#categories"><span aria-hidden="true">⌕</span> 浏览条目</a>
</header>
<div class="page-shell">
<aside id="left"><details class="tocbox" open><summary>目录</summary><nav aria-label="本页目录">%s</nav></details></aside>
<main id="content"><div class="titlebar"><h1 id="firstHeading">%s</h1></div>
<nav class="article-toolbar" aria-label="条目操作"><div><span class="current">条目</span><span class="unavailable" aria-disabled="true" title="静态站点未提供讨论">讨论</span></div>
<div><span class="current">阅读</span><span class="unavailable" aria-disabled="true" title="静态站点未提供在线编辑">编辑</span><span class="unavailable" aria-disabled="true" title="静态站点未提供版本历史">查看历史</span></div></nav>
<details class="mobile-toc"><summary>目录</summary><nav aria-label="本页目录">%s</nav></details>
%s</main>%s</div>
<footer class="site-footer">本站整理《%s》的作品内容。引用范围与原文出处见各条目参考资料。</footer>
</body></html>""" % (ESC(name), ESC(site), ESC(site), ESC(work), toc, ESC(name), toc, article, tools(), ESC(work))

def additions_html(additions):
    additions = additions or {}
    parts = []
    if additions.get("images"):
        parts.append('<section class="user-gallery"><h2 id="user-images">图片</h2>')
        for image in additions["images"]:
            parts.append('<figure><img loading="lazy" src="%s" alt="%s"><figcaption>%s%s</figcaption></figure>' % (
                ESC(image["url"], quote=True), ESC(image.get("caption", ""), quote=True), ESC(image.get("caption", "")),
                '<span class="upload-source"> 来源：%s</span>' % link_urls(image["source"]) if image.get("source") else ""))
        parts.append('</section>')
    if additions.get("reviews"):
        parts.append('<section class="user-reviews"><h2 id="user-reviews">用户书评</h2>')
        for review in additions["reviews"]:
            parts.append('<article class="user-review"><h3>%s</h3><p class="review-meta">%s</p>' % (
                ESC(review.get("title") or "书评"), ESC(" · ".join(x for x in [review.get("author"), review.get("date")] if x))))
            for para in review["body"].split("\n\n"):
                if para.strip():
                    parts.append('<p>%s</p>' % ESC(para.strip()))
            if review.get("source"):
                parts.append('<p class="upload-source">来源：%s</p>' % link_urls(review["source"]))
            parts.append('</article>')
        parts.append('</section>')
    return "\n".join(parts)

def entry_page(e, link_map, work, site, additions=None, entry_by_id=None):
    name = e["name"]
    kind = KIND_LABEL.get(e.get("kind"), "条目")
    once = set()
    sources = e.get("sources") or []
    sections = e.get("sections") or []
    entry_by_id = entry_by_id or {eid: {"id": eid, "name": title} for eid, title in TITLE_BY_ID.items()}
    relations = published_relations(e, entry_by_id)
    toc = ['<ul><li><a href="#firstHeading">序言</a></li>']
    for n, s in enumerate(sections, 1):
        if s.get("heading"):
            toc.append('<li><a href="#section-%d">%s</a></li>' % (n, ESC(s["heading"])))
    rec = e.get("reception") or {}
    if any(rec.get(k) for k in ("in_work", "user", "external")):
        toc.append('<li><a href="#reception">评价</a></li>')
    if sources:
        toc.append('<li><a href="#refs-open">参考资料</a></li>')
    if relations:
        toc.append('<li><a href="#relations">条目关系</a></li>')
    if e.get("links"):
        toc.append('<li><a href="#related">相关条目</a></li>')
    toc.append("</ul>")

    rows = [("类型", ESC(kind)), ("作品", ESC("《%s》" % work))]
    aliases = [a for a in (e.get("aliases") or []) if a != name]
    if aliases:
        rows.append(("别称", ESC("、".join(aliases))))
    if e.get("chapter") is not None:
        rows.append((UNIT["infobox"], ESC(chapter_label(e["chapter"]))))
    if e.get("participants"):
        rows.append(("参与者", "、".join(linkify(p, link_map, name) for p in e["participants"])))
    if e.get("place"):
        rows.append(("地点", linkify(e["place"], link_map, name)))
    if e.get("stub"):
        rows.append(("条目状态", "材料待补"))
    infobox = '<table class="infobox"><caption>%s</caption><tbody>%s</tbody></table>' % (
        ESC(name), "".join('<tr><th scope="row">%s</th><td>%s</td></tr>' % row for row in rows))
    body = ['<div class="shortdescription">《%s》%s</div>' % (ESC(work), ESC(kind)),
            '<article class="article-body">' + infobox,
            '<p class="lead">%s%s</p>' % (linkify(e.get("lead", ""), link_map, name, once=once), refs_mark(e.get("lead_refs") or []))]
    for n, s in enumerate(sections, 1):
        if s.get("heading"):
            body.append('<h2 id="section-%d">%s</h2>' % (n, ESC(s["heading"])))
        paras = [p.strip() for p in str(s.get("body", "")).split("\n\n") if p.strip()]
        for i, para in enumerate(paras):
            body.append('<p>%s%s</p>' % (linkify(para, link_map, name, once=once),
                refs_mark(s.get("refs") or []) if i == len(paras)-1 else ""))
    if any(rec.get(k) for k in ("in_work", "user", "external")):
        body.append('<h2 id="reception">评价</h2>')
        for key, label in (("in_work", "作品内"), ("user", "读者"), ("external", "外部资料")):
            if rec.get(key):
                body.append('<p><b>%s</b> %s%s</p>' % (label, ESC(str(rec[key])), refs_mark((e.get("reception_refs") or {}).get(key) or [])))
    if sources:
        body.extend(['<section class="refsblock" id="refs-closed"><h2>参考资料</h2>',
            '<a class="refsopen" href="#refs-open">展开原文引用（%d 条）</a>' % len(sources),
            '<a class="refsshut" href="#refs-closed">收起原文引用</a>', '<ol class="references" id="refs-open">'])
        for n, s in enumerate(sources, 1):
            body.append('<li class="refrow" id="ref%d"><span class="ref-cite">%s</span><blockquote>%s</blockquote>%s</li>' % (
                n, ESC(str(s.get("cite") or chapter_label(s.get("chapter", "?")))), ESC(str(s.get("quote", ""))),
                '<p class="source-note">%s</p>' % ESC(str(s["note"])) if s.get("note") else ""))
        body.append("</ol></section>")
    if relations:
        body.append('<h2 id="relations">条目关系</h2><ul>%s</ul>' % "".join(
            '<li>%s：<a href="%s">%s</a>%s</li>' % (
                ESC(relation["label"]), ESC(link_map[target["name"]], quote=True), ESC(target["name"]),
                refs_mark(published_refs(relation, len(sources)))) for relation, target in relations))
    related = [TITLE_BY_ID.get(i) for i in e.get("links") or []]
    related = [t for t in related if t in link_map and t != name]
    if e.get("links"):
        body.append('<h2 id="related">相关条目</h2><ul>%s</ul>' % "".join(
            '<li><a href="%s">%s</a></li>' % (ESC(link_map[t], quote=True), ESC(t)) for t in related))
    if e.get("note"):
        body.append('<div class="note">%s</div>' % link_urls(str(e["note"])))
    body.append(additions_html(additions))
    body.append("</article>")
    return page(site, work, name, "".join(toc), "\n".join(body))

def index_page(entries, work, site, meta, link_map, home_note="", additions=None):
    intro = "《%s》%s。本页收录%s范围内的%d条百科条目。" % (
        work, "，%s著" % meta["author"] if meta.get("author") else "",
        meta.get("scope", "已整理"), len(entries))
    body = ['<article class="article-body"><p class="lead">%s</p>' % ESC(intro)]
    if home_note:
        body.append('<p class="note">%s</p>' % link_urls(home_note))
    toc = ['<ul><li><a href="#firstHeading">简介</a></li><li><a href="#categories">条目索引</a></li>']
    body.append('<h2 id="categories">条目索引</h2><div class="category-grid">')
    for kind in KIND_ORDER:
        items = sorted((e for e in entries if e["kind"] == kind), key=lambda e: e["name"])
        if not items:
            continue
        toc.append('<li><a href="#category-%s">%s</a></li>' % (kind, KIND_LABEL[kind]))
        body.append('<section class="category"><h3 id="category-%s">%s</h3><ul>%s</ul></section>' % (
            kind, KIND_LABEL[kind], "".join('<li><a href="%s">%s</a></li>' % (ESC(link_map[e["name"]], quote=True), ESC(e["name"])) for e in items)))
    body.append("</div>" + additions_html(additions) + "</article>")
    toc.append("</ul>")
    return page(site, work, work, "".join(toc), "\n".join(body))

def render(doc, out, site="小说维基", work="", unit="回", home_note="", overlay=None):
    global TITLE_BY_ID
    entries = doc.get("entries") or []
    if not entries:
        raise ValueError("条目文件为空")
    for key in ("id", "name"):
        vals = [e.get(key) for e in entries]
        if any(not isinstance(v, str) or not v for v in vals) or len(vals) != len(set(vals)):
            raise ValueError("条目 %s 缺失或重复" % key)
    for e in entries:
        if e.get("kind") not in KIND_LABEL:
            raise ValueError("未知条目类型: %s" % e.get("kind"))
        for nums in [e.get("lead_refs") or []] + [s.get("refs") or [] for s in e.get("sections") or []]:
            if any(type(n) is not int or not 1 <= n <= len(e.get("sources") or []) for n in nums):
                raise ValueError("%s 的 refs 越界" % e["id"])
    UNIT.update(unit=unit, infobox="回目" if unit == "回" else "章节")
    TITLE_BY_ID = {e["id"]: e["name"] for e in entries}
    mapping = filenames(entries)
    entry_by_id = {e["id"]: e for e in entries}
    link_map = dict(mapping)
    for e in entries:
        for alias in e.get("aliases") or []:
            link_map.setdefault(alias, mapping[e["name"]])
    meta = doc.get("meta") or {}
    work = work or meta.get("book") or "未命名"
    overlay = overlay or {}
    pages = {"index.html": index_page(entries, work, site, meta, link_map, home_note, overlay.get("work"))}
    pages.update({mapping[e["name"]]: entry_page(e, link_map, work, site, overlay.get("entries", {}).get(e["id"]), entry_by_id) for e in entries})
    css = load_css()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    # Only remove stale files listed by this renderer, never arbitrary user assets.
    manifest = out / ".novel2wiki-manifest.json"
    old = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    if not manifest.exists():
        old = [p.name for p in out.glob("*.html")
               if '<div id="foot">本站为《' in p.read_text(encoding="utf-8")]
    for filename in old:
        p = (out / filename).resolve()
        if p.parent == out and filename not in pages and p.is_file() and p.suffix == ".html":
            p.unlink()
    for filename, raw in pages.items():
        (out / filename).write_text(raw, encoding="utf-8", newline="\n")
    (out / "assets" / "wiki.css").write_text(css, encoding="utf-8", newline="\n")
    manifest.write_text(json.dumps(list(pages), ensure_ascii=False, indent=2), encoding="utf-8")
    return mapping

def main():
    ap = argparse.ArgumentParser(description="entries.json → Vector 2022 风格静态 Wiki")
    ap.add_argument("entries")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--site", default="小说维基")
    ap.add_argument("--work", "--title", default="")
    ap.add_argument("--unit", default="回")
    ap.add_argument("--home-note", default="")
    args = ap.parse_args()
    try:
        doc = json.loads(Path(args.entries).read_text(encoding="utf-8"))
        mapping = render(doc, args.out, args.site, args.work, args.unit, args.home_note)
    except (ValueError, OSError) as exc:
        print("渲染失败: %s" % exc, file=sys.stderr)
        return 1
    print("首页 + %d 个词条页 → %s" % (len(mapping), args.out))
    return 0

if __name__ == "__main__":
    sys.exit(main())
