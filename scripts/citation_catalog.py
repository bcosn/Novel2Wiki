"""Frozen paragraph references; copies evidence without guessing or paraphrasing."""
from __future__ import annotations
import copy
import re
from core_store import digest


class CitationErrors(ValueError):
    def __init__(self, errors):
        self.errors=errors
        super().__init__("%d citation errors: %s" % (len(errors), "; ".join(errors[:3])))


def catalog(chapters, selected=None):
    if not isinstance(chapters, dict) or not chapters.get("chapters"):
        raise ValueError("frozen chapters required")
    ids, rows = set(), []
    for order, chapter in enumerate(chapters["chapters"], 1):
        index = chapter.get("index")
        if type(index) is not int or index < 1 or index in ids:
            raise ValueError("citation catalog requires unique positive chapter indices")
        ids.add(index)
        content = chapter.get("content")
        if not isinstance(content, str):
            raise ValueError("chapter content must be text")
        if selected and index not in selected:
            continue
        for paragraph, text in enumerate(re.split(r"\n\s*\n", content), 1):
            if text.strip():
                rows.append({"id": "c%dp%d" % (order, paragraph), "chapter": index,
                             "paragraph": paragraph, "text": text.strip()})
    if selected and set(selected) - ids:
        raise ValueError("selected chapter does not exist")
    return {"version": 1, "evidence_hash": digest(chapters), "paragraphs": rows}


def materialize(doc, chapters):
    value = copy.deepcopy(doc)
    frozen_hash = digest(chapters)
    declared = value.get("meta", {}).get("evidence_hash")
    if declared is not None and declared != frozen_hash:
        raise ValueError("evidence_hash is stale; prepare the current draft context")
    for entry in value.get("entries", []):
        relations=entry.get("relations",[])
        for relation in relations:
            relation.setdefault("refs",copy.deepcopy(relation.get("evidence_ids",[])))
        fields=[(entry,"lead_refs")]+[(s,"refs") for s in entry.get("sections",[])]+[(r,"refs") for r in relations]
        named=[r for obj,key in fields for r in (obj.get(key,[]) if isinstance(obj.get(key,[]),list) else []) if isinstance(r,str)]
        if not named:
            continue
        if declared!=frozen_hash:
            raise ValueError("evidence refs require meta.evidence_hash from prepare-draft")
        sources=entry.setdefault("sources",[])
        mapping={}
        for index,source in enumerate(sources,1):
            if "evidence_id" in source:
                key=source["evidence_id"]
                if not isinstance(key,str) or key in mapping:
                    raise ValueError("%s/sources duplicate or invalid evidence_id for named refs" % entry.get("id","?"))
                mapping[key]=index
        for ref in named:
            if ref not in mapping:
                sources.append({"evidence_id":ref});mapping[ref]=len(sources)
        for obj,key in fields:
            if isinstance(obj.get(key),list):
                obj[key]=[mapping[r] if isinstance(r,str) else r for r in obj[key]]
    refs = [(e.get("id","?"),i,s) for e in value.get("entries", []) for i,s in enumerate(e.get("sources", []),1) if isinstance(s, dict) and "evidence_id" in s]
    if not refs:
        return value
    if declared != frozen_hash:
        raise ValueError("evidence_id requires meta.evidence_hash from prepare-draft")
    pool = {r["id"]: r for r in catalog(chapters)["paragraphs"]}
    errors=[]
    for entry_id,index,source in refs:
        try:
            key = source["evidence_id"]
            if not isinstance(key, str) or key not in pool:
                raise ValueError("unknown evidence_id")
            row = pool[key]
            text = row["text"]
            start, end = source.get("start", 0), source.get("end", len(text))
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                raise ValueError("evidence offsets must select a nonempty range within one paragraph")
            quote = text[start:end]
            if "chapter" in source and (type(source["chapter"]) is not int or source["chapter"] != row["chapter"]):
                raise ValueError("chapter conflicts with evidence_id")
            if "quote" in source and source["quote"] != quote:
                raise ValueError("quote conflicts with frozen evidence; no automatic correction")
            source.update(chapter=row["chapter"], quote=quote)
            source.setdefault("cite", "第%d章" % row["chapter"])
        except ValueError as exc:
            errors.append("%s/source[%d] %s" % (entry_id,index,exc))
    if errors:
        raise CitationErrors(errors)
    return value
