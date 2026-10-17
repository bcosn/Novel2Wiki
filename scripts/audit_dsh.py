"""Check a dedicated headless profile without invoking the model API."""
import argparse
import json
import os
import subprocess
from pathlib import Path
from core_store import write_json

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TOOLS = {"web_search", "web_fetch", "workflow", "subagent", "subagent_fork", "send_message", "list_agents", "interrupt_agent", "create_goal", "get_goal", "update_goal", "todo_write", "exit_plan_mode"}
FORBIDDEN_IDS = {"web", "web-search-deepseek", "web-fetch-http", "tool-web", "mcp-resources", "subagent", "subagent-spawn-in-process", "subagent-fork-in-process", "tool-subagent", "tool-subagent-fork", "workflow-ptc", "tool-workflow", "session-title-llm"}

def config_status(path):
    # A narrow parser of composed entry headers, never evaluates !!js or copies config values.
    states = {}
    identity = None
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("- id: "):
            identity = line[6:].strip()
            states[identity] = "enabled"
        elif identity and line.startswith("  disabled:"):
            value = line.split(":", 1)[1].strip()
            states[identity] = "disabled" if value == "true" else "enabled" if value == "false" else "conditional"
    return states

def audit(node, cli, model_patch, out):
    scratch = ROOT / ".validation" / "dsh-audit"
    scratch.mkdir(parents=True, exist_ok=True)
    probe = ROOT / "scripts/dsh_probe.mjs"
    overlay = scratch / "probe.patch.yml"
    overlay.write_text('- insert:\n    - id: novel2wiki-request-audit\n      name: ' + json.dumps(str(probe), ensure_ascii=False) + '\n', encoding="utf-8")
    env = dict(os.environ, DSH_HOME=str(scratch / "home"))
    base = [node, cli, "--profile", "headless"]
    records = {}
    for label in ("baseline", "core-first", "core-repeat"):
        report = scratch / (label + ".json")
        report.unlink(missing_ok=True)
        env["NOVEL2WIKI_AUDIT_OUT"] = str(report)
        command = base + ["--patch", str(Path(model_patch).resolve())]
        if label != "baseline":
            command += ["--patch", str(ROOT / "config/dsh-core.patch.yml")]
        command += ["--patch", str(overlay), "--json", "只回复OK，不调用工具。"]
        run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=45)
        if not report.is_file():
            raise RuntimeError("DSH audit did not intercept an agent request; exit=%s" % run.returncode)
        records[label] = json.loads(report.read_text(encoding="utf-8"))
    composed = subprocess.run(base + ["--patch", str(ROOT / "config/dsh-core.patch.yml"), "--dump-config"], cwd=ROOT, env=env, capture_output=True, timeout=30)
    if composed.returncode:
        raise RuntimeError("DSH composition failed")
    dump = scratch / "composed.yml"
    dump.write_bytes(composed.stdout)
    states = config_status(dump)
    first, repeat = records["core-first"], records["core-repeat"]
    failed = sorted(FORBIDDEN_TOOLS.intersection(first["toolNames"]))
    enabled = sorted(i for i in FORBIDDEN_IDS if states.get(i) != "disabled")
    report = {"status": "passed" if not failed and not enabled else "failed", "requests": records,
              "forbidden_tools_present": failed, "forbidden_plugins_not_disabled": enabled,
              "stable_tool_schema": first["toolsHash"] == repeat["toolsHash"],
              "stable_messages": first["messagesHash"] == repeat["messagesHash"],
              "tool_schema_chars_reduction": records["baseline"]["toolSchemaChars"] - first["toolSchemaChars"],
              "disabled_plugins": sorted(i for i, state in states.items() if state == "disabled"),
              "scope": "dedicated headless profile; existing desktop/global profile unchanged", "model_api_calls": 0,
              "note": "Stable context and smaller schemas are measured; provider cache-hit/cost improvement requires live usage."}
    if not report["stable_tool_schema"] or not report["stable_messages"]:
        report["status"] = "failed"
    write_json(out, report)
    return {k: report[k] for k in ("status", "stable_tool_schema", "stable_messages", "tool_schema_chars_reduction", "model_api_calls")}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--node", required=True)
    p.add_argument("--cli", required=True)
    p.add_argument("--model-patch", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    result = audit(a.node, a.cli, a.model_patch, a.out)
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "passed" else 1)
