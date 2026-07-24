"""HUV + ICD-10 tanı kontrol adaptörü (karar sırası.txt Adım 4).

Tanı kuralları Qdrant ``huv_diagnosis_rules`` collection'ından yüklenir.
"""

from __future__ import annotations

import re
from typing import Any

from .. import _sut_bootstrap  # noqa: F401  (sys.path kurar)
from .. import settings
from ..models import LayerResult, LayerStatus

_TZH_CODE_RE = re.compile(r"^TZH\.", re.IGNORECASE)

_QDRANT_READER: Any | None = None


def _is_tzh_meta_code(code: str) -> bool:
    """TZH tarifesine özgü meta kodlar (TZH.Ilac, TZH.01.00001 vb.)."""

    return bool(_TZH_CODE_RE.match(code.strip()))


def _get_qdrant_reader():
    global _QDRANT_READER
    if _QDRANT_READER is None:
        from ..persistence.diagnosis_rules_qdrant import DiagnosisRulesQdrantReader

        _QDRANT_READER = DiagnosisRulesQdrantReader()
    return _QDRANT_READER


def _load_lookup(huv_codes: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        reader = _get_qdrant_reader()
        if not reader.ping():
            return None, (
                f"Qdrant tanı kural collection'ına erişilemiyor: "
                f"{settings.DIAGNOSIS_RULES_COLLECTION}"
            )
        return reader.build_lookup(huv_codes), None
    except Exception as exc:
        return None, f"Qdrant tanı kuralı yüklenemedi: {exc}"


def check_diagnoses(
    huv_codes: list[str],
    diagnoses: list[str],
) -> LayerResult:
    """HUV işlemleri + ICD-10 tanılarını değerlendirir, LayerResult döner."""

    skipped_tzh = [c for c in huv_codes if _is_tzh_meta_code(c)]
    evaluable = [c for c in huv_codes if not _is_tzh_meta_code(c)]

    if not huv_codes:
        return LayerResult(
            layer="tani_kurali",
            status=LayerStatus.SKIPPED,
            message="HUV kodu yok; tanı kuralı değerlendirilmedi.",
        )
    if not evaluable:
        return LayerResult(
            layer="tani_kurali",
            status=LayerStatus.SKIPPED,
            message=(
                "Yalnızca TZH meta kodları var; tanı kuralı kapsamı dışında atlandı: "
                + ", ".join(skipped_tzh)
            ),
            detail={"skipped_tzh_codes": skipped_tzh},
        )

    lookup, lookup_error = _load_lookup(evaluable)
    if lookup is None:
        return LayerResult(
            layer="tani_kurali",
            status=LayerStatus.INSUFFICIENT,
            message=lookup_error or "Tanı kuralı kaynağı yüklenemedi.",
        )

    from diagnosis_rules.provision_diagnosis_checker import evaluate_provision

    result = evaluate_provision(lookup, evaluable, diagnoses)

    overall = str(result.get("overall_status") or "unknown")
    items = result.get("items", []) or []
    blocking = [
        item
        for item in items
        if (item.get("allowed") is False or item.get("requires_manual_review"))
        and not _is_tzh_meta_code(str(item.get("huv_code") or ""))
    ]
    review_required = any(item.get("requires_manual_review") for item in items)

    item_statuses = {str(item.get("status")) for item in items}
    missing_diagnosis = "missing_diagnosis" in item_statuses
    diagnosis_mismatch = bool(
        item_statuses & {"diagnosis_mismatch", "diagnosis_excluded"}
    )

    status = _map_status(overall, review_required)
    message = _build_message(overall, blocking)

    if skipped_tzh:
        message = f"{message} TZH meta kodları tanı kuralından hariç: {', '.join(skipped_tzh)}."

    return LayerResult(
        layer="tani_kurali",
        status=status,
        message=message,
        detail={
            "overall_status": overall,
            "overall_allowed": result.get("overall_allowed"),
            "review_required": review_required,
            "missing_diagnosis": missing_diagnosis,
            "diagnosis_mismatch": diagnosis_mismatch,
            "blocking_items": blocking,
            "skipped_tzh_codes": skipped_tzh,
            "lookup_source": lookup.get("source", "qdrant"),
            "collection": lookup.get("collection"),
            "result": result,
        },
    )


def _map_status(overall: str, review_required: bool) -> LayerStatus:
    if overall == "not_payable_by_diagnosis":
        return LayerStatus.FAIL
    if overall == "review_required" or review_required:
        return LayerStatus.REVIEW
    if overall == "allowed":
        return LayerStatus.PASS
    return LayerStatus.INSUFFICIENT


def _build_message(overall: str, blocking: list[dict[str, Any]]) -> str:
    if overall == "allowed":
        return "Tanı kuralları işlemleri destekliyor."
    if overall == "not_payable_by_diagnosis":
        codes = ", ".join(sorted({str(b.get("huv_code")) for b in blocking if b.get("huv_code")}))
        return f"Tanı uyumsuzluğu nedeniyle ödenemeyen işlem(ler): {codes or '-'}."
    if overall == "review_required":
        return "Tanı kuralları manuel inceleme gerektiriyor."
    return "Tanı kuralı sonucu belirsiz."
