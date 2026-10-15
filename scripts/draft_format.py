"""Opt-in short draft input. Only deterministic framing and empty defaults."""
from __future__ import annotations
import copy

FORMAT = "compact-v1"
KINDS = {"character": "人物", "place": "地点", "object": "物品", "event": "事件", "race": "种族"}

class DraftFormatErrors(ValueError):
    def __init__(self, errors):
        self.errors=errors
        super().__init__("; ".join(errors[:3]))


def expand(doc, chapters, work):
    value = copy.deepcopy(doc)
    meta = value.get("meta", {})
    mode = meta.get("draft_format")
    if mode is None:
        return value
    if mode != FORMAT:
        raise ValueError("unsupported draft_format")
    title = chapters.get("meta", {}).get("title") or work
    author = chapters.get("meta", {}).get("creator") or ""
    if not isinstance(title, str) or not isinstance(author, str):
        raise ValueError("frozen title and creator must be text")
    if meta.get("book", work) != work or ("author" in meta and meta["author"] != author):
        raise ValueError("compact draft metadata conflicts with frozen work/author")
    meta.update(book=work, author=author)
    errors=[]
    for index,e in enumerate(value.get("entries", []),1):
        if not isinstance(e, dict):
            continue  # The shared shape validator provides the entry-local diagnostic.
        entry_id = e.get("id") if isinstance(e.get("id"),str) and e["id"].strip() else "entry[%d]" % index
        facts = e.pop("lead_facts", None)
        if not isinstance(facts, str) or not facts.strip():
            errors.append("%s/lead_facts requires nonempty factual prose" % entry_id)
            continue
        if "lead" in e:
            errors.append("%s/lead conflicts with compact lead_facts" % entry_id)
            continue
        kind, name = e.get("kind"), e.get("name")
        if not isinstance(kind,str) or kind not in KINDS or not isinstance(name, str) or not name.strip():
            errors.append("%s/identity requires a valid kind and name" % entry_id)
            continue
        # This sentence conveys trusted bibliography and the explicitly selected kind.
        # It supplies no plot, relationship, motivation or inferred identity.
        e["lead"] = "%s是%s《%s》中的%s。%s" % (name, author, title, KINDS[kind], facts.strip())
        for key, default in {"aliases": [], "sources": [], "links": [], "stub": False,
                             "reception": {"in_work": "", "user": "", "external": ""}}.items():
            e.setdefault(key, default)
    if errors:
        raise DraftFormatErrors(errors)
    # The canonical document is the existing expanded format, so resume/old renderers
    # never need to reinterpret a compact draft or generate its framing twice.
    meta.pop("draft_format")
    return value


def template(work, author, scope, evidence_hash, entries):
    if not 1 <= len(entries) <= 3:
        raise ValueError("a draft template requires 1–3 explicitly selected entries")
    ids, names, rows = set(), set(), []
    for kind, entry_id, name in entries:
        if kind not in KINDS or not entry_id.strip() or not name.strip():
            raise ValueError("template entry requires kind, id and name")
        if entry_id in ids or name in names:
            raise ValueError("duplicate template entry id or name")
        ids.add(entry_id); names.add(name)
        rows.append({"id": entry_id, "kind": kind, "name": name, "lead_facts": "",
                     "lead_refs": [], "sections": [], "links": []})
    return {"meta": {"book": work, "author": author, "scope": scope,
                     "evidence_hash": evidence_hash, "draft_format": FORMAT}, "entries": rows}
