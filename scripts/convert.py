#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
convert.py — 电子书 → 按章切分的 JSON（纯标准库，无第三方依赖）

支持格式
    .epub             zip + OPF spine，章节由正文里的「第N章」标记切分
    .txt              自动探测编码（utf-8 / gbk / big5），支持尾注抽取
    .mobi .azw .azw3  PalmDB 容器 + PalmDOC 解压，章节由 HTML 结构或「第N章」标记切分

用法
    python convert.py <输入文件> [更多文件...] [-o 输出目录] [--title 书名] [--author 作者]
    python convert.py --selftest          # 自检（含 PalmDOC 压缩/解压往返验证）

输出
    每本书一个 <书名>.chapters.json，结构见 README 或 SKILL.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import sys
import zipfile
from datetime import datetime, timezone
from html import unescape
from lxml import etree, html as lxml_html
from charset_normalizer import from_bytes
import posixpath
from urllib.parse import unquote

# Windows 控制台默认代码页不是 UTF-8，中文会乱码；强制 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------- 通用工具

# 章节标题行：第N章 / 第N回 / 第N节，允许「第十二章」这类中文数字
CN_NUM = "0-9０-９零一二三四五六七八九十百千两"
RE_CHAPTER_LINE = re.compile(
    r"^[ \t]*第\s*([" + CN_NUM + r"]{1,8})\s*([章回节卷篇])\s*(.*?)[ \t]*$", re.M
)
RE_VOLUME_LINE = re.compile(
    r"^[ \t]*第\s*([" + CN_NUM + r"]{1,8})\s*([卷部篇])\s*(.*?)[ \t]*$", re.M
)
# 站点水印只认两种形态：书名+网址状片段、书名+章号。
# 不要用「任意汉字+逗号+拉丁字母」的宽模式，会误删「英灵殿，Valhalla」这类正常行文。
RE_WATERMARK_URL = re.compile(
    r"[\u4e00-\u9fff]{2,10}\s*[，,]\s*[a-z0-9][a-z0-9._/-]{5,}"
)
# 书名与章号之间不能有空白，否则会误删「署名 + 章标题」这种组合
RE_WATERMARK_CHAP_GENERIC = re.compile(r"[\u4e00-\u9fff]{2,10}第\s*\d{1,4}\s*章[^\s]{0,20}")
RE_WATERMARK_BARE = re.compile(r"(?<![\w/])(?:www\.)?[a-z0-9-]{3,}\.(?:com|net|org|cn|cc|top|xyz)\b[^\s]{0,40}")
RE_SITE_DECL = re.compile(
    r"^[ \t]*-*\s*声明：本书为.{0,400}$|"
    r"^[ \t]*-*\s*(?:本书由|本电子书由).{0,200}(?:整理|制作|上传).{0,200}$",
    re.M,
)
# 注释区标记：□注释 / 【注释】 / 注释
RE_NOTES_HEAD = re.compile(r"^[ \t]*[□■◆【\[]?\s*注\s*释\s*[】\]]?\s*$", re.M)
RE_NOTE_ITEM = re.compile(r"^[ \t]*([⑴-⑽⒈-⒑]|\(\d{1,2}\)|\[\d{1,2}\]|\d{1,2}[、.])\s*", re.M)
RE_PAGEBREAK = re.compile(r'<mbp:pagebreak[^>]*>', re.I)
RE_HTML_TAG = re.compile(r"<[^>]+>")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cn_to_int(s: str) -> int | None:
    """中文数字 / 阿拉伯数字 → int。失败返回 None。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    s = s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if s.isdigit():
        return int(s)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    total, section, number = 0, 0, 0
    for ch in s:
        if ch in digits:
            number = digits[ch]
        elif ch in units:
            unit = units[ch]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
        else:
            return None
    return total + section + number


def normalize_text(t: str) -> str:
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[\u3000\u00a0]+", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def html_to_text(html: str) -> str:
    """Parse HTML structurally and preserve paragraph/line boundaries."""
    if not html.strip():
        return ""
    root = lxml_html.fromstring(html, parser=lxml_html.HTMLParser(no_network=True))
    pieces = []
    blocks = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "pre", "section", "article"}
    def visit(node):
        if not isinstance(node.tag, str):
            return
        tag = node.tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()
        if tag in {"head", "script", "style"}:
            return
        if tag in blocks:
            pieces.append("\n\n")
        if tag in {"br", "pagebreak", "mbp:pagebreak"}:
            pieces.append("\n")
        if node.text:
            pieces.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                pieces.append(child.tail)
        if tag in blocks:
            pieces.append("\n\n")
        elif tag in {"td", "th"}:
            pieces.append(" ")
    visit(root)
    return normalize_text("".join(pieces))


def sniff_encoding(raw: bytes) -> tuple[str, str]:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", "utf-8 (bom)"
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16", "utf-16 (bom)"
    try:
        raw.decode("utf-8")
        return "utf-8", "utf-8"
    except UnicodeDecodeError:
        pass
    match = from_bytes(raw, cp_isolation=["gb18030", "big5"]).best()
    if match is None:
        raise ValueError("无法可靠识别编码；请用 --encoding 指定，不以替换字符继续")
    return match.encoding, "%s (charset-normalizer; heuristic)" % match.encoding


# ---------------------------------------------------------------- 章节切分

def split_chapters(text: str, fallback_title: str) -> list[dict]:
    """按「第N章 / 第N回」标记切分；无标记则整篇一章。

    会剔除没有正文的标记：电子书的目录页把回目列成纯文本行，那些行不是章节。
    """
    hits = list(RE_CHAPTER_LINE.finditer(text))
    # 章标题行不能以句末标点结尾。正文里会出现「此开卷第一回也。作者自云…」这种句子，
    # 它同样能匹配上面的正则，会把每个回目切成两半。
    hits = [h for h in hits if not h.group(3).rstrip().endswith(("。", "！", "？", ".", "!", "?"))]
    if not hits:
        return [{"index": 1, "title": fallback_title, "content": text, "chars": len(text)}]

    chapters = []
    for i, h in enumerate(hits):
        start = h.end()
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        body = text[start:end].strip()
        if not body:
            continue                      # 目录页的回目行：标题后面没有正文
        num = cn_to_int(h.group(1))
        # 标题只取章标记之后的文字；没有就退回「第N回」形式，不要把标记本身当标题
        title = h.group(3).strip() or f"第{h.group(1)}{h.group(2)}"
        chapters.append({
            "index": num if num is not None else i + 1,
            "title": title,
            "heading": h.group(0).strip(),
            "content": body,
            "chars": len(body),
        })
    if not chapters:
        return [{"index": 1, "title": fallback_title, "content": text, "chars": len(text)}]
    return chapters


def extract_notes(text: str) -> tuple[str, list[dict]]:
    """抽出尾部注释区，返回 (正文, 注释列表)。"""
    m = RE_NOTES_HEAD.search(text)
    if not m:
        return text, []
    body = text[:m.start()].strip()
    note_zone = text[m.end():].strip()
    notes = []
    items = list(RE_NOTE_ITEM.finditer(note_zone))
    for i, h in enumerate(items):
        start = h.end()
        end = items[i + 1].start() if i + 1 < len(items) else len(note_zone)
        notes.append({
            "marker": h.group(1),
            "text": re.sub(r"\s+", " ", note_zone[start:end].strip()),
        })
    return body, notes


def clean_watermarks(text: str, book_title: str = "") -> tuple[str, list[dict]]:
    """清除站点水印并记录。

    只清「网址状片段」和「书名+章号」两种形态，避免误删正常行文。
    书名已知时，模式限定在该书名之后，命中更准。
    """
    removed = []

    def _rec(m):
        removed.append({"text": m.group(0).strip(), "offset": m.start()})
        return ""

    patterns = []
    if book_title:
        esc = re.escape(book_title)
        # 书名 + 逗号 + 网址状片段；书名 + 章号（紧贴）
        patterns.append(re.compile(esc + r"\s*[，,]\s*[a-z0-9][a-z0-9._/-]{5,}"))
        patterns.append(re.compile(esc + r"第\s*\d{1,4}\s*章[^\s]{0,20}"))
    else:
        patterns.append(RE_WATERMARK_CHAP_GENERIC)
        patterns.append(RE_WATERMARK_URL)
        patterns.append(RE_WATERMARK_BARE)

    for pat in patterns:
        text = pat.sub(_rec, text)
    text = re.sub(r"[ \t]+", " ", text)
    return normalize_text(text), removed


def strip_site_declarations(text: str) -> tuple[str, str, str]:
    """剥离头/尾站点声明，返回 (正文, 头部, 尾部)。"""
    head = ""
    m = RE_SITE_DECL.search(text)
    if m and m.start() < len(text) * 0.15:
        head = text[:m.end()].strip()
        text = text[m.end():]
    tail = ""
    m = RE_SITE_DECL.search(text)
    if m:
        tail = text[m.start():].strip()
        text = text[:m.start()]
    return text.strip(), head, tail


# ---------------------------------------------------------------- EPUB

def read_epub(path: str) -> tuple[dict, str, list[str]]:
    """Keep the existing EPUB backend, replace regex XML parsing with lxml."""
    warn = []
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    with zipfile.ZipFile(path) as z:
        try:
            container = etree.fromstring(z.read("META-INF/container.xml"), parser)
            roots = container.xpath("//*[local-name()='rootfile']/@full-path")
            if not roots:
                raise ValueError("EPUB 缺少 OPF 路径")
            opf_name = unquote(roots[0])
            opf = etree.fromstring(z.read(opf_name), parser)
        except (KeyError, etree.XMLSyntaxError) as exc:
            raise ValueError("EPUB 结构损坏: %s" % exc) from exc
        def metadata(tag):
            values = opf.xpath("//*[local-name()='metadata']/*[local-name()='%s']/text()" % tag)
            return values[0].strip() if values else ""
        meta = {"title": metadata("title"), "creator": metadata("creator"), "language": metadata("language")}
        manifest = {item.get("id"): item for item in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']")}
        spine = []
        for ref in opf.xpath("//*[local-name()='spine']/*[local-name()='itemref']"):
            item = manifest.get(ref.get("idref"))
            if item is None:
                raise ValueError("EPUB spine 引用缺失: %s" % ref.get("idref"))
            if item.get("media-type") not in {"application/xhtml+xml", "text/html"}:
                continue
            name = posixpath.normpath(posixpath.join(posixpath.dirname(opf_name), unquote(item.get("href", "").split("#")[0])))
            if name.startswith("../") or name.startswith("/"):
                raise ValueError("EPUB 资源路径越界")
            spine.append(name)
        if not spine:
            raise ValueError("EPUB 缺少可读取 spine；不按文件名猜测正文顺序")
        parts = []
        for name in spine:
            try:
                raw = z.read(name)
            except KeyError as exc:
                raise ValueError("EPUB 正文文件缺失: %s" % name) from exc
            try:
                tree = etree.fromstring(raw, parser)
                html = etree.tostring(tree, encoding="unicode", method="html")
            except etree.XMLSyntaxError:
                tree = lxml_html.fromstring(raw, parser=lxml_html.HTMLParser(encoding="utf-8", no_network=True))
                html = lxml_html.tostring(tree, encoding="unicode")
                warn.append("XHTML 使用 HTML 容错解析: %s" % name)
            parts.append(html_to_text(html))
        return meta, normalize_text("\n\n".join(parts)), warn


# ---------------------------------------------------------------- MOBI / AZW3

def palmdoc_decompress(data: bytes) -> bytes:
    """PalmDOC (compression type 2) 解压。

    分支（按 PalmDOC 规范）：
        0x01-0x08  后面紧跟的 N 个字节原样拷贝
        0x09-0x7F  自表示字符，直接输出
        0x80-0xBF  空格 + (该字节 ^ 0x80)
        0xC0-0xFF  距离-长度对，取下一字节组成 16 位：
                   distance = (pair >> 3) & 0x07FF
                   length   = (pair & 0x07) + 3
    """
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        c = data[i]
        i += 1
        if 1 <= c <= 8:                                  # 字面量
            out += data[i:i + c]
            i += c
        elif c < 0x80:                                   # 自表示字符（含 0x09 tab）
            out.append(c)
        elif c < 0xC0:                                   # 空格 + 字符
            out.append(0x20)
            out.append(c ^ 0x80)
        else:                                            # 距离-长度对
            if i >= n:
                break
            pair = (c << 8) | data[i]
            i += 1
            distance = (pair >> 3) & 0x07FF
            length = (pair & 0x07) + 3
            if distance == 0 or distance > len(out):
                continue
            for _ in range(length):
                out.append(out[-distance])
    return bytes(out)


def read_palmdb_records(raw: bytes) -> tuple[str, list[bytes]]:
    """解析 PalmDB，返回 (书名, 记录字节列表)。"""
    if len(raw) < 78:
        raise ValueError("文件太小，不是 PalmDB")
    name = raw[0:32].split(b"\x00")[0]
    try:
        title = name.decode("utf-8")
    except UnicodeDecodeError:
        title = name.decode("latin-1", errors="replace")
    n_records = struct.unpack(">H", raw[76:78])[0]
    offsets = []
    for i in range(n_records):
        off = 78 + i * 8
        if off + 8 > len(raw):
            break
        offsets.append(struct.unpack(">I", raw[off:off + 4])[0])
    offsets.append(len(raw))
    records = [raw[offsets[i]:offsets[i + 1]] for i in range(len(offsets) - 1)]
    return title, records


def read_mobi(path: str) -> tuple[dict, str, list[str]]:
    """MOBI / AZW / AZW3 → (元数据, 纯文本, 警告)。

    记录 0 布局：PalmDOC 头(16 字节) + MOBI 头。
    **MOBI 头内部的字段偏移以 MOBI 标识为基准**，不是以记录起点。
    下表 off 即代码里 u16/u32 传入的偏移（magic + off），括号内为记录起点偏移：
        MOBI 标识   off 0    （记录 16）
        header_len  off 4    （记录 20）
        mobi_type   off 8    （记录 24）
        text_enc    off 12   （记录 28）
        uid         off 16   （记录 32）
        file_ver    off 20   （记录 36）
        first_nonbook off 64 （记录 80）
        full_name_off off 68 （记录 84）
        full_name_len off 72 （记录 88）
        locale      off 76   （记录 92）
        first_image off 108  （记录 124）
        exth_flags  off 112  （记录 128）
        drm_offset  off 168  （记录 184）
    PalmDOC 头（记录起点）：compression@0、text_length@4、record_count@8、record_size@10
    EXTH 块位置：magic + 16 + header_len
    """
    warn: list[str] = []
    meta_title = meta_creator = meta_publisher = ""
    raw = open(path, "rb").read()
    book_title, records = read_palmdb_records(raw)

    if len(records) < 2:
        raise ValueError("PalmDB 记录不足，无法定位 MOBI 头")

    def parse_header(rec: bytes):
        magic = rec.find(b"MOBI")
        if magic < 0:
            return None
        def u16(off): return struct.unpack(">H", rec[magic + off:magic + off + 2])[0]
        def u32(off): return struct.unpack(">I", rec[magic + off:magic + off + 4])[0]
        try:
            return {
                "magic": magic,
                "header_len": u32(4),
                "mobi_type": u32(8),
                "encoding": u32(12),
                "uid": u32(16),
                "file_version": u32(20),
                "full_name_off": u32(68),
                "full_name_len": u32(72),
                "exth_flags": u32(112),
                "drm_offset": u32(168),
                # PalmDOC 头在记录开头
                "compression": struct.unpack(">H", rec[0:2])[0],
                "text_length": struct.unpack(">I", rec[4:8])[0],
                "record_count": struct.unpack(">H", rec[8:10])[0],
                "record_size": struct.unpack(">H", rec[10:12])[0],
            }
        except struct.error:
            return None

    info = parse_header(records[0])
    header_index = 0
    if info is None:
        # KF8：记录 0 不是 MOBI 头，向后找
        for idx in range(1, min(len(records), 8)):
            cand = parse_header(records[idx])
            if cand:
                info = cand
                header_index = idx
                warn.append(f"记录 0 不是 MOBI 头，改用记录 {idx}（KF8 结构）")
                break
    if info is None:
        raise ValueError("未找到 MOBI 头（可能不是 MOBI/AZW3，或为加密文件）")

    if info["drm_offset"] != 0 and info["drm_offset"] != 0xFFFFFFFF:
        warn.append(f"文件带 DRM（drm_offset={info['drm_offset']}），正文可能无法完整解出")

    # EXTH：紧跟 MOBI 头。header_len 是否含那 16 字节的 PalmDOC 头在各实现里有出入，
    # 所以不硬编码偏移，直接在记录里定位 EXTH 标识。
    rec0 = records[header_index]
    exth_start = -1
    if info["exth_flags"] & 0x40:
        guess = info["magic"] + info["header_len"]
        for cand in (guess, info["magic"] + 16 + info["header_len"]):
            if rec0[cand:cand + 4] == b"EXTH":
                exth_start = cand
                break
        if exth_start < 0:
            found = rec0.find(b"EXTH", info["magic"] + 16)
            if 0 <= found <= info["magic"] + info["header_len"] + 32:
                exth_start = found
                warn.append(f"EXTH 不在常规偏移，按标识定位到 {found}")

    if exth_start >= 0:
        exth_len = struct.unpack(">I", rec0[exth_start + 4:exth_start + 8])[0]
        count = struct.unpack(">I", rec0[exth_start + 8:exth_start + 12])[0]
        q = exth_start + 12
        end = min(exth_start + exth_len, len(rec0))
        kf8_boundary = None
        for _ in range(count):
            if q + 8 > end:
                break
            rtype, rlen = struct.unpack(">II", rec0[q:q + 8])
            if rlen < 8:
                break
            val = rec0[q + 8:q + rlen]
            if rtype == 100:
                meta_creator = meta_creator or val.decode("utf-8", errors="replace").strip()
            elif rtype == 503:
                meta_title = meta_title or val.decode("utf-8", errors="replace").strip()
            elif rtype == 101:
                meta_publisher = meta_publisher or val.decode("utf-8", errors="replace").strip()
            elif rtype == 121:
                kf8_boundary = struct.unpack(">I", val[:4])[0]
            q += rlen
        if kf8_boundary:
            warn.append(f"含 KF8 边界（记录 {kf8_boundary}）")
    elif info["exth_flags"] & 0x40:
        warn.append("EXTH 标志位已置位但未找到 EXTH 块，元数据可能缺失")

    # 正文记录
    first_content = header_index + 1
    n_text = info["record_count"] or (len(records) - first_content)
    text_records = records[first_content:first_content + n_text]
    if not text_records:
        raise ValueError("MOBI 头声明的正文记录不存在")

    enc = {65001: "utf-8", 1252: "cp1252"}.get(info["encoding"])
    if enc is None:
        enc = "utf-8"
        warn.append(f"未知文字编码 {info['encoding']}，按 utf-8 处理")

    comp = info["compression"]
    if comp == 1:
        payload = b"".join(text_records)
    elif comp == 2:
        payload = b"".join(palmdoc_decompress(r) for r in text_records)
    elif comp == 17480:
        raise ValueError("该文件使用 HUFF/CDIC 压缩（compression 17480），本脚本未实现；"
                         "请先用 Calibre 转换：ebook-convert in.azw3 out.epub")
    else:
        raise ValueError(f"未知压缩方式 compression={comp}")

    if info["text_length"]:
        payload = payload[:info["text_length"]]
    html = payload.decode(enc, errors="replace")

    # 书名：优先 EXTH 503，其次 MOBI full name，最后 PalmDB 名
    if not meta_title and info["full_name_len"] and info["full_name_off"]:
        off = info["full_name_off"]
        meta_title = rec0[off:off + info["full_name_len"]].decode("utf-8", errors="replace").strip()

    return {
        "title": meta_title or book_title,
        "creator": meta_creator,
        "publisher": meta_publisher,
        "language": "",
    }, html, warn


# ---------------------------------------------------------------- DOCX / DOC

def read_docx(path: str) -> tuple[dict, str, list[str]]:
    """DOCX → (元数据, 文本, 警告)。按段落还原，尾注/脚注一并取出。"""
    ET = etree
    xml_parser = etree.XMLParser(resolve_entities=False, no_network=True)

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    warn: list[str] = []
    meta = {"title": "", "creator": "", "language": "zh"}

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())

        def read(name: str) -> bytes:
            try:
                return z.read(name)
            except KeyError:
                return ""

        # 核心属性里的标题/作者
        core = read("docProps/core.xml")
        if core:
            props = ET.fromstring(core, xml_parser)
            for field in ("title", "creator"):
                values = props.xpath("//*[local-name()='%s']/text()" % field)
                if values:
                    meta[field] = values[0].strip()

        def paragraphs(xml: str) -> list[str]:
            if not xml:
                return []
            try:
                root = ET.fromstring(xml, xml_parser)
            except ET.XMLSyntaxError as exc:
                warn.append(f"XML 解析失败：{exc}")
                return []
            out = []
            for p in root.iter(f"{W}p"):
                buf = []
                for node in p.iter():
                    if node.tag == f"{W}t" and node.text:
                        buf.append(node.text)
                    elif node.tag == f"{W}tab":
                        buf.append("\t")
                    elif node.tag in (f"{W}br", f"{W}cr"):
                        buf.append("\n")
                line = "".join(buf).strip()
                if line:
                    out.append(line)
            return out

        parts = paragraphs(read("word/document.xml"))
        if not parts:
            raise ValueError("DOCX 里没有正文段落（word/document.xml 为空或结构异常）")

        # 脚注 / 尾注：并入文末，供后续按注释区解析
        for extra, label in (("word/footnotes.xml", "脚注"), ("word/endnotes.xml", "尾注")):
            if extra in names:
                notes = paragraphs(read(extra))
                if notes:
                    parts.append("")
                    parts.append(f"□{label}")
                    parts.extend(notes)

    return meta, normalize_text("\n\n".join(parts)), warn


def convert_with_calibre(path: str, target_ext: str = ".epub") -> str:
    """用已安装的 Calibre ebook-convert 转换格式，返回产物路径。

    仅用于本模块不便直接解析的格式（如旧版二进制 .doc、HUFF/CDIC 压缩的 azw3）。
    """
    import shutil
    import subprocess
    import tempfile

    exe = shutil.which("ebook-convert")
    if not exe:
        for cand in (r"C:\Program Files\Calibre2\ebook-convert.exe",
                     r"C:\Program Files (x86)\Calibre2\ebook-convert.exe"):
            if os.path.isfile(cand):
                exe = cand
                break
    if not exe:
        raise ValueError("需要 Calibre 的 ebook-convert 来转换该格式，但未找到它；"
                         "请安装 Calibre，或先手工转成 epub/txt")

    out = os.path.join(tempfile.mkdtemp(prefix="n2w_"),
                       os.path.splitext(os.path.basename(path))[0] + target_ext)
    proc = subprocess.run([exe, path, out], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not os.path.isfile(out):
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise ValueError("Calibre 转换失败：" + " / ".join(tail))
    return out


# ---------------------------------------------------------------- 主流程

def build_result(path: str, title_override: str, author_override: str, encoding_override: str = "") -> dict:
    ext = os.path.splitext(path)[1].lower()
    warn: list[str] = []
    meta: dict = {}
    html = None

    if ext == ".epub":
        meta, text, warn = read_epub(path)
        book_title = title_override or meta.get("title", "")
        text, head, tail = strip_site_declarations(text)
        text, watermarks = clean_watermarks(text, book_title)
    elif ext in (".mobi", ".azw", ".azw3", ".prc"):
        try:
            meta, html, warn = read_mobi(path)
        except ValueError as exc:
            # HUFF/CDIC 等本模块不实现的压缩：退回 Calibre
            if "Calibre" not in str(exc):
                raise
            warn.append(f"本模块无法解析（{exc}），改用 Calibre 转换")
            epub_path = convert_with_calibre(path, ".epub")
            meta, text, more = read_epub(epub_path)
            warn += more
            book_title = title_override or meta.get("title", "")
            text, head, tail = strip_site_declarations(text)
            text, watermarks = clean_watermarks(text, book_title)
            html = None
        if html is not None:
            book_title = title_override or meta.get("title", "")
            # HTML 里先用 pagebreak 切出原始小节，再转纯文本
            html = RE_PAGEBREAK.sub("\n\n@@PAGEBREAK@@\n\n", html)
            text = html_to_text(html)
            text, head, tail = strip_site_declarations(text)
            text, watermarks = clean_watermarks(text, book_title)
            text = text.replace("@@PAGEBREAK@@", "\n\n")
    elif ext == ".pdf":
        import pdftext
        text, warn = pdftext.extract_pdf_text(path)
        meta = {"title": "", "creator": "", "language": "zh"}
        book_title = title_override or meta.get("title", "")
        text, head, tail = strip_site_declarations(text)
        text, watermarks = clean_watermarks(text, book_title)
    elif ext == ".docx":
        meta, text, warn = read_docx(path)
        book_title = title_override or meta.get("title", "")
        text, head, tail = strip_site_declarations(text)
        text, watermarks = clean_watermarks(text, book_title)
    elif ext == ".doc":
        warn.append("旧版二进制 .doc 由 Calibre 转换后处理")
        epub_path = convert_with_calibre(path, ".epub")
        meta, text, more = read_epub(epub_path)
        warn += more
        book_title = title_override or meta.get("title", "")
        text, head, tail = strip_site_declarations(text)
        text, watermarks = clean_watermarks(text, book_title)
    elif ext in (".txt", ".text"):
        with open(path, "rb") as source:
            raw = source.read()
        enc, enc_note = (encoding_override, encoding_override + " (explicit)") if encoding_override else sniff_encoding(raw)
        text = normalize_text(raw.decode(enc, errors="strict"))
        meta = {"title": "", "creator": "", "language": "zh", "source_encoding": enc_note}
        # 标题在同一行重复两次
        lines = text.split("\n")
        if lines:
            m = re.match(r"^(.{1,30}?)\1$", lines[0].strip())
            if m:
                meta["title"] = m.group(1)
                lines[0] = ""
                text = normalize_text("\n".join(lines))
                warn.append(f"剥离重复标题行：{m.group(0)}")
        book_title = title_override or meta.get("title", "")
        text, head, tail = strip_site_declarations(text)
        text, watermarks = clean_watermarks(text, book_title)
    else:
        raise ValueError(f"不支持的格式：{ext}"
                         "（支持 epub / txt / mobi / azw / azw3 / pdf / docx / doc）")

    title = title_override or meta.get("title") or os.path.splitext(os.path.basename(path))[0]
    creator = author_override or meta.get("creator", "")

    body, notes = extract_notes(text)
    chapters = split_chapters(body, title)

    nums = [c["index"] for c in chapters]
    missing = []
    if nums:
        missing = [i for i in range(1, max(nums) + 1) if i not in nums]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    total = sum(c["chars"] for c in chapters)

    return {
        "meta": {
            "title": title,
            "creator": creator,
            "language": meta.get("language", "zh"),
            "source": os.path.basename(path),
            "source_format": ext.lstrip("."),
            "source_encoding": meta.get("source_encoding", "utf-8"),
            "generated_at": now_iso(),
            "chapter_count": len(chapters),
            "total_chars": total,
        },
        "integrity": {
            "missing_chapters": missing,
            "duplicate_chapters": dup,
            "stripped_header": head,
            "stripped_tail": tail,
            "watermarks_removed": watermarks,
            "note_count": len(notes),
            "warnings": warn,
        },
        "notes": notes,
        "chapters": chapters,
    }


def _selftest_mobi() -> tuple[bool, str]:
    """构造一个未压缩的真实结构 MOBI 文件，验证 PalmDB/MOBI/EXTH 解析链。"""
    import tempfile

    body_html = (
        "<html><body>"
        "<p>第1章 序言</p><p>一个男人要走过多少路。</p>"
        "<p>第2章 全民公敌 1</p><p>这是地狱中的魔王们相互撕咬。</p>"
        "</body></html>"
    )
    payload = body_html.encode("utf-8")

    def exth(rtype: int, value: bytes) -> bytes:
        return struct.pack(">II", rtype, 8 + len(value)) + value

    recs = [exth(100, "测试作者".encode("utf-8")),
            exth(503, "测试书名".encode("utf-8"))]
    exth_body = struct.pack(">II", 12 + sum(len(r) for r in recs), len(recs)) + b"".join(recs)
    exth_block = b"EXTH" + exth_body

    # 记录 0：PalmDOC 头(0-15) + MOBI 头(字段偏移以 MOBI 标识为基准) + EXTH
    magic = 16
    header_len = 232
    rec0 = bytearray(magic + header_len)
    struct.pack_into(">H", rec0, 0, 1)                    # PalmDOC: compression = 1（未压缩）
    struct.pack_into(">I", rec0, 4, len(payload))         # PalmDOC: text length
    struct.pack_into(">H", rec0, 8, 1)                    # PalmDOC: text record count
    struct.pack_into(">H", rec0, 10, 4096)                # PalmDOC: record size
    rec0[magic:magic + 4] = b"MOBI"                       # MOBI 标识
    struct.pack_into(">I", rec0, magic + 4, header_len)   # MOBI 头长度
    struct.pack_into(">I", rec0, magic + 8, 2)            # mobi type
    struct.pack_into(">I", rec0, magic + 12, 65001)       # encoding = utf-8
    struct.pack_into(">I", rec0, magic + 16, 0xDEADBEEF)  # uid
    struct.pack_into(">I", rec0, magic + 20, 6)           # file version
    # full name 放在 EXTH 之后，避免与 EXTH 块重叠
    full_name = "PalmDB 书名".encode("utf-8")
    full_name_off = magic + header_len + len(exth_block)
    struct.pack_into(">I", rec0, magic + 68, full_name_off)   # full name 偏移
    struct.pack_into(">I", rec0, magic + 72, len(full_name))  # full name 长度
    struct.pack_into(">I", rec0, magic + 112, 0x40)           # EXTH present
    record0 = bytes(rec0) + exth_block + full_name

    # PalmDB 头：78 字节 + 2 条记录信息
    pdb = bytearray(78)
    pdb[0:32] = b"TestTitle".ljust(32, b"\x00")
    struct.pack_into(">H", pdb, 32, 0)                    # attributes
    struct.pack_into(">H", pdb, 34, 0)                    # version
    struct.pack_into(">I", pdb, 36, 0)                    # ctime
    struct.pack_into(">I", pdb, 40, 0)                    # mtime
    struct.pack_into(">I", pdb, 44, 0)                    # btime
    struct.pack_into(">I", pdb, 48, 0)                    # modnum
    struct.pack_into(">I", pdb, 52, 0)                    # appinfo
    struct.pack_into(">I", pdb, 56, 0)                    # sortinfo
    pdb[60:64] = b"BOOK"                                  # type
    pdb[64:68] = b"MOBI"                                  # creator
    struct.pack_into(">I", pdb, 68, 0x12345678)           # uid seed
    struct.pack_into(">I", pdb, 72, 0)                    # next record list
    struct.pack_into(">H", pdb, 76, 2)                    # record count

    data_start = 78 + 2 * 8
    off0 = data_start
    off1 = off0 + len(record0)
    pdb += struct.pack(">IBBH", off0, 0, 0, 0)
    pdb += struct.pack(">IBBH", off1, 0, 0, 0)

    tmp = os.path.join(tempfile.gettempdir(), "_n2w_selftest.mobi")
    with open(tmp, "wb") as fh:
        fh.write(bytes(pdb) + record0 + payload)
    try:
        meta, html, _warn = read_mobi(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    if meta["title"] != "测试书名":
        return False, f"书名解析为 {meta['title']!r}"
    if meta["creator"] != "测试作者":
        return False, f"作者解析为 {meta['creator']!r}"
    if "这是地狱中的魔王们相互撕咬" not in html:
        return False, "正文未正确取出"
    return True, f"书名/作者/正文均正确，正文 {len(html)} 字节"


def _selftest_pdf() -> tuple[bool, str]:
    """构造一个最小 PDF（传统 xref 表 + FlateDecode + 单字节 WinAnsi 文本）。"""
    import tempfile
    import zlib as _zlib

    content = b"BT /F1 12 Tf 1 0 0 1 72 700 Tm (Hello PDF) Tj ET\n" \
              b"BT /F1 12 Tf 1 0 0 1 72 686 Tm (Second line) Tj ET\n"
    comp = _zlib.compress(content)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(comp)).encode() + b" /Filter /FlateDecode >>\nstream\n"
        + comp + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n"
            f"{xref_pos}\n%%EOF\n").encode()

    tmp = os.path.join(tempfile.gettempdir(), "_n2w_selftest.pdf")
    with open(tmp, "wb") as fh:
        fh.write(bytes(out))
    try:
        import pdftext
        text, _warn = pdftext.extract_pdf_text(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if "Hello PDF" not in text:
        return False, f"未抽出正文：{text[:60]!r}"
    if "Second line" not in text:
        return False, "第二行丢失"
    return True, f"两行都抽出，共 {len(text)} 字"


def _selftest_docx() -> tuple[bool, str]:
    """构造一个最小 DOCX，验证段落还原与脚注并入。"""
    import tempfile

    doc = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>第1章 测试章</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>第一段正文内容。</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>第二段正文内容。</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    foot = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:p><w:r><w:t>⑴这是脚注内容</w:t></w:r></w:p>"
        "</w:footnotes>"
    )
    core = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>测试文档</dc:title><dc:creator>测试作者</dc:creator>"
            "</cp:coreProperties>")

    tmp = os.path.join(tempfile.gettempdir(), "_n2w_selftest.docx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("docProps/core.xml", core)
        z.writestr("word/document.xml", doc)
        z.writestr("word/footnotes.xml", foot)
    try:
        meta, text, _warn = read_docx(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if meta["title"] != "测试文档" or meta["creator"] != "测试作者":
        return False, f"元数据不对：{meta}"
    for frag in ("第一段正文内容", "第二段正文内容", "脚注"):
        if frag not in text:
            return False, f"缺少 {frag}"
    return True, f"元数据与 {len(text)} 字正文均正确"


def selftest() -> int:
    """自检：PalmDOC 往返、编码探测、章节切分、注释抽取。"""
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' — ' + extra) if extra else ''}")
        ok = ok and cond

    print("PalmDOC 压缩/解压往返：")
    # 手工构造一段含各分支的数据，逐分支验证
    comp = bytearray()
    comp.append(0x09)                      # 自表示 tab
    comp += bytes([len(b"abc")]) + b"abc"  # 字面量 3 字节
    pair = (3 << 3) | (6 - 3)              # distance=3, length=6 → 回抄 "abc" 两遍
    comp += bytes([0xC0 | (pair >> 8), pair & 0xFF])
    comp.append(0x80 | ord("!"))           # 空格 + '!'（该分支覆盖 0x80-0xBF，即字符 0x00-0x3F）
    out = palmdoc_decompress(bytes(comp))
    check("自表示字符", out.startswith(b"\t"), repr(out))
    check("字面量拷贝", b"abc" in out, repr(out))
    check("距离-长度对回抄 + 空格分支", out == b"\tabcabcabc !", repr(out))

    print("MOBI 端到端（构造文件）：")
    mobi_ok, mobi_msg = _selftest_mobi()
    check("PalmDB + EXTH 解析", mobi_ok, mobi_msg)

    print("PDF 端到端（构造文件）：")
    pdf_ok, pdf_msg = _selftest_pdf()
    check("xref + FlateDecode + 文本算子", pdf_ok, pdf_msg)

    print("DOCX 端到端（构造文件）：")
    docx_ok, docx_msg = _selftest_docx()
    check("段落还原 + 元数据 + 脚注", docx_ok, docx_msg)

    print("编码探测：")
    check("utf-8 无 BOM", sniff_encoding("中文测试".encode("utf-8"))[0] == "utf-8")
    check("utf-8 BOM", sniff_encoding("中文测试".encode("utf-8-sig"))[0] == "utf-8-sig")
    check("GBK", sniff_encoding("中文测试内容".encode("gbk"))[0] in ("gbk", "gb18030"))

    print("章节切分：")
    t = "第1章 序言\n内容一\n第2章 全民公敌 1\n内容二\n第十二章 尾声\n内容三"
    ch = split_chapters(t, "x")
    check("切出 3 章", len(ch) == 3, str([c["index"] for c in ch]))
    check("中文数字章号 = 12", ch[-1]["index"] == 12, str(ch[-1]["index"]))
    check("标题解析（不含章标记）", ch[1]["title"] == "全民公敌 1", ch[1]["title"])

    print("注释抽取：")
    body, notes = extract_notes("正文内容。\n□注释\n⑴第一条注释\n⑵第二条注释")
    check("抽出 2 条", len(notes) == 2, str(len(notes)))
    check("正文不含注释区", "第一条" not in body)

    print()
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="电子书 → 按章切分的 JSON")
    ap.add_argument("inputs", nargs="*", help="epub / txt / mobi / azw3 文件")
    ap.add_argument("-o", "--outdir", default=".", help="输出目录（默认当前目录）")
    ap.add_argument("--title", default="", help="覆盖书名")
    ap.add_argument("--author", default="", help="覆盖作者")
    ap.add_argument("--selftest", action="store_true", help="运行自检")
    ap.add_argument("--encoding", default="", help="TXT 编码覆盖，解码失败不静默替换")
    args = ap.parse_args()

    if args.selftest:
        return selftest()
    if not args.inputs:
        ap.print_help()
        return 2

    rc = 0
    for path in args.inputs:
        if not os.path.isfile(path):
            print(f"[跳过] 不存在：{path}", file=sys.stderr)
            rc = 1
            continue
        try:
            result = build_result(path, args.title, args.author, args.encoding)
        except Exception as exc:                      # noqa: BLE001
            print(f"[失败] {os.path.basename(path)}：{exc}", file=sys.stderr)
            rc = 1
            continue

        base = re.sub(r'[\\/:*?"<>|]', "_", result["meta"]["title"]) or "output"
        out = os.path.join(args.outdir, base + ".chapters.json")
        os.makedirs(args.outdir, exist_ok=True)
        from core_store import write_json
        write_json(out, result)

        m, g = result["meta"], result["integrity"]
        print(f"{m['title']} / {m['creator']}  [{m['source_format']} / {m['source_encoding']}]")
        print(f"  章节 {m['chapter_count']}   字数 {m['total_chars']}   注释 {g['note_count']} 条")
        if g["missing_chapters"]:
            print(f"  缺失章号: {','.join(map(str, g['missing_chapters']))}")
        if g["duplicate_chapters"]:
            print(f"  重复章号: {','.join(map(str, g['duplicate_chapters']))}")
        if g["watermarks_removed"]:
            print(f"  清除水印 {len(g['watermarks_removed'])} 处")
        for w in g["warnings"]:
            print(f"  警告: {w}")
        print(f"  → {out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
