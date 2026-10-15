#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 entries.json 渲染成单文件可跳转 HTML。

零依赖、零外部请求，双击即可打开。
条目正文里的人名/地名/物名自动指向对应条目；条目末尾给出关联条目与考据出处。

用法：
    python render.py <entries.json> -o <out.html> [--title 书名]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from entry_relations import published_relations, published_refs

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

KIND_LABEL = {
    "character": "人物",
    "place": "地点",
    "object": "物品",
    "event": "事件",
    "race": "种族",
    "concept": "设定",
}
KIND_ORDER = ["character", "place", "object", "event", "race", "concept"]

CSS = """
:root{--bg:#fbfaf7;--fg:#23201c;--muted:#6b6459;--line:#e3ded4;--accent:#8a5a2b;--card:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#1b1917;--fg:#e8e3da;--muted:#a49b8d;--line:#332f2a;--accent:#d9a86c;--card:#232019}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font:16px/1.85 -apple-system,"Segoe UI","Noto Sans CJK SC","Source Han Sans SC","Microsoft YaHei",sans-serif}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid transparent}
a:hover{border-bottom-color:var(--accent)}
header{padding:2.4rem 1.4rem 1.2rem;border-bottom:1px solid var(--line)}
.wrap{max-width:820px;margin:0 auto}
h1{margin:0 0 .35rem;font-size:1.6rem;letter-spacing:.02em}
.meta{color:var(--muted);font-size:.86rem}
nav{padding:1.2rem 1.4rem 2rem;border-bottom:1px solid var(--line)}
nav h2{font-size:.82rem;letter-spacing:.16em;color:var(--muted);margin:1.4rem 0 .5rem;font-weight:600}
nav h2:first-child{margin-top:0}
nav ul{list-style:none;margin:0;padding:0;display:flex;flex-wrap:wrap;gap:.45rem}
nav li a{display:inline-block;padding:.22rem .6rem;border:1px solid var(--line);
 border-radius:999px;font-size:.9rem;background:var(--card)}
main{padding:1.6rem 1.4rem 5rem}
article{background:var(--card);border:1px solid var(--line);border-radius:10px;
 padding:1.3rem 1.4rem;margin:0 0 1.6rem;scroll-margin-top:1rem}
article h2{margin:0 0 .2rem;font-size:1.24rem}
.kind{display:inline-block;font-size:.74rem;letter-spacing:.1em;color:var(--muted);
 border:1px solid var(--line);border-radius:4px;padding:.05rem .4rem;margin-bottom:.5rem}
.aliases{color:var(--muted);font-size:.84rem;margin:.1rem 0 .9rem}
.lead{margin:0 0 1rem}
article h3{font-size:.95rem;margin:1.3rem 0 .4rem;color:var(--accent);font-weight:600}
article p{margin:0 0 .8rem}
.sources{margin-top:1.2rem;border-top:1px dashed var(--line);padding-top:.8rem}
.sources h3{margin-top:0}
.sources ol{margin:.3rem 0 0;padding-left:1.3rem;color:var(--muted);font-size:.9rem}
.sources li{margin-bottom:.35rem}
.sources .ch{color:var(--accent);margin-right:.3rem}
.rel{margin-top:.9rem;font-size:.9rem;color:var(--muted)}
.rel a{margin-right:.5rem}
.rec{margin-top:1rem;border-left:3px solid var(--line);padding-left:.8rem;font-size:.94rem}
.rec b{color:var(--muted);font-weight:600;font-size:.84rem;letter-spacing:.06em;display:block}
.note{margin-top:.9rem;font-size:.86rem;color:var(--muted);
 border-top:1px dashed var(--line);padding-top:.7rem;line-height:1.7}
.note b{color:var(--muted);font-weight:600;font-size:.78rem;letter-spacing:.08em;
 display:block;margin-bottom:.2rem}
.stub{display:inline-block;font-size:.72rem;color:var(--muted);border:1px dashed var(--line);
 border-radius:4px;padding:.05rem .4rem;margin-left:.4rem}
footer{color:var(--muted);font-size:.82rem;text-align:center;padding:0 1.4rem 3rem}
"""


def build_link_map(entries: list) -> dict:
    """别名 -> 条目 id。用于正文自动跳转。"""
    m = {}
    for e in entries:
        m[e["name"]] = e["id"]
        for a in e.get("aliases") or []:
            m.setdefault(a, e["id"])
    return m


def linkify(text: str, link_map: dict, self_id: str, min_len: int = 1,
            once: set | None = None) -> str:
    """把正文里的实体名变成锚点链接。长名优先，避免短名先匹配。

    min_len 用于正文自动链接：单字名（如叙述者「我」）在散文里极易误伤，
    正文传 2 只链两字以上的名字；目录、关联、参与人等结构化字段可传 1。

    once 传入一个集合时，每个目标只链第一次出现（维基格式手册：同一个词只链第一次），
    集合里记已链过的目标 id。不传则每次都链。
    """
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
            if tid == self_id or (once is not None and tid in once):
                out.append(html.escape(name))
            else:
                if once is not None:
                    once.add(tid)
                out.append('<a href="#%s">%s</a>' % (html.escape(tid), html.escape(name)))
            pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


RE_URL = re.compile(r"https?://[^\s；，。、）】\"']+")


def link_urls(text: str) -> str:
    """把已转义文本里的裸 URL 变成可点击链接。

    只做显示层处理，页面本身不发起任何请求。
    """
    def rep(m):
        u = m.group(0)
        return '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (u, u)
    return RE_URL.sub(rep, text)


def render(doc: dict, title: str) -> str:
    entries = doc.get("entries", [])
    meta = doc.get("meta", {})
    link_map = build_link_map(entries)
    by_id = {e["id"]: e for e in entries}

    groups: dict[str, list] = {}
    for e in entries:
        groups.setdefault(e.get("kind", "other"), []).append(e)

    book = title or meta.get("book", "未命名")
    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>%s · 百科条目</title>" % html.escape(book),
        "<style>%s</style></head><body>" % CSS,
        '<header><div class="wrap">',
        "<h1>%s</h1>" % html.escape(book),
        '<div class="meta">%s</div>' % " · ".join(filter(None, [
            ("作者 " + html.escape(meta["author"])) if meta.get("author") else "",
            ("范围 " + html.escape(meta["scope"])) if meta.get("scope") else "",
            "共 %d 条" % len(entries),
            html.escape(meta.get("generated_at", "")),
        ])),
        "</div></header>",
    ]

    # 目录
    parts.append('<nav><div class="wrap">')
    for kind in KIND_ORDER:
        if kind not in groups:
            continue
        parts.append("<h2>%s</h2><ul>" % KIND_LABEL.get(kind, kind))
        for e in sorted(groups[kind], key=lambda x: x["name"]):
            parts.append('<li><a href="#%s">%s</a></li>'
                         % (html.escape(e["id"]), html.escape(e["name"])))
        parts.append("</ul>")
    parts.append("</div></nav>")

    # 条目
    parts.append('<main><div class="wrap">')
    # 正文自动链接：每个目标只链第一次出现（维基格式手册：同一个词只链第一次）
    once: set = set()
    for kind in KIND_ORDER:
        for e in sorted(groups.get(kind, []), key=lambda x: x["name"]):
            eid = html.escape(e["id"])
            parts.append('<article id="%s">' % eid)
            parts.append('<span class="kind">%s</span>' % KIND_LABEL.get(kind, kind))
            parts.append("<h2>%s%s</h2>" % (
                html.escape(e["name"]),
                '<span class="stub">材料不足</span>' if e.get("stub") else ""))
            al = [a for a in (e.get("aliases") or []) if a != e["name"]]
            if al:
                parts.append('<div class="aliases">别称 %s</div>'
                             % html.escape("、".join(al)))
            if e.get("lead"):
                parts.append('<p class="lead">%s</p>'
                             % linkify(e["lead"], link_map, e["id"], min_len=2, once=once))

            for sec in e.get("sections") or []:
                if sec.get("heading"):
                    parts.append("<h3>%s</h3>" % html.escape(sec["heading"]))
                for para in str(sec.get("body", "")).split("\n\n"):
                    if para.strip():
                        parts.append("<p>%s</p>"
                                     % linkify(para.strip(), link_map, e["id"], min_len=2, once=once))

            # 事件专有字段
            if kind == "event":
                bits = []
                if e.get("chapter"):
                    bits.append("第 %s 章" % e["chapter"])
                if e.get("place"):
                    bits.append("地点 " + linkify(str(e["place"]), link_map, e["id"]))
                if e.get("participants"):
                    bits.append("参与 " + "、".join(
                        linkify(p, link_map, e["id"]) for p in e["participants"]))
                if bits:
                    parts.append('<div class="rel">%s</div>' % " · ".join(bits))

            # 评价
            rec = e.get("reception") or {}
            for key, label in (("in_work", "作品内的评价"),
                               ("user", "读者评价"),
                               ("external", "外部评价")):
                if rec.get(key):
                    parts.append('<div class="rec"><b>%s</b>%s</div>'
                                 % (label, linkify(str(rec[key]), link_map, e["id"])))

            # 关联条目
            relations = published_relations(e, by_id)
            if relations:
                parts.append('<div class="rel"><h3>条目关系</h3><ul>%s</ul></div>' % "".join(
                    '<li>%s：<a href="#%s">%s</a>%s</li>' % (
                        html.escape(relation["label"]), html.escape(target["id"], quote=True), html.escape(target["name"]),
                        '<sup>%s</sup>' % "".join('<a href="#%s-ref%d">[%d]</a>' % (eid, ref, ref)
                            for ref in published_refs(relation, len(e.get("sources") or [])))
                        if published_refs(relation, len(e.get("sources") or [])) else "")
                    for relation, target in relations))
            rel = [by_id[r] for r in (e.get("links") or []) if r in by_id and r != e["id"]]
            if rel:
                parts.append('<div class="rel">关联 %s</div>' % " ".join(
                    '<a href="#%s">%s</a>' % (html.escape(r["id"]), html.escape(r["name"]))
                    for r in rel))

            # 考据
            src = e.get("sources") or []
            if src:
                parts.append('<div class="sources"><h3>考据</h3><ol>')
                for ref, s in enumerate(src, 1):
                    parts.append('<li id="%s-ref%d"><span class="ch">第%s章</span>%s</li>'
                                 % (eid, ref, html.escape(str(s.get("chapter", "?"))),
                                    html.escape(str(s.get("quote", "")))))
                parts.append("</ol></div>")

            # 备注（如外部评价来源、考据分歧提示）
            if e.get("note"):
                parts.append('<div class="note"><b>备注</b>%s</div>'
                             % link_urls(linkify(str(e["note"]), link_map, e["id"], min_len=2)))
            parts.append("</article>")
    parts.append("</div></main>")

    parts.append('<footer><div class="wrap">单文件生成，无外部请求 · '
                 '正文中的实体名可直接点击跳转</div></footer>')
    parts.append("</body></html>")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="entries.json → 单文件可跳转 HTML")
    ap.add_argument("entries", help="条目 JSON")
    ap.add_argument("-o", "--out", required=True, help="输出 HTML 路径")
    ap.add_argument("--title", default="", help="覆盖书名")
    args = ap.parse_args()

    with open(args.entries, encoding="utf-8") as fh:
        doc = json.load(fh)
    out = render(doc, args.title)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out)

    n = len(doc.get("entries", []))
    kinds = {}
    for e in doc.get("entries", []):
        kinds[e.get("kind")] = kinds.get(e.get("kind"), 0) + 1
    print("%s → %s" % (os.path.basename(args.entries), args.out))
    print("  条目 %d 条  %s" % (n, " / ".join("%s %d" % (KIND_LABEL.get(k, k), v)
                                             for k, v in kinds.items())))
    print("  大小 %.1f KB" % (os.path.getsize(args.out) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
