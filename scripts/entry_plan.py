"""Read-only editorial placement plans; paragraph matches are candidate locations only.

Plans retain every entity, distinguish content placement from identity, and bind
decisions to the complete current source snapshot. They contain no draft prose.
"""
from __future__ import annotations

import copy
import json
from collections import Counter

from citation_catalog import catalog
from core_store import canonical, digest
from verify_example import TABLES


BASE_TABLES = {"characters": "character", "places": "place",
               "objects": "object", "races": "race"}
EXPLICIT_KINDS = {"event", "concept"}
KINDS = set(BASE_TABLES.values()) | EXPLICIT_KINDS
IMPORTANCE = {"main", "major", "minor", "mention", "unknown"}
IMPORTANCE_SOURCES = {"table", "missing", "decision"}
DISPOSITIONS = {"standalone", "stub", "merge", "table_only", "review"}
DEPTHS = {"expanded", "standard", "brief", "none"}
EDIT_FIELDS = {"importance", "disposition", "depth", "reason", "evidence_ids",
               "topics", "merge_target"}
ITEM_FIELDS = EDIT_FIELDS | {"id", "kind", "name", "importance_source"}
PLAN_FIELDS = {"version", "work", "scope", "source_hash", "selected_chapters",
               "items", "plan_hash"}
TOPIC_FIELDS = {"id", "heading", "focus", "evidence_ids"}
RELATION_FIELDS = {"source", "target", "label", "evidence_ids"}


def _text(value, label, maximum=None, *, empty=False):
    if (not isinstance(value, str) or (not empty and not value.strip())
            or "\n" in value or "\r" in value
            or (maximum is not None and len(value) > maximum)):
        raise ValueError("invalid " + label)


def _load(store, work):
    """Read one snapshot without creating works, versions, records or receipts."""
    _text(work, "work")
    connection = getattr(store, "connection", None)
    own_transaction = connection is not None and not connection.in_transaction
    if own_transaction:
        connection.execute("BEGIN")
    try:
        chapters = store.get_document(work, "chapters")
        tables = {key: store.get_document(work, key) for key in TABLES}
        entries = store.get_document(work, "entries") or {"entries": []}
        saved_plan = store.get_document(work, "_entry_plan")
        history = ({row[0] for row in connection.execute("SELECT record_key FROM records WHERE work_id=? AND kind='entries'",(work,))}
                   if connection is not None else {row.get("id") for row in entries["entries"]})
        if not isinstance(chapters, dict):
            raise ValueError("frozen chapters required")
        for key, document in tables.items():
            if not isinstance(document, dict) or not isinstance(document.get("items"), list):
                raise ValueError("complete structured tables required: " + key)
        if not isinstance(entries, dict) or not isinstance(entries.get("entries"), list):
            raise ValueError("invalid current entries")
        result = chapters, tables, entries, saved_plan, history
    except BaseException:
        if own_transaction:
            connection.execute("ROLLBACK")
        raise
    if own_transaction:
        connection.execute("COMMIT")
    return result


def _selection(chapters, selected):
    if selected is not None:
        if (not isinstance(selected, (list, tuple, set)) or not selected
                or any(type(index) is not int or index < 1 for index in selected)):
            raise ValueError("selected_chapters must be nonempty positive chapter indices")
        selected = sorted(set(selected))
    value = catalog(chapters, selected)
    if not value["paragraphs"]:
        raise ValueError("no nonempty paragraphs in selected scope")
    indices = selected if selected is not None else sorted(row["index"] for row in chapters["chapters"])
    return indices, value["paragraphs"]


def _scope(chapters, selected):
    all_indices = sorted(row["index"] for row in chapters["chapters"])
    declared = chapters.get("meta", {}).get("scope")
    if selected == all_indices and isinstance(declared, str) and declared.strip():
        return declared
    return "章节：" + ",".join(map(str, selected))


def _identity(work, kind, name):
    return "e-" + digest([work, kind, name])[:16]


def _current_entries(document):
    by_key, by_id = {}, {}
    for entry in document["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("invalid current entry")
        _text(entry.get("id"), "current entry id")
        _text(entry.get("name"), "current entry name")
        if not isinstance(entry.get("kind"), str) or entry["kind"] not in KINDS:
            raise ValueError("invalid current entry kind")
        key = entry["kind"], entry["name"]
        if key in by_key or entry["id"] in by_id:
            raise ValueError("current entry identity mapping conflict")
        by_key[key], by_id[entry["id"]] = entry, key
    return by_key, by_id


def _candidate_evidence(name, source, entry, paragraphs):
    aliases = source.get("aliases", [])
    if not isinstance(aliases, list):
        aliases = []
    terms = [name] + [alias for alias in aliases if isinstance(alias, str) and alias.strip()]
    # Quotes locate existing event material without treating a title as proof.
    sources = (entry or {}).get("sources", [])
    quotes = [(row.get("chapter"), row["quote"]) for row in sources
              if isinstance(row, dict) and isinstance(row.get("quote"), str) and row["quote"].strip()]
    return [row["id"] for row in paragraphs
            if any(term in row["text"] for term in terms)
            or any(chapter == row["chapter"] and quote in row["text"] for chapter, quote in quotes)]


def _item(work, kind, name, source, entry, paragraphs):
    raw_importance = source.get("importance")
    known = isinstance(raw_importance, str) and raw_importance in IMPORTANCE
    importance = raw_importance if known else "unknown"
    if entry is not None:
        disposition, depth, reason = "standalone", "expanded" if importance == "main" else "standard", "保留已有独立条目，后续按证据复核。"
    elif importance == "main":
        disposition, depth, reason = "standalone", "expanded", "已有主要程度标记，优先展开有据主题。"
    elif importance == "major":
        disposition, depth, reason = "standalone", "standard", "已有重要程度标记，按独立条目规划。"
    elif importance == "minor":
        disposition, depth, reason = "stub", "brief", "暂列短条目；关键作用可经明确决策升级。"
    elif importance == "mention":
        disposition, depth, reason = "table_only", "none", "暂按提及程度保留表格；重要作用可另行确认。"
    elif kind != "race" and type(source.get("level")) is int and source["level"] == 0:
        disposition, depth, reason = "table_only", "none", "未标重要性的顶层分类容器暂保留表格。"
    else:
        disposition, depth, reason = "review", "none", "重要程度未确认，保留候选待判断。"
    return {"id": entry["id"] if entry else _identity(work, kind, name),
            "kind": kind, "name": name, "importance": importance,
            "importance_source": "table" if known else "missing",
            "disposition": disposition, "depth": depth, "reason": reason,
            "evidence_ids": _candidate_evidence(name, source, entry, paragraphs),
            "topics": [], "merge_target": None}


def _base_items(work, tables, entries, paragraphs):
    current, _ = _current_entries(entries)
    items, seen = [], set()
    for table, kind in BASE_TABLES.items():
        for row in tables[table]["items"]:
            if not isinstance(row, dict):
                raise ValueError("invalid base table row")
            name = row.get("name")
            _text(name, "base table name")
            key = kind, name
            if key in seen:
                raise ValueError("duplicate base table identity")
            seen.add(key)
            items.append(_item(work, kind, name, row, current.get(key), paragraphs))
    # Preserve existing pages, including events and intentionally renamed narrators.
    for key, entry in current.items():
        if key not in seen:
            items.append(_item(work, *key, {}, entry, paragraphs))
    return items


def _refs(value, pool, label, *, required=False):
    if (not isinstance(value, list) or (required and not value)
            or any(not isinstance(ref, str) or ref not in pool for ref in value)
            or len(value) != len(set(value))):
        raise ValueError("invalid " + label)


def _validate_items(items, base_items, work, paragraphs):
    if not isinstance(items, list):
        raise ValueError("plan items must be an array")
    base = {(row["kind"], row["name"]): row for row in base_items}
    pool = {row["id"] for row in paragraphs}
    by_id, by_key, topic_ids = {}, {}, set()
    for row in items:
        if not isinstance(row, dict) or set(row) != ITEM_FIELDS:
            raise ValueError("invalid plan item fields; draft prose is not accepted")
        _text(row["id"], "plan item id")
        _text(row["name"], "plan item name")
        for field, choices in (("kind", KINDS), ("importance", IMPORTANCE),
                               ("importance_source", IMPORTANCE_SOURCES),
                               ("disposition", DISPOSITIONS), ("depth", DEPTHS)):
            if not isinstance(row[field], str) or row[field] not in choices:
                raise ValueError("invalid plan item " + field)
        key = row["kind"], row["name"]
        if row["id"] in by_id or key in by_key:
            raise ValueError("duplicate plan item identity")
        expected = base.get(key)
        if expected is None and row["kind"] not in EXPLICIT_KINDS:
            raise ValueError("new candidates must be explicit events or concepts")
        expected_id = expected["id"] if expected else _identity(work, *key)
        if row["id"] != expected_id:
            raise ValueError("plan ID conflicts with current kind/name mapping")
        if row["importance_source"] != "decision":
            expected_importance = expected["importance"] if expected else "unknown"
            expected_source = expected["importance_source"] if expected else "missing"
            if row["importance"] != expected_importance or row["importance_source"] != expected_source:
                raise ValueError("importance change requires an explicit decision")
        _text(row["reason"], "short placement reason", 200)
        _refs(row["evidence_ids"], pool, "item evidence_ids",
              required=expected is None or row["disposition"] == "merge")
        if row["disposition"] in {"standalone", "stub"}:
            if row["depth"] == "none":
                raise ValueError("independent entries require a writing depth")
        elif row["depth"] != "none":
            raise ValueError("non-independent placement requires depth none")
        if row["merge_target"] is not None:
            _text(row["merge_target"], "merge_target")
        if row["disposition"] != "merge" and row["merge_target"] is not None:
            raise ValueError("merge_target requires merge disposition")
        if not isinstance(row["topics"], list):
            raise ValueError("topics must be an array")
        for topic in row["topics"]:
            if not isinstance(topic, dict) or set(topic) != TOPIC_FIELDS:
                raise ValueError("invalid topic fields; draft prose is not accepted")
            _text(topic["id"], "topic id", 80)
            _text(topic["heading"], "topic heading", 40)
            _text(topic["focus"], "topic focus", 160, empty=True)
            if topic["id"] in topic_ids:
                raise ValueError("topic IDs must be unique across the plan")
            topic_ids.add(topic["id"])
            _refs(topic["evidence_ids"], pool, "topic evidence_ids", required=True)
        by_id[row["id"]], by_key[key] = row, row
    if set(base) - set(by_key):
        raise ValueError("plan must retain every current entity and entry")
    for row in items:
        if row["disposition"] == "merge":
            target = by_id.get(row["merge_target"])
            if target is None or target is row or target["disposition"] not in {"standalone", "stub"}:
                raise ValueError("merge requires a different independent target; chains and cycles are invalid")


def _validate_relations(relations, items, paragraphs):
    """Relations place explicit labeled links, never aliases or inferred inverses."""
    if not isinstance(relations, list):
        raise ValueError("relations must be an array")
    by_id = {row["id"]: row for row in items}
    pool, seen = {row["id"] for row in paragraphs}, set()
    for relation in relations:
        if not isinstance(relation, dict) or set(relation) != RELATION_FIELDS:
            raise ValueError("invalid relation fields")
        _text(relation["source"], "relation source")
        _text(relation["target"], "relation target")
        _text(relation["label"], "relation label", 30)
        source = by_id.get(relation["source"])
        target = by_id.get(relation["target"])
        if source is None or target is None or source is target:
            raise ValueError("relations require two different existing plan IDs")
        if target["disposition"] not in {"standalone", "stub"}:
            raise ValueError("relation targets must be independent pages")
        _refs(relation["evidence_ids"], pool, "relation evidence_ids", required=True)
        key = relation["source"], relation["target"], relation["label"]
        if key in seen:
            raise ValueError("duplicate directed relation")
        seen.add(key)


def _committed_relations(entries, chapters):
    """Retain the published graph unless an editor explicitly replaces it.

    Local source numbers belong to an individual article. Only the frozen
    evidence IDs can be carried into a new complete plan.
    """
    relations, seen = [], set()
    for entry in entries["entries"]:
        edges = entry.get("relations", [])
        if not isinstance(edges, list):
            raise ValueError("committed relations must be an array; provide explicit decisions.relations")
        for edge in edges:
            if not isinstance(edge, dict):
                raise ValueError("invalid committed relation; provide explicit decisions.relations")
            relation = {"source": entry["id"],
                        **{field: copy.deepcopy(edge.get(field))
                           for field in ("target", "label", "evidence_ids")}}
            key = canonical(relation)
            if key not in seen:
                seen.add(key)
                relations.append(relation)
    meta = entries.get("meta", {})
    if relations and (not isinstance(meta, dict)
                      or meta.get("evidence_hash") != digest(chapters)):
        raise ValueError("committed relation evidence is stale or unconfirmed; review and provide explicit decisions.relations")
    return relations


def _restore_items(saved, items, work, source_hash, removed):
    if saved is None:
        return items
    if (not isinstance(saved,dict) or saved.get("version")!=1 or saved.get("work")!=work
            or not isinstance(saved.get("items"),list)
            or saved.get("plan_hash")!=digest({k:v for k,v in saved.items() if k!="plan_hash"})):
        raise ValueError("invalid saved entry plan; provide a valid --base-plan")
    if saved.get("source_hash")!=source_hash:
        return items
    by_key={(r["kind"],r["name"]):r for r in items}
    for row in saved["items"]:
        key=row["kind"],row["name"]
        if row["id"] in removed:
            if key in by_key:
                by_key[key].update(disposition="review",depth="none",reason="已有页面被明确删除，保留候选待重新确认。",topics=[],merge_target=None)
            continue
        if key in by_key:
            if row["id"]==by_key[key]["id"]:by_key[key]=copy.deepcopy(row)
        elif row["kind"] in EXPLICIT_KINDS:
            by_key[key]=copy.deepcopy(row)
    return list(by_key.values())


def build(store, work, selected=None, decisions=None, base_plan=None):
    """Build a complete plan, applying only bounded explicit editorial decisions."""
    chapters, tables, entries, stored_plan, history = _load(store, work)
    indices, paragraphs = _selection(chapters, selected)
    items = _base_items(work, tables, entries, paragraphs)
    base_items = copy.deepcopy(items)
    source_hash=digest({"chapters":chapters,"tables":tables})
    saved_plan=base_plan if base_plan is not None else stored_plan
    if base_plan is not None and (not isinstance(base_plan,dict) or base_plan.get("source_hash")!=source_hash):
        raise ValueError("base plan source_hash is stale; review its decisions against current sources")
    published={e["id"] for e in entries["entries"]}
    items=_restore_items(saved_plan,items,work,source_hash,history-published)
    by_key = {(row["kind"], row["name"]): row for row in items}
    relations = []
    if decisions is not None:
        if (not isinstance(decisions, dict) or set(decisions) - {"items", "relations"}
                or not isinstance(decisions.get("items", []), list)):
            raise ValueError("decisions accept only items and relations arrays")
        if "relations" in decisions:
            relations = copy.deepcopy(decisions["relations"])
        seen = set()
        for decision in decisions.get("items", []):
            if (not isinstance(decision, dict) or not {"kind", "name", "reason"} <= set(decision)
                    or set(decision) - (EDIT_FIELDS | {"kind", "name"})):
                raise ValueError("invalid decision fields; a short reason is required")
            _text(decision["name"], "decision name")
            _text(decision["reason"], "short decision reason", 200)
            if "topics" in decision and "evidence_ids" not in decision:
                # Topic selection replaces the broad literal-name locator pool.
                # Explicit item evidence always takes priority, including [].
                topics = decision["topics"]
                if not isinstance(topics, list):
                    raise ValueError("topics must be an array")
                evidence_ids = []
                pool = {paragraph["id"] for paragraph in paragraphs}
                for topic in topics:
                    if not isinstance(topic, dict):
                        raise ValueError("invalid topic fields")
                    refs = topic.get("evidence_ids")
                    _refs(refs, pool, "topic evidence_ids")
                    for ref in refs:
                        if ref not in evidence_ids:
                            evidence_ids.append(ref)
                decision = dict(decision, evidence_ids=evidence_ids)
            kind = decision["kind"]
            if not isinstance(kind, str) or kind not in KINDS:
                raise ValueError("invalid decision kind")
            key = kind, decision["name"]
            if key in seen:
                raise ValueError("duplicate decision identity")
            seen.add(key)
            row = by_key.get(key)
            if row is None:
                if kind not in EXPLICIT_KINDS or not decision.get("evidence_ids"):
                    raise ValueError("new events and concepts require explicit evidence_ids")
                row = _item(work, kind, decision["name"], {}, None, paragraphs)
                items.append(row)
                by_key[key] = row
            for field in EDIT_FIELDS & set(decision):
                row[field] = copy.deepcopy(decision[field])
            if "importance" in decision:
                row["importance_source"] = "decision"
    if decisions is None or "relations" not in decisions:
        relations = _committed_relations(entries, chapters)
        if saved_plan is not None and saved_plan.get("source_hash")==source_hash:
            committed={canonical(edge):edge for edge in relations}
            ordered=[];seen=set()
            for edge in saved_plan.get("relations",[]):
                key=canonical(edge)
                if edge["source"] not in history or key in committed:
                    ordered.append(copy.deepcopy(edge));seen.add(key)
            ordered.extend(edge for key,edge in committed.items() if key not in seen)
            relations=ordered
        ids={row["id"] for row in items}
        if any(edge["source"] not in ids or edge["target"] not in ids for edge in relations):
            raise ValueError("unpublished relation candidates need the original plan; use --base-plan or explicit items/relations decisions")
    _validate_items(items, base_items, work, paragraphs)
    _validate_relations(relations, items, paragraphs)
    plan = {"version": 1, "work": work, "scope": _scope(chapters, indices),
            "source_hash": source_hash,
            "selected_chapters": indices, "items": items, "relations": relations}
    plan["plan_hash"] = digest(plan)
    return plan


def validate(plan, store, work):
    """Reject stale, incomplete, malformed or identity-conflicting plans, without writes."""
    if (not isinstance(plan, dict) or not PLAN_FIELDS <= set(plan)
            or set(plan) - (PLAN_FIELDS | {"relations"})):
        raise ValueError("invalid plan fields")
    if type(plan["version"]) is not int or plan["version"] != 1 or plan["work"] != work:
        raise ValueError("plan version or work mismatch")
    try:
        actual_hash = digest({key: value for key, value in plan.items() if key != "plan_hash"})
    except (TypeError, ValueError):
        raise ValueError("plan is not valid JSON") from None
    if plan["plan_hash"] != actual_hash:
        raise ValueError("plan_hash mismatch")
    chapters, tables, entries, _, _ = _load(store, work)
    if plan["source_hash"] != digest({"chapters": chapters, "tables": tables}):
        raise ValueError("source_hash is stale; rebuild the plan against current sources")
    indices, paragraphs = _selection(chapters, plan["selected_chapters"])
    if plan["selected_chapters"] != indices or plan["scope"] != _scope(chapters, indices):
        raise ValueError("plan scope or selected_chapters mismatch")
    base_items = _base_items(work, tables, entries, paragraphs)
    _validate_items(plan["items"], base_items, work, paragraphs)
    _validate_relations(plan.get("relations", []), plan["items"], paragraphs)


def inspect(plan, offset=0, limit=8):
    """Return at most twelve compact metadata rows; never reasons, prose or quotes."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 12:
        raise ValueError("invalid plan listing range (limit 1–12)")
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list):
        raise ValueError("invalid plan items")
    if not isinstance(plan.get("relations", []), list):
        raise ValueError("invalid plan relations")
    items = plan["items"]
    counts = Counter(row["disposition"] for row in items)
    result = {"total": len(items), "offset": offset,
              "relationship_count": len(plan.get("relations", [])),
              "counts": {key: counts[key] for key in sorted(DISPOSITIONS)},
              "items": [{key: (str(row[key])[:40] if key == "id" else str(row[key])[:24])
                         for key in ("id", "kind", "name", "importance", "disposition", "depth")}
                        for row in items[offset:offset + limit]],
              "next_offset": None}
    # Existing IDs and names may contain characters escaped as surrogate pairs.
    # Reduce only the metadata page size, retaining the correct continuation.
    while result["items"]:
        next_offset = offset + len(result["items"])
        result["next_offset"] = next_offset if next_offset < len(items) else None
        if len(json.dumps(result)) <= 6000 and len(canonical(result)) <= 6000:
            break
        result["items"].pop()
    return result
