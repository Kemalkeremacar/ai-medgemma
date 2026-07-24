"""Belge kaynağı soyutlaması (karar sırası.txt Adım 3 - belgeleri bulma).

Şu an dosya sistemi tabanlı (``FilesystemDocumentSource``). İleride URL/indirme
veya başka bir kaynak ``DocumentSource`` protokolünü uygulayarak takılabilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .. import settings
from ..models import DocumentInput

PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
TEXT_SUFFIXES = {".txt", ".md"}


@dataclass
class DocumentRef:
    """Çözümlenmiş, erişilebilir bir belge referansı."""

    path: Path
    doc_type: str | None = None
    title: str | None = None
    declared_hasta_id: str | None = None
    declared_patient_name: str | None = None
    exists: bool = False
    kind: str = "unknown"  # pdf | image | text | unknown
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def _classify(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "unknown"


@runtime_checkable
class DocumentSource(Protocol):
    def resolve(self, doc: DocumentInput) -> DocumentRef:
        """Bir belge girdisini erişilebilir referansa çözümler."""

    def resolve_all(self, docs: list[DocumentInput]) -> list[DocumentRef]:
        ...


class FilesystemDocumentSource:
    """Belgeleri ``DOCUMENT_ROOT`` altında (veya mutlak yolla) çözümler.

    Güvenlik: göreli yollar her zaman ``DOCUMENT_ROOT`` içine sınırlanır
    (path traversal engellenir).
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or settings.DOCUMENT_ROOT).resolve()

    def resolve(self, doc: DocumentInput) -> DocumentRef:
        raw = Path(doc.path)
        try:
            if raw.is_absolute():
                resolved = raw.resolve()
            else:
                resolved = (self.root / raw).resolve()
                # Path traversal kontrolü.
                if not str(resolved).startswith(str(self.root)):
                    return DocumentRef(
                        path=resolved,
                        doc_type=doc.doc_type,
                        title=doc.title,
                        declared_hasta_id=doc.declared_hasta_id,
                        declared_patient_name=doc.declared_patient_name,
                        exists=False,
                        kind="unknown",
                        error="Belge yolu DOCUMENT_ROOT dışında; reddedildi.",
                    )
        except Exception as exc:
            return DocumentRef(
                path=raw,
                doc_type=doc.doc_type,
                title=doc.title,
                exists=False,
                error=f"Yol çözümlenemedi: {exc}",
            )

        exists = resolved.is_file()
        meta: dict[str, Any] = {}
        if doc.doc_type_confidence:
            meta["doc_type_confidence"] = doc.doc_type_confidence
        if doc.doc_type_source:
            meta["doc_type_source"] = doc.doc_type_source
        return DocumentRef(
            path=resolved,
            doc_type=doc.doc_type,
            title=doc.title,
            declared_hasta_id=doc.declared_hasta_id,
            declared_patient_name=doc.declared_patient_name,
            exists=exists,
            kind=_classify(resolved) if exists else "unknown",
            error=None if exists else "Dosya bulunamadı.",
            meta=meta,
        )

    def resolve_all(self, docs: list[DocumentInput]) -> list[DocumentRef]:
        return [self.resolve(doc) for doc in docs]
