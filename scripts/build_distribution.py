"""Build the skill ZIP from an explicit public-file allowlist."""
from pathlib import Path
import argparse
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = (".github", "assets", "config", "docs", "example1", "schema", "scripts", "tests")
FILES = (".gitattributes", ".gitignore", "LICENSE", "README.md", "SKILL.md", "pyproject.toml", "requirements-lock.txt")
TEXT = {".py", ".md", ".json", ".css", ".html", ".yml", ".yaml", ".toml", ".txt", ".mjs", ".ps1"}

def build(destination):
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    paths = [ROOT / f for f in FILES if (ROOT / f).is_file()]
    for directory in DIRECTORIES:
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file() and not set(p.parts).intersection({"__pycache__", ".validation", ".git", "node_modules"}) and p.suffix not in {".pyc", ".sqlite", ".jsonl", ".mp4"})
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(paths):
            if path.resolve() == destination:
                continue
            raw = path.read_bytes()
            if path.suffix in TEXT or path.name in {"LICENSE", ".gitignore", ".gitattributes"}:
                raw = raw.replace(b"\r\n", b"\n")
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), date_time=(2026, 10, 14, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, raw)
    return {"status": "built", "files": len(paths), "path": str(destination), "bytes": destination.stat().st_size}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "novel2wiki.zip"))
    a = p.parse_args()
    print(json.dumps(build(a.out), ensure_ascii=False))
