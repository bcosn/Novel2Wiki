#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepare bounded AI review packets and keep a per-work call ledger. No model API."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

PROFILES = {"economy": 0, "balanced": 1, "strict": 2}
CONTENT_KINDS = {"attribution", "voice", "evidence", "fact"}
INSTRUCTIONS = ("检查第三人称、说话人归属、推测与事实区分、正文和所给证据是否支持。"
    "只返回具体错误与最小改法，不扩写，不请求全文或另起模型。"
    "格式为 issues=[{entry_id,kind,sentence,fix}], needs_followup=布尔。"
    "kind 为 attribution/voice/evidence/fact/style。材料不足说明不足，不猜。")

def signature(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def serialized(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"

def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(serialized(obj), encoding="utf-8", newline="\n")
    os.replace(temp, path)

def priority(e):
    score = 0
    if e.get("name") in ("我", "叙述者"):
        score += 5
    if len(e.get("participants") or []) > 1:
        score += 3
    for s in e.get("sources") or []:
        if s.get("certainty") in ("reported", "inferred", "dream"):
            score += 3
        if s.get("speaker") or s.get("perspective") == "character":
            score += 1
    if re.search("叙述者|据称|指称|推测|可能|梦中", e.get("lead", "")):
        score += 2
    return score

def plan(doc, state, include=()):
    entries = doc.get("entries") or []
    by_id = {e["id"]: e for e in entries}
    if len(by_id) != len(entries):
        raise ValueError("条目 id 重复")
    reviewed = {eid: sig for h in state["history"] for eid, sig in h["entry_signatures"].items()}
    issues = state["history"][-1]["issues"] if state["history"] else []
    affected = list(dict.fromkeys(i["entry_id"] for i in issues if i["kind"] in CONTENT_KINDS and i["entry_id"] in by_id))
    requested = list(dict.fromkeys(list(include) + affected))
    if any(i not in by_id for i in include):
        raise ValueError("--include 包含不存在的条目")
    if len(include) > state["settings"]["sample_size"]:
        raise ValueError("--include 超过 sample_size")
    unseen = [e for e in entries if reviewed.get(e["id"]) != signature(e)]
    unseen.sort(key=lambda e: (-priority(e), e["id"]))
    order = list(requested)
    if unseen and unseen[0]["id"] not in order:
        order.append(unseen[0]["id"])
    for kind in ("character", "event", "place", "object", "race"):
        candidate = next((e["id"] for e in unseen if e.get("kind") == kind and e["id"] not in order), None)
        if candidate:
            order.append(candidate)
    order += [e["id"] for e in unseen if e["id"] not in order]
    packet = {"status": "ready", "work": state["work"], "round": state["calls"] + 1,
              "instructions": INSTRUCTIONS, "entries": []}
    limit = state["settings"]["context_chars"]
    if len(serialized(packet)) > limit:
        raise ValueError("context_chars 太小，连审查指令都容纳不了")
    oversized = []
    for eid in order:
        if len(packet["entries"]) >= state["settings"]["sample_size"]:
            break
        candidate = {k: by_id[eid][k] for k in ("id", "kind", "name", "lead", "lead_refs", "sections", "sources", "reception") if k in by_id[eid]}
        attempt = dict(packet, entries=packet["entries"] + [candidate])
        if len(serialized(attempt)) <= limit:
            packet = attempt
        elif eid in include:
            raise ValueError("%s 单条超过 context_chars，先缩小审查范围" % eid)
        else:
            oversized.append(eid)
    if not packet["entries"]:
        raise ValueError("没有可放入字符上限的未审条目；缩小单条审查范围或由用户调整参数")
    # Only expose short ids outside the bounded packet; never silently omit a candidate.
    state["omitted_candidates"] = oversized
    return packet

def operate(args):
    doc = json.loads(Path(args.entries).read_text(encoding="utf-8"))
    if not doc.get("entries"):
        raise ValueError("条目文件为空")
    work = (doc.get("meta") or {}).get("book") or Path(args.entries).stem
    state_path = Path(args.state).resolve()
    out_path = Path(args.out).resolve() if args.out else None
    if out_path == state_path or state_path == Path(args.entries).resolve() or out_path == Path(args.entries).resolve():
        raise ValueError("entries、state 与 packet 必须使用不同路径")
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("version") != 1 or state.get("work") != work:
            raise ValueError("state 版本或作品不匹配；同一作品必须复用自己的台账")
        if args.mode is not None and args.mode != state["settings"]["mode"]:
            raise ValueError("已有台账的 mode 不可在续跑时更改")
        for key in ("max_reviews", "sample_size", "context_chars"):
            if getattr(args, key) is not None and getattr(args, key) != state["settings"][key]:
                raise ValueError("已有台账的 %s 不可由续跑命令更改" % key)
    else:
        if args.claim or args.record_result:
            raise ValueError("先生成审查包和台账")
        mode = args.mode or "balanced"
        state = {"version": 1, "work": work, "settings": {"mode": mode,
            "max_reviews": args.max_reviews if args.max_reviews is not None else PROFILES[mode],
            "sample_size": args.sample_size if args.sample_size is not None else 4,
            "context_chars": args.context_chars if args.context_chars is not None else 12000},
            "calls": 0, "history": [], "pending": None}
    settings = state["settings"]
    if settings["max_reviews"] < 0 or settings["sample_size"] < 1 or settings["context_chars"] < 500:
        raise ValueError("max_reviews≥0、sample_size≥1、context_chars≥500")
    current = signature(doc)
    pending = state.get("pending")
    if args.claim:
        if not pending or pending["status"] != "planned":
            raise ValueError("没有未领取的审查包；不能重复 claim")
        if pending["document_signature"] != current:
            raise ValueError("正文已变化，先重新生成未领取的审查包")
        if state["calls"] >= settings["max_reviews"]:
            raise ValueError("审查额度已用完")
        if signature(pending["packet"]) != pending["packet_signature"]:
            raise ValueError("审查包完整性错误")
        pending["status"] = "claimed"
        state["calls"] += 1
        save(state_path, state)
        return {"status": "claimed", "calls": state["calls"], "max_reviews": settings["max_reviews"]}
    if args.record_result:
        if not pending or pending["status"] != "claimed":
            raise ValueError("只能记录已领取审查包的结果")
        result = json.loads(Path(args.record_result).read_text(encoding="utf-8"))
        ids = {e["id"] for e in pending["packet"]["entries"]}
        issues = result.get("issues")
        if not isinstance(issues, list) or type(result.get("needs_followup")) is not bool:
            raise ValueError("result 需要 issues 数组和 needs_followup 布尔")
        for issue in issues:
            if not isinstance(issue, dict) or issue.get("entry_id") not in ids or issue.get("kind") not in CONTENT_KINDS | {"style"}:
                raise ValueError("issue 的条目不在样本中，或 kind 无效")
            if not isinstance(issue.get("sentence"), str) or not isinstance(issue.get("fix"), str):
                raise ValueError("issue 需要 sentence 与 fix")
        followup = result["needs_followup"] and any(i["kind"] in CONTENT_KINDS for i in issues)
        state["history"].append({"entry_signatures": pending["entry_signatures"],
            "issues": issues, "needs_followup": followup})
        state["pending"] = None
        save(state_path, state)
        return {"status": "recorded", "calls": state["calls"], "needs_followup": followup}
    if not out_path:
        raise ValueError("生成审查包需要 -o")
    if pending:
        if pending["status"] == "claimed":
            raise ValueError("上一审查已领取但未记录结果；不能再发新包")
        if pending["document_signature"] == current:
            save(out_path, pending["packet"])
            return {"status": "reused", "calls": state["calls"], "packet_chars": len(serialized(pending["packet"]))}
        state["pending"] = None
    reason = None
    if state["calls"] >= settings["max_reviews"]:
        reason = "已达到作品级审查上限"
    elif state["history"] and not state["history"][-1]["needs_followup"]:
        reason = "上一轮无需要复查的内容问题"
    if reason:
        packet = {"status": "stop", "work": work, "reason": reason, "calls": state["calls"]}
        save(out_path, packet)
        save(state_path, state)
        return packet
    include = [s.strip() for s in (args.include or "").split(",") if s.strip()]
    packet = plan(doc, state, include)
    ids = {e["id"] for e in packet["entries"]}
    state["pending"] = {"status": "planned", "document_signature": current,
        "packet_signature": signature(packet), "packet": packet,
        "entry_signatures": {e["id"]: signature(e) for e in doc["entries"] if e["id"] in ids}}
    save(out_path, packet)
    save(state_path, state)
    return {"status": "ready", "sample_ids": [e["id"] for e in packet["entries"]],
            "packet_chars": len(serialized(packet)), "calls": state["calls"], "max_reviews": settings["max_reviews"],
            "omitted_candidates": state.get("omitted_candidates", [])}

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="作品级 AI 审查参数、限量上下文与调用台账")
    ap.add_argument("entries")
    ap.add_argument("--state", required=True)
    ap.add_argument("-o", "--out")
    ap.add_argument("--mode", choices=PROFILES)
    ap.add_argument("--max-reviews", type=int)
    ap.add_argument("--sample-size", type=int)
    ap.add_argument("--context-chars", type=int)
    ap.add_argument("--include")
    actions = ap.add_mutually_exclusive_group()
    actions.add_argument("--claim", action="store_true")
    actions.add_argument("--record-result")
    args = ap.parse_args()
    state_path = Path(args.state).resolve()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock = state_path.with_name(state_path.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print("台账正在使用，稍后重试；不要并行领取审查", file=sys.stderr)
        return 1
    try:
        os.close(descriptor)
        print(serialized(operate(args)).strip())
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("审查计划失败: %s" % exc, file=sys.stderr)
        return 1
    finally:
        lock.unlink()

if __name__ == "__main__":
    sys.exit(main())
