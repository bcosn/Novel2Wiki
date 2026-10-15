"""Apply an explicit editorial outline without inventing article content."""
from __future__ import annotations
import copy
import re

CAPS={"expanded":6,"standard":4,"brief":1,"none":0}
GUIDE="""\n本批有已确定的条目计划，以editorial中的去向、深度和主题为写作边界，importance不是原作事实。
只写editorial.articles列出的对象。main/major可展开有据主题，minor通常短写；重要性不代表必须写满篇幅。
导语只给作者/作品/有据身份及必要概括。正文严格按topics分工，同一具体事实只在一个主题展开；主题内可保留必要时序，不按原段逐节翻写。
sections每节填topic_id、body、refs；heading由程序按计划补齐，不改标题、不另造主题、不漏既定主题。引用仅用该主题允许的证据ID。
短条可只有导语；stub按计划填写，不补背景或性格来填结构。相同段落不能在两个小节重复。
短写仍保留身份与有区分度的核心行为，不能只剩姓名和泛泛身份；可在导语用一两句写清。标题概括主体有据的主题，参与某事不等于执行其中每个动作。
身份通常留导语；有据主题不按时间、场景或单个动作机械拆节。标题不能把人物相信的用途、疗效或评价改成客观性质。
merge_mentions保留原主体，简要放到目标条目的合适主题并挂其来源；并入主题不等于同一实体，不放进aliases，不把次要人物冒充主要人物。
不为计划中的review/table_only对象写新页。未决对象仍在计划和原表中，不能声称全量完成。
meta.entry_plan_hash保留editorial.plan_hash。finalize必须带--plan对应计划文件，程序检查结构与去向；事实和语义仍须核对。
种族和设定页的身份、范围、层级和成员关系已在规划阶段按证据判断，不按固定词表补全。relations由程序复制有据关系与引用；不额外猜反向关系或当前所属。
"""


def select(plan, ids):
    if (not isinstance(ids,(list,tuple)) or not 1<=len(ids)<=3
            or any(not isinstance(key,str) or not key.strip() for key in ids)
            or len(set(ids))!=len(ids)):
        raise ValueError("select 1–3 distinct --plan-entry IDs")
    pool={r["id"]:r for r in plan["items"]}
    titles={}
    for item in plan["items"]:
        if item["disposition"] not in ("standalone","stub"):continue
        if item["name"] in titles:
            raise ValueError("independent page titles conflict; disambiguate before drafting: "+item["name"])
        titles[item["name"]]=item["id"]
    rows=[]
    for key in ids:
        if key not in pool:
            raise ValueError("planned entry not found: "+key)
        r=pool[key]
        if r["disposition"] not in ("standalone","stub"):
            raise ValueError("%s disposition is %s; decide its placement before drafting" % (key,r["disposition"]))
        topics=r.get("topics",[])
        if r["depth"] not in CAPS or len(topics)>CAPS[r["depth"]]:
            raise ValueError(key+" outline exceeds its chosen depth")
        if r["disposition"]=="standalone" and not topics:
            raise ValueError(key+" needs evidence-backed topics before drafting; use stub for a lead-only entry")
        row={k:copy.deepcopy(r[k]) for k in ("id","kind","name","importance","disposition","depth","reason","evidence_ids","topics")}
        row["merge_mentions"]=[{k:copy.deepcopy(m[k]) for k in ("id","kind","name","reason","evidence_ids")} for m in plan["items"] if m["disposition"]=="merge" and m.get("merge_target")==key]
        names={p["id"]:p["name"] for p in plan["items"]}
        row["relations"]=[dict(copy.deepcopy(edge),target_name=names[edge["target"]]) for edge in plan.get("relations",[]) if edge["source"]==key]
        rows.append(row)
    return {"plan_hash":plan["plan_hash"],"scope":plan["scope"],"articles":rows}


def _relations(plan,key):
    return [{"target":r["target"],"label":r["label"],"evidence_ids":copy.deepcopy(r["evidence_ids"]),
             "refs":copy.deepcopy(r["evidence_ids"])} for r in plan.get("relations",[]) if r["source"]==key]


def sync_relations(doc,plan):
    """Refresh program-owned graph edges, including retained prose on update."""
    value=copy.deepcopy(doc)
    for e in value["entries"]:
        if isinstance(e,dict) and isinstance(e.get("id"),str):
            expected=_relations(plan,e["id"])
            if expected or "relations" in e:e["relations"]=expected
    return value


def apply(doc, plan):
    value=copy.deepcopy(doc)
    if value.get("meta",{}).get("entry_plan_hash")!=plan["plan_hash"]:
        raise ValueError("draft requires meta.entry_plan_hash from its prepared context")
    pool={r["id"]:r for r in plan["items"]}
    errors=[]
    for e in value.get("entries",[]):
        if not isinstance(e,dict):
            continue
        key=e.get("id","?");r=pool.get(key) if isinstance(key,str) else None
        if r is None or r["disposition"] not in ("standalone","stub"):
            errors.append(str(key)+"/plan entry is not approved for an independent page");continue
        if e.get("name")!=r["name"] or e.get("kind")!=r["kind"]:
            errors.append(str(key)+"/identity conflicts with entry plan");continue
        try:select(plan,[key])
        except ValueError as exc:errors.append(str(exc));continue
        if e.get("stub") is not (r["disposition"]=="stub"):
            errors.append(key+"/stub conflicts with entry plan")
        expected=_relations(plan,key)
        if "relations" in e:
            raw=e["relations"]
            fields=("target","label","evidence_ids")
            if (not isinstance(raw,list) or any(not isinstance(v,dict) for v in raw)
                    or [{f:v.get(f) for f in fields} for v in raw] != [{f:v[f] for f in fields} for v in expected]):
                errors.append(key+"/relations conflict with explicit entry plan")
        if expected or "relations" in e:e["relations"]=expected
        topics={t["id"]:t for t in r["topics"]};seen=set()
        if not isinstance(e.get("sections"),list):continue
        for section in e["sections"]:
            if not isinstance(section,dict):continue
            topic=section.get("topic_id")
            if not isinstance(topic,str) or topic not in topics:
                errors.append(key+"/sections unknown or missing topic_id");continue
            if topic in seen:errors.append(key+"/sections repeated topic_id: "+topic)
            seen.add(topic)
            expected=topics[topic]["heading"]
            if section.get("heading") not in (None,"",expected):
                errors.append(key+"/sections heading conflicts with planned topic: "+topic)
            section["heading"]=expected
            if not isinstance(section.get("body"),str) or not section["body"].strip():
                errors.append(key+"/sections planned topic requires nonempty body: "+topic)
        if set(topics)-seen:
            errors.append(key+"/sections missing planned topics: "+",".join(sorted(set(topics)-seen)))
    if errors:raise StructureErrors(errors)
    return value


class StructureErrors(ValueError):
    def __init__(self,errors):
        self.errors=errors
        super().__init__("; ".join(errors[:3]))


def check(doc,plan):
    """Mechanical checks only: topic reference ownership and explicit placement."""
    pool={r["id"]:r for r in plan["items"]};errors=[]
    for e in doc.get("entries",[]):
        r=pool[e["id"]];key=e["id"]
        def evidence_refs(nums):
            return {e["sources"][n-1].get("evidence_id") for n in nums if type(n) is int and 0<n<=len(e["sources"])}
        topics={t["id"]:t for t in r["topics"]}
        paragraphs={}
        for section in e["sections"]:
            topic=topics[section["topic_id"]]
            actual=evidence_refs(section.get("refs",[]))
            if None in actual or not actual or actual-set(topic["evidence_ids"]):
                errors.append(key+"/sections evidence falls outside planned topic: "+topic["id"])
            for raw in re.split(r"\n\s*\n",section.get("body","")):
                paragraph=re.sub(r"\s+","",raw)
                if len(paragraph)>=40 and paragraph in paragraphs:
                    errors.append(key+"/sections same factual paragraph repeated across topics")
                paragraphs[paragraph]=topic["id"]
        allowed=set(r["evidence_ids"])|{v for t in r["topics"] for v in t["evidence_ids"]}
        actual=evidence_refs(e.get("lead_refs",[]))
        if None in actual or not actual or actual-allowed:
            errors.append(key+"/lead_refs require planned evidence")
        for edge in e.get("relations",[]):
            if evidence_refs(edge.get("refs",[]))!=set(edge["evidence_ids"]):
                errors.append(key+"/relations require their own planned evidence")
        texts=[(e.get("lead",""),evidence_refs(e.get("lead_refs",[])))]+[(s.get("body",""),evidence_refs(s.get("refs",[]))) for s in e["sections"]]
        for child in plan["items"]:
            if child["disposition"]=="merge" and child.get("merge_target")==key:
                if child["name"] in e.get("aliases",[]):
                    errors.append(key+"/aliases a merged subject is not an alias: "+child["name"])
                if not any(child["name"] in text and refs&set(child["evidence_ids"]) for text,refs in texts):
                    errors.append(key+"/merge missing named subject with its evidence: "+child["name"])
    return errors


def status(plan,doc):
    current={e["id"] for e in doc["entries"]}
    expected={r["id"] for r in plan["items"] if r["disposition"] in ("standalone","stub")}
    pending=[r["id"] for r in plan["items"] if r["disposition"]=="review"]
    unverified=[]
    for e in doc["entries"]:
        if e["id"] not in expected:continue
        sample={"meta":{"entry_plan_hash":plan["plan_hash"]},"entries":[e]}
        try:
            apply(sample,plan)
            # Stored entries already contain local numeric reference numbers;
            # applying an outline seeds named relation refs for new drafts.
            if check(sample,plan):unverified.append(e["id"])
        except (ValueError,KeyError,TypeError):unverified.append(e["id"])
    return {"plan_hash":plan["plan_hash"],"planned_pages":len(expected),"present_pages":len(current&expected),
            "missing_page_count":len(expected-current),"missing_page_ids":sorted(expected-current)[:12],
            "unresolved_count":len(pending),"unresolved_ids":pending[:12],
            "unverified_page_count":len(unverified),"unverified_page_ids":unverified[:12],
            "retained_outside_plan_count":len(current-expected),
            "table_only_count":sum(r["disposition"]=="table_only" for r in plan["items"]),
            "merge_count":sum(r["disposition"]=="merge" for r in plan["items"]),
            "mechanical_coverage_complete":not(expected-current) and not pending and not unverified and not(current-expected)}
