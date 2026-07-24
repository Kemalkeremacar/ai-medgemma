from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
DEFAULT_HANDOFF_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_medgemma_shadow_handoff_20260709"
DEFAULT_REQUESTS_PATH = DEFAULT_HANDOFF_DIR / "medgemma_review_reduction_shadow_requests.jsonl"
DEFAULT_RESPONSES_PATH = DEFAULT_HANDOFF_DIR / "medgemma_review_reduction_shadow_responses.jsonl"
DEFAULT_OUT_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_medgemma_shadow_merge_20260709"

SCHEMA_VERSION = "review_reduction_medgemma_shadow_merge.v1"
VALIDATION_SCHEMA_VERSION = "review_reduction_medgemma_shadow_response_validation.v1"
TRIAGE_SCHEMA_VERSION = "review_reduction_medgemma_shadow_triage.v1"
RESPONSE_SCHEMA_VERSION = "review_reduction_medgemma_shadow_response.v1"

REQUIRED_RESPONSE_FIELDS = [
    "schema_version",
    "request_id",
    "code",
    "medgemma_status",
    "clinical_plausibility",
    "diagnosis_cohort_safety",
    "mapping_assessment",
    "confidence",
    "confidence_label",
    "eligible_for_human_expert_fast_track",
    "recommended_triage",
    "supported_prefixes",
    "prefixes_to_keep_review",
    "missing_evidence",
    "risk_notes",
    "reasoning_summary",
    "no_live_write_ack",
    "no_human_approval_claim_ack",
    "shadow_only_ack",
]

ALLOWED_VALUES = {
    "schema_version": {RESPONSE_SCHEMA_VERSION},
    "medgemma_status": {"completed", "blocked", "error"},
    "clinical_plausibility": {"plausible", "partially_plausible", "not_plausible", "insufficient_evidence"},
    "diagnosis_cohort_safety": {
        "narrow_candidate",
        "conditional_only",
        "keep_manual_review",
        "mapping_required_first",
        "insufficient_evidence",
    },
    "mapping_assessment": {
        "not_needed",
        "mapping_required_first",
        "canonical_identity_uncertain",
        "candidate_for_mapping_review",
    },
    "confidence_label": {"very_low", "low", "medium", "high"},
    "recommended_triage": {
        "send_to_expert_priority_1",
        "send_to_expert_priority_2",
        "mapping_backlog_first",
        "keep_manual_review_observation",
        "reject_for_review_reduction",
        "blocked_no_inference",
    },
}

VALIDATION_CSV_FIELDS = [
    "request_id",
    "code",
    "row_status",
    "is_response_valid",
    "medgemma_status",
    "confidence",
    "eligible_for_human_expert_fast_track",
    "recommended_triage",
    "errors",
    "warnings",
]

TRIAGE_CSV_FIELDS = [
    "request_id",
    "rank",
    "code",
    "code_type",
    "lookup_status",
    "clinical_theme",
    "review_rows",
    "top3_diagnosis_share",
    "review_reduction_potential",
    "source_risk_level",
    "source_recommended_action",
    "medgemma_status",
    "clinical_plausibility",
    "diagnosis_cohort_safety",
    "mapping_assessment",
    "confidence",
    "eligible_for_human_expert_fast_track",
    "recommended_triage",
    "merged_triage_category",
    "supported_prefixes",
    "prefixes_to_keep_review",
    "missing_evidence",
    "risk_notes",
    "reasoning_summary",
    "human_admin_approval_present",
    "apply_ready",
    "auto_apply",
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            if not isinstance(row, dict):
                raise ValueError(f"jsonl_line_{line_number}_root_not_object")
            rows.append(row)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def safety_block() -> dict[str, bool]:
    return {
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "live_runtime_override": False,
        "auto_apply": False,
        "exports_case_level_rows": False,
        "calls_medgemma": False,
        "claims_human_admin_approval": False,
    }


def request_id(row: dict[str, Any]) -> str:
    return str(row.get("request_id") or "").strip()


def as_float(value: Any) -> float | None:
    try:
        confidence = float(value)
    except Exception:
        return None
    if confidence < 0.0 or confidence > 1.0:
        return None
    return confidence


def list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if isinstance(value, str) and ";" in value:
        return [part.strip() for part in value.split(";") if part.strip()]
    return [str(value)] if str(value or "").strip() else []


def validate_response_row(
    response: dict[str, Any],
    *,
    request_by_id: dict[str, dict[str, Any]],
    duplicate_response_ids: set[str],
) -> dict[str, Any]:
    rid = request_id(response)
    request = request_by_id.get(rid) or {}
    candidate = request.get("candidate") or {}
    input_prefixes = set(str(item) for item in candidate.get("top_diagnosis_prefixes_for_expert_review") or [])
    errors: list[str] = []
    warnings: list[str] = []

    for field in REQUIRED_RESPONSE_FIELDS:
        if field not in response:
            errors.append(f"missing_required_field:{field}")
    if not rid:
        errors.append("missing_request_id")
    if rid in duplicate_response_ids:
        errors.append("duplicate_response_request_id")
    if not request:
        errors.append("response_request_id_not_in_requests")
    if request and str(response.get("code") or "") != str(candidate.get("code") or ""):
        errors.append("response_code_does_not_match_request")
    for field, allowed_values in ALLOWED_VALUES.items():
        if field in response and response.get(field) not in allowed_values:
            errors.append(f"invalid_enum:{field}")

    confidence = as_float(response.get("confidence"))
    if confidence is None:
        errors.append("confidence_must_be_number_between_0_and_1")
    if not isinstance(response.get("eligible_for_human_expert_fast_track"), bool):
        errors.append("eligible_for_human_expert_fast_track_must_be_boolean")
    for field in ["no_live_write_ack", "no_human_approval_claim_ack", "shadow_only_ack"]:
        if response.get(field) is not True:
            errors.append(f"{field}_must_be_true")

    if response.get("eligible_for_human_expert_fast_track") is True:
        if response.get("medgemma_status") != "completed":
            errors.append("fast_track_true_requires_completed_status")
        if confidence is None or confidence < 0.85:
            errors.append("fast_track_true_requires_confidence_at_least_0_85")
        if response.get("clinical_plausibility") != "plausible":
            errors.append("fast_track_true_requires_plausible_clinical_plausibility")
        if response.get("diagnosis_cohort_safety") not in {"narrow_candidate", "conditional_only"}:
            errors.append("fast_track_true_requires_narrow_or_conditional_cohort_safety")
        if response.get("mapping_assessment") in {"mapping_required_first", "canonical_identity_uncertain"}:
            errors.append("fast_track_true_forbidden_when_mapping_unresolved")
        if response.get("recommended_triage") not in {"send_to_expert_priority_1", "send_to_expert_priority_2"}:
            errors.append("fast_track_true_requires_expert_priority_triage")
    if response.get("medgemma_status") == "blocked":
        if response.get("eligible_for_human_expert_fast_track") is True:
            errors.append("blocked_response_cannot_be_fast_track")
        if response.get("recommended_triage") != "blocked_no_inference":
            warnings.append("blocked_response_should_use_blocked_no_inference_triage")

    supported_prefixes = set(list_value(response.get("supported_prefixes")))
    unexpected_supported = sorted(supported_prefixes - input_prefixes)
    if unexpected_supported:
        warnings.append("supported_prefix_not_in_input:" + ",".join(unexpected_supported))

    is_valid = not errors
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "request_id": rid,
        "code": response.get("code"),
        "row_status": "valid" if is_valid else "error",
        "is_response_valid": is_valid,
        "medgemma_status": response.get("medgemma_status"),
        "confidence": response.get("confidence"),
        "eligible_for_human_expert_fast_track": response.get("eligible_for_human_expert_fast_track"),
        "recommended_triage": response.get("recommended_triage"),
        "errors": errors,
        "warnings": warnings,
        "source_request": request,
        "source_response": response,
    }


def merged_triage_category(validation_row: dict[str, Any]) -> str:
    if not validation_row.get("is_response_valid"):
        return "response_invalid_manual_review_required"
    response = validation_row.get("source_response") or {}
    if response.get("medgemma_status") == "blocked":
        return "medgemma_blocked_no_triage"
    if response.get("eligible_for_human_expert_fast_track") is True:
        return "fast_track_to_human_expert_review"
    if response.get("mapping_assessment") in {"mapping_required_first", "candidate_for_mapping_review"}:
        return "mapping_backlog_before_policy"
    if response.get("recommended_triage") == "reject_for_review_reduction":
        return "reject_for_review_reduction"
    if response.get("diagnosis_cohort_safety") in {"keep_manual_review", "insufficient_evidence"}:
        return "keep_manual_review_observation"
    return "human_expert_review_optional"


def build_validation_report(request_rows: list[dict[str, Any]], response_rows: list[dict[str, Any]]) -> dict[str, Any]:
    request_by_id = {request_id(row): row for row in request_rows if request_id(row)}
    response_ids = [request_id(row) for row in response_rows if request_id(row)]
    duplicate_response_ids = {value for value, count in Counter(response_ids).items() if count > 1}
    validation_rows = [
        validate_response_row(
            response,
            request_by_id=request_by_id,
            duplicate_response_ids=duplicate_response_ids,
        )
        for response in response_rows
    ]
    missing_request_ids = sorted(set(request_by_id) - set(response_ids))
    extra_response_ids = sorted(set(response_ids) - set(request_by_id))
    valid_rows = [row for row in validation_rows if row.get("is_response_valid")]
    error_rows = [row for row in validation_rows if row.get("errors")]
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "mode": "review_reduction_medgemma_shadow_response_validation",
        "counts": {
            "request_rows": len(request_rows),
            "response_rows": len(response_rows),
            "valid_responses": len(valid_rows),
            "error_rows": len(error_rows),
            "warning_rows": sum(1 for row in validation_rows if row.get("warnings")),
            "missing_request_ids": len(missing_request_ids),
            "extra_response_ids": len(extra_response_ids),
            "duplicate_response_request_ids": len(duplicate_response_ids),
            "fast_track_rows": sum(
                1
                for row in valid_rows
                if (row.get("source_response") or {}).get("eligible_for_human_expert_fast_track") is True
            ),
            "recommended_triage_counts": dict(
                Counter(str((row.get("source_response") or {}).get("recommended_triage") or "") for row in valid_rows)
            ),
            "clinical_plausibility_counts": dict(
                Counter(str((row.get("source_response") or {}).get("clinical_plausibility") or "") for row in valid_rows)
            ),
            "merged_triage_category_counts": dict(Counter(merged_triage_category(row) for row in validation_rows)),
        },
        "missing_request_id_values": missing_request_ids,
        "extra_response_id_values": extra_response_ids,
        "blocked_reason": "validation_errors_present" if error_rows or missing_request_ids or extra_response_ids else "",
        "safety": safety_block(),
        "validation_rows": validation_rows,
    }


def triage_row(validation_row: dict[str, Any]) -> dict[str, Any]:
    request = validation_row.get("source_request") or {}
    candidate = request.get("candidate") or {}
    response = validation_row.get("source_response") or {}
    return {
        "schema_version": TRIAGE_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "request_id": validation_row.get("request_id"),
        "rank": candidate.get("rank"),
        "code": candidate.get("code"),
        "code_type": candidate.get("code_type"),
        "lookup_status": candidate.get("lookup_status"),
        "clinical_theme": candidate.get("clinical_theme"),
        "review_rows": candidate.get("review_rows"),
        "top3_diagnosis_share": candidate.get("top3_diagnosis_share"),
        "review_reduction_potential": candidate.get("review_reduction_potential"),
        "source_risk_level": candidate.get("risk_level"),
        "source_recommended_action": candidate.get("recommended_action"),
        "medgemma_status": response.get("medgemma_status") if validation_row.get("is_response_valid") else "",
        "clinical_plausibility": response.get("clinical_plausibility") if validation_row.get("is_response_valid") else "",
        "diagnosis_cohort_safety": response.get("diagnosis_cohort_safety") if validation_row.get("is_response_valid") else "",
        "mapping_assessment": response.get("mapping_assessment") if validation_row.get("is_response_valid") else "",
        "confidence": response.get("confidence") if validation_row.get("is_response_valid") else "",
        "eligible_for_human_expert_fast_track": (
            response.get("eligible_for_human_expert_fast_track") if validation_row.get("is_response_valid") else False
        ),
        "recommended_triage": response.get("recommended_triage") if validation_row.get("is_response_valid") else "",
        "merged_triage_category": merged_triage_category(validation_row),
        "supported_prefixes": list_value(response.get("supported_prefixes")) if validation_row.get("is_response_valid") else [],
        "prefixes_to_keep_review": (
            list_value(response.get("prefixes_to_keep_review")) if validation_row.get("is_response_valid") else []
        ),
        "missing_evidence": list_value(response.get("missing_evidence")) if validation_row.get("is_response_valid") else [],
        "risk_notes": list_value(response.get("risk_notes")) if validation_row.get("is_response_valid") else [],
        "reasoning_summary": response.get("reasoning_summary") if validation_row.get("is_response_valid") else "",
        "validation_errors": validation_row.get("errors") or [],
        "validation_warnings": validation_row.get("warnings") or [],
        "human_admin_approval_present": False,
        "apply_ready": False,
        "auto_apply": False,
        "safety": safety_block(),
    }


def build_html(rows: list[dict[str, Any]], report: dict[str, Any]) -> str:
    columns = [
        "rank",
        "code",
        "clinical_theme",
        "review_rows",
        "source_recommended_action",
        "clinical_plausibility",
        "diagnosis_cohort_safety",
        "mapping_assessment",
        "confidence",
        "merged_triage_category",
        "supported_prefixes",
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
        "<title>MedGemma Review Reduction Shadow Triage</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 8px;font-size:12px}th{background:#f3f3f3}</style>"
        "</head><body>"
        "<h1>MedGemma Review Reduction Shadow Triage</h1>"
        f"<p>Generated at: {html.escape(str(report.get('generated_at')))}</p>"
        f"<p>Rows: {html.escape(str(len(rows)))}</p>"
        "<p>This is shadow metadata only. It does not approve, auto-apply, or override live decisions.</p>"
        f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        "</body></html>"
    )


def write_merge_outputs(
    *,
    requests_path: Path = DEFAULT_REQUESTS_PATH,
    responses_path: Path = DEFAULT_RESPONSES_PATH,
    out_dir: Path = DEFAULT_OUT_DIR,
) -> dict[str, Any]:
    request_rows = load_jsonl(requests_path)
    response_rows = load_jsonl(responses_path)
    validation_report = build_validation_report(request_rows, response_rows)
    triage_rows = [triage_row(row) for row in validation_report["validation_rows"]]
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / "TASK_MANIFEST.json"
    validation_report_path = out_dir / "medgemma_review_reduction_response_validation_report.json"
    validation_rows_json_path = out_dir / "medgemma_review_reduction_response_validation_rows.json"
    validation_rows_csv_path = out_dir / "medgemma_review_reduction_response_validation_rows.csv"
    triage_json_path = out_dir / "medgemma_review_reduction_shadow_triage.json"
    triage_csv_path = out_dir / "medgemma_review_reduction_shadow_triage.csv"
    dashboard_path = out_dir / "medgemma_review_reduction_shadow_triage_dashboard.html"
    generated_files = [
        str(report_path),
        str(validation_report_path),
        str(validation_rows_json_path),
        str(validation_rows_csv_path),
        str(triage_json_path),
        str(triage_csv_path),
        str(dashboard_path),
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "mode": "review_reduction_medgemma_shadow_merge",
        "source_requests_path": str(requests_path),
        "source_responses_path": str(responses_path),
        "out_dir": str(out_dir),
        "generated_files": generated_files,
        "counts": {
            **validation_report["counts"],
            "triage_rows": len(triage_rows),
        },
        "blocked_reason": validation_report["blocked_reason"],
        "safety": safety_block(),
        "instructions": {
            "does_not_apply": True,
            "does_not_write_qdrant": True,
            "does_not_write_production_db": True,
            "does_not_claim_human_admin_approval": True,
            "next_gate": "Use fast-track and mapping-backlog rows only as inputs for human domain expert/admin review.",
        },
    }
    write_json(report_path, report)
    write_json(validation_report_path, {key: value for key, value in validation_report.items() if key != "validation_rows"})
    write_json(validation_rows_json_path, validation_report["validation_rows"])
    write_csv(validation_rows_csv_path, validation_report["validation_rows"], VALIDATION_CSV_FIELDS)
    write_json(triage_json_path, triage_rows)
    write_csv(triage_csv_path, triage_rows, TRIAGE_CSV_FIELDS)
    dashboard_path.write_text(build_html(triage_rows, report), encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate MedGemma review-reduction shadow responses and merge them into a triage worklist."
    )
    parser.add_argument("--requests-path", type=Path, default=DEFAULT_REQUESTS_PATH)
    parser.add_argument("--responses-path", type=Path, default=DEFAULT_RESPONSES_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = write_merge_outputs(
        requests_path=args.requests_path,
        responses_path=args.responses_path,
        out_dir=args.out_dir,
    )
    if args.print_summary:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"MedGemma review-reduction shadow merge written: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
