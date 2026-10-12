#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯标准库 PDF 文本抽取，供 convert.py 调用。

支持：传统 xref 表与 xref 流、对象流（/ObjStm）、FlateDecode（含 predictor）、
CID 字体经 ToUnicode CMap 还原中文、Tj/TJ/'/" 文本算子。

不支持（会明确报错）：加密 PDF、纯扫描图片 PDF、LZW/JPX 等流过滤器。
排版还原是启发式，双栏与表格会串行。
"""

from __future__ import annotations

import re
import zlib

WS = b"\x00\t\n\x0c\r "
# '>' 必须是分隔符，否则 '>>' 会被当成普通 token，字典解析不出来
DELIM = b"()<>[]{}/%"
TOKEN_RE = re.compile(rb"/([^\s/<>\[\](){}%]*)|(\[|\]|<<|>>)|(\(|\))|([^\s/<>\[\](){}%]+)")


def is_ws(c: int) -> bool:
    return c in (0, 9, 10, 12, 13, 32)


def is_delim(c: int) -> bool:
    return c in (40, 41, 60, 62, 91, 93, 123, 125, 47, 37)


class Lexer:
    """PDF 对象词法分析器。"""

    def __init__(self, data: bytes, pos: int = 0):
        self.d = data
        self.i = pos
        self.n = len(data)

    def skip_ws(self):
        while self.i < self.n:
            c = self.d[self.i]
            if is_ws(c):
                self.i += 1
            elif c == 0x25:  # %
                while self.i < self.n and self.d[self.i] not in (10, 13):
                    self.i += 1
            else:
                return

    def read_token(self) -> bytes | None:
        self.skip_ws()
        if self.i >= self.n:
            return None
        c = self.d[self.i]
        if c == 0x2F:  # /
            j = self.i + 1
            while j < self.n and not is_ws(self.d[j]) and not is_delim(self.d[j]):
                j += 1
            tok = self.d[self.i:j]
            self.i = j
            return tok
        if c == 0x3C:  # < or <<
            if self.i + 1 < self.n and self.d[self.i + 1] == 0x3C:
                self.i += 2
                return b"<<"
            j = self.d.find(b">", self.i)
            j = self.n if j < 0 else j + 1
            tok = self.d[self.i:j]
            self.i = j
            return tok
        if c == 0x3E:  # >>
            self.i += 2
            return b">>"
        if c in (0x5B, 0x5D):  # [ ]
            self.i += 1
            return b"[" if c == 0x5B else b"]"
        if c == 0x28:  # (
            depth = 0
            j = self.i
            while j < self.n:
                ch = self.d[j]
                if ch == 0x5C:
                    j += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            tok = self.d[self.i:j]
            self.i = j
            return tok
        j = self.i
        while j < self.n and not is_ws(self.d[j]) and not is_delim(self.d[j]):
            j += 1
        tok = self.d[self.i:j]
        self.i = j
        return tok


def unescape_literal(s: bytes) -> bytes:
    """解 PDF 字面量字符串的转义（不含首尾括号）。"""
    out = bytearray()
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c != 0x5C:
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        e = s[i]
        simple = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
                  0x28: 40, 0x29: 41, 0x5C: 92}
        if e in simple:
            out.append(simple[e])
            i += 1
        elif 0x30 <= e <= 0x37:
            oct_digits = bytearray()
            while i < n and len(oct_digits) < 3 and 0x30 <= s[i] <= 0x37:
                oct_digits.append(s[i])
                i += 1
            out.append(int(oct_digits.decode(), 8) & 0xFF)
        elif e in (10, 13):
            i += 1
            if e == 13 and i < n and s[i] == 10:
                i += 1
        else:
            out.append(e)
            i += 1
    return bytes(out)


def parse_literal(tok: bytes) -> bytes:
    return unescape_literal(tok[1:-1])


def parse_hex(tok: bytes) -> bytes:
    h = re.sub(rb"[^0-9A-Fa-f]", b"", tok[1:-1])
    if len(h) % 2:
        h += b"0"
    return bytes.fromhex(h.decode("ascii"))


def decode_pdf_string(tok: bytes) -> bytes:
    if tok.startswith(b"("):
        return parse_literal(tok)
    if tok.startswith(b"<"):
        return parse_hex(tok)
    return b""


# ---------------------------------------------------------------- 对象模型

class Ref:
    __slots__ = ("num", "gen")

    def __init__(self, num: int, gen: int = 0):
        self.num, self.gen = num, gen

    def __repr__(self):
        return f"Ref({self.num})"


class Stream:
    __slots__ = ("dict", "raw")

    def __init__(self, d: dict, raw: bytes):
        self.dict, self.raw = d, raw


class StringVal:
    """已解码的 PDF 字符串。

    解码后的字节无法再靠首字节区分字符串与名字/数字，用标记类型承载。
    """
    __slots__ = ("data",)

    def __init__(self, data: bytes):
        self.data = data

    def __repr__(self):
        return f"StringVal({self.data!r})"


def parse_object(lex: Lexer):
    """从当前位置解析一个 PDF 对象。"""
    tok = lex.read_token()
    if tok is None:
        return None
    if tok == b"<<":
        return parse_dict(lex, consume_open=False)
    if tok == b"[":
        arr = []
        while True:
            save = lex.i
            t = lex.read_token()
            if t is None:
                break
            if t == b"]":
                break
            lex.i = save
            arr.append(parse_object(lex))
        return arr
    if tok.startswith(b"/"):
        return tok
    if tok.startswith(b"(") or (tok.startswith(b"<") and not tok.startswith(b"<<")):
        return StringVal(decode_pdf_string(tok))
    # 数字 / 引用 / 关键字
    try:
        num = int(tok)
    except ValueError:
        return tok
    save = lex.i
    t2 = lex.read_token()
    if t2 is not None:
        try:
            gen = int(t2)
            t3 = lex.read_token()
            if t3 == b"R":
                return Ref(num, gen)
        except ValueError:
            pass
    lex.i = save
    return num


def parse_dict(lex: Lexer, consume_open: bool = True) -> dict:
    """解析字典。consume_open=True 时先吃掉开头的 `<<`。"""
    if consume_open:
        save = lex.i
        if lex.read_token() != b"<<":
            lex.i = save
    d: dict = {}
    while True:
        save = lex.i
        key = lex.read_token()
        if key is None:
            break
        if key == b">>":
            break
        if not key.startswith(b"/"):
            lex.i = save
            break
        d[key[1:].decode("latin-1")] = parse_object(lex)
    return d


def resolve(obj, objects: dict, depth: int = 0):
    """把 Ref 解析成实际对象。"""
    while isinstance(obj, Ref) and depth < 32:
        obj = objects.get(obj.num)
        depth += 1
    return obj


def get_int(obj, objects: dict, default: int = 0) -> int:
    obj = resolve(obj, objects)
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return int(obj)
    return default


# ---------------------------------------------------------------- 文件解析

OBJ_RE = re.compile(rb"(?<![0-9])(\d{1,10})\s+(\d{1,5})\s+obj\b")


def extract_objects(data: bytes) -> tuple[dict, list[bytes]]:
    """扫描全部 `N G obj` 定义，返回 ({num: obj}, 内容流字节列表)。

    直接扫描而不是严格走 xref：对损坏/变体的 PDF 更宽容。
    """
    objects: dict = {}
    for m in OBJ_RE.finditer(data):
        num = int(m.group(1))
        body_start = m.end()
        end = data.find(b"endobj", body_start)
        if end < 0:
            end = len(data)
        body = data[body_start:end]
        sm = re.search(rb"\bstream\r\n|\bstream\n|\bstream\r", body)
        if sm:
            dpart = body[:sm.start()]
            lex = Lexer(dpart)
            # dpart 以 `<<` 开头，必须让 parse_dict 自己吃掉它（consume_open=True）
            d = parse_dict(lex, consume_open=True) if dpart.lstrip().startswith(b"<<") else {}
            raw = body[sm.end():]
            raw = re.sub(rb"\s*endstream\s*$", b"", raw)
            length = get_int(d.get("Length"), objects, -1)
            if 0 <= length <= len(raw):
                raw = raw[:length]
            objects[num] = Stream(d, raw)
        else:
            lex = Lexer(body)
            objects[num] = parse_object(lex)
    return objects, []


def decode_stream(st: Stream, objects: dict) -> bytes:
    """按 /Filter 解出流内容。"""
    raw = st.raw
    filters = resolve(st.dict.get("Filter"), objects)
    if filters is None:
        return raw
    if not isinstance(filters, list):
        filters = [filters]
    parms = resolve(st.dict.get("DecodeParms"), objects)
    if not isinstance(parms, list):
        parms = [parms] * len(filters)

    out = raw
    for i, f in enumerate(filters):
        if isinstance(f, bytes):
            name = f.decode("latin-1")
        else:
            name = str(f)
        name = name.lstrip("/")          # 名字 token 带前导斜杠，如 /FlateDecode
        if name in ("FlateDecode", "Fl"):
            try:
                out = zlib.decompress(out)
            except zlib.error:
                try:
                    out = zlib.decompressobj().decompress(out)
                except zlib.error:
                    # 可能带 predictor
                    out = _inflate_with_predictor(out, resolve(parms[i], objects), objects)
        elif name in ("ASCIIHexDecode", "AHx"):
            h = re.sub(rb"[^0-9A-Fa-f]", b"", out.split(b">")[0])
            if len(h) % 2:
                h += b"0"
            out = bytes.fromhex(h.decode("ascii"))
        elif name in ("ASCII85Decode", "A85"):
            import base64
            out = base64.a85decode(out, adobe=True)
        else:
            raise ValueError(f"不支持的流过滤器 {name}（本模块只做 Flate/ASCIIHex/ASCII85）")
    return out


def _inflate_with_predictor(data: bytes, parms, objects: dict) -> bytes:
    """Flate 解压 + PNG/TIFF predictor 还原。"""
    if not isinstance(parms, dict):
        raise ValueError("FlateDecode 解压失败且无 DecodeParms 可用")
    predictor = get_int(parms.get("Predictor"), objects, 1)
    colors = get_int(parms.get("Colors"), objects, 1)
    bpc = get_int(parms.get("BitsPerComponent"), objects, 8)
    columns = get_int(parms.get("Columns"), objects, 1)
    obj = zlib.decompressobj()
    out = obj.decompress(data)
    if predictor < 2:
        return out
    bpp = max(1, (colors * bpc + 7) // 8)
    row_len = (columns * colors * bpc + 7) // 8
    if predictor == 2:  # TIFF
        if bpc != 8:
            return out
        prev = bytearray(row_len)
        res = bytearray()
        for r in range(0, len(out) - row_len + 1, row_len):
            row = bytearray(out[r:r + row_len])
            for i in range(bpp, row_len):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
            res += row
            prev = row
        return bytes(res)
    # PNG predictors
    res = bytearray()
    prev = bytearray(row_len)
    pos = 0
    while pos + 1 + row_len <= len(out):
        ft = out[pos]
        row = bytearray(out[pos + 1:pos + 1 + row_len])
        pos += 1 + row_len
        if ft == 1:
            for i in range(bpp, row_len):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif ft == 2:
            for i in range(row_len):
                row[i] = (row[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(row_len):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(row_len):
                a = row[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[i] = (row[i] + pr) & 0xFF
        res += row
        prev = row
    return bytes(res)


# ---------------------------------------------------------------- ToUnicode

def parse_tounicode(cmap_bytes: bytes) -> dict[int, str]:
    """解析 ToUnicode CMap，返回 {字符码: 文本}。"""
    mapping: dict[int, str] = {}
    text = cmap_bytes.decode("latin-1", errors="replace")

    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            mapping[int(src, 16)] = _hex_to_text(dst)

    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for m in re.finditer(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<([0-9A-Fa-f]*)>|\[(.*?)\])", block, re.S):
            lo, hi = int(m.group(1), 16), int(m.group(2), 16)
            if m.group(4) is not None:
                base = _hex_to_text(m.group(4))
                for k in range(lo, hi + 1):
                    mapping[k] = _shift_text(base, k - lo)
            elif m.group(5) is not None:
                items = re.findall(r"<([0-9A-Fa-f]*)>", m.group(5))
                for k, hx in zip(range(lo, hi + 1), items):
                    mapping[k] = _hex_to_text(hx)
    return mapping


def _hex_to_text(h: str) -> str:
    if not h:
        return ""
    if len(h) % 2:
        h += "0"
    b = bytes.fromhex(h)
    try:
        return b.decode("utf-16-be")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def _shift_text(base: str, delta: int) -> str:
    if not base:
        return ""
    return "".join(chr(ord(c) + delta) if ord(c) + delta < 0x110000 else c for c in base)


# ---------------------------------------------------------------- 字体与内容流

STD_ENCODING_HINTS = {
    "WinAnsiEncoding": "cp1252",
    "MacRomanEncoding": "mac_roman",
    "PDFDocEncoding": "latin-1",
}


def build_font_maps(objects: dict) -> tuple[dict[int, dict[int, str]], dict[int, str]]:
    """返回 ({字体对象号: ToUnicode 映射}, {字体对象号: 单字节编码名})。"""
    tou: dict[int, dict[int, str]] = {}
    simple_enc: dict[int, str] = {}
    for num, obj in objects.items():
        if not isinstance(obj, dict):
            continue
        tu = resolve(obj.get("ToUnicode"), objects)
        if isinstance(tu, Stream):
            try:
                tou[num] = parse_tounicode(decode_stream(tu, objects))
            except Exception:  # noqa: BLE001
                pass
        enc = resolve(obj.get("Encoding"), objects)
        if isinstance(enc, bytes):
            simple_enc[num] = STD_ENCODING_HINTS.get(enc.decode("latin-1"), "latin-1")
        elif isinstance(enc, dict):
            simple_enc[num] = "differences"
    return tou, simple_enc


def collect_font_resources(objects: dict) -> dict[str, int]:
    """把资源名（如 /F1）映射到字体对象号。

    优先走页面树并按 /Resources 继承查找；页面树不可用时退回全对象扫描。
    """
    name_to_obj: dict[str, int] = {}

    def absorb(res):
        res = resolve(res, objects)
        if not isinstance(res, dict):
            return
        fonts = resolve(res.get("Font"), objects)
        if isinstance(fonts, dict):
            for name, val in fonts.items():
                if isinstance(val, Ref):
                    name_to_obj.setdefault(name, val.num)

    pages = list(iter_pages(objects))
    if pages:
        # 页面通常不直接带 Resources，需从父节点继承；先把所有 Pages 节点的资源收进来
        for obj in objects.values():
            if isinstance(obj, dict) and obj.get("Kids") is not None:
                absorb(obj.get("Resources"))
        for page in pages:
            absorb(page.get("Resources"))
    if not name_to_obj:
        for obj in objects.values():
            if isinstance(obj, dict):
                absorb(obj.get("Resources"))
    return name_to_obj


def iter_pages(objects: dict):
    """按文档结构找出所有页面对象（走 /Root -> /Pages -> /Kids）。"""
    root = None
    for obj in objects.values():
        if isinstance(obj, dict):
            t = resolve(obj.get("Type"), objects)
            if t == b"/Catalog" or (isinstance(obj.get("Pages"), Ref) and root is None):
                root = obj
                break
    if root is None:
        return
    pages_ref = root.get("Pages")
    seen: set[int] = set()

    def walk(node, depth=0):
        if depth > 64 or not isinstance(node, dict):
            return
        t = resolve(node.get("Type"), objects)
        kids = resolve(node.get("Kids"), objects)
        if t == b"/Page" or (kids is None and node.get("Contents") is not None):
            yield node
            return
        if isinstance(kids, list):
            for kid in kids:
                key = kid.num if isinstance(kid, Ref) else id(kid)
                if key in seen:
                    continue
                seen.add(key)
                yield from walk(resolve(kid, objects), depth + 1)

    yield from walk(resolve(pages_ref, objects))


def page_content_streams(objects: dict) -> list[bytes]:
    """只取页面 /Contents 指向的流——避免把嵌入字体等二进制当成内容流。"""
    out: list[bytes] = []
    for page in iter_pages(objects):
        contents = resolve(page.get("Contents"), objects)
        refs = contents if isinstance(contents, list) else [contents]
        for c in refs:
            st = resolve(c, objects)
            if not isinstance(st, Stream):
                continue
            try:
                out.append(decode_stream(st, objects))
            except Exception:  # noqa: BLE001
                continue
    return out


def content_streams(objects: dict) -> list[bytes]:
    """取页面内容流；页面树不可用时退回启发式，但排除明显的二进制（字体等）。"""
    streams = page_content_streams(objects)
    if streams:
        return streams
    out = []
    for obj in objects.values():
        if not isinstance(obj, Stream):
            continue
        try:
            data = decode_stream(obj, objects)
        except Exception:  # noqa: BLE001
            continue
        # 字体/图片等二进制：含 NUL 或常见字体表头，直接排除
        if b"\x00" in data[:64] or data[:4] in (b"OTTO", b"ttcf", b"\x00\x01\x00\x00"):
            continue
        if not (b"BT" in data or b"Tj" in data or b"TJ" in data):
            continue
        out.append(data)
    return out


def _scan_line_metrics(data: bytes) -> tuple[float, float]:
    """预扫内容流，估计行距与平均字号，返回 (line_height, mean_size)。

    阈值不能写死：不同 PDF 的行距差异很大。用相邻 Y 差值的中位数当行距。
    """
    lex = Lexer(data)
    pending: list = []
    size = 10.0
    sizes: list[float] = []
    ys: list[float] = []
    while True:
        save = lex.i
        tok = lex.read_token()
        if tok is None:
            break
        if tok == b"Tf":
            if len(pending) >= 2 and isinstance(pending[-1], (int, float)):
                size = float(pending[-1]) or 10.0
            pending = []
            continue
        if tok in (b"Tm", b"Td", b"TD"):
            n = 6 if tok == b"Tm" else 2
            if len(pending) >= n and all(isinstance(v, (int, float)) for v in pending[-n:]):
                ys.append(float(pending[-1]))
            pending = []
            continue
        if tok in (b"Tj", b"TJ", b"'", b'"'):
            sizes.append(size)
            pending = []
            continue
        if tok == b"[":
            lex.i = save
            parse_object(lex)
            pending = []
            continue
        if tok == b"<<":
            lex.i = save
            parse_object(lex)
            pending = []
            continue
        if tok.startswith(b"/") or tok.startswith(b"(") or tok.startswith(b"<"):
            pending.append(tok)
            continue
        try:
            pending.append(int(tok) if re.fullmatch(rb"[+-]?\d+", tok) else float(tok))
        except ValueError:
            pending = []

    mean_size = sum(sizes) / len(sizes) if sizes else 10.0
    # 同一行可能被多次定位（同一个 Y 重复出现），先去重再取相邻差值：
    # 中文卷每行一个 Y，英文卷相邻行 Y 不同，这样两种都能得到真实行距。
    distinct: list[float] = []
    for y in ys:
        if not distinct or abs(y - distinct[-1]) > 0.5:
            distinct.append(y)
    gaps = sorted(abs(distinct[i + 1] - distinct[i]) for i in range(len(distinct) - 1))
    gaps = [g for g in gaps if g > 0.5]
    if not gaps:
        return 0.0, mean_size
    mid = gaps[len(gaps) // 2]
    return mid, mean_size


def extract_text_from_content(data: bytes, name_to_obj: dict[str, int],
                              tou: dict[int, dict[int, str]],
                              simple_enc: dict[int, str]) -> str:
    """按内容流里的文本算子还原文字。

    关键点：
    * 不能把 TD/Td/Tm 一律当换行——很多 PDF 每段甚至每字都用一次定位，一律换行会把
      整篇拆成一行一段。
    * 换行阈值必须自适应：见 _scan_line_metrics 的说明。
    * 不能在 ET 上换行：中文 PDF 常把每个 marked-content 段包成独立 BT…ET。
    """
    line_height, mean_size = _scan_line_metrics(data)
    lex = Lexer(data)
    out: list[str] = []
    cur_font = None
    size = 10.0
    tx = ty = 0.0        # 文本矩阵平移分量
    last_y: float | None = None   # 上一次真正显示文字时的 Y（判断换行的基准）
    end_x = 0.0          # 当前行已排到哪
    pending: list = []

    def _finish_line():
        nonlocal end_x
        if out and out[-1] != "\n":
            out.append("\n")
        end_x = tx

    def _gap(x: float):
        nonlocal end_x
        if not out or out[-1] == "\n":
            end_x = x
            return
        d = x - end_x
        if d > max(size * 2.5, 8):
            out.append("  ")
        elif d > max(size * 0.25, 1.2):
            out.append(" ")

    def show(raw: bytes, x: float, y: float, advance: float):
        """advance = 本次显示的**总**前进量（字符串宽度 + 其后的字距偏移）。"""
        nonlocal end_x, last_y
        # 换行判定：优先看 X 是否回退——同一行内 X 必然递增，X 回退就是新行，
        # 这与字号、行距都无关，比猜行距稳健得多。Y 大幅变化作为补充信号
        # （用于同一 X 起点的多行，此时 X 不构成回退）。
        new_line = False
        if out and out[-1] != "\n":
            if x < end_x - max(size * 0.6, 2.0):
                new_line = True
            elif last_y is not None and abs(y - last_y) > max(size * 1.8, 6.0):
                new_line = True
        if new_line:
            _finish_line()
        _gap(x)
        out.append(decode_show(raw, cur_font, tou, simple_enc))
        end_x = x + advance
        last_y = y

    while True:
        save = lex.i
        tok = lex.read_token()
        if tok is None:
            break

        if tok == b"Tf":
            if len(pending) >= 2:
                if isinstance(pending[-2], bytes) and pending[-2].startswith(b"/"):
                    cur_font = name_to_obj.get(pending[-2][1:].decode("latin-1"))
                if isinstance(pending[-1], (int, float)):
                    size = float(pending[-1]) or 10.0
            pending = []
            continue

        if tok in (b"Tj", b"'", b'"'):
            if pending:
                last = pending[-1]
                raw_str = None
                if isinstance(last, StringVal):
                    raw_str = last.data
                elif isinstance(last, bytes) and (last.startswith(b"(") or last.startswith(b"<")):
                    raw_str = decode_pdf_string(last)
                if raw_str is not None:
                    show(raw_str, tx, ty, max(1, len(raw_str)) * size * 0.5)
            if tok in (b"'", b'"'):
                ty -= size * 1.2
                tx = 0.0
                _finish_line()
            pending = []
            continue

        if tok == b"TJ":
            if pending and isinstance(pending[-1], list):
                # 把数组拆成若干「字符串 + 紧随其后的数字偏移」组，一次算出总前进量。
                # 数字为正（右移）按 1/1000 em 计入；负数是字距微调，不计宽度；
                # 小于 -180 视为词间空格。
                group: StringVal | None = None
                adv = 0.0
                for el in pending[-1]:
                    if isinstance(el, StringVal):
                        if group is not None:
                            show(group.data, tx, ty, adv)
                            adv = 0.0
                        group = el
                    elif isinstance(el, (int, float)):
                        step = float(el) * size / 1000.0
                        if step > 0:
                            adv += step
                        if el < -180:
                            out.append(" ")
                if group is not None:
                    show(group.data, tx, ty,
                         adv + max(1, len(group.data)) * size * 0.5)
            pending = []
            continue

        if tok == b"BT":
            # BT 重置文本矩阵，相对定位基准也要归零，否则 TD/Td 偏移会跨段累加
            tx = ty = 0.0
            pending = []
            continue

        if tok == b"Tm":
            if len(pending) >= 6 and all(isinstance(v, (int, float)) for v in pending[-6:]):
                # 只更新坐标，**不要**在这里换行：同一行的多个片段各有自己的 Tm。
                # 是否换行交给 show() 按 Y 坐标差判断。
                tx, ty = float(pending[-2]), float(pending[-1])
            pending = []
            continue

        if tok in (b"Td", b"TD"):
            if len(pending) >= 2 and all(isinstance(v, (int, float)) for v in pending[-2:]):
                tx += float(pending[-2])
                ty += float(pending[-1])
                if tok == b"TD" and isinstance(pending[-2], (int, float)):
                    pass
            pending = []
            continue

        if tok == b"T*":
            ty -= size * 1.2
            _finish_line()
            pending = []
            continue

        # 注意：不要在 ET 上换行。很多 PDF（尤其中文）把每个字/每个 marked-content 段
        # 都包成独立的 BT…ET，在 ET 换行会把整篇拆成一字一行。换行只由 Y 坐标变化决定。
        if tok == b"ET":
            pending = []
            continue

        # 其它 token 作为操作数累积
        if tok == b"<<":
            lex.i = save
            parse_object(lex)
            pending = []
            continue
        if tok == b"[":
            lex.i = save
            pending.append(parse_object(lex))
            continue
        if tok.startswith(b"/") or tok.startswith(b"(") or tok.startswith(b"<"):
            pending.append(tok)
            continue
        try:
            pending.append(int(tok) if re.fullmatch(rb"[+-]?\d+", tok) else float(tok))
        except ValueError:
            pending = []

    text = "".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def decode_show(raw: bytes, font_obj: int | None,
                tou: dict[int, dict[int, str]],
                simple_enc: dict[int, str]) -> str:
    """把显示字符串按字体映射还原成 Unicode。"""
    cmap = tou.get(font_obj) if font_obj is not None else None
    if cmap:
        # 先试双字节（CID 字体常见）
        if max(cmap) > 0xFF:
            out = []
            for i in range(0, len(raw) - 1, 2):
                code = (raw[i] << 8) | raw[i + 1]
                out.append(cmap.get(code, ""))
            joined = "".join(out)
            if joined.strip():
                return joined
        return "".join(cmap.get(b, "") for b in raw)
    enc = "latin-1"
    if font_obj is not None:
        enc = simple_enc.get(font_obj, "latin-1")
    if enc == "differences":
        enc = "latin-1"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("latin-1", errors="replace")


def extract_pdf_text(path: str) -> tuple[str, list[str]]:
    """抽取 PDF 全文。返回 (文本, 警告列表)。"""
    warn: list[str] = []
    raw = open(path, "rb").read()
    if not raw.startswith(b"%PDF"):
        raise ValueError("不是 PDF 文件（缺少 %PDF 头）")
    if b"/Encrypt" in raw:
        warn.append("文件含 /Encrypt，可能加密；若抽取为空请改用 Calibre")

    objects, _ = extract_objects(raw)
    if not objects:
        raise ValueError("未能解析出任何 PDF 对象")

    tou, simple_enc = build_font_maps(objects)
    name_to_obj = collect_font_resources(objects)

    streams = content_streams(objects)
    if not streams:
        raise ValueError("未找到含文本的内容流——可能是纯扫描图片 PDF（需 OCR，本脚本不做）")

    pages = []
    for st in streams:
        t = extract_text_from_content(st, name_to_obj, tou, simple_enc)
        pages.append(t)
    text = "\n\f\n".join(pages)

    if not text.strip():
        raise ValueError("内容流存在但未抽出任何文字——可能是纯扫描图片 PDF，或用了未支持的字体编码")

    # 归一化
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), warn
