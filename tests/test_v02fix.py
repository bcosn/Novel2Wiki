import contextlib
import copy
import importlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
import zipfile
from argparse import Namespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
quote = importlib.import_module("quote_source_check")
voice = importlib.import_module("check_voice")
render = importlib.import_module("render_wiki")
review = importlib.import_module("review_plan")
merge = importlib.import_module("merge_append")
verify = importlib.import_module("verify_example")

def entry(eid="e-1", name="甲", kind="character"):
    return {"id": eid, "name": name, "kind": kind, "lead": name+"是小说中的人物。",
        "lead_refs": [1], "sections": [{"heading": "经历", "body": "曾在酒店工作。", "refs": [1]}],
        "sources": [{"chapter": 1, "quote": "甲在酒店工作。"}],
        "reception": {"in_work": "", "user": "", "external": ""}, "links": [], "stub": False}

class FixTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".validation"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="test-", dir=scratch)
        self.path = pathlib.Path(self.temp.name).resolve()
        self.assertTrue(self.path.is_relative_to(scratch.resolve()))
    def tearDown(self):
        self.temp.cleanup()
    def write(self, name, data):
        p = self.path / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p
    def review_args(self, **overrides):
        values = dict(entries=str(self.path/"entries.json"), state=str(self.path/"state.json"),
            out=str(self.path/"packet.json"), mode=None, max_reviews=None, sample_size=None,
            context_chars=None, include=None, claim=False, record_result=None)
        values.update(overrides)
        return Namespace(**values)
    def test_quote_cannot_cross_source_boundary(self):
        e = entry()
        e.update(lead="「乙丙」", lead_refs=[1,2], sources=[{"chapter":1,"quote":"甲乙"},{"chapter":2,"quote":"丙丁"}])
        errors, _ = quote.check({"entries":[e]})
        self.assertTrue(any("本节" in err for err in errors))
    def test_quote_must_use_its_own_section_refs(self):
        e = entry()
        e.update(lead="「不同引文」", sources=[{"chapter":1,"quote":"甲乙"},{"chapter":1,"quote":"不同引文"}])
        self.assertTrue(quote.check({"entries":[e]})[0])
        e["lead_refs"]=[2]
        self.assertFalse(quote.check({"entries":[e]})[0])
    def test_real_chapter_and_wrapped_lines(self):
        self.assertTrue(quote.quote_in_chapter("甲乙。", "甲\n乙。"))
        self.assertFalse(quote.quote_in_chapter("甲乙。", "甲\n\n乙。"))
        e=entry()
        errors,_=quote.check({"entries":[e]}, {"chapters":[{"index":2,"content":"甲在酒店工作。"}]})
        self.assertTrue(any("不存在" in err for err in errors))
    def test_voice_does_not_ban_direct_dialogue_or_necessary_names(self):
        e=entry()
        e["lead"]="叙述者说「我十二岁开始工作」，掌柜告诉叙述者需要温酒。"
        self.assertFalse(voice.check({"entries":[e]})[0])
        e["lead"]="我十二岁开始工作。"
        self.assertTrue(voice.check({"entries":[e]})[0])
        e["lead"]="「我」认为掌柜不耐烦。"
        self.assertTrue(voice.check({"entries":[e]})[0])
    def test_advisory_pronouns_do_not_assert_ambiguity(self):
        a,b=entry(name="甲某"),entry("e-2","乙某")
        a["lead"]="甲某见到乙某后，他离开酒店。"
        errors,hints=voice.check({"entries":[a,b]})
        self.assertFalse(errors)
        self.assertTrue(hints)
    def test_titles_and_named_objects_are_not_narrator_voice(self):
        e=entry(name="人物甲")
        obj=entry("e-2","你我镜","object")
        e["lead"]="人物甲是小说《我独自升级》中的人物，持有你我镜。"
        self.assertFalse(voice.check({"entries":[e,obj]})[0])
    def test_review_is_reused_claimed_once_and_capped(self):
        self.write("entries.json",{"meta":{"book":"书"},"entries":[entry(),entry("e-2","乙","place")]})
        result=review.operate(self.review_args(context_chars=2000,sample_size=2))
        self.assertLessEqual(result["packet_chars"],2000)
        self.assertEqual(review.operate(self.review_args())["status"],"reused")
        self.assertEqual(review.operate(self.review_args(claim=True))["calls"],1)
        with self.assertRaises(ValueError):
            review.operate(self.review_args(claim=True))
        with self.assertRaises(ValueError):
            review.operate(self.review_args())
        resultfile=self.write("result.json",{"issues":[],"needs_followup":False})
        review.operate(self.review_args(record_result=str(resultfile)))
        self.assertEqual(review.operate(self.review_args())["status"],"stop")
        with self.assertRaises(ValueError):
            review.operate(self.review_args(max_reviews=5))
    def test_economy_never_issues_an_ai_packet(self):
        self.write("entries.json",{"entries":[entry()]})
        self.assertEqual(review.operate(self.review_args(mode="economy"))["status"],"stop")
        state=json.loads((self.path/"state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["calls"],0)
        self.assertIsNone(state["pending"])
    def test_strict_content_followup_uses_second_call_and_then_stops(self):
        self.write("entries.json",{"entries":[entry(),entry("e-2","乙")]})
        review.operate(self.review_args(mode="strict",sample_size=1))
        review.operate(self.review_args(claim=True))
        resultfile=self.write("result.json",{"issues":[{"entry_id":"e-1","kind":"voice","sentence":"句子","fix":"改法"}],"needs_followup":True})
        review.operate(self.review_args(record_result=str(resultfile)))
        self.assertEqual(review.operate(self.review_args())["sample_ids"],["e-1"])
        self.assertEqual(review.operate(self.review_args(claim=True))["calls"],2)
        review.operate(self.review_args(record_result=str(resultfile)))
        self.assertEqual(review.operate(self.review_args())["status"],"stop")
    def test_strict_stops_after_style_only_result(self):
        self.write("entries.json",{"entries":[entry(),entry("e-2","乙")]})
        review.operate(self.review_args(mode="strict",sample_size=1))
        review.operate(self.review_args(claim=True))
        resultfile=self.write("result.json",{"issues":[{"entry_id":"e-1","kind":"style","sentence":"句子","fix":"改法"}],"needs_followup":True})
        review.operate(self.review_args(record_result=str(resultfile)))
        self.assertEqual(review.operate(self.review_args())["status"],"stop")
    def test_changed_draft_cannot_claim_an_old_packet(self):
        self.write("entries.json",{"entries":[entry()]})
        review.operate(self.review_args())
        changed=entry();changed["lead"]="修改后的文字。"
        self.write("entries.json",{"entries":[changed]})
        with self.assertRaises(ValueError):
            review.operate(self.review_args(claim=True))
        self.assertEqual(review.operate(self.review_args())["status"],"ready")
    def test_oversized_explicit_sample_does_not_consume_calls(self):
        e=entry();e["lead"]="长"*5000
        self.write("entries.json",{"entries":[e]})
        with self.assertRaises(ValueError):
            review.operate(self.review_args(context_chars=500,include="e-1"))
        self.assertFalse((self.path/"state.json").exists())
    def merge_run(self, doc):
        batch=self.write("append.json",doc)
        argv=sys.argv
        try:
            sys.argv=["merge",str(batch),"--base-dir",str(self.path),"--prefix","书"]
            with contextlib.redirect_stdout(io.StringIO()):
                return merge.main()
        finally:
            sys.argv=argv
    def test_pending_survives_next_batch_and_replay(self):
        first={"new_entities":{},"new_timeline":{},"corrections":[{"name":"甲","reason":"旧问题"}],"unresolved":["未决一"]}
        self.merge_run(first)
        second={"new_entities":{},"new_timeline":{},"unresolved":["未决二"]}
        self.merge_run(second);self.merge_run(second)
        pending=json.loads((self.path/"书._pending.json").read_text(encoding="utf-8"))
        self.assertEqual(len(pending["corrections"]),1)
        self.assertEqual(pending["unresolved"],["未决一","未决二"])
    def test_conflicting_duplicate_cannot_partly_write_tables(self):
        first={"new_entities":{"characters":[{"name":"甲","age":18}]},"new_timeline":{}}
        self.merge_run(first)
        snapshot={p.name:p.read_bytes() for p in self.path.glob("书.*.json")}
        second={"new_entities":{"characters":[{"name":"乙","age":17}],"objects":[{"name":"某物","level":1}]},"new_timeline":{"characters":[{"name":"甲","field":"age","from_chapter":1,"to":"18"}]}}
        self.merge_run(second)
        before={p.name:p.read_bytes() for p in self.path.glob("书.*.json")}
        third=copy.deepcopy(second);third["new_entities"]["objects"][0]["level"]=2
        with self.assertRaises(ValueError):
            self.merge_run(third)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.path.glob("书.*.json")})
        self.assertTrue(snapshot)
    def test_invalid_range_advance_does_not_write_any_table(self):
        first={"new_entities":{"characters":[{"name":"甲"}]},"new_timeline":{}}
        self.merge_run(first)
        before={p.name:p.read_bytes() for p in self.path.glob("书.*.json")}
        second={"new_entities":{"characters":[{"name":"乙"}]},"new_timeline":{},
            "range_advance":[{"kind":"character","name":"甲","field":"age","from_chapter":1,"new_to_chapter":2}]}
        with self.assertRaises(ValueError):
            self.merge_run(second)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.path.glob("书.*.json")})
    def test_independent_tracks_merge_and_close_separately(self):
        first={"new_entities":{"characters":[{"name":"甲"}]},"new_timeline":{"characters":[
            {"name":"甲","field":"status","track":"身体","from_chapter":1,"to_chapter":None},
            {"name":"甲","field":"status","track":"来店","from_chapter":1,"to_chapter":None}]}}
        self.merge_run(first)
        second={"new_entities":{},"new_timeline":{},"range_advance":[
            {"kind":"character","name":"甲","field":"status","track":"身体","from_chapter":1,"new_to_chapter":2}]}
        self.merge_run(second)
        items=json.loads((self.path/"书.character_timeline.json").read_text(encoding="utf-8"))["items"]
        self.assertEqual(len(items),2)
        self.assertEqual(next(i for i in items if i["track"]=="身体")["to_chapter"],2)
        self.assertIsNone(next(i for i in items if i["track"]=="来店")["to_chapter"])
    def test_render_has_real_anchors_safe_names_and_local_assets(self):
        es=[entry("e-1","A/B"),entry("e-2","A:B"),entry("e-3","index")]
        es[0]["aliases"]=["<b>别名</b>"]
        out=self.path/"site"
        mapping=render.render({"entries":es},out)
        self.assertEqual(len(set(n.casefold() for n in mapping.values())),3)
        self.assertNotEqual(mapping["index"],"index.html")
        self.assertFalse(verify.site_errors(out))
        page=(out/mapping["A/B"]).read_text(encoding="utf-8")
        self.assertIn('href="#section-1"',page)
        self.assertIn("&lt;b&gt;别名&lt;/b&gt;",page)
        self.assertNotIn("<b>别名</b>",page)
        custom=out/"assets"/"user.txt";custom.write_text("keep",encoding="utf-8")
        render.render({"entries":es[:1]},out)
        self.assertTrue(custom.exists())
        self.assertFalse((out/mapping["A:B"]).exists())
    def test_missing_example_is_failure(self):
        argv=sys.argv
        try:
            sys.argv=["verify",str(self.path),"--book","不存在"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verify.main(),1)
        finally:
            sys.argv=argv
    def test_site_missing_anchor_and_external_resource_fail(self):
        out=self.path/"site";out.mkdir();(out/"assets").mkdir()
        (out/"assets"/"wiki.css").write_text("",encoding="utf-8")
        (out/"index.html").write_text('<a href="#missing">bad</a><img src="https://example.org/x.png">',encoding="utf-8")
        errors=verify.site_errors(out)
        self.assertTrue(any("锚点" in e for e in errors))
        self.assertTrue(any("外部资源" in e for e in errors))
    def test_distribution_matches_skill_and_contains_no_private_sessions(self):
        with zipfile.ZipFile(ROOT / "novel2wiki.zip") as archive:
            names=archive.namelist()
            self.assertFalse(any(".validation/" in n or ".git/" in n or "session.v4.jsonl" in n for n in names))
            core=[ROOT/"SKILL.md"]
            for folder in ("scripts","schema","assets"):
                core.extend(p for p in (ROOT/folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix!=".pyc")
            for p in core:
                self.assertEqual(archive.read(p.relative_to(ROOT).as_posix()),p.read_bytes().replace(b"\r\n",b"\n"),p.name)

if __name__=="__main__":
    unittest.main()
