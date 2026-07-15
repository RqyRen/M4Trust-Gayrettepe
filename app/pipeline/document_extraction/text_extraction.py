"""PDF/DOCX metin cikarimi (ADR-002 SS3.1 "PDF veya DOCX metin cikarimi").

Sayfa sinirlari korunur ki source-reference'lardaki `page` alani (ADR common
source-reference schema) gercek sayfaya isaret etsin. OCR bu ilk surumde
KAPSAM DISI (ADR-002 SS3.1 "gerektiginde OCR" - dijital PDF/DOCX disindaki
taranmis belgeler icin ayri bir gelistirme).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from docx import Document as DocxDocument

from app.contracts.errors import ErrorCode, PipelineFailure
from app.pipeline.document_extraction.media import DOCX, PDF


@dataclass(frozen=True)
class ExtractedPage:
    page: int  # one-based (ADR source-reference schema)
    text: str


@dataclass(frozen=True)
class ExtractedDocument:
    pages: list[ExtractedPage]
    method: str  # canonical textExtractionMethod enum degeri

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def full_text(self) -> str:
        """Her sayfayi acik [PAGE n] isaretiyle birlestirir (LLM'in dogru
        page numarasi raporlayabilmesi icin)."""
        return "\n\n".join(f"[PAGE {p.page}]\n{p.text}" for p in self.pages)


def _extract_pdf(path: Path) -> ExtractedDocument:
    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 - kutuphane cesitli parse hatasi firlatabilir
        raise PipelineFailure(
            ErrorCode.CORRUPTED_FILE,
            "PDF content could not be parsed",
            details={"reason": "parse error"},
        ) from exc

    if reader.is_encrypted:
        raise PipelineFailure(
            ErrorCode.ENCRYPTED_DOCUMENT_UNSUPPORTED,
            "encrypted PDF is not supported",
            details={"reason": "encrypted"},
        )

    pages = [ExtractedPage(page=i + 1, text=(page.extract_text() or "")) for i, page in enumerate(reader.pages)]
    if not pages:
        raise PipelineFailure(
            ErrorCode.CORRUPTED_FILE,
            "PDF contains no pages",
            details={"reason": "empty document"},
        )
    return ExtractedDocument(pages=pages, method="DIGITAL_PDF")


def _extract_docx(path: Path) -> ExtractedDocument:
    try:
        document = DocxDocument(str(path))
    except Exception as exc:  # noqa: BLE001
        raise PipelineFailure(
            ErrorCode.CORRUPTED_FILE,
            "DOCX content could not be parsed",
            details={"reason": "parse error"},
        ) from exc

    text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
    if not text.strip():
        raise PipelineFailure(
            ErrorCode.CORRUPTED_FILE,
            "DOCX contains no extractable text",
            details={"reason": "empty document"},
        )
    # DOCX'te native sayfa siniri yok; tek mantiksal sayfa olarak ele alinir.
    return ExtractedDocument(pages=[ExtractedPage(page=1, text=text)], method="DOCX")


def extract_text(path: Path, *, detected_media_type: str) -> ExtractedDocument:
    """Tespit edilen medya turune gore metni cikarir.

    Firlatir: PipelineFailure(CORRUPTED_FILE / ENCRYPTED_DOCUMENT_UNSUPPORTED)
    """
    if detected_media_type == PDF:
        return _extract_pdf(path)
    if detected_media_type == DOCX:
        return _extract_docx(path)
    raise PipelineFailure(
        ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        "no text extractor registered for this media type",
        details={"field": "input.mediaType", "reason": "unsupported format"},
    )
