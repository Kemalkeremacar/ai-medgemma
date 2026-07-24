from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_LIB_ROOT = Path(__file__).resolve().parent.parent
_PROVIZYON_ROOT = _LIB_ROOT.parent

if str(_LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIB_ROOT))

from sut_engine.rule_compiler import load_rules
from sut_engine.sut_evaluator import SUTEvaluator
from sut_engine.settings import DEFAULT_QDRANT_URL, DEFAULT_TEI_BASE_URL

from unified_catalog.backfill_unified_qdrant import DEFAULT_COLLECTION
from unified_catalog.loaders import load_sut_records, sut_by_code
from unified_catalog.normalization import extract_huv_codes, extract_sut_codes, fold
from unified_catalog.unified_retriever import DEFAULT_OUT_DIR, UnifiedCatalogRetriever


DEFAULT_RULES = _PROVIZYON_ROOT / "data" / "generated" / "sut_rules_merged.json"
DEFAULT_SUT_INDEX = _PROVIZYON_ROOT / "data" / "generated" / "sut_index_core.json"


def advise(
    question: str,
    *,
    out_dir: Path = DEFAULT_OUT_DIR,
    rules_path: Path = DEFAULT_RULES,
    sut_index: Path = DEFAULT_SUT_INDEX,
    use_qdrant: bool = True,
    collection: str = DEFAULT_COLLECTION,
    qdrant_url: str = DEFAULT_QDRANT_URL,
    tei_url: str = DEFAULT_TEI_BASE_URL,
    context_limit: int = 8,
    provizyon_context: dict[str, Any] | None = None,
    input_services: list[dict[str, Any]] | None = None,
    allow_huv_crosswalk: bool = False,
) -> dict[str, Any]:
    sut_records = sut_by_code(load_sut_records(sut_index)) if sut_index.exists() else {}
    extracted_huv_codes = extract_huv_codes(question)
    input_sut_codes = _clean_sut_codes(question, extracted_huv_codes)
    input_sut_codes = _merge_sut_codes(input_sut_codes, input_services or [])

    retrieved: list = []
    qdrant_error: str | None = None
    if allow_huv_crosswalk:
        input_huv_codes = extracted_huv_codes
        retriever = UnifiedCatalogRetriever(
            out_dir=out_dir,
            use_qdrant=use_qdrant,
            collection=collection,
            qdrant_url=qdrant_url,
            tei_url=tei_url,
            context_limit=context_limit,
        )
        retrieved = retriever.retrieve(question, limit=context_limit)
        qdrant_error = retriever.qdrant_error
        resolved = _resolved_from_retrieval(
            retrieved,
            input_huv_codes,
            allow_semantic=not (input_huv_codes or input_sut_codes),
        )
    else:
        # HUV→SUT crosswalk kapalı: yalnızca doğrudan SUT kodları kural motoruna alınır.
        input_huv_codes = []
        resolved = []

    resolved.extend(_direct_sut_resolved(input_sut_codes, sut_records))
    resolved = _dedupe_resolved(resolved)

    services_for_eval = []
    for item in resolved:
        if item.get("sut_code") and item.get("rule_eval_allowed"):
            services_for_eval.append(_service_for_eval(item, input_services or []))

    rules = load_rules(rules_path) if rules_path.exists() else []
    evaluator_input = _evaluator_input(provizyon_context, services_for_eval)
    evaluation = SUTEvaluator(rules).evaluate(
        evaluator_input
    )
    rule_lookup = _rules_for_codes(rules, [service["code"] for service in services_for_eval])
    warnings = _advisor_warnings(resolved, services_for_eval, qdrant_error)
    if not allow_huv_crosswalk and extracted_huv_codes:
        warnings = [
            *warnings,
            "HUV→SUT crosswalk kapalı; HUV kodları SUT’a çevrilmedi.",
        ]
    return {
        "question": question,
        "input_huv_codes": input_huv_codes,
        "input_sut_codes": input_sut_codes,
        "allow_huv_crosswalk": allow_huv_crosswalk,
        "retrieval": {
            "qdrant_enabled": use_qdrant and allow_huv_crosswalk,
            "qdrant_collection": collection,
            "qdrant_error": qdrant_error,
            "result_count": len(retrieved),
            "results": [entry.to_dict() for entry in retrieved],
        },
        "resolved_services": resolved,
        "sut_services_evaluated": services_for_eval,
        "provizyon_context_used": {
            key: value for key, value in evaluator_input.items()
            if key != "services"
        },
        "sut_rule_evaluation": evaluation,
        "sut_rules_for_evaluated_codes": rule_lookup,
        "warnings": warnings,
    }


def format_advice(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Soru: {result.get('question')}")
    lines.append("")
    lines.append("Çözümlenen işlemler:")
    if not result.get("resolved_services"):
        lines.append("- İşlem çözümlenemedi. HUV/SUT kodu veya daha belirgin işlem adı gerekli.")
    for item in result.get("resolved_services", []):
        status = "kural motoruna alındı" if item.get("rule_eval_allowed") else "kural motoruna alınmadı"
        lines.append(
            "- "
            f"{item.get('input_kind')} {item.get('input_code') or '-'} "
            f"{item.get('input_name') or ''} -> "
            f"SUT {item.get('sut_code') or '-'} {item.get('sut_name') or ''} | "
            f"{item.get('relation_type') or 'direct_sut'} | "
            f"güven={item.get('confidence') or '-'} | {status}"
        )
        if item.get("warnings"):
            for warning in item["warnings"][:3]:
                lines.append(f"  Uyarı: {warning}")

    evaluation = result.get("sut_rule_evaluation") or {}
    lines.append("")
    lines.append(f"SUT kural sonucu: {evaluation.get('overall_status', 'BILINMIYOR')}")
    summary = evaluation.get("summary") or {}
    lines.append(
        "Bulgular: "
        f"fail={summary.get('fail_count', 0)}, "
        f"warning={summary.get('warning_count', 0)}, "
        f"insufficient={summary.get('insufficient_info_count', 0)}, "
        f"info={summary.get('info_count', 0)}"
    )
    for service in evaluation.get("service_results", []) or []:
        if not service.get("findings"):
            lines.append(f"- SUT {service.get('service_code')}: engelleyici kural bulgusu yok.")
            continue
        for finding in service.get("findings", []):
            lines.append(
                "- "
                f"{finding.get('status')} {finding.get('rule_type')} "
                f"SUT {finding.get('service_code')}: {finding.get('message')}"
            )
            if finding.get("source_quote"):
                lines.append(f"  Kaynak: {finding.get('source_quote')}")

    if result.get("warnings"):
        lines.append("")
        lines.append("Genel uyarılar:")
        for warning in result["warnings"]:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def _resolved_from_retrieval(
    entries: list,
    input_huv_codes: list[str],
    *,
    allow_semantic: bool,
) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    exact_huv_set = set(input_huv_codes)
    for entry in entries:
        row = entry.row
        if exact_huv_set and row.get("huv_code") not in exact_huv_set:
            continue
        if not exact_huv_set and (not allow_semantic or entry.source not in {"qdrant_unified", "local_fallback"}):
            continue
        allowed, reasons = _rule_eval_allowed(row)
        resolved.append(
            {
                "input_kind": "HUV",
                "input_code": row.get("huv_code"),
                "input_name": row.get("huv_name"),
                "sut_code": row.get("sut_code"),
                "sut_name": row.get("sut_name"),
                "relation_type": row.get("relation_type"),
                "confidence": row.get("confidence"),
                "confidence_score": row.get("confidence_score"),
                "review_recommended": row.get("review_recommended"),
                "review_reason": row.get("review_reason"),
                "rule_eval_allowed": allowed,
                "warnings": [*entry.warnings, *reasons],
                "retrieval_source": entry.source,
                "retrieval_score": entry.score,
            }
        )
    return resolved


def _direct_sut_resolved(input_sut_codes: list[str], sut_records: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for code in input_sut_codes:
        sut = sut_records.get(code.upper())
        result.append(
            {
                "input_kind": "SUT",
                "input_code": code.upper(),
                "input_name": sut.name if sut else "",
                "sut_code": code.upper(),
                "sut_name": sut.name if sut else "",
                "relation_type": "direct_sut",
                "confidence": "high",
                "confidence_score": 1.0,
                "review_recommended": False,
                "review_reason": "",
                "rule_eval_allowed": True,
                "warnings": [],
                "retrieval_source": "direct_sut",
                "retrieval_score": 1.0,
            }
        )
    return result


def _service_for_eval(item: dict[str, Any], input_services: list[dict[str, Any]]) -> dict[str, Any]:
    service = {
        "code": item["sut_code"],
        "name": item.get("sut_name") or item["sut_code"],
        "quantity": 1,
        "date": "2026-01-01",
    }
    override = _matching_input_service(item, input_services)
    if not override:
        return service
    for key, value in override.items():
        if value is None or key in {"code", "service_code", "islem_kodu", "code_type"}:
            continue
        if key == "name":
            service.setdefault("input_name", value)
            continue
        service[key] = value
    return service


def _matching_input_service(item: dict[str, Any], input_services: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidate_codes = {
        str(item.get("input_code") or "").upper(),
        str(item.get("sut_code") or "").upper(),
    }
    for service in input_services:
        code = str(
            service.get("code")
            or service.get("service_code")
            or service.get("islem_kodu")
            or ""
        ).upper()
        if code and code in candidate_codes:
            return service

    input_name = fold(str(item.get("input_name") or ""))
    sut_name = fold(str(item.get("sut_name") or ""))
    for service in input_services:
        name = fold(str(service.get("name") or service.get("service_name") or service.get("islem_adi") or ""))
        if name and name in {input_name, sut_name}:
            return service
    return None


def _evaluator_input(provizyon_context: dict[str, Any] | None, services_for_eval: list[dict[str, Any]]) -> dict[str, Any]:
    payload = dict(provizyon_context or {})
    payload.setdefault("provizyon_id", "unified-advisor")
    payload["services"] = services_for_eval
    return payload


def _rule_eval_allowed(row: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not row.get("sut_code"):
        reasons.append("SUT kodu bulunmadığı için SUT kural motoruna alınmadı.")
    if row.get("review_recommended"):
        reasons.append("HUV-SUT eşleşmesi review gerektiriyor.")
    if row.get("confidence") != "high":
        reasons.append(f"Eşleşme güveni yüksek değil: {row.get('confidence')}.")
    if row.get("relation_type") not in {"exact_match", "close_match"}:
        reasons.append(f"İlişki tipi kural kararı için kesin değil: {row.get('relation_type')}.")
    return not reasons, reasons


def _rules_for_codes(rules: list[Any], codes: list[str]) -> dict[str, list[dict[str, Any]]]:
    codes_set = {code.upper() for code in codes if code}
    result: dict[str, list[dict[str, Any]]] = {code: [] for code in sorted(codes_set)}
    for rule in rules:
        source = str(rule.source_code or "").upper()
        if source in codes_set:
            result.setdefault(source, []).append(rule.to_dict())
    return result


def _advisor_warnings(resolved: list[dict[str, Any]], services_for_eval: list[dict[str, Any]], qdrant_error: str | None) -> list[str]:
    warnings: list[str] = []
    if qdrant_error:
        warnings.append(f"Qdrant unified arama hatası/fallback: {qdrant_error}")
    if not resolved:
        warnings.append("Final unified katalogda yeterli eşleşme bulunamadı.")
    if len(services_for_eval) < 2 and len(resolved) >= 2:
        warnings.append("Birden fazla işlem/adayı çözümlendi ancak güven kapısı nedeniyle en az iki SUT kodu birlikte kural motoruna alınamadı.")
    if not services_for_eval:
        warnings.append("SUT kural motoruna alınabilecek güvenilir SUT kodu yok.")
    return warnings


def _dedupe_resolved(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = (
            str(item.get("input_kind") or ""),
            str(item.get("input_code") or ""),
            str(item.get("sut_code") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _clean_sut_codes(question: str, huv_codes: list[str]) -> list[str]:
    huv_suffixes = {code.split(".", 1)[1] for code in huv_codes if "." in code}
    return [
        code for code in extract_sut_codes(question)
        if code not in huv_suffixes
    ]


def _merge_sut_codes(codes: list[str], input_services: list[dict[str, Any]]) -> list[str]:
    """Soru metnindeki SUT kodlarına input_services'teki doğrudan SUT kodlarını ekle."""

    seen = {c.upper() for c in codes}
    merged = list(codes)
    for service in input_services:
        code = str(
            service.get("code")
            or service.get("service_code")
            or service.get("islem_kodu")
            or ""
        ).strip().upper()
        code_type = str(service.get("code_type") or "").strip().upper()
        if not code or code in seen:
            continue
        if code_type == "SUT" or (len(code) == 6 and code.isdigit() and not code.startswith("0")):
            seen.add(code)
            merged.append(code)
    return merged


def to_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
