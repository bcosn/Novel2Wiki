"""Freeze three small public-text excerpts. Never echo the story to stdout."""
import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path
from lxml import html
from core_store import file_digest, write_json

SOURCES = [("madman", "狂人日记", "https://zh.wikisource.org/zh-hans/狂人日记"),
           ("medicine", "药", "https://zh.wikisource.org/zh-hans/药"),
           ("red-chamber", "红楼梦第一回", "https://zh.wikisource.org/zh-hans/紅樓夢/第001回")]

def fetch(directory, chars=1000):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for key, title, url in SOURCES:
        encoded = urllib.parse.quote(url, safe=":/?=&")
        request = urllib.request.Request(encoded, headers={"User-Agent": "Novel2Wiki/0.3 small-regression-fixtures"})
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read(2 * 1024 * 1024)
        tree = html.fromstring(raw)
        containers = tree.xpath("//*[contains(concat(' ',normalize-space(@class),' '),' mw-parser-output ')]")
        if not containers:
            raise ValueError("missing story container: " + title)
        paragraphs = [p.text_content().strip() for p in containers[0].xpath(".//p")]
        paragraphs = [p for p in paragraphs if len(p) >= 20 and "維基文庫" not in p and "维基文库" not in p]
        text = "\n\n".join(paragraphs)[:chars]
        if len(text) < 300:
            raise ValueError("insufficient story excerpt: " + title)
        path = directory / (key + ".txt")
        path.write_text("第1章 测试节选\n" + text + "\n", encoding="utf-8", newline="\n")
        records.append({"file": path.name, "title": title, "source_url": url, "source_page_sha256": hashlib.sha256(raw).hexdigest(),
                        "sha256": file_digest(path), "chars": len(text), "scope": "首段节选，截断位置不代表章节边界"})
    write_json(directory / "manifest.json", {"purpose": "small regression excerpts; no model training", "samples": records})
    return {"samples": len(records), "chars": sum(r["chars"] for r in records), "directory": str(directory.resolve())}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--chars", type=int, default=1000)
    a = p.parse_args()
    if not 500 <= a.chars <= 1500:
        p.error("sample budget must be 500–1500 characters")
    print(json.dumps(fetch(a.out, a.chars), ensure_ascii=False))
