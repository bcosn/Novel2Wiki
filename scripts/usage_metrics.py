"""Explicit usage accounting; no estimated hits are reported as actual cache use."""
import json
from core_store import canonical, now

COUNTERS = ("uncached_input_tokens", "cached_input_tokens", "output_tokens", "tool_calls", "material_chars")

def record_usage(store, work, event):
    allowed = {"id", "stage", "agent", *COUNTERS}
    if not isinstance(event, dict) or set(event) - allowed or not isinstance(event.get("id"), str) or not event["id"] or len(event["id"]) > 160:
        raise ValueError("invalid usage event")
    if any(type(event.get(k, 0)) is not int or event.get(k, 0) < 0 for k in COUNTERS):
        raise ValueError("usage counters must be nonnegative integers")
    payload = canonical(event)
    with store.transaction():
        store.ensure_work(work)
        previous = store.connection.execute("SELECT work_id,payload FROM usage WHERE id=?", (event["id"],)).fetchone()
        if previous:
            if previous["work_id"] != work or previous["payload"] != payload:
                raise ValueError("usage ID conflicts")
            return {"status": "reused", "id": event["id"]}
        store.connection.execute("INSERT INTO usage VALUES(?,?,?,?)", (event["id"], work, payload, now()))
    return {"status": "recorded", "id": event["id"]}

def usage_summary(store, work):
    events = [json.loads(r[0]) for r in store.connection.execute("SELECT payload FROM usage WHERE work_id=?", (work,))]
    result = {k: sum(e.get(k, 0) for e in events) for k in COUNTERS}
    inputs = result["cached_input_tokens"] + result["uncached_input_tokens"]
    result.update(events=len(events), cache_read_ratio=result["cached_input_tokens"] / inputs if inputs else None,
                  input_tokens=inputs, note="Only supplied measured events; missing usage is not treated as zero cost.")
    return result
