"""Aggregate draft shape errors without quoting entry prose."""
from __future__ import annotations
from entry_relations import relation_errors


KINDS = frozenset({"character", "place", "object", "event", "race", "concept"})


def shape_errors(doc, check_links=True) -> list[str]:
    """Return all structural errors; partial batches may defer link resolution.

    Named evidence references and positive, one-based source numbers are both
    accepted here. Citation existence and numeric bounds belong to the frozen
    evidence validator after materialization.
    """
    if not isinstance(doc, dict):
        return ["document requires an object"]
    errors = []
    if "meta" in doc and not isinstance(doc["meta"], dict):
        errors.append("document/meta requires an object")
    rows = doc.get("entries")
    if not isinstance(rows, list):
        return errors + ["document/entries requires an array"]

    ids, names, links = {}, {}, []

    def refs_errors(value, label):
        if not isinstance(value, list):
            errors.append(label + " requires an array")
            return
        for index, ref in enumerate(value, 1):
            if not ((type(ref) is int and ref > 0)
                    or (isinstance(ref, str) and bool(ref.strip()))):
                errors.append("%s[%d] requires a positive source number or evidence ID" % (label, index))

    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            errors.append("entry[%d] requires an object" % index)
            continue
        entry_id = row.get("id")
        valid_id = isinstance(entry_id, str) and bool(entry_id.strip())
        prefix = entry_id if valid_id else "entry[%d]" % index
        if not valid_id:
            errors.append(prefix + "/id requires nonempty text")
        elif entry_id in ids:
            errors.append("%s/id duplicates entry[%d]" % (prefix, ids[entry_id]))
        else:
            ids[entry_id] = index
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(prefix + "/name requires nonempty text")
        elif name in names:
            errors.append("%s/name duplicates entry[%d]" % (prefix, names[name]))
        else:
            names[name] = index
        if not isinstance(row.get("lead"), str) or not row["lead"].strip():
            errors.append(prefix + "/lead requires nonempty text")
        if not isinstance(row.get("kind"), str) or row["kind"] not in KINDS:
            errors.append(prefix + "/kind requires character, place, object, event, race or concept")
        if type(row.get("stub")) is not bool:
            errors.append(prefix + "/stub requires a boolean")
        reception = row.get("reception")
        if not isinstance(reception, dict):
            errors.append(prefix + "/reception requires a text object")
        elif any(not isinstance(value, str) for value in reception.values()):
            errors.append(prefix + "/reception values must be text")

        for field in ("sections", "sources", "links", "aliases"):
            required = field != "aliases"
            value = row.get(field, [])
            if required and field not in row:
                errors.append(prefix + "/" + field + " is required")
                continue
            if not isinstance(value, list):
                errors.append(prefix + "/" + field + " requires an array")
                continue
            for position, item in enumerate(value, 1):
                label = "%s/%s[%d]" % (prefix, field, position)
                if field in ("links", "aliases"):
                    if not isinstance(item, str):
                        errors.append(label + " requires text")
                    elif field == "links":
                        if not item.strip():
                            errors.append(label + " requires a nonempty target ID")
                        else:
                            links.append((label, item))
                    continue
                if not isinstance(item, dict):
                    errors.append(label + " requires an object")
                    continue
                if field == "sections":
                    for text_field in ("body", "heading"):
                        if not isinstance(item.get(text_field, ""), str):
                            errors.append(label + "/" + text_field + " requires text")
                    if "refs" in item:
                        refs_errors(item["refs"], label + "/refs")
                elif not isinstance(item.get("quote", ""), str):
                    errors.append(label + "/quote requires text")
        if "lead_refs" in row:
            refs_errors(row["lead_refs"], prefix + "/lead_refs")
        if "relations" in row:
            errors.extend(relation_errors(row["relations"], prefix))

    if check_links:
        for label, target in links:
            if target not in ids:
                errors.append(label + " has no target in the draft")
    return errors
