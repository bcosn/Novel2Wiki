"""Explicit, evidence-backed entry relations; no taxonomy inference."""
from __future__ import annotations


def relation_errors(value, prefix):
    errors = []
    if not isinstance(value, list):
        return [prefix + "/relations requires an array"]
    for index, relation in enumerate(value, 1):
        path = "%s/relations[%d]" % (prefix, index)
        if not isinstance(relation, dict):
            errors.append(path + " requires an object")
            continue
        target = relation.get("target")
        if not isinstance(target, str) or not target.strip():
            errors.append(path + "/target requires a nonempty entry ID")
        label = relation.get("label")
        if (not isinstance(label, str) or not label.strip()
                or any(character in label for character in "\r\n\u2028\u2029")):
            errors.append(path + "/label requires nonempty single-line text")
        refs = relation.get("evidence_ids")
        if (not isinstance(refs, list) or not refs
                or any(not isinstance(ref, str) or not ref.strip() for ref in refs)):
            errors.append(path + "/evidence_ids requires nonempty evidence IDs")
        if "refs" in relation:
            numbered = relation["refs"]
            if (not isinstance(numbered, list)
                    or any(not ((type(ref) is int and ref > 0)
                               or (isinstance(ref, str) and ref.strip())) for ref in numbered)):
                errors.append(path + "/refs requires positive source numbers or evidence IDs")
    return errors


def published_relations(entry, by_id):
    """Return safe relations and existing targets, without inventing missing pages.

    Pending target IDs are preserved in the document for a later publication.
    They are not aliases and do not participate in name-based prose linkifying.
    """
    relations = entry.get("relations")
    if not isinstance(relations, list):
        return []
    result = []
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        target, label = relation.get("target"), relation.get("label")
        if (isinstance(target, str) and target in by_id
                and target != entry.get("id")
                and isinstance(label, str) and label.strip()
                and not any(character in label for character in "\r\n\u2028\u2029")):
            result.append((relation, by_id[target]))
    return result


def published_refs(relation, source_count):
    """Only numeric, in-range local references can become HTML anchors."""
    refs = relation.get("refs")
    if not isinstance(refs, list):
        return []
    return [ref for ref in refs if type(ref) is int and 1 <= ref <= source_count]
