"""Launch the dedicated, lean DSH profile without changing global plugins."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--node", default=shutil.which("node"))
    p.add_argument("--cli", required=True, help="installed desktop-cli.js or DSH bin.js")
    p.add_argument("--task-file", required=True, help="short task instructions or manifest reference, not the entire novel")
    p.add_argument("--model-patch")
    p.add_argument("--credentials-file", help="read an existing credential store; never copy its values")
    p.add_argument("--home", default=".novel2wiki/dsh")
    p.add_argument("--session-id")
    p.add_argument("--allow-subagent", action="store_true")
    a = p.parse_args()
    if not a.node:
        p.error("Node executable not found; use --node")
    task = Path(a.task_file).read_text(encoding="utf-8", errors="strict")
    if len(task) > 4000:
        p.error("task instructions exceed 4000 characters; reference bounded input files instead")
    home = Path(a.home).resolve()
    home.mkdir(parents=True, exist_ok=True)
    command = [a.node, str(Path(a.cli).resolve()), "--profile", "headless"]
    if a.model_patch:
        command += ["--patch", str(Path(a.model_patch).resolve())]
    command += ["--patch", str(ROOT / "config/dsh-core.patch.yml")]
    if a.credentials_file:
        credential_file = Path(a.credentials_file).resolve()
        if not credential_file.is_file():
            p.error("credential store does not exist")
        overlay = home / "credentials-reference.patch.yml"
        overlay.write_text('- id: credentials\n  config:\n    watch: false\n    path: ' + json.dumps(str(credential_file), ensure_ascii=False) + '\n', encoding="utf-8")
        command += ["--patch", str(overlay)]
    if a.allow_subagent:
        command += ["--patch", str(ROOT / "config/dsh-subagent.patch.yml")]
    if a.session_id:
        command += ["--session-id", a.session_id]
    command += ["-"]
    env = dict(os.environ, DSH_HOME=str(home), PYTHONUTF8="1")
    process = subprocess.Popen(command, cwd=Path.cwd(), env=env, stdin=subprocess.PIPE)
    try:
        process.communicate(task.encode("utf-8"))
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        return 130
    return process.returncode

if __name__ == "__main__":
    raise SystemExit(main())
