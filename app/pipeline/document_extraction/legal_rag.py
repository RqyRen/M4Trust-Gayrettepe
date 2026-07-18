"""Sozlesme metnine en ilgili kanun maddelerini getirir (Legal RAG grounding).

Embedding'ler legal_corpus/scripts/build_embeddings.py ile onceden (build-time)
uretilip diske yazilir; bu modul yalniz diskten yukleyip cosine similarity ile
en yakin top_k maddeyi dondurur. Ayri bir vektor veritabani (ChromaDB vb.)
KULLANILMAZ: kulliyat kucuk (~900 parca), numpy array + brute-force cosine
similarity runtime'da yeterince hizli.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.pipeline.document_extraction.embedding_provider import embed_texts

_EMBEDDINGS_DIR = Path(__file__).resolve().parents[3] / "legal_corpus" / "embeddings"

_normalized_vectors: np.ndarray | None = None
_chunks: list[dict] | None = None


def _load() -> tuple[np.ndarray, list[dict]]:
    global _normalized_vectors, _chunks
    if _normalized_vectors is None:
        vectors_path = _EMBEDDINGS_DIR / "legal_embeddings.npz"
        chunks_path = _EMBEDDINGS_DIR / "legal_chunks.json"
        if not vectors_path.exists() or not chunks_path.exists():
            raise FileNotFoundError(
                f"Legal corpus embeddings not found under {_EMBEDDINGS_DIR}. "
                "Run legal_corpus/scripts/build_embeddings.py first."
            )
        vectors = np.load(vectors_path)["vectors"]
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        _normalized_vectors = vectors / np.clip(norms, 1e-9, None)
        _chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    return _normalized_vectors, _chunks


def retrieve(query_text: str, top_k: int = 5) -> list[dict]:
    """query_text'e en ilgili top_k kanun maddesini benzerlik skoruyla (0-1) dondurur.

    Her sonuc chunk kaydinin (source, madde_no/heading, text) uzerine "score"
    eklenmis halidir, en yuksek skordan dusuge siralidir.
    """
    vectors, chunks = _load()
    query_vec = embed_texts([query_text])[0]
    query_vec = query_vec / max(float(np.linalg.norm(query_vec)), 1e-9)
    scores = vectors @ query_vec
    top_indices = np.argsort(-scores)[:top_k]
    return [{**chunks[i], "score": float(scores[i])} for i in top_indices]
