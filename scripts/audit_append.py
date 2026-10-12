#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追加产物的 AI 审查：校验 append 文件，并输出待研判队列。

在第四步（追加）之后运行。只读校验 + 出报告，不改数据。

校验项：
    * 结构：顶层字段齐备，meta 完整
    * 悬空引用：new_timeline / range_advance / corrections 的 name 必须在对应表里存在
    * 章号越界：first_chapter / from_chapter 必须落在声明的追加范围内
    * 幂等：同一 (name, field, from_chapter) 不得重复
    * 字段一致性：新实体与新时间线条目的字段必须与既有表完全一致
    * 未研判的矛盾：conflicts 每条必须带结论与性质判定，否则报错

用法：
    python audit_append.py <append.json> --base-dir <example1 目录> [--report r.md]
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

# conflicts 每条必须能判出性质。四类，第四类是「同一实体异写」：
# 同名异写、一地异名、同一人不同称呼——这类不算矛盾，但必须显式判定并合并，
# 否则会重复建条。只判三类会把这类逼进 unresolved。
# 措辞允许两种：规范的「同一实体异写」，或「非矛盾（…异名/异写…）」。
KINDS = ("原文自身前后不一", "前面抽错了", "看似矛盾实为不同事", "同一实体异写")
RE_ALIAS_KIND = re.compile(r"非矛盾[（(][^）)]*(异名|异写|别名|同一人|一地)")
# 章节单位按作品而定：章回体写「回」，现代长篇写「章」。
RE_RANGE = re.compile(r"第(\d+)-(\d+)(?:回|章)")


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def check(append_path: str, base_dir: str, prefix: str = "红楼梦") -> tuple[list, list, dict]:
    errors, warns = [], []
    ap = load(append_path)

    def err(msg):
        errors.append(msg)

    def warn(msg):
        warns.append(msg)

    # ---- 范围 ----
    m = RE_RANGE.search(ap.get("meta", {}).get("appended_range", ""))
    if not m:
        err("meta.appended_range 无法解析出章号范围（应形如「第11-20回」或「第11-20章」）")
        lo, hi = 0, 10 ** 9
    else:
        lo, hi = int(m.group(1)), int(m.group(2))

    # ---- 既有表 ----
    pools, fields = {}, {}
    for kind, fname in (("characters", "characters.json"), ("places", "places.json"),
                        ("objects", "objects.json"), ("races", "races.json"),
                        ("character_timeline", "character_timeline.json"),
                        ("object_timeline", "object_timeline.json")):
        p = os.path.join(base_dir, prefix + "." + fname)
        items = load(p)["items"]
        pools[kind] = {i["name"] for i in items}
        fields[kind] = set(items[0].keys()) if items else set()

    # ---- 顶层字段 ----
    for key in ("meta", "new_entities", "new_timeline", "range_advance"):
        if key not in ap:
            err("缺少顶层字段 " + key)

    # ---- 新实体 ----
    for kind in ("characters", "places", "objects", "races"):
        for it in ap.get("new_entities", {}).get(kind, []):
            name = it.get("name", "?")
            missing = fields[kind] - set(it.keys())
            if missing:
                err("新实体 %s/%s 缺字段: %s" % (kind, name, ",".join(sorted(missing))))
            fc = it.get("first_chapter")
            if not isinstance(fc, int) or not (lo <= fc <= hi):
                err("新实体 %s/%s 的 first_chapter=%r 不在追加范围内" % (kind, name, fc))
            if name in pools[kind]:
                warn("新实体 %s/%s 与既有表重名，可能是重复而非新增" % (kind, name))

    # ---- 新时间线 ----
    # 注意：新时间线可以引用**本批新增**的实体，所以判定池 = 旧表 ∪ 本批新增。
    # 只查旧表会把新增实体的变化误报成悬空引用。
    merged = {k: set(v) for k, v in pools.items()}
    for kind in ("characters", "places", "objects", "races"):
        merged[kind] |= {i.get("name") for i in ap.get("new_entities", {}).get(kind, [])}

    pair_map = {"characters": "character_timeline", "objects": "object_timeline"}
    seen = Counter()
    for grp, tl in pair_map.items():
        for it in ap.get("new_timeline", {}).get(grp, []):
            name, field = it.get("name", "?"), it.get("field", "?")
            missing = fields[tl] - set(it.keys())
            if missing:
                err("新时间线 %s/%s 缺字段: %s" % (tl, name, ",".join(sorted(missing))))
            if name not in merged[grp]:
                err("新时间线 %s 的 name=%s 既不在 %s 旧表、也不在本批新增里（悬空引用）"
                    % (tl, name, grp))
            fc = it.get("from_chapter")
            if fc is not None and (not isinstance(fc, int) or not (lo <= fc <= hi)):
                err("新时间线 %s/%s 的 from_chapter=%r 不在追加范围内" % (tl, name, fc))
            seen[(name, field, fc)] += 1

    for k, c in seen.items():
        if c > 1:
            err("时间线重复条目 (name=%s, field=%s, from_chapter=%s) 出现 %d 次，违反幂等" % (k[0], k[1], k[2], c))

    # ---- 范围收口 ----
    for it in ap.get("range_advance", []):
        name, field = it.get("name", "?"), it.get("field", "?")
        grp = "character_timeline" if it.get("kind", "character") == "character" else "object_timeline"
        if name not in pools.get("characters" if grp == "character_timeline" else "objects", set()):
            err("range_advance 的 name=%s 在对应表里不存在" % name)
        ntc = it.get("new_to_chapter")
        if not isinstance(ntc, int):
            err("range_advance %s/%s 的 new_to_chapter 必须是整数" % (name, field))

    # ---- 矛盾研判 ----
    adjudicated = []
    for i, c in enumerate(ap.get("conflicts", []), 1):
        note = json.dumps(c, ensure_ascii=False)
        if any(k in note for k in KINDS) or RE_ALIAS_KIND.search(note):
            adjudicated.append(c)
        else:
            err("conflicts 第 %d 条没有性质判定（须含其一：%s；或写作「非矛盾（…异名/异写）」）"
                % (i, " / ".join(KINDS)))

    for i, c in enumerate(ap.get("corrections", []), 1):
        for key in ("name", "field", "old_value", "should_be", "reason", "evidence"):
            if key not in c:
                err("corrections 第 %d 条缺字段 %s" % (i, key))

    stats = {
        "new_entities": {k: len(v) for k, v in ap.get("new_entities", {}).items()},
        "new_timeline": {k: len(v) for k, v in ap.get("new_timeline", {}).items()},
        "range_advance": len(ap.get("range_advance", [])),
        "conflicts": len(ap.get("conflicts", [])),
        "conflicts_adjudicated": len(adjudicated),
        "corrections": len(ap.get("corrections", [])),
        "unresolved": len(ap.get("unresolved", [])),
    }
    return errors, warns, stats


def render(path, ap, errors, warns, stats):
    lines = [
        "# 追加审查报告",
        "",
        "- 文件：`%s`" % os.path.basename(path),
        "- 追加范围：%s" % ap.get("meta", {}).get("appended_range", "?"),
        "",
        "## 统计",
        "",
        "| 项 | 数量 |",
        "| --- | --- |",
    ]
    for k, v in stats.items():
        lines.append("| %s | %s |" % (k, v if not isinstance(v, dict) else
                                      " / ".join("%s %d" % (a, b) for a, b in v.items())))
    lines += ["", "## 校验", ""]
    if errors:
        lines += ["**错误 %d 条：**" % len(errors), ""]
        lines += ["- " + e for e in errors]
    else:
        lines.append("结构校验全部通过。")
    if warns:
        lines += ["", "**警告 %d 条：**" % len(warns), ""]
        lines += ["- " + w for w in warns]

    lines += ["", "## 待研判队列", ""]
    conflicts = ap.get("conflicts", [])
    if not conflicts:
        lines.append("无矛盾。")
    else:
        for i, c in enumerate(conflicts, 1):
            lines.append("### %d. %s" % (i, c.get("name", "?")))
            for k, v in c.items():
                if k != "name":
                    lines.append("- **%s**：%s" % (k, v))
            lines.append("")

    corr = ap.get("corrections", [])
    if corr:
        lines += ["## 待复核的更正（不直接改旧文件）", ""]
        for i, c in enumerate(corr, 1):
            lines.append("%d. **%s.%s**：%s → %s" % (i, c.get("name"), c.get("field"),
                                                    c.get("old_value"), c.get("should_be")))
            lines.append("   - 依据：%s" % c.get("evidence", ""))
        lines.append("")

    unres = ap.get("unresolved", [])
    if unres:
        lines += ["## 研判后仍未决", ""]
        lines += ["- " + str(u) for u in unres]
        lines.append("")

    lines += ["", "## 人工复核建议", "",
              "`corrections` 一律**不自动应用**——它意味着前面抽错了，改动会影响已固化的数据。",
              "`unresolved` 需要人或更强模型补判。"]
    return "\n".join(lines)


def check_interactions(items: list, char_names: set) -> tuple[list, list]:
    """校验互动表：主体间视角必须覆盖每个参与者。

    核心规则：互动不能简化为主客关系，所以每个 participants 都必须有对应的
    perspectives 条目——只写主动方即报错。
    """
    errors, warns = [], []
    seen_ids = Counter()
    for it in items:
        iid = it.get("id", "?")
        seen_ids[iid] += 1
        parts = it.get("participants") or []
        persp = it.get("perspectives") or {}

        if len(parts) < 2:
            errors.append("互动 %s 的 participants 少于两人（%r）——单人不成互动" % (iid, parts))
        for p in parts:
            if p not in char_names:
                errors.append("互动 %s 的参与者 %s 在人物表里不存在（悬空引用）" % (iid, p))
        missing = [p for p in parts if p not in persp]
        if missing:
            errors.append("互动 %s 缺 %s 的 perspectives——只写主动方即为主客关系，违反主体间要求"
                          % (iid, "/".join(missing)))
        extra = [p for p in persp if p not in parts]
        if extra:
            warns.append("互动 %s 的 perspectives 里有未列入 participants 的人：%s" % (iid, "/".join(extra)))
        for p, v in persp.items():
            for key in ("attitude", "intent", "reading"):
                if key not in v:
                    errors.append("互动 %s 的 %s 视角缺字段 %s" % (iid, p, key))
        for key in ("id", "participants", "chapter", "kind", "summary", "asymmetry", "change", "note"):
            if key not in it:
                errors.append("互动 %s 缺字段 %s" % (iid, key))
        if not str(it.get("asymmetry", "")).strip():
            errors.append("互动 %s 的 asymmetry 为空——不对称之处必须显式写，没有写「无」" % iid)
    for iid, c in seen_ids.items():
        if c > 1:
            errors.append("互动 id 重复：%s 出现 %d 次" % (iid, c))
    return errors, warns


def main() -> int:
    ap = argparse.ArgumentParser(description="追加产物的审查与矛盾研判校验")
    ap.add_argument("append", help="第四步产出的 append.json，或互动表 *.interactions.json")
    ap.add_argument("--base-dir", required=True, help="既有四表与时间线所在目录")
    ap.add_argument("--prefix", default="红楼梦", help="既有表文件名前缀，如「红楼梦」")
    ap.add_argument("--report", default="", help="报告输出路径（.md）")
    args = ap.parse_args()

    # 按内容判断是不是互动表，不依赖文件名
    doc = load(args.append)
    is_inter = (isinstance(doc.get("items"), list) and doc["items"]
                and "participants" in doc["items"][0])
    if is_inter:
        # 从 base-dir 发现人物表；文件名优先，找不到就用目录里唯一的那份
        prefix = os.path.basename(args.append).split(".")[0]
        cand = os.path.join(args.base_dir, prefix + ".characters.json")
        if not os.path.isfile(cand):
            found = sorted(f for f in os.listdir(args.base_dir) if f.endswith(".characters.json"))
            if len(found) == 1:
                cand = os.path.join(args.base_dir, found[0])
            elif not found:
                print("base-dir 里没有 *.characters.json，无法校验参与者的悬空引用")
                return 1
            else:
                print("base-dir 里有多份人物表（%s），请把待校验文件命名为 <书名>.interactions.json"
                      % " / ".join(found))
                return 1
        char_names = {i["name"] for i in load(cand)["items"]}
        items = doc["items"]
        errs, warns = check_interactions(items, char_names)
        print("互动表校验：%s" % os.path.basename(args.append))
        print("  条数 %d   参与者悬空检查依据：%s" % (len(items), os.path.basename(cand)))
        for e in errs:
            print("  [ERROR] " + e)
        for w in warns:
            print("  [WARN ] " + w)
        if not errs:
            print("  全部通过（每个参与者都有主体间视角）")
        return 1 if errs else 0

    errors, warns, stats = check(args.append, args.base_dir, args.prefix)
    report = render(args.append, load(args.append), errors, warns, stats)
    if args.report:
        with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(report)
        print("报告：" + args.report)
    print()
    print(report)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
