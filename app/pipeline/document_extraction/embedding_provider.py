"""Metin -> vektor donusumu icin tek giris noktasi (Legal RAG).

Kanun kulliyati embedding'i (legal_corpus/scripts/build_embeddings.py) ve
runtime retrieval (legal_rag.py) AYNI vektor uzayini kullanmak zorunda, bu
yuzden ikisi de bu tek fonksiyonu cagirir.

Su an BAAI/bge-m3 (yerel, FlagEmbedding) kullanilir -- ucretsiz ama agir
(torch + ~2-3GB model agirligi). Ileride OpenAI embeddings gibi ucretli/hafif
bir saglayiciya gecilmek istenirse tek degisecek yer burasidir: kanun
kulliyatini yeniden embed edip (build_embeddings.py) bu fonksiyonun govdesini
degistirmek yeterlidir, retrieval veya pipeline kodunun geri kalanina
dokunulmaz.
"""

from __future__ import annotations

import numpy as np

_model = None


def _get_model():
    global _model
    if _model is None:
        from FlagEmbedding import BGEM3FlagModel

        _model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
    return _model


def embed_texts(texts: list[str]) -> np.ndarray:
    """Metin listesini yogun (dense) vektorlere cevirir. Sekil: (len(texts), 1024)."""
    if not texts:
        return np.empty((0, 1024), dtype=np.float32)
    model = _get_model()
    vectors = model.encode(texts)["dense_vecs"]
    return np.asarray(vectors, dtype=np.float32)
