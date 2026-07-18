"""Kanun kulliyati chunk'larini embed edip diske yazar (bir kerelik, build-time islem).

    .venv/Scripts/python.exe legal_corpus/scripts/build_embeddings.py

legal_corpus/chunks/*.json -> legal_corpus/embeddings/legal_embeddings.npz
                               legal_corpus/embeddings/legal_chunks.json

ChromaDB gibi ayri bir vektor veritabani KULLANILMAZ: kulliyat kucuk (~900
parca), numpy array + brute-force cosine similarity runtime'da yeterince
hizli ve hicbir yeni agir bagimlilik gerektirmiyor (numpy zaten opencv
uzerinden bu repoda mevcut). embeddings/legal_chunks.json, vektorlerle ayni
sirada tutulan chunk kayitlaridir -- retrieval sonucu bir satir indexini
bu dosyadaki karsilik gelen kayida esler.
"""

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = ROOT / "chunks"
EMBEDDINGS_DIR = ROOT / "embeddings"
BATCH_SIZE = 32

# app/ paketinden embed_texts'i yeniden kullanir (build-time ve runtime AYNI
# vektor uzayini kullanmali) -- repo koku sys.path'e eklenir cunku bu script
# `python legal_corpus/scripts/build_embeddings.py` olarak calistirilir.
sys.path.insert(0, str(ROOT.parent))
from app.pipeline.document_extraction.embedding_provider import embed_texts  # noqa: E402


def load_all_chunks() -> list[dict]:
    chunks: list[dict] = []
    for path in sorted(CHUNKS_DIR.glob("*.json")):
        chunks.extend(json.loads(path.read_text(encoding="utf-8")))
    return chunks


def main() -> None:
    chunks = load_all_chunks()
    if not chunks:
        print(f"No chunks found under {CHUNKS_DIR}. Run chunk_legal_texts.py first.")
        return

    print(f"Embedding {len(chunks)} chunks with BAAI/bge-m3 (first run loads the model, ~2-3GB)...")
    vectors = np.empty((len(chunks), 1024), dtype=np.float32)
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        vectors[i : i + len(batch)] = embed_texts([c["text"] for c in batch])
        print(f"  embedded {min(i + BATCH_SIZE, len(chunks))}/{len(chunks)}")

    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(EMBEDDINGS_DIR / "legal_embeddings.npz", vectors=vectors)
    (EMBEDDINGS_DIR / "legal_chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nDone. {len(chunks)} vectors -> {EMBEDDINGS_DIR}")


if __name__ == "__main__":
    main()
