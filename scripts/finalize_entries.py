"""Validate, commit, export and render a draft in one deterministic operation."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path
from core_store import digest, validate_document, write_json
from quote_source_check import check as check_quotes
from check_voice import check as check_voice
from verify_example import TABLES, table_errors, site_errors


def finalize(store, work, inputs, directory, report, expected_hash=None):
    directory, report = Path(directory).resolve(), Path(report).resolve()
    protected = {store.path, *(Path(p).resolve() for p in inputs)}
    exports = {directory / "data" / (work + "." + k + ".json") for k in TABLES + ["chapters", "entries"]}
    site = directory / "site"
    if report in protected | exports or site == report or site in report.parents or directory == store.path or exports & protected or any(site == p or site in p.parents for p in protected):
        raise ValueError("outputs cannot overwrite the database or batch inputs")
    batches = []
    for path in inputs:
        p = Path(path)
        if p.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("batch exceeds 16 MiB")
        value = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
            raise ValueError("batch requires an entries array")
        if not isinstance(value.get("meta", {}), dict) or value.get("meta", {}).get("book", work) != work:
            raise ValueError("batch belongs to another work")
        batches.append(value)
    if not batches:
        raise ValueError("no batches")
    for key in ("scope", "author"):
        if len({batch.get("meta", {}).get(key) for batch in batches}) > 1:
            raise ValueError("batch metadata differs: " + key)
    entries = [entry for batch in batches for entry in batch["entries"]]
    if not entries:
        raise ValueError("empty draft")
    doc = {"meta": dict(batches[0].get("meta", {})), "entries": entries}
    doc["meta"].update(book=work, entry_count=len(entries))
    errors, hints = [], []
    try:
        validate_document("entries", doc)
        for e in entries:
            if any(k not in e for k in ("lead", "sections", "sources", "reception", "links", "stub")):
                raise ValueError("entry missing required fields")
            if not isinstance(e["lead"], str) or not e["lead"].strip():
                raise ValueError("entry requires a nonempty lead")
            if not isinstance(e["reception"], dict):
                raise ValueError("entry reception must be an object")
            if any(not isinstance(s, dict) or not isinstance(s.get("body", ""), str) for s in e["sections"]):
                raise ValueError("sections must contain objects with string bodies")
            if any(not isinstance(s, dict) or not isinstance(s.get("quote", ""), str) for s in e["sources"]):
                raise ValueError("sources must contain objects with string quotes")
        if len({e["name"] for e in entries}) != len(entries):
            raise ValueError("duplicate entry names")
        doc["entries"].sort(key=lambda e: e["id"])
        doc["meta"]["by_kind"] = dict(Counter(e["kind"] for e in entries))
    except ValueError as exc:
        errors.append(str(exc))
    with store.transaction():
        chapters = store.get_document(work, "chapters")
        tables = {k: store.get_document(work, k) for k in TABLES}
        if chapters is None or any(v is None for v in tables.values()):
            errors.append("frozen chapters and all eight tables are required")
        elif not errors:
            errors.extend(table_errors(tables))
            quote_errors, _ = check_quotes(doc, chapters)
            voice_errors, hints = check_voice(doc)
            errors.extend(quote_errors + voice_errors)
        if not errors:
            previous = store.get_document(work, "entries")
            actual = digest(previous) if previous is not None else None
            if expected_hash is not None and expected_hash != actual:
                raise ValueError("entry version changed; refresh expected hash")
            if previous is not None and expected_hash is None and digest(doc) != actual:
                raise ValueError("changed draft requires --expected-hash from workspace status")
            store.put_document(work, "entries", doc)
    if not errors:
        # Export this validated snapshot; failures can be retried without regenerating prose.
        for kind, value in dict(tables, chapters=chapters, entries=doc).items():
            write_json(directory / "data" / (work + "." + kind + ".json"), value)
        import local_uploads
        import render_wiki
        overlay = local_uploads.export_overlay(store, work, directory / "site")
        render_wiki.render(doc, directory / "site", work=work, unit="章", overlay=overlay)
        errors.extend(site_errors(directory / "site"))
    result = {"status": "invalid" if errors else "completed", "work": work,
              "entries": len(entries), "error_count": len(errors), "hint_count": len(hints),
              "errors": errors, "hints": hints, "entries_hash": digest(doc)}
    write_json(report, result)
    # Messages and stdout contain counts and locations, never whole drafts or evidence.
    return {k: result[k] for k in ("status", "work", "entries", "error_count", "hint_count", "entries_hash")} | {
        "report": str(report), "output": str(directory),
        "error_preview": [e[:160] for e in errors[:3]]}
