"""SUT deterministic kural motoru adaptörü (karar sırası.txt Adım 4).

Mevcut ``unified_catalog.unified_advisor.advise`` akışını sarar.
HUV→SUT crosswalk eşleştirmesi ``enable_huv_sut_crosswalk`` ile kontrol edilir
(varsayılan kapalı). Doğrudan SUT kodları bu bayraktan bağımsız değerlendirilir.
"""

from __future__ import annotations

import re
from typing import Any

from .. import _sut_bootstrap  # noqa: F401  (sys.path kurar)
from .. import settings
from ..models import LayerResult, LayerStatus, ProvizyonJob

_NUMERIC_HUV_RE = re.compile(r"^\d{2}\.\d")
_SUT_CODE_RE = re.compile(r"^[1-9]\d{5}$")
_TZH_CODE_RE = re.compile(r"^TZH\.", re.IGNORECASE)


def _is_sut_code(code: str) -> bool:
    return bool(_SUT_CODE_RE.match(code.strip()))


def _is_tzh_meta_code(code: str) -> bool:
    return bool(_TZH_CODE_RE.match(code.strip()))


def _local_sut_candidates(huv_codes: list[str]) -> list[dict[str, Any]]:
    """Yerel crosswalk'tan SUT adaylarını hızlıca toplar (Qdrant/MedGemma yok)."""

    try:
        from unified_catalog.unified_retriever import UnifiedCatalogRetriever
    except Exception:
        return []

    retriever = UnifiedCatalogRetriever(out_dir=settings.SUT_OUT_DIR, use_qdrant=False)
    resolved: list[dict[str, Any]] = []
    for code in huv_codes:
        if not _NUMERIC_HUV_RE.match(code.strip()):
            continue
        for row in retriever.by_huv.get(code.strip(), []):
            sut = str(row.get("sut_code") or "").strip()
            if not sut:
                continue
            relation = str(row.get("relation_type") or "")
            resolved.append(
                {
                    "input_code": code,
                    "sut_code": sut,
                    "rule_eval_allowed": relation in {"exact_match", "equivalent", "close_match"},
                }
            )
    return resolved


def _sut_only_procedures(procedures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Crosswalk kapalıyken yalnızca doğrudan SUT kodlu işlemleri bırak."""

    out: list[dict[str, Any]] = []
    for proc in procedures:
        code = str(proc.get("code") or "").strip()
        code_type = str(proc.get("code_type") or "").strip().upper()
        if _is_sut_code(code) or code_type == "SUT":
            out.append(proc)
    return out


def _early_skip_sut(
    job: ProvizyonJob,
    procedures: list[dict[str, Any]],
    *,
    enable_huv_sut_crosswalk: bool,
) -> LayerResult | None:
    """TZH-only, crosswalk-kapalı HUV-only veya eşlemesiz durumlarda advise() atla."""

    codes = [str(p.get("code") or "").strip() for p in procedures if p.get("code")]
    if not codes:
        return LayerResult(
            layer="sut_kurali",
            status=LayerStatus.SKIPPED,
            message="Değerlendirilecek işlem yok; SUT kuralı atlandı.",
        )

    numeric_huv = [c for c in codes if _NUMERIC_HUV_RE.match(c)]
    sut_direct = [c for c in codes if _is_sut_code(c)]
    if sut_direct:
        return None

    if not numeric_huv and all(_is_tzh_meta_code(c) for c in codes):
        return LayerResult(
            layer="sut_kurali",
            status=LayerStatus.SKIPPED,
            message="Yalnızca TZH meta kodları; SUT kural kontrolü atlandı.",
            detail={"skipped_reason": "tzh_only"},
        )

    if not enable_huv_sut_crosswalk:
        return LayerResult(
            layer="sut_kurali",
            status=LayerStatus.SKIPPED,
            message=(
                "HUV→SUT eşleştirmesi kapalı; doğrudan SUT kodu olmadığı için "
                "SUT işlem kuralı atlandı. HUV kuralları ayrı değerlendirilir."
            ),
            detail={
                "skipped_reason": "huv_sut_crosswalk_disabled",
                "huv_codes": job.all_huv_codes() or numeric_huv,
            },
        )

    huv_codes = job.all_huv_codes() or numeric_huv
    local = _local_sut_candidates(huv_codes)
    if not _has_evaluable_sut_mapping(local):
        return LayerResult(
            layer="sut_kurali",
            status=LayerStatus.SKIPPED,
            message=(
                "HUV kodları için güvenilir SGK SUT eşlemesi yok; "
                "TZH provizyonunda SUT kural kontrolü atlandı."
            ),
            detail={"skipped_reason": "no_local_sut_mapping", "local_candidates": local},
        )
    return None


def check_sut_rules(
    job: ProvizyonJob,
    *,
    use_qdrant: bool = True,
    enable_huv_sut_crosswalk: bool = False,
) -> LayerResult:
    """İşlemler arası SUT kurallarını değerlendirir, LayerResult döner."""

    procedures = _procedures_payload(job)
    skipped = _early_skip_sut(
        job, procedures, enable_huv_sut_crosswalk=enable_huv_sut_crosswalk
    )
    if skipped is not None:
        return skipped

    if not enable_huv_sut_crosswalk:
        procedures = _sut_only_procedures(procedures)
        if not procedures:
            return LayerResult(
                layer="sut_kurali",
                status=LayerStatus.SKIPPED,
                message=(
                    "HUV→SUT eşleştirmesi kapalı; doğrudan SUT kodu olmadığı için "
                    "SUT işlem kuralı atlandı."
                ),
                detail={"skipped_reason": "huv_sut_crosswalk_disabled"},
            )

    if not settings.SUT_RULES_PATH.exists():
        return LayerResult(
            layer="sut_kurali",
            status=LayerStatus.INSUFFICIENT,
            message=f"SUT kural dosyası bulunamadı: {settings.SUT_RULES_PATH}",
        )

    from unified_catalog.unified_advisor import advise

    question = _build_question(procedures)
    try:
        result = advise(
            question,
            out_dir=settings.SUT_OUT_DIR,
            rules_path=settings.SUT_RULES_PATH,
            sut_index=settings.SUT_INDEX_PATH,
            use_qdrant=use_qdrant and enable_huv_sut_crosswalk,
            collection=settings.SUT_UNIFIED_COLLECTION,
            qdrant_url=settings.QDRANT_URL,
            tei_url=settings.TEI_URL,
            provizyon_context=_context_payload(job),
            input_services=procedures,
            allow_huv_crosswalk=enable_huv_sut_crosswalk,
        )
    except Exception as exc:  # RAG/motor hatası kuyruğu durdurmasın
        return LayerResult(
            layer="sut_kurali",
            status=LayerStatus.INSUFFICIENT,
            message=f"SUT kural motoru çalıştırılamadı: {exc}",
            detail={"error": str(exc)},
        )

    evaluation = result.get("sut_rule_evaluation") or {}
    overall = str(evaluation.get("overall_status") or "UNKNOWN")
    summary = evaluation.get("summary") or {}
    blocking = _blocking_findings(evaluation)
    resolved_services = result.get("resolved_services", [])
    result_warnings = result.get("warnings", [])

    status = _map_status(overall)
    message = _build_message(overall, blocking)

    # TZH HUV kodlarında SGK SUT eşlemesi olmayabilir; kural motoru çalışmadıysa atla.
    if status == LayerStatus.PASS and not _has_evaluable_sut_mapping(resolved_services):
        status = LayerStatus.SKIPPED
        if not enable_huv_sut_crosswalk:
            message = (
                "HUV→SUT eşleştirmesi kapalı; değerlendirilebilir doğrudan SUT "
                "işlemi bulunamadı."
            )
            skipped_reason = "huv_sut_crosswalk_disabled"
        else:
            message = (
                "HUV kodları için güvenilir SGK SUT eşlemesi yok; "
                "TZH provizyonunda SUT kural kontrolü atlandı."
            )
            skipped_reason = "no_local_sut_mapping"
        return LayerResult(
            layer="sut_kurali",
            status=status,
            message=message,
            detail={
                "overall_status": overall,
                "summary": summary,
                "blocking_findings": blocking,
                "resolved_services": resolved_services,
                "warnings": result_warnings,
                "skipped_reason": skipped_reason,
                "result": result,
            },
        )

    return LayerResult(
        layer="sut_kurali",
        status=status,
        message=message,
        detail={
            "overall_status": overall,
            "summary": summary,
            "blocking_findings": blocking,
            "resolved_services": resolved_services,
            "warnings": result_warnings,
            "huv_sut_crosswalk_enabled": enable_huv_sut_crosswalk,
            "result": result,
        },
    )


def _procedures_payload(job: ProvizyonJob) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for proc in job.procedures:
        payload.append(proc.model_dump(exclude_none=True))
    if not payload:
        for code in job.all_sut_codes():
            payload.append({"code": code, "code_type": "SUT"})
        for code in job.all_huv_codes():
            payload.append({"code": code, "code_type": "HUV"})
    return payload


def _build_question(procedures: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for proc in procedures:
        label = " ".join(str(proc.get(k)) for k in ("code", "name") if proc.get(k))
        if label:
            parts.append(label)
    suffix = "birlikte ödenir mi" if len(parts) >= 2 else "SUT karşılığı ve kuralları nedir"
    return " ".join([*parts, suffix]) if parts else "SUT kuralları"


def _context_payload(job: ProvizyonJob) -> dict[str, Any]:
    documents = [doc.title or doc.doc_type or doc.path for doc in job.documents]
    return {
        "provizyon_id": job.provizyon_id,
        "hasta_id": job.hasta_id,
        "code_family": job.code_family,
        "diagnosis_code_source": job.diagnosis_code_source(),
        "huv_codes": job.all_huv_codes(),
        "sut_codes": job.all_sut_codes(),
        "diagnoses": job.diagnoses,
        "documents": documents,
        "notes": job.notes,
        "facility_level": job.facility_level,
        "age": job.yas,
        "patient_age": job.yas,
    }


def _has_evaluable_sut_mapping(resolved_services: list[dict[str, Any]]) -> bool:
    """SUT kural motoruna alınabilecek en az bir eşleme var mı?"""

    for svc in resolved_services:
        if svc.get("rule_eval_allowed"):
            return True
        if str(svc.get("sut_code") or "").strip():
            return True
    return False


def _blocking_findings(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for service in evaluation.get("service_results", []) or []:
        for finding in service.get("findings", []) or []:
            if finding.get("status") == "FAIL":
                findings.append(finding)
    return findings


def _map_status(overall: str) -> LayerStatus:
    mapping = {
        "FAIL": LayerStatus.FAIL,
        "WARNING": LayerStatus.REVIEW,
        "INSUFFICIENT_INFO": LayerStatus.INSUFFICIENT,
        "PASS": LayerStatus.PASS,
    }
    return mapping.get(overall, LayerStatus.INSUFFICIENT)


def _build_message(overall: str, blocking: list[dict[str, Any]]) -> str:
    if overall == "PASS":
        return "SUT kuralları işlemler için engelleyici bulgu üretmedi."
    if overall == "FAIL":
        codes = ", ".join(sorted({str(b.get("service_code")) for b in blocking if b.get("service_code")}))
        return f"SUT kuralı engelleyici bulgu: {codes or '-'}."
    if overall == "WARNING":
        return "SUT kuralları manuel doğrulama gerektiren uyarılar içeriyor."
    return "SUT kural sonucu belirsiz."
