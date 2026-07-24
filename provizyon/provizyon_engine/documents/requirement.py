"""Zorunlu evrak kontrolü (karar sırası.txt Adım 2).

"İşlem için belge gerekli ama belge yoksa MedGemma'ya sormaya gerek yok ->
eksik evrak." Bu katman erken kapıdır.

Belge gerekliliği üç kaynaktan belirlenir (öncelik sırasıyla):
1. ``config/document_requirements.json`` override dosyası (tam kod veya prefix).
2. SUT kural motorundaki ``required_document`` tipli kurallar.
3. HUV kodları SUT'a çevrilerek SUT kural motoruyla eşleştirilir.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import _sut_bootstrap  # noqa: F401
from .. import settings
from ..models import LayerResult, LayerStatus, ProvizyonJob

logger = logging.getLogger(__name__)

_REQUIRED_DOC_CODES: set[str] | None = None
_OVERRIDES: dict[str, bool] | None = None
_PREFIX_RULES: list[tuple[str, bool]] | None = None

_OVERRIDE_PATH = settings.PROVIZYON_ROOT / "config" / "document_requirements.json"


def _load_overrides() -> tuple[dict[str, bool], list[tuple[str, bool]]]:
    """Load both exact code overrides and prefix-based rules."""
    global _OVERRIDES, _PREFIX_RULES
    if _OVERRIDES is not None and _PREFIX_RULES is not None:
        return _OVERRIDES, _PREFIX_RULES
    exact: dict[str, bool] = {}
    prefixes: list[tuple[str, bool]] = []
    if _OVERRIDE_PATH.exists():
        try:
            raw = json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                key = str(k).strip()
                if key.startswith("_"):
                    continue
                if key.startswith("prefix:"):
                    prefixes.append((key[7:].strip().upper(), bool(v)))
                else:
                    exact[key.upper()] = bool(v)
        except Exception:
            logger.warning("document_requirements.json okunamadı", exc_info=True)
    prefixes.sort(key=lambda x: len(x[0]), reverse=True)
    _OVERRIDES = exact
    _PREFIX_RULES = prefixes
    return exact, prefixes


def _load_required_doc_codes() -> set[str]:
    """SUT kurallarından ``required_document`` tipli kuralı olan kodlar."""

    global _REQUIRED_DOC_CODES
    if _REQUIRED_DOC_CODES is not None:
        return _REQUIRED_DOC_CODES
    codes: set[str] = set()
    if settings.SUT_RULES_PATH.exists():
        try:
            from sut_engine.rule_compiler import load_rules

            for rule in load_rules(settings.SUT_RULES_PATH):
                if rule.rule_type == "required_document" and rule.source_code:
                    codes.add(str(rule.source_code).strip().upper())
        except Exception:
            codes = set()
    _REQUIRED_DOC_CODES = codes
    return codes


def _code_requires_document(
    code: str,
    exact: dict[str, bool],
    prefixes: list[tuple[str, bool]],
    required_codes: set[str],
) -> bool | None:
    """Returns True/False if a determination can be made, None otherwise."""
    upper = code.strip().upper()

    if upper in exact:
        return exact[upper]

    for prefix, val in prefixes:
        if upper.startswith(prefix):
            return val

    if upper in required_codes:
        return True

    return None


def _is_direct_sut_code(code: str) -> bool:
    """6 haneli SGK SUT kodu — HUV crosswalk gerektirmez."""

    code = code.strip()
    return len(code) == 6 and code.isdigit() and not code.startswith("0")


def _resolve_huv_to_sut(huv_codes: list[str]) -> list[str]:
    """HUV kodlarını SUT karşılığına çevir (unified catalog üzerinden)."""
    sut_codes: list[str] = []
    if not huv_codes:
        return sut_codes
    try:
        from unified_catalog.unified_retriever import UnifiedCatalogRetriever

        retriever = UnifiedCatalogRetriever(
            out_dir=settings.SUT_OUT_DIR,
            use_qdrant=False,
            context_limit=len(huv_codes) * 3,
        )
        for huv in huv_codes:
            for row in retriever.by_huv.get(huv.strip(), []):
                sut = str(row.get("sut_code") or "").strip().upper()
                if sut:
                    sut_codes.append(sut)
    except Exception:
        logger.debug("HUV->SUT çevirisi yapılamadı", exc_info=True)
    return sut_codes


def _has_usable_document(job: ProvizyonJob) -> bool:
    return len(job.documents) > 0


def check_requirement(job: ProvizyonJob, *, documents_present: bool | None = None) -> LayerResult:
    """İşlem(ler) belge gerektiriyor mu, gerekiyorsa belge var mı?"""

    if documents_present is None:
        documents_present = _has_usable_document(job)

    exact, prefixes = _load_overrides()
    required_codes = _load_required_doc_codes()

    job_codes: set[str] = set()
    for code in job.all_huv_codes():
        job_codes.add(code.strip().upper())
    for code in job.all_sut_codes():
        job_codes.add(code.strip().upper())

    requiring: list[str] = []
    undetermined_huv: list[str] = []

    for code in sorted(job_codes):
        result = _code_requires_document(code, exact, prefixes, required_codes)
        if result is True:
            requiring.append(code)
        elif result is None and not _is_direct_sut_code(code):
            undetermined_huv.append(code)

    if undetermined_huv:
        sut_equivalents = _resolve_huv_to_sut(undetermined_huv)
        for sut_code in sut_equivalents:
            result = _code_requires_document(sut_code, exact, prefixes, required_codes)
            if result is True:
                requiring.append(sut_code)

    requires_document = bool(requiring)

    if not requires_document:
        return LayerResult(
            layer="zorunlu_evrak",
            status=LayerStatus.PASS,
            message="İşlemler için zorunlu belge tespit edilmedi.",
            detail={"requires_document": False, "documents_present": documents_present},
        )

    if documents_present:
        return LayerResult(
            layer="zorunlu_evrak",
            status=LayerStatus.PASS,
            message="Belge gerektiren işlem(ler) için belge mevcut.",
            detail={
                "requires_document": True,
                "requiring_codes": requiring,
                "documents_present": True,
            },
        )

    return LayerResult(
        layer="zorunlu_evrak",
        status=LayerStatus.FAIL,
        message=f"Belge gerektiren işlem(ler) için hiç belge yok: {', '.join(sorted(requiring))}.",
        detail={
            "requires_document": True,
            "requiring_codes": requiring,
            "documents_present": False,
        },
    )
