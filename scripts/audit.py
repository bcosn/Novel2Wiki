#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转换产物的审查与降噪，在 convert.py 产出 JSON 之后运行。

审查：扫描噪声与可疑内容，出结构化报告，默认不改数据。
降噪：加 --denoise 时另存清洗后的 JSON，并记录每一处改动。

检测项：水印残留、页码碎片、重复段落、整章重复、控制字符与零宽字符、
过短章、章标题混进正文、缺章与重复章号、汉字占比异常。

用法：
    python audit.py <chapters.json> [--denoise] [-o out.json] [--report report.md]
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

# 只匹配明确的站点/网址形态，避免误伤正常行文（如「英灵殿，Valhalla」）
RE_URL = re.compile(r"(?:https?://|www\.)[^\s，。；）】\"']+", re.I)
RE_DOMAINISH = re.compile(r"(?<![\w/])[a-z0-9-]{3,}\.(?:com|net|org|cn|cc|top|xyz|info|me)\b[^\s]{0,30}", re.I)
RE_SITE_NAME = re.compile(r"(txt80|八零电子书|久久小说|全本小说网|笔趣阁|顶点小说|新笔趣|"
                          r"www\.\S+\.(?:com|net|cn|cc)|请记住本站|手机用户请|最新章节|"
                          r"无弹窗|全文阅读|免费下载|首发地址)", re.I)
RE_PAGE_NUM = re.compile(r"^\s*(?:[-—–\[\(]*\s*(?:第\s*)?\d{1,4}\s*(?:页)?\s*[-—–\]\)]*)\s*$")
RE_PAGE_NUM2 = re.compile(r"^\s*\d{1,4}\s*/\s*\d{1,4}\s*$")
RE_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
RE_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")
RE_CHAPTER_HEAD = re.compile(r"^[ \t]*第\s*[\d零一二三四五六七八九十百千两]{1,8}\s*[章回节卷篇]")


def audit(data: dict, do_denoise: bool = False) -> tuple[dict, list[dict]]:
    """审查（并按需降噪）。返回 (处理后的 data, 报告条目列表)。"""
    findings: list[dict] = []
    chapters = data.get("chapters", [])
    meta = data.get("meta", {})

    def note(kind: str, severity: str, where: str, detail: str, sample: str = ""):
        findings.append({"kind": kind, "severity": severity, "where": where,
                         "detail": detail, "sample": sample[:120]})

    # ---- 1. 站点/水印残留 ----
    url_hits = site_hits = 0
    for ch in chapters:
        body = ch.get("content", "")
        for pat, label in ((RE_URL, "网址"), (RE_DOMAINISH, "域名"), (RE_SITE_NAME, "站点名")):
            for m in pat.finditer(body):
                if label == "网址":
                    url_hits += 1
                else:
                    site_hits += 1
                note("watermark", "warn", f"第{ch.get('index')}章", f"{label}残留",
                     m.group(0))
                if url_hits + site_hits > 50:
                    break
    if url_hits or site_hits:
        note("watermark", "warn", "全书", f"疑似站点水印残留 {url_hits + site_hits} 处")

    # ---- 2. 页码碎片 ----
    page_lines = []
    for ch in chapters:
        for line in ch.get("content", "").split("\n"):
            s = line.strip()
            if not s:
                continue
            if RE_PAGE_NUM.match(s) or RE_PAGE_NUM2.match(s):
                page_lines.append((ch.get("index"), s))
    if page_lines:
        note("page-number", "warn", "全书", f"疑似页码碎片 {len(page_lines)} 行",
             " | ".join(f"第{i}章:{s}" for i, s in page_lines[:5]))

    # ---- 3. 重复段落（跨章重复）----
    # 注意：这通常反映**源文件本身**的重复（盗版站常整段重复），不是解析伪影。
    # 因此只报告、不自动删——删掉就是删正文。
    seen: dict[str, list[int]] = {}
    for ch in chapters:
        for para in ch.get("content", "").split("\n\n"):
            p = para.strip()
            if len(p) >= 40:            # 太短的重复是正常行文，不判
                seen.setdefault(p, []).append(ch.get("index"))
    dups = {p: idx for p, idx in seen.items() if len(idx) > 1}
    if dups:
        worst = sorted(dups.items(), key=lambda kv: -len(kv[1]))[:5]
        note("duplicate-paragraph", "warn", "全书",
             f"跨章重复段落 {len(dups)} 段（多为源文件自带，需人工判断）",
             " | ".join(f"{len(v)}次:{k[:40]}" for k, v in worst))

    # ---- 3b. 章级相似度：整章重复/近似重复 ----
    sigs: dict[frozenset, list[int]] = {}
    for ch in chapters:
        paras = {p.strip() for p in ch.get("content", "").split("\n\n") if len(p.strip()) >= 20}
        if len(paras) >= 5:
            sigs.setdefault(frozenset(paras), []).append(ch.get("index"))
    for _sig, idxs in sigs.items():
        if len(idxs) > 1:
            note("duplicate-chapter", "error", f"第{','.join(map(str, idxs))}章",
                 "这些章正文完全重复（源文件问题，建议人工删重）")

    # ---- 4. 转换伪影 ----
    ctrl = zero = repl = 0
    for ch in chapters:
        body = ch.get("content", "")
        ctrl += len(RE_CTRL.findall(body))
        zero += len(RE_ZERO_WIDTH.findall(body))
        repl += body.count("\ufffd")
    if ctrl:
        note("artifact", "error", "全书", f"控制字符 {ctrl} 个（编码/解析异常）")
    if zero:
        note("artifact", "warn", "全书", f"零宽字符 {zero} 个")
    if repl:
        note("artifact", "error", "全书", f"替换符 U+FFFD {repl} 个（解码失败）")

    # ---- 5. 空章 / 过短章 ----
    short = [ch for ch in chapters if ch.get("chars", 0) < 50]
    if short:
        note("short-chapter", "warn", "全书",
             f"过短章节 {len(short)} 个",
             " | ".join(f"第{c.get('index')}章 {c.get('chars')}字" for c in short[:6]))

    # ---- 6. 章节标题混进正文 ----
    head_in_body = []
    for ch in chapters:
        first = (ch.get("content", "").split("\n") or [""])[0].strip()
        if RE_CHAPTER_HEAD.match(first):
            head_in_body.append((ch.get("index"), first[:30]))
    if head_in_body:
        note("title-in-body", "warn", "全书",
             f"正文首行又出现章标题 {len(head_in_body)} 处",
             " | ".join(f"第{i}章:{t}" for i, t in head_in_body[:5]))

    # ---- 7. 结构异常 ----
    nums = [c.get("index") for c in chapters]
    missing = [n for n in range(1, max(nums) + 1) if n not in nums] if nums else []
    dup_idx = sorted({n for n in nums if nums.count(n) > 1})
    if missing:
        note("structure", "warn", "全书", f"缺章号 {len(missing)} 个",
             ",".join(map(str, missing[:20])))
    if dup_idx:
        note("structure", "error", "全书", f"重复章号 {len(dup_idx)} 个",
             ",".join(map(str, dup_idx)))

    # ---- 8. 字符集异常（正文里混入大量拉丁/符号）----
    for ch in chapters:
        body = ch.get("content", "")
        if len(body) < 200:
            continue
        cjk = sum(1 for c in body if "\u4e00" <= c <= "\u9fff")
        ratio = cjk / len(body)
        if ratio < 0.35:
            note("charset", "warn", f"第{ch.get('index')}章",
                 f"汉字占比仅 {ratio:.0%}，可能是解析串码或外文内容")

    # ---- 降噪 ----
    # 只处理无歧义的噪声：页码碎片、控制字符、零宽字符。
    # 重复段落与整章重复不自动删——那是源文件内容，删掉就是删正文。
    removed = []
    if do_denoise:
        for ch in chapters:
            body = ch.get("content", "")
            lines = body.split("\n")
            kept = []
            for line in lines:
                s = line.strip()
                if s and (RE_PAGE_NUM.match(s) or RE_PAGE_NUM2.match(s)):
                    removed.append({"chapter": ch.get("index"), "kind": "page-number",
                                    "text": s})
                    continue
                kept.append(line)
            body = "\n".join(kept)
            # 去掉零宽与控制字符（保留换行）
            body2 = RE_ZERO_WIDTH.sub("", body)
            body2 = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", body2)
            body2 = re.sub(r"\n{3,}", "\n\n", body2).strip()
            if body2 != body:
                removed.append({"chapter": ch.get("index"), "kind": "artifact",
                                "text": f"清理 {len(body) - len(body2)} 字符"})
            ch["content"] = body2
            ch["chars"] = len(body2)

        meta["total_chars"] = sum(c.get("chars", 0) for c in chapters)
        note("denoise", "info", "全书", f"降噪移除 {len(removed)} 处（仅页码与不可见字符）")
        data["denoise_log"] = removed

    data["audit"] = {
        "findings": findings,
        "counts": dict(Counter(f["kind"] for f in findings)),
        "denoised": do_denoise,
    }
    return data, findings


def render_report(path: str, data: dict, findings: list[dict]) -> str:
    meta = data.get("meta", {})
    lines = [
        f"# 审查报告：{meta.get('title', '(无书名)')}",
        "",
        f"- 来源：`{meta.get('source', '?')}`（{meta.get('source_format', '?')}）",
        f"- 章节：{meta.get('chapter_count', '?')}　字数：{meta.get('total_chars', '?')}",
        f"- 审查条目：{len(findings)}",
        "",
    ]
    if not findings:
        lines.append("未发现明显噪声。")
    else:
        lines += ["| 级别 | 类别 | 位置 | 说明 | 样例 |", "| --- | --- | --- | --- | --- |"]
        order = {"error": 0, "warn": 1, "info": 2}
        for f in sorted(findings, key=lambda x: order.get(x["severity"], 9)):
            sample = f["sample"].replace("|", "\\|").replace("\n", " ")[:60]
            detail = f["detail"].replace("|", "\\|")[:60]
            lines.append(f"| {f['severity']} | {f['kind']} | {f['where']} | {detail} | {sample} |")
    lines += ["", "## 人工复核建议", "",
              "自动判定只做保守过滤，**降噪前请先看上面的样例**。",
              "被移除的内容都记在产物的 `denoise_log` 里，可回溯。"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="转换产物的审查与降噪")
    ap.add_argument("input", help="convert.py 产出的 chapters.json")
    ap.add_argument("--denoise", action="store_true", help="另存一份降噪后的 JSON")
    ap.add_argument("-o", "--out", default="", help="降噪输出路径（默认 <原名>.denoised.json）")
    ap.add_argument("--report", default="", help="报告输出路径（.md）")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as fh:
        data = json.load(fh)

    data, findings = audit(data, do_denoise=args.denoise)

    if args.denoise:
        out = args.out or (os.path.splitext(args.input)[0] + ".denoised.json")
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        print(f"降噪产物：{out}（移除 {len(data.get('denoise_log', []))} 处）")
    else:
        # 即使不降噪也把审查结果写回原文件，便于后续步骤读取
        with open(args.input, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    report = render_report(args.input, data, findings)
    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(report)
        print(f"报告：{args.report}")

    print()
    print(report)
    errors = [f for f in findings if f["severity"] == "error"]
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
