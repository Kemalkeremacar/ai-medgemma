"""SUT + ICD-10 tanı kontrol adaptörü (EK-2B SUT tanı kuralları).

Tanı kuralları Qdrant ``sut_diagnosis_rules`` collection'ından yüklenir.
"""

from __future__ import annotations

from typing import Any

from .. import _sut_bootstrap  # noqa: F401  (sys.path kurar)
from .. import settings
from ..models import Cinsiyet, LayerResult, LayerStatus, ProvizyonJob

_QDRANT_READER: Any | None = None


def _get_qdrant_reader():
    global _QDRANT_READER
    if _QDRANT_READER is None:
        from ..persistence.sut_diagnosis_rules_qdrant import SutDiagnosisRulesQdrantReader

        _QDRANT_READER = SutDiagnosisRulesQdrantReader()
    return _QDRANT_READER


def _load_lookup(sut_codes: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        reader = _get_qdrant_reader()
        if not reader.ping():
            return None, (
                f"Qdrant SUT tanı kural collection'ına erişilemiyor: "
                f"{settings.SUT_DIAGNOSIS_RULES_COLLECTION}"
            )
        return reader.build_lookup(sut_codes), None
    except Exception as exc:
        return None, f"Qdrant SUT tanı kuralı yüklenemedi: {exc}"


def _sex_value(cinsiyet: Cinsiyet) -> str | None:
    if cinsiyet == Cinsiyet.ERKEK:
        return "erkek"
    if cinsiyet == Cinsiyet.KADIN:
        return "kadin"
    return None


def check_sut_diagnoses(job: ProvizyonJob) -> LayerResult:
    """SUT işlem kodları + ICD-10 tanılarını değerlendirir."""

    sut_codes = job.all_sut_codes()
    if not sut_codes:
        return LayerResult(
            layer="sut_tani_kurali",
            status=LayerStatus.SKIPPED,
            message="SUT kodu yok; SUT tanı kuralı değerlendirilmedi.",
        )

    lookup, lookup_error = _load_lookup(sut_codes)
    if lookup is None:
        return LayerResult(
            layer="sut_tani_kurali",
            status=LayerStatus.INSUFFICIENT,
            message=lookup_error or "SUT tanı kuralı kaynağı yüklenemedi.",
        )

    from diagnosis_rules.sut_provision_diagnosis_checker import evaluate_sut_provision

    result = evaluate_sut_provision(
        lookup,
        sut_codes,
        job.diagnoses,
        age=job.yas,
        sex=_sex_value(job.cinsiyet),
    )

    overall = str(result.get("overall_status") or "unknown")
    items = result.get("items", []) or []
    blocking = [
        item
        for item in items
        if item.get("allowed") is False or item.get("requires_manual_review")
    ]
    review_required = any(item.get("requires_manual_review") for item in items)

    item_statuses = {str(item.get("status")) for item in items}
    tentative_statuses = {str(item.get("tentative_status")) for item in items if item.get("tentative_status")}
    missing_diagnosis = "missing_diagnosis" in item_statuses or "missing_diagnosis" in tentative_statuses
    diagnosis_mismatch = bool(
        item_statuses & {"diagnosis_mismatch", "diagnosis_excluded"}
        or tentative_statuses & {"diagnosis_mismatch"}
    )

    status = _map_status(overall, review_required)
    message = _build_message(overall, blocking)

    return LayerResult(
        layer="sut_tani_kurali",
        status=status,
        message=message,
        detail={
            "overall_status": overall,
            "overall_allowed": result.get("overall_allowed"),
            "review_required": review_required,
            "missing_diagnosis": missing_diagnosis,
            "diagnosis_mismatch": diagnosis_mismatch,
            "blocking_items": blocking,
            "lookup_source": lookup.get("source", "qdrant"),
            "collection": lookup.get("collection"),
            "result": result,
        },
    )


def _map_status(overall: str, review_required: bool) -> LayerStatus:
    if overall == "not_payable_by_sut_diagnosis":
        return LayerStatus.FAIL
    if overall == "review_required" or review_required:
        return LayerStatus.REVIEW
    if overall == "allowed":
        return LayerStatus.PASS
    return LayerStatus.INSUFFICIENT


def _build_message(overall: str, blocking: list[dict[str, Any]]) -> str:
    if overall == "allowed":
        return "SUT tanı kuralları işlemleri destekliyor."
    if overall == "not_payable_by_sut_diagnosis":
        codes = ", ".join(sorted({str(b.get("sut_code")) for b in blocking if b.get("sut_code")}))
        return f"SUT tanı uyumsuzluğu nedeniyle ödenemeyen işlem(ler): {codes or '-'}."
    if overall == "review_required":
        return "SUT tanı kuralları manuel inceleme gerektiriyor."
    return "SUT tanı kuralı sonucu belirsiz."
