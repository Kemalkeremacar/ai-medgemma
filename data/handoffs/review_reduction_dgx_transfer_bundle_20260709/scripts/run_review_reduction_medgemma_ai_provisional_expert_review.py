from __future__ import annotations

import argparse
import csv
import html
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
SUT_ROOT = ROOT / "SUT"
if str(SUT_ROOT) not in sys.path:
    sys.path.insert(0, str(SUT_ROOT))

try:
    from sut_engine.settings import (
        DEFAULT_MEDGEMMA_API_KEY,
        DEFAULT_MEDGEMMA_BASE_URL,
        DEFAULT_MEDGEMMA_MODEL,
        DEFAULT_MEDGEMMA_TIMEOUT,
    )
except Exception:
    DEFAULT_MEDGEMMA_BASE_URL = "http://192.168.1.209:8000/v1"
    DEFAULT_MEDGEMMA_API_KEY = "sk-no-key"
    DEFAULT_MEDGEMMA_MODEL = "/raid/monassist1/medgemma_model_gptq_w4"
    DEFAULT_MEDGEMMA_TIMEOUT = 900


SOURCE_EXPERT_JSON = (
    ROOT
    / "SUT/generated/shadow_quality_gate/review_reduction_operational_packs_20260709"
    / "expert_fast_track_review_pack"
    / "expert_fast_track_candidates_all19.json"
)
OUT_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_expert_review_20260709"

SCHEMA_VERSION = "review_reduction_ai_provisional_expert_review.v1"
REQUEST_SCHEMA_VERSION = "review_reduction_ai_provisional_expert_request.v1"
RESPONSE_SCHEMA_VERSION = "review_reduction_ai_provisional_expert_response.v1"

SYSTEM_PROMPT = (
    "Türkçe yanıt ver. Verilmeyen resmi kaynak, klinik ayrıntı veya uzman onayını uydurma. "
    "Sen insan uzman değilsin; AI-only provisional reviewer olarak yanıt ver. "
    "Sadece strict JSON object döndür."
)

REQUEST_CSV_FIELDS = [
    "request_id",
    "priority_order",
    "rank",
    "code",
    "clinical_theme",
    "review_rows",
    "priority_tier",
    "diagnosis_cohort_safety",
    "supported_prefixes_for_expert_review",
]

DECISION_CSV_FIELDS = [
    "priority_order",
    "rank",
    "code",
    "clinical_theme",
    "review_rows",
    "priority_tier",
    "ai_review_status",
    "ai_expert_decision",
    "confidence",
    "confidence_score",
    "evidence_strength",
    "safety_level",
    "approved_prefixes_if_any",
    "rejected_prefixes_if_any",
    "prefixes_requiring_manual_review",
    "missing_evidence",
    "risk_notes",
    "reasoning_summary",
    "official_source_needed",
    "human_admin_approval_present",
    "shadow_staging_allowed_by_ai_only",
    "apply_ready",
    "auto_apply",
    "validation_status",
    "validation_errors",
    "validation_warnings",
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def csv_value(value: Any) -> str:
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else str(value)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field, "")) for field in fieldnames})


def safety_block(*, calls_medgemma: bool) -> dict[str, bool]:
    return {
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "live_runtime_override": False,
        "auto_apply": False,
        "exports_case_level_rows": False,
        "calls_medgemma": calls_medgemma,
        "claims_human_admin_approval": False,
    }


def response_schema() -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "required_fields": [
            "schema_version",
            "request_id",
            "code",
            "ai_review_status",
            "ai_reviewer_role",
            "ai_expert_decision",
            "approved_prefixes_if_any",
            "rejected_prefixes_if_any",
            "prefixes_requiring_manual_review",
            "confidence",
            "evidence_strength",
            "safety_level",
            "missing_evidence",
            "risk_notes",
            "reasoning_summary",
            "official_source_needed",
            "human_admin_approval_present",
            "shadow_staging_allowed_by_ai_only",
            "apply_ready",
            "auto_apply",
            "required_shadow_gates",
            "no_live_write_ack",
            "no_qdrant_write_ack",
            "no_human_approval_claim_ack",
            "shadow_only_ack",
        ],
        "allowed_values": {
            "schema_version": [RESPONSE_SCHEMA_VERSION],
            "ai_review_status": ["completed", "blocked", "error"],
            "ai_reviewer_role": ["ai_provisional_medical_policy_reviewer"],
            "ai_expert_decision": [
                "ai_provisional_approve_shadow_staging_specific_prefixes",
                "ai_provisional_conditional_only",
                "ai_keep_manual_review",
                "ai_reject_review_reduction",
                "ai_request_more_evidence",
            ],
            "evidence_strength": ["weak", "moderate", "strong"],
            "safety_level": ["low_risk", "medium_risk", "high_risk"],
        },
        "hard_constraints": [
            "This is not human/admin/expert approval.",
            "human_admin_approval_present must be false.",
            "apply_ready and auto_apply must be false.",
            "official_source_needed must be true for every non-rejected row.",
            "shadow_staging_allowed_by_ai_only may be true only for AI provisional decisions; it still does not allow live apply.",
            "approved_prefixes_if_any must be a subset of the input supported prefixes.",
            "If confidence < 0.85, ai_expert_decision must not be ai_provisional_approve_shadow_staging_specific_prefixes.",
            "For non-specific symptom dominant cohorts, prefer conditional_only unless a very narrow prefix subset is justified.",
            "Return no markdown or code fences.",
        ],
    }


def compact_candidate(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "priority_order": row.get("priority_order"),
        "priority_tier": row.get("priority_tier"),
        "rank": row.get("rank"),
        "code": row.get("code"),
        "code_type": row.get("code_type"),
        "clinical_theme": row.get("clinical_theme"),
        "review_rows": row.get("review_rows"),
        "top3_diagnosis_share": row.get("top3_diagnosis_share"),
        "review_reduction_potential": row.get("review_reduction_potential"),
        "source_risk_level": row.get("source_risk_level"),
        "source_recommended_action": row.get("source_recommended_action"),
        "medgemma_confidence_previous_triage": row.get("medgemma_confidence"),
        "diagnosis_cohort_safety_previous_triage": row.get("diagnosis_cohort_safety"),
        "supported_prefixes_for_expert_review": row.get("supported_prefixes_for_expert_review") or [],
        "prefixes_medgemma_says_keep_review": row.get("prefixes_medgemma_says_keep_review") or [],
        "top_diagnoses": row.get("top_diagnoses"),
        "current_runtime_rule_context": row.get("current_runtime_rule_context") or {},
        "medgemma_missing_evidence_previous_triage": row.get("medgemma_missing_evidence") or [],
        "medgemma_risk_notes_previous_triage": row.get("medgemma_risk_notes") or [],
        "medgemma_reasoning_summary_previous_triage": row.get("medgemma_reasoning_summary"),
        "expert_review_question": row.get("expert_review_question"),
    }


def render_prompt(request: dict[str, Any]) -> str:
    payload = {
        "response_schema": response_schema(),
        "request": request,
        "review_instructions": {
            "role": "AI-only provisional medical-policy reviewer; not a human expert",
            "decision_goal": "Fill the expert decision form as a provisional AI opinion when no human expert exists.",
            "be_conservative": True,
            "use_only_aggregate_evidence": True,
            "never_claim_approval": True,
            "separate_prefixes": "Identify prefixes that could be shadow-staged versus prefixes that must remain manual REVIEW.",
            "reject_if_needed": "Reject or keep manual review if evidence is broad, non-specific, unsafe, or needs official source.",
        },
    }
    return (
        "Aşağıdaki review-reduction adayı için insan expert yokluğunda AI-only provisional expert review yap.\n"
        "Bu çıktı canlı karar/onay değildir; sadece daha sonra shadow staging veya ek kanıt için önceliklendirme sağlar.\n"
        "Sadece verilen aggregate tanı dağılımını kullan. Resmi SUT/komite kaynağı yoksa bunu açıkça belirt.\n"
        "approved_prefixes_if_any alanına sadece gerçekten dar ve savunulabilir prefix'leri yaz.\n"
        "Geniş, non-specific veya riskli prefix'leri prefixes_requiring_manual_review alanına koy.\n"
        "human_admin_approval_present=false, apply_ready=false, auto_apply=false olmalı.\n"
        "Sadece strict JSON object döndür; markdown/code fence yok.\n\n"
        f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}"
    )


def request_row(candidate: dict[str, Any]) -> dict[str, Any]:
    request_id = f"ai_expert_{int(candidate.get('priority_order')):04d}_{candidate.get('code')}"
    request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "request_id": request_id,
        "task": "medgemma_ai_only_provisional_expert_review",
        "candidate": compact_candidate(candidate),
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "safety": safety_block(calls_medgemma=False),
    }
    request["prompt"] = render_prompt(request)
    return request


def request_csv_row(request: dict[str, Any]) -> dict[str, Any]:
    candidate = request.get("candidate") or {}
    return {
        "request_id": request.get("request_id"),
        "priority_order": candidate.get("priority_order"),
        "rank": candidate.get("rank"),
        "code": candidate.get("code"),
        "clinical_theme": candidate.get("clinical_theme"),
        "review_rows": candidate.get("review_rows"),
        "priority_tier": candidate.get("priority_tier"),
        "diagnosis_cohort_safety": candidate.get("diagnosis_cohort_safety_previous_triage"),
        "supported_prefixes_for_expert_review": candidate.get("supported_prefixes_for_expert_review"),
    }


def _extract_fenced_json(text: str) -> str:
    stripped = (text or "").strip()
    if "```json" in stripped:
        start = stripped.find("```json") + len("```json")
        end = stripped.find("```", start)
        if end != -1:
            return stripped[start:end].strip()
    if stripped.startswith("```"):
        stripped = stripped[3:]
        end = stripped.rfind("```")
        if end != -1:
            stripped = stripped[:end]
    return stripped.strip()


def extract_json_object_text(text: str) -> str:
    candidate = _extract_fenced_json(text)
    if candidate.startswith("{") and candidate.endswith("}"):
        return candidate
    start = candidate.find("{")
    if start == -1:
        raise ValueError("json_object_not_found")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return candidate[start : index + 1]
    raise ValueError("json_object_not_closed")


def parse_json_object(raw_text: str) -> dict[str, Any]:
    parsed = json.loads(extract_json_object_text(raw_text))
    if not isinstance(parsed, dict):
        raise ValueError("json_root_not_object")
    return parsed


def chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
    max_tokens: int,
    temperature: float,
) -> tuple[str, dict[str, Any]]:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    elapsed_seconds = round(time.time() - started, 3)
    parsed = json.loads(raw)
    return str(parsed["choices"][0]["message"]["content"]), {
        "elapsed_seconds": elapsed_seconds,
        "usage": parsed.get("usage") or {},
        "finish_reason": (parsed.get("choices") or [{}])[0].get("finish_reason"),
    }


def fallback_error_response(request: dict[str, Any], error_type: str, error_message: str) -> dict[str, Any]:
    candidate = request.get("candidate") or {}
    return {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "request_id": request.get("request_id"),
        "code": candidate.get("code"),
        "ai_review_status": "error",
        "ai_reviewer_role": "ai_provisional_medical_policy_reviewer",
        "ai_expert_decision": "ai_request_more_evidence",
        "approved_prefixes_if_any": [],
        "rejected_prefixes_if_any": [],
        "prefixes_requiring_manual_review": candidate.get("supported_prefixes_for_expert_review") or [],
        "confidence": 0.0,
        "evidence_strength": "weak",
        "safety_level": "high_risk",
        "missing_evidence": [error_type],
        "risk_notes": [error_message[:500]],
        "reasoning_summary": f"AI provisional expert review tamamlanamadı: {error_type}.",
        "official_source_needed": True,
        "human_admin_approval_present": False,
        "shadow_staging_allowed_by_ai_only": False,
        "apply_ready": False,
        "auto_apply": False,
        "required_shadow_gates": [
            "human_admin_approval_required",
            "official_source_or_committee_validation_required",
            "deterministic_batch_preview_required",
        ],
        "no_live_write_ack": True,
        "no_qdrant_write_ack": True,
        "no_human_approval_claim_ack": True,
        "shadow_only_ack": True,
    }


def list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def confidence_score(value: Any) -> float | None:
    try:
        confidence = float(value)
    except Exception:
        label = str(value or "").strip().lower()
        label_scores = {
            "very_high": 0.95,
            "very high": 0.95,
            "high": 0.9,
            "yüksek": 0.9,
            "medium_high": 0.82,
            "medium-high": 0.82,
            "moderate": 0.75,
            "medium": 0.75,
            "orta": 0.75,
            "low": 0.4,
            "düşük": 0.4,
            "weak": 0.3,
        }
        return label_scores.get(label)
    if 0.0 <= confidence <= 1.0:
        return confidence
    if 1.0 < confidence <= 100.0:
        return confidence / 100.0
    return None


def output_paths(out_dir: Path) -> dict[str, Path]:
    return {
        "requests_json": out_dir / "ai_provisional_expert_review_requests.json",
        "requests_jsonl": out_dir / "ai_provisional_expert_review_requests.jsonl",
        "requests_csv": out_dir / "ai_provisional_expert_review_requests.csv",
        "responses_jsonl": out_dir / "ai_provisional_expert_review_responses.jsonl",
        "raw_jsonl": out_dir / "ai_provisional_expert_review_raw_responses.jsonl",
        "validation_json": out_dir / "ai_provisional_expert_review_validation_rows.json",
        "validation_csv": out_dir / "ai_provisional_expert_review_validation_rows.csv",
        "decisions_json": out_dir / "ai_provisional_expert_decisions_all19.json",
        "decisions_csv": out_dir / "ai_provisional_expert_decisions_all19.csv",
        "shadow_candidates_json": out_dir / "ai_provisional_shadow_staging_candidates.json",
        "shadow_candidates_csv": out_dir / "ai_provisional_shadow_staging_candidates.csv",
        "dashboard": out_dir / "ai_provisional_expert_review_dashboard.html",
        "manifest": out_dir / "TASK_MANIFEST.json",
    }


def validate_response(response: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    schema = response_schema()
    candidate = request.get("candidate") or {}
    input_prefixes = set(str(item) for item in list_value(candidate.get("supported_prefixes_for_expert_review")))
    errors: list[str] = []
    warnings: list[str] = []

    for field in schema["required_fields"]:
        if field not in response:
            errors.append(f"missing_required_field:{field}")
    for field, values in schema["allowed_values"].items():
        if field in response and response.get(field) not in values:
            errors.append(f"invalid_enum:{field}")
    if response.get("request_id") != request.get("request_id"):
        errors.append("request_id_mismatch")
    if str(response.get("code") or "") != str(candidate.get("code") or ""):
        errors.append("code_mismatch")
    confidence = confidence_score(response.get("confidence"))
    if confidence is None:
        errors.append("confidence_must_be_number_or_known_label")
    if response.get("human_admin_approval_present") is not False:
        errors.append("human_admin_approval_present_must_be_false")
    if response.get("apply_ready") is not False:
        errors.append("apply_ready_must_be_false")
    if response.get("auto_apply") is not False:
        errors.append("auto_apply_must_be_false")
    for field in ["no_live_write_ack", "no_qdrant_write_ack", "no_human_approval_claim_ack", "shadow_only_ack"]:
        if response.get(field) is not True:
            errors.append(f"{field}_must_be_true")
    if response.get("ai_expert_decision") == "ai_provisional_approve_shadow_staging_specific_prefixes":
        if confidence is None or confidence < 0.85:
            errors.append("ai_provisional_approval_requires_confidence_at_least_0_85")
        if not list_value(response.get("approved_prefixes_if_any")):
            errors.append("ai_provisional_approval_requires_approved_prefixes")
        if response.get("shadow_staging_allowed_by_ai_only") is not True:
            errors.append("ai_provisional_approval_requires_shadow_staging_allowed_by_ai_only_true")
    approved = set(list_value(response.get("approved_prefixes_if_any")))
    rejected = set(list_value(response.get("rejected_prefixes_if_any")))
    manual = set(list_value(response.get("prefixes_requiring_manual_review")))
    unexpected = sorted((approved | rejected | manual) - input_prefixes)
    if unexpected:
        warnings.append("prefix_not_in_input:" + ",".join(unexpected))
    if approved & manual:
        warnings.append("approved_prefix_also_manual_review:" + ",".join(sorted(approved & manual)))

    is_valid = not errors
    return {
        "request_id": request.get("request_id"),
        "code": candidate.get("code"),
        "validation_status": "valid" if is_valid else "error",
        "is_response_valid": is_valid,
        "errors": errors,
        "warnings": warnings,
        "source_request": request,
        "source_response": response,
    }


def decision_row(validation: dict[str, Any]) -> dict[str, Any]:
    request = validation.get("source_request") or {}
    candidate = request.get("candidate") or {}
    response = validation.get("source_response") or {}
    return {
        "priority_order": candidate.get("priority_order"),
        "rank": candidate.get("rank"),
        "code": candidate.get("code"),
        "clinical_theme": candidate.get("clinical_theme"),
        "review_rows": candidate.get("review_rows"),
        "priority_tier": candidate.get("priority_tier"),
        "ai_review_status": response.get("ai_review_status"),
        "ai_expert_decision": response.get("ai_expert_decision"),
        "confidence": response.get("confidence"),
        "confidence_score": confidence_score(response.get("confidence")),
        "evidence_strength": response.get("evidence_strength"),
        "safety_level": response.get("safety_level"),
        "approved_prefixes_if_any": list_value(response.get("approved_prefixes_if_any")),
        "rejected_prefixes_if_any": list_value(response.get("rejected_prefixes_if_any")),
        "prefixes_requiring_manual_review": list_value(response.get("prefixes_requiring_manual_review")),
        "missing_evidence": list_value(response.get("missing_evidence")),
        "risk_notes": list_value(response.get("risk_notes")),
        "reasoning_summary": response.get("reasoning_summary"),
        "official_source_needed": response.get("official_source_needed"),
        "human_admin_approval_present": False,
        "shadow_staging_allowed_by_ai_only": response.get("shadow_staging_allowed_by_ai_only") is True,
        "apply_ready": False,
        "auto_apply": False,
        "validation_status": validation.get("validation_status"),
        "validation_errors": validation.get("errors") or [],
        "validation_warnings": validation.get("warnings") or [],
    }


def build_html(rows: list[dict[str, Any]]) -> str:
    columns = [
        "priority_order",
        "code",
        "review_rows",
        "priority_tier",
        "ai_expert_decision",
        "confidence",
        "confidence_score",
        "approved_prefixes_if_any",
        "prefixes_requiring_manual_review",
    ]
    header = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
    body = []
    for row in rows:
        body.append(
            "<tr>"
            + "".join(f"<td>{html.escape(csv_value(row.get(col, '')))}</td>" for col in columns)
            + "</tr>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>AI Provisional Expert Review</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 8px;font-size:12px}th{background:#f3f3f3}</style>"
        "</head><body>"
        "<h1>AI Provisional Expert Review</h1>"
        "<p>AI-only shadow opinion. This is not human/admin/expert approval and does not authorize live changes.</p>"
        f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        "</body></html>"
    )


def run_review(
    *,
    source_json: Path = SOURCE_EXPERT_JSON,
    out_dir: Path = OUT_DIR,
    base_url: str = DEFAULT_MEDGEMMA_BASE_URL,
    api_key: str = DEFAULT_MEDGEMMA_API_KEY,
    model: str = DEFAULT_MEDGEMMA_MODEL,
    timeout: int = DEFAULT_MEDGEMMA_TIMEOUT,
    max_tokens: int = 1400,
    temperature: float = 0.0,
    overwrite: bool = False,
) -> dict[str, Any]:
    candidates = list(load_json(source_json) or [])
    requests = [request_row(candidate) for candidate in candidates]
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = output_paths(out_dir)
    if overwrite:
        for path in paths.values():
            if path.exists():
                path.unlink()
    elif paths["responses_jsonl"].exists() or paths["raw_jsonl"].exists():
        raise FileExistsError("Output exists; pass --overwrite to replace generated AI provisional expert review outputs.")

    write_json(paths["requests_json"], requests)
    write_jsonl(paths["requests_jsonl"], requests)
    write_csv(paths["requests_csv"], [request_csv_row(request) for request in requests], REQUEST_CSV_FIELDS)

    status_counts: Counter[str] = Counter()
    parse_counts: Counter[str] = Counter()
    total_elapsed = 0.0
    responses: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    for index, request in enumerate(requests, start=1):
        raw_row = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now_iso(),
            "request_index": index,
            "request_id": request.get("request_id"),
            "code": (request.get("candidate") or {}).get("code"),
            "medgemma_model": model,
            "safety": safety_block(calls_medgemma=True),
        }
        try:
            raw_text, metadata = chat_completion(
                base_url=base_url,
                api_key=api_key,
                model=model,
                prompt=str(request.get("prompt") or ""),
                timeout=timeout,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            raw_row["raw_text"] = raw_text
            raw_row["metadata"] = metadata
            total_elapsed += float(metadata.get("elapsed_seconds") or 0.0)
            try:
                response = parse_json_object(raw_text)
                parse_status = "parsed"
            except Exception as parse_exc:
                response = fallback_error_response(request, "medgemma_response_parse_error", str(parse_exc))
                raw_row["parse_error"] = str(parse_exc)
                parse_status = "parse_error"
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            response = fallback_error_response(request, type(exc).__name__, str(exc))
            raw_row["api_error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
            parse_status = "api_error"

        validation = validate_response(response, request)
        responses.append(response)
        validations.append(validation)
        append_jsonl(paths["raw_jsonl"], raw_row)
        append_jsonl(paths["responses_jsonl"], response)
        parse_counts[parse_status] += 1
        status_counts[str(response.get("ai_review_status") or "")] += 1
        print(
            json.dumps(
                {
                    "index": index,
                    "total": len(requests),
                    "code": (request.get("candidate") or {}).get("code"),
                    "parse_status": parse_status,
                    "ai_review_status": response.get("ai_review_status"),
                    "ai_expert_decision": response.get("ai_expert_decision"),
                    "confidence": response.get("confidence"),
                    "validation_status": validation.get("validation_status"),
                },
                ensure_ascii=False,
            )
        )

    decisions = [decision_row(validation) for validation in validations]
    shadow_candidates = [
        row
        for row in decisions
        if row.get("validation_status") == "valid"
        and row.get("shadow_staging_allowed_by_ai_only") is True
        and row.get("ai_expert_decision")
        in {
            "ai_provisional_approve_shadow_staging_specific_prefixes",
            "ai_provisional_conditional_only",
        }
        and row.get("approved_prefixes_if_any")
    ]
    write_json(paths["validation_json"], validations)
    write_csv(paths["validation_csv"], validations, ["request_id", "code", "validation_status", "is_response_valid", "errors", "warnings"])
    write_json(paths["decisions_json"], decisions)
    write_csv(paths["decisions_csv"], decisions, DECISION_CSV_FIELDS)
    write_json(paths["shadow_candidates_json"], shadow_candidates)
    write_csv(paths["shadow_candidates_csv"], shadow_candidates, DECISION_CSV_FIELDS)
    paths["dashboard"].write_text(build_html(decisions), encoding="utf-8")

    valid_count = sum(1 for validation in validations if validation.get("is_response_valid"))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "mode": "medgemma_ai_only_provisional_expert_review",
        "source_expert_fast_track_json": str(source_json),
        "out_dir": str(out_dir),
        "medgemma_base_url": base_url,
        "medgemma_model": model,
        "counts": {
            "requests": len(requests),
            "responses": len(responses),
            "valid_responses": valid_count,
            "error_responses": len(responses) - valid_count,
            "parse_counts": dict(parse_counts),
            "status_counts": dict(status_counts),
            "decision_counts": dict(Counter(str(row.get("ai_expert_decision") or "") for row in decisions)),
            "shadow_candidate_rows": len(shadow_candidates),
        },
        "timing": {"total_model_elapsed_seconds": round(total_elapsed, 3)},
        "safety": safety_block(calls_medgemma=True),
        "generated_files": [str(path) for path in paths.values()],
        "critical_warning": (
            "These are AI-only provisional opinions. They do not constitute human/admin/expert approval "
            "and do not authorize live rules, Qdrant writes, runtime override, or payment approval."
        ),
    }
    write_json(paths["manifest"], manifest)
    return manifest

def postprocess_existing(
    *,
    source_json: Path = SOURCE_EXPERT_JSON,
    out_dir: Path = OUT_DIR,
    base_url: str = DEFAULT_MEDGEMMA_BASE_URL,
    model: str = DEFAULT_MEDGEMMA_MODEL,
) -> dict[str, Any]:
    candidates = list(load_json(source_json) or [])
    requests = [request_row(candidate) for candidate in candidates]
    paths = output_paths(out_dir)
    responses = read_jsonl(paths["responses_jsonl"])
    by_request_id = {str(response.get("request_id")): response for response in responses}
    ordered_responses = [by_request_id.get(str(request.get("request_id"))) for request in requests]
    missing = [str(request.get("request_id")) for request, response in zip(requests, ordered_responses) if response is None]
    if missing:
        raise ValueError("missing_existing_responses:" + ",".join(missing))
    responses = [response for response in ordered_responses if response is not None]
    validations = [validate_response(response, request) for request, response in zip(requests, responses)]
    decisions = [decision_row(validation) for validation in validations]
    shadow_candidates = [
        row
        for row in decisions
        if row.get("validation_status") == "valid"
        and row.get("shadow_staging_allowed_by_ai_only") is True
        and row.get("ai_expert_decision")
        in {
            "ai_provisional_approve_shadow_staging_specific_prefixes",
            "ai_provisional_conditional_only",
        }
        and row.get("approved_prefixes_if_any")
    ]
    write_json(paths["validation_json"], validations)
    write_csv(paths["validation_csv"], validations, ["request_id", "code", "validation_status", "is_response_valid", "errors", "warnings"])
    write_json(paths["decisions_json"], decisions)
    write_csv(paths["decisions_csv"], decisions, DECISION_CSV_FIELDS)
    write_json(paths["shadow_candidates_json"], shadow_candidates)
    write_csv(paths["shadow_candidates_csv"], shadow_candidates, DECISION_CSV_FIELDS)
    paths["dashboard"].write_text(build_html(decisions), encoding="utf-8")

    previous_manifest: dict[str, Any] = {}
    if paths["manifest"].exists():
        previous_manifest = load_json(paths["manifest"]) or {}
    old_timing = previous_manifest.get("timing") or {}
    valid_count = sum(1 for validation in validations if validation.get("is_response_valid"))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "mode": "medgemma_ai_only_provisional_expert_review",
        "postprocessed_existing_responses": True,
        "source_expert_fast_track_json": str(source_json),
        "out_dir": str(out_dir),
        "medgemma_base_url": base_url,
        "medgemma_model": model,
        "counts": {
            "requests": len(requests),
            "responses": len(responses),
            "valid_responses": valid_count,
            "error_responses": len(responses) - valid_count,
            "parse_counts": (previous_manifest.get("counts") or {}).get("parse_counts") or {},
            "status_counts": dict(Counter(str(response.get("ai_review_status") or "") for response in responses)),
            "decision_counts": dict(Counter(str(row.get("ai_expert_decision") or "") for row in decisions)),
            "shadow_candidate_rows": len(shadow_candidates),
        },
        "timing": {"total_model_elapsed_seconds": old_timing.get("total_model_elapsed_seconds", 0.0)},
        "safety": safety_block(calls_medgemma=True),
        "generated_files": [str(path) for path in paths.values()],
        "critical_warning": (
            "These are AI-only provisional opinions. They do not constitute human/admin/expert approval "
            "and do not authorize live rules, Qdrant writes, runtime override, or payment approval."
        ),
    }
    write_json(paths["manifest"], manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MedGemma AI-only provisional expert review for fast-track candidates.")
    parser.add_argument("--source-json", type=Path, default=SOURCE_EXPERT_JSON)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--base-url", default=DEFAULT_MEDGEMMA_BASE_URL)
    parser.add_argument("--api-key", default=DEFAULT_MEDGEMMA_API_KEY)
    parser.add_argument("--model", default=DEFAULT_MEDGEMMA_MODEL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_MEDGEMMA_TIMEOUT)
    parser.add_argument("--max-tokens", type=int, default=1400)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--postprocess-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.postprocess_only:
        manifest = postprocess_existing(
            source_json=args.source_json,
            out_dir=args.out_dir,
            base_url=args.base_url,
            model=args.model,
        )
    else:
        manifest = run_review(
            source_json=args.source_json,
            out_dir=args.out_dir,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            timeout=args.timeout,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            overwrite=args.overwrite,
        )
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"AI provisional expert review written: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
