#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate required tables, timeline intervals and generated site navigation."""
from __future__ import annotations
import argparse
import collections
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

TABLES = ["characters", "places", "objects", "races", "character_timeline",
          "object_timeline", "interactions", "custom_attributes"]

class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links, self.resources = [], [], []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        for key in ("src", "srcset"):
            if attrs.get(key):
                self.resources.append(attrs[key])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.resources.append(attrs.get("href", ""))

def table_errors(tables):
    errors = []
    pools = {}
    for name in ("characters", "places", "objects", "races"):
        items = tables[name].get("items")
        if not isinstance(items, list):
            errors.append("%s.items 不是数组" % name)
            items = []
        names = [i.get("name") for i in items]
        pools[name] = set(names)
        if any(not isinstance(n, str) or not n for n in names) or len(names) != len(set(names)):
            errors.append("%s 有缺失姓名或重名" % name)
    for name, group in (("character_timeline", "characters"), ("object_timeline", "objects")):
        entity_start = {i["name"]: i.get("first_chapter", 1) for i in tables[group].get("items") or []}
        intervals = collections.defaultdict(list)
        for item in tables[name].get("items") or []:
            entity = item.get("name")
            if entity not in pools[group]:
                errors.append("%s/%s 悬空引用" % (name, entity))
            start, end = item.get("from_chapter"), item.get("to_chapter")
            if start is None:
                start = entity_start.get(entity, 1)
            if type(start) is not int or start < 1 or end is not None and (type(end) is not int or end < start):
                errors.append("%s/%s 分段章号无效" % (name, entity))
                continue
            # Generic 'other' records may describe independent events; only a named
            # track defines one state stream. Gaps can mean unknown, not continuity.
            if item.get("field") != "other" or item.get("track"):
                intervals[(entity, item.get("field"), item.get("track", ""))].append((start, end))
        for key, ranges in intervals.items():
            ranges.sort(key=lambda x: x[0])
            for previous, following in zip(ranges, ranges[1:]):
                if previous[1] is None or previous[1] >= following[0]:
                    errors.append("%s/%s 分段重叠或旧范围未收口" % (name, key))
    interactions = tables["interactions"].get("items") or []
    from audit_append import check_interactions
    inter_errors, _ = check_interactions(interactions, pools["characters"])
    errors.extend(inter_errors)
    for item in tables["custom_attributes"].get("items") or []:
        group = {"character": "characters", "object": "objects", "place": "places", "race": "races"}.get(item.get("target_kind"))
        if not group or item.get("target") not in pools[group]:
            errors.append("动态属性 target 悬空: %s" % item.get("target"))
    scopes = {t.get("meta", {}).get("scope") for t in tables.values() if t.get("meta", {}).get("scope")}
    if len(scopes) > 1:
        errors.append("各表 scope 不一致")
    return errors

def site_errors(site):
    errors = []
    if not site.is_dir():
        return ["站点目录不存在: %s" % site]
    if not (site / "index.html").is_file():
        errors.append("站点首页不存在")
    if not (site / "assets" / "wiki.css").is_file():
        errors.append("站点样式不存在")
    parsed = {}
    for path in site.glob("*.html"):
        page = PageLinks()
        page.feed(path.read_text(encoding="utf-8"))
        parsed[path.name] = page
        if len(page.ids) != len(set(page.ids)):
            errors.append("%s 重复 HTML id" % path.name)
        for resource in page.resources:
            if resource.startswith(("http:", "https:", "//")):
                errors.append("%s 引用了外部资源" % path.name)
            elif resource and not (site / unquote(urlsplit(resource).path)).is_file():
                errors.append("%s 资源不存在: %s" % (path.name, resource))
    for filename, page in parsed.items():
        for href in page.links:
            parts = urlsplit(href)
            if parts.scheme or parts.netloc:
                continue  # External citations are links, not automatic resource requests.
            target = unquote(parts.path) or filename
            if not target.endswith(".html"):
                continue
            if target not in parsed:
                errors.append("%s 链接文件不存在: %s" % (filename, href))
            elif parts.fragment and unquote(parts.fragment) not in parsed[target].ids:
                errors.append("%s 锚点不存在: %s" % (filename, href))
    return errors

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="校验必要文件、表结构、时间分段与站点锚点")
    ap.add_argument("dir")
    ap.add_argument("--book", default="红楼梦")
    ap.add_argument("--site-dir", help="生成站点目录；默认 <dir>/<book>_wiki")
    ap.add_argument("--tables-only", action="store_true", help="在条目生成前只校验结构化表")
    args = ap.parse_args()
    root, errors, tables = Path(args.dir), [], {}
    for name in TABLES:
        p = root / ("%s.%s.json" % (args.book, name))
        try:
            tables[name] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            errors.append("必要文件 %s 缺失或无效: %s" % (p.name, exc))
    if len(tables) == len(TABLES):
        errors.extend(table_errors(tables))
    if not args.tables_only:
        p = root / (args.book + ".entries.json")
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            from quote_source_check import check
            quote_errors, _ = check(doc)
            errors.extend(quote_errors)
            ids = [e.get("id") for e in doc.get("entries") or []]
            names = [e.get("name") for e in doc.get("entries") or []]
            if len(set(ids)) != len(ids) or len(set(names)) != len(names):
                errors.append("条目有重复 id 或名称")
            pool = set(ids)
            for e in doc.get("entries") or []:
                if any(i not in pool for i in e.get("links") or []):
                    errors.append("%s 相关条目悬空" % e.get("id"))
            from render_wiki import filenames
            site = Path(args.site_dir) if args.site_dir else root / (args.book + "_wiki")
            for name in filenames(doc.get("entries") or []).values():
                if not (site / name).is_file():
                    errors.append("条目页面缺失: %s" % name)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append("条目文件缺失或无效: %s" % exc)
        site = Path(args.site_dir) if args.site_dir else root / (args.book + "_wiki")
        errors.extend(site_errors(site))
    for error in errors:
        print("  [FAIL] " + error)
    print("失败项 %d" % len(errors))
    return 1 if errors else 0
if __name__ == "__main__":
    sys.exit(main())
