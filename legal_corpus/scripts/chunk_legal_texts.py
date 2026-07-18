"""Kanun/yonetmelik/teblig metinlerini madde bazinda parcalara ayirir.

    .venv/Scripts/python.exe legal_corpus/scripts/chunk_legal_texts.py

legal_corpus/raw/*.md -> legal_corpus/chunks/*.json

Madde numarali metinler (6098, 6493, 5549, KVKK) her "**MADDE N**" isaretleyicisine
gore bir chunk'a bolunur. Madde yapisi olmayan metinler (bazi yonetmelik/teblig
bolumleri) markdown basliklarina gore parcalanir, boylece hicbir icerik sessizce
atlanmaz. Mantik M4Trust prototipindeki chunk_documents.py'den birebir tasindi.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "raw"
CHUNKS_DIR = ROOT / "chunks"

MADDE_RE = re.compile(
    r"\*\*\s*(GEÇİCİ\s+|EK\s+)?MADDE\s+(\d+(?:/[A-ZÇĞİÖŞÜ])?)[^*\n]{0,120}\*\*",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
PAGE_MARKER_RE = re.compile(r"\{\d+\}-+\s*")
TRAILING_HEADING_RE = re.compile(r"(?:\n+#{1,6}[^\n]*)+\Z")

MIN_HEADING_CHUNK_LEN = 40


def clean(text: str) -> str:
    text = PAGE_MARKER_RE.sub("", text).strip()
    text = TRAILING_HEADING_RE.sub("", text).strip()
    return text


def chunk_by_madde(markdown: str, source: str) -> list[dict]:
    matches = list(MADDE_RE.finditer(markdown))
    chunks = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = clean(markdown[start:end])
        if not body:
            continue
        prefix = m.group(1).strip() if m.group(1) else None
        number = m.group(2)
        madde_no = f"{prefix} {number}" if prefix else number
        label = f"{prefix} MADDE {number}" if prefix else f"MADDE {number}"
        chunks.append(
            {
                "source": source,
                "strategy": "madde",
                "madde_no": madde_no,
                "chunk_id": f"{source}#{i:03d}-madde-{madde_no.replace(' ', '-').replace('/', '-')}",
                "text": f"{label} - {body}",
            }
        )
    return chunks


def chunk_by_heading(markdown: str, source: str) -> list[dict]:
    matches = list(HEADING_RE.finditer(markdown))
    chunks = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        body = clean(markdown[start:end])
        if len(body) < MIN_HEADING_CHUNK_LEN:
            continue
        title = m.group(2).strip()
        chunks.append(
            {
                "source": source,
                "strategy": "heading",
                "heading": title,
                "chunk_id": f"{source}#heading-{i}",
                "text": f"{title}\n{body}",
            }
        )
    return chunks


def chunk_file(md_path: Path) -> list[dict]:
    source = md_path.stem
    markdown = md_path.read_text(encoding="utf-8")
    chunks = chunk_by_madde(markdown, source)
    if chunks:
        return chunks
    return chunk_by_heading(markdown, source)


def main() -> None:
    md_files = sorted(RAW_DIR.glob("*.md"))
    if not md_files:
        print(f"No markdown files found under {RAW_DIR}")
        return

    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    total_chunks = 0
    for md_path in md_files:
        chunks = chunk_file(md_path)
        out_path = CHUNKS_DIR / f"{md_path.stem}.json"
        out_path.write_text(json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8")

        strategy = chunks[0]["strategy"] if chunks else "none"
        print(f"{md_path.name}: {len(chunks)} chunks ({strategy})")
        total_chunks += len(chunks)

    print(f"\nTotal: {total_chunks} chunks across {len(md_files)} files -> {CHUNKS_DIR}")


if __name__ == "__main__":
    main()
