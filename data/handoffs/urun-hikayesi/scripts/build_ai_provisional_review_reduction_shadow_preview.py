from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
RULE_ENGINE_RESULTS_CSV = ROOT / "SUT/generated/dgx_handoff/rule_engine_results.csv"
AI_CANDIDATES_JSON = (
    ROOT
    / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_expert_review_20260709"
    / "ai_provisional_shadow_staging_candidates.json"
)
AI_DECISIONS_JSON = (
    ROOT
    / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_expert_review_20260709"
    / "ai_provisional_expert_decisions_all19.json"
)
OUT_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_shadow_preview_20260709"

SCHEMA_VERSION = "review_reduction_ai_provisional_shadow_preview.v1"

SUMMARY_FIELDS = [
    "code",
    "clinical_theme",
    "ai_expert_decision",
    "confidence",
    "confidence_score",
    "review_rows_from_ai_pack",
    "historical_review_code_rows",
    "candidate_prefix_hit_rows",
    "candidate_shadow_pass_occurrence_rows",
    "candidate_blocked_by_manual_prefix_rows",
    "candidate_other_diagnosis_rows",
    "candidate_review_code_not_seen_delta_vs_ai_pack",
    "row_level_full_release_rows",
    "row_level_partial_resolution_rows",
    "approved_prefixes_if_any",
    "prefixes_requiring_manual_review",
    "human_admin_approval_present",
    "apply_ready",
    "auto_apply",
    "preview_only",
]

PREFIX_FIELDS = [
    "code",
    "prefix_type",
    "diagnosis_prefix",
    "review_code_rows_with_prefix",
    "share_of_historical_review_code_rows",
]

HOLD_FIELDS = [
    "code",
    "clinical_theme",
    "review_rows",
    "ai_expert_decision",
    "confidence",
    "confidence_score",
    "validation_status",
    "shadow_staging_allowed_by_ai_only",
    "hold_reason",
    "apply_ready",
    "auto_apply",
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


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


def split_values(value: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[;|,\s]+", str(value or "")):
        normalized = part.strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def normalize_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    if code.startswith("SUT::"):
        code = code.split("::", 1)[1].strip()
    return code


def normalize_prefix(value: Any) -> str:
    return str(value or "").strip().upper()


def diagnosis_matches_prefix(diagnosis: str, prefix: str) -> bool:
    diagnosis = str(diagnosis or "").upper().strip()
    prefix = normalize_prefix(prefix)
    return bool(diagnosis and prefix and diagnosis.startswith(prefix))


def matched_prefixes(diagnoses: list[str], prefixes: list[str]) -> list[str]:
    matched: list[str] = []
    for prefix in prefixes:
        if any(diagnosis_matches_prefix(diagnosis, prefix) for diagnosis in diagnoses):
            matched.append(prefix)
    return matched


def safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "evet"}


def safety_block() -> dict[str, bool]:
    return {
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "live_runtime_override": False,
        "auto_apply": False,
        "exports_case_level_rows": False,
        "exports_case_ids": False,
        "human_admin_approval_present": False,
        "preview_only": True,
    }


def make_policy(candidate: dict[str, Any]) -> dict[str, Any]:
    approved = [normalize_prefix(prefix) for prefix in candidate.get("approved_prefixes_if_any") or [] if normalize_prefix(prefix)]
    manual = [
        normalize_prefix(prefix)
        for prefix in candidate.get("prefixes_requiring_manual_review") or []
        if normalize_prefix(prefix)
    ]
    return {
        "code": normalize_code(candidate.get("code")),
        "clinical_theme": candidate.get("clinical_theme"),
        "ai_expert_decision": candidate.get("ai_expert_decision"),
        "confidence": candidate.get("confidence"),
        "confidence_score": candidate.get("confidence_score"),
        "review_rows_from_ai_pack": int(float(candidate.get("review_rows") or 0)),
        "approved_prefixes_if_any": list(dict.fromkeys(approved)),
        "prefixes_requiring_manual_review": list(dict.fromkeys(manual)),
        "human_admin_approval_present": False,
        "apply_ready": False,
        "auto_apply": False,
    }


def blank_stats(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        **policy,
        "historical_review_code_rows": 0,
        "candidate_prefix_hit_rows": 0,
        "candidate_shadow_pass_occurrence_rows": 0,
        "candidate_blocked_by_manual_prefix_rows": 0,
        "candidate_other_diagnosis_rows": 0,
        "row_level_full_release_rows": 0,
        "row_level_partial_resolution_rows": 0,
        "approved_prefix_counts": Counter(),
        "manual_prefix_counts": Counter(),
    }


def code_can_shadow_pass(policy: dict[str, Any], diagnoses: list[str]) -> tuple[bool, list[str], list[str]]:
    approved_hits = matched_prefixes(diagnoses, policy["approved_prefixes_if_any"])
    manual_hits = matched_prefixes(diagnoses, policy["prefixes_requiring_manual_review"])
    return bool(approved_hits) and not bool(manual_hits), approved_hits, manual_hits


def hold_reason(row: dict[str, Any]) -> str:
    reasons: list[str] = []
    if row.get("validation_status") != "valid":
        reasons.extend(str(item) for item in row.get("validation_errors") or [])
    if safe_bool(row.get("shadow_staging_allowed_by_ai_only")) is not True:
        reasons.append("shadow_staging_allowed_by_ai_only_false")
    if row.get("approved_prefixes_if_any") in (None, "", []):
        reasons.append("no_ai_provisional_approved_prefixes")
    if safe_bool(row.get("human_admin_approval_present")):
        reasons.append("unexpected_human_admin_approval_claim")
    return ";".join(reasons) or "not_selected_for_ai_only_shadow_preview"


def build_html(summary_rows: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
    columns = [
        "code",
        "clinical_theme",
        "ai_expert_decision",
        "confidence_score",
        "historical_review_code_rows",
        "candidate_shadow_pass_occurrence_rows",
        "row_level_full_release_rows",
        "row_level_partial_resolution_rows",
    ]
    header = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    body = []
    for row in summary_rows:
        body.append(
            "<tr>"
            + "".join(f"<td>{html.escape(csv_value(row.get(column, '')))}</td>" for column in columns)
            + "</tr>"
        )
    counts = manifest.get("counts") or {}
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>AI Provisional Shadow Preview</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 8px;font-size:12px}th{background:#f3f3f3}"
        ".warn{color:#8a4b00;font-weight:bold}</style>"
        "</head><body>"
        "<h1>AI Provisional Shadow Preview</h1>"
        "<p class='warn'>Aggregate-only preview. Not human/admin approval. Not live rule change.</p>"
        f"<p>Rows scanned: {html.escape(str(counts.get('historical_rows_scanned', '')))}</p>"
        f"<p>Row-level full release candidates: {html.escape(str(counts.get('row_level_full_release_rows', '')))}</p>"
        f"<p>Partial resolution rows: {html.escape(str(counts.get('row_level_partial_resolution_rows', '')))}</p>"
        f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        "</body></html>"
    )


def build_preview(
    *,
    rule_engine_csv: Path = RULE_ENGINE_RESULTS_CSV,
    candidates_json: Path = AI_CANDIDATES_JSON,
    decisions_json: Path = AI_DECISIONS_JSON,
    out_dir: Path = OUT_DIR,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    policies = [make_policy(candidate) for candidate in load_json(candidates_json)]
    policy_by_code = {policy["code"]: policy for policy in policies}
    stats_by_code = {policy["code"]: blank_stats(policy) for policy in policies}
    baseline_decisions: Counter[str] = Counter()
    row_transition_counts: Counter[str] = Counter()
    rows_scanned = 0

    with rule_engine_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows_scanned += 1
            overall_decision = str(row.get("overall_decision") or "").strip().upper()
            baseline_decisions[overall_decision] += 1
            if overall_decision != "REVIEW":
                row_transition_counts["unchanged_non_review"] += 1
                continue

            review_codes = [normalize_code(code) for code in split_values(row.get("review_codes"))]
            if not review_codes:
                row_transition_counts["unchanged_review_no_review_codes"] += 1
                continue

            diagnoses = split_values(row.get("diagnoses"))
            resolved_codes: set[str] = set()
            saw_candidate_code = False
            for code in review_codes:
                policy = policy_by_code.get(code)
                if not policy:
                    continue
                saw_candidate_code = True
                stats = stats_by_code[code]
                stats["historical_review_code_rows"] += 1
                can_pass, approved_hits, manual_hits = code_can_shadow_pass(policy, diagnoses)
                for prefix in approved_hits:
                    stats["approved_prefix_counts"][prefix] += 1
                for prefix in manual_hits:
                    stats["manual_prefix_counts"][prefix] += 1
                if approved_hits:
                    stats["candidate_prefix_hit_rows"] += 1
                if can_pass:
                    stats["candidate_shadow_pass_occurrence_rows"] += 1
                    resolved_codes.add(code)
                elif manual_hits:
                    stats["candidate_blocked_by_manual_prefix_rows"] += 1
                else:
                    stats["candidate_other_diagnosis_rows"] += 1

            if not saw_candidate_code:
                row_transition_counts["unchanged_review_no_candidate_code"] += 1
                continue
            if not resolved_codes:
                row_transition_counts["unchanged_review_candidate_code_not_resolved"] += 1
                continue
            if all(code in resolved_codes for code in review_codes):
                row_transition_counts["review_to_ai_provisional_shadow_pass_candidate"] += 1
                for code in resolved_codes:
                    stats_by_code[code]["row_level_full_release_rows"] += 1
            else:
                row_transition_counts["review_partial_resolution_still_review"] += 1
                for code in resolved_codes:
                    stats_by_code[code]["row_level_partial_resolution_rows"] += 1

    summary_rows: list[dict[str, Any]] = []
    prefix_rows: list[dict[str, Any]] = []
    for code, stats in stats_by_code.items():
        historical_review_rows = int(stats["historical_review_code_rows"])
        summary = {
            "code": code,
            "clinical_theme": stats.get("clinical_theme"),
            "ai_expert_decision": stats.get("ai_expert_decision"),
            "confidence": stats.get("confidence"),
            "confidence_score": stats.get("confidence_score"),
            "review_rows_from_ai_pack": stats.get("review_rows_from_ai_pack"),
            "historical_review_code_rows": historical_review_rows,
            "candidate_prefix_hit_rows": stats["candidate_prefix_hit_rows"],
            "candidate_shadow_pass_occurrence_rows": stats["candidate_shadow_pass_occurrence_rows"],
            "candidate_blocked_by_manual_prefix_rows": stats["candidate_blocked_by_manual_prefix_rows"],
            "candidate_other_diagnosis_rows": stats["candidate_other_diagnosis_rows"],
            "candidate_review_code_not_seen_delta_vs_ai_pack": int(stats.get("review_rows_from_ai_pack") or 0)
            - historical_review_rows,
            "row_level_full_release_rows": stats["row_level_full_release_rows"],
            "row_level_partial_resolution_rows": stats["row_level_partial_resolution_rows"],
            "approved_prefixes_if_any": stats.get("approved_prefixes_if_any"),
            "prefixes_requiring_manual_review": stats.get("prefixes_requiring_manual_review"),
            "human_admin_approval_present": False,
            "apply_ready": False,
            "auto_apply": False,
            "preview_only": True,
        }
        summary_rows.append(summary)
        for prefix, count in stats["approved_prefix_counts"].most_common():
            prefix_rows.append(
                {
                    "code": code,
                    "prefix_type": "ai_provisional_approved_prefix",
                    "diagnosis_prefix": prefix,
                    "review_code_rows_with_prefix": count,
                    "share_of_historical_review_code_rows": round(count / historical_review_rows, 8)
                    if historical_review_rows
                    else 0.0,
                }
            )
        for prefix, count in stats["manual_prefix_counts"].most_common():
            prefix_rows.append(
                {
                    "code": code,
                    "prefix_type": "manual_review_prefix",
                    "diagnosis_prefix": prefix,
                    "review_code_rows_with_prefix": count,
                    "share_of_historical_review_code_rows": round(count / historical_review_rows, 8)
                    if historical_review_rows
                    else 0.0,
                }
            )

    all_decisions = load_json(decisions_json) if decisions_json.exists() else []
    simulated_codes = set(policy_by_code)
    hold_rows = []
    for row in all_decisions:
        code = normalize_code(row.get("code"))
        if code in simulated_codes:
            continue
        if row.get("validation_status") == "valid" and safe_bool(row.get("shadow_staging_allowed_by_ai_only")) and row.get(
            "approved_prefixes_if_any"
        ):
            continue
        hold_rows.append(
            {
                "code": code,
                "clinical_theme": row.get("clinical_theme"),
                "review_rows": row.get("review_rows"),
                "ai_expert_decision": row.get("ai_expert_decision"),
                "confidence": row.get("confidence"),
                "confidence_score": row.get("confidence_score"),
                "validation_status": row.get("validation_status"),
                "shadow_staging_allowed_by_ai_only": row.get("shadow_staging_allowed_by_ai_only"),
                "hold_reason": hold_reason(row),
                "apply_ready": False,
                "auto_apply": False,
            }
        )

    paths = {
        "summary_json": out_dir / "ai_provisional_shadow_preview_summary.json",
        "summary_csv": out_dir / "ai_provisional_shadow_preview_summary.csv",
        "prefix_json": out_dir / "ai_provisional_shadow_preview_by_prefix.json",
        "prefix_csv": out_dir / "ai_provisional_shadow_preview_by_prefix.csv",
        "guardrail_holds_json": out_dir / "ai_provisional_shadow_preview_guardrail_holds.json",
        "guardrail_holds_csv": out_dir / "ai_provisional_shadow_preview_guardrail_holds.csv",
        "dashboard": out_dir / "ai_provisional_shadow_preview_dashboard.html",
        "manifest": out_dir / "TASK_MANIFEST.json",
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "mode": "aggregate_only_deterministic_shadow_preview",
        "inputs": {
            "rule_engine_results_csv": str(rule_engine_csv),
            "ai_provisional_candidates_json": str(candidates_json),
            "ai_provisional_decisions_json": str(decisions_json),
        },
        "out_dir": str(out_dir),
        "counts": {
            "historical_rows_scanned": rows_scanned,
            "baseline_decision_counts": dict(baseline_decisions),
            "row_transition_counts": dict(row_transition_counts),
            "candidate_count": len(policies),
            "row_level_full_release_rows": row_transition_counts["review_to_ai_provisional_shadow_pass_candidate"],
            "row_level_partial_resolution_rows": row_transition_counts["review_partial_resolution_still_review"],
            "candidate_shadow_pass_occurrence_rows": sum(
                int(row["candidate_shadow_pass_occurrence_rows"]) for row in summary_rows
            ),
            "guardrail_hold_rows": len(hold_rows),
        },
        "safety": safety_block(),
        "interpretation": {
            "candidate_shadow_pass_occurrence_rows": (
                "Rows where the candidate code itself would be treated as AI-provisional shadow-pass for the approved prefix set."
            ),
            "row_level_full_release_rows": (
                "Conservative aggregate estimate of REVIEW rows whose entire review_codes set would be resolved by this AI-only preview."
            ),
            "row_level_partial_resolution_rows": (
                "Rows where at least one candidate code is resolved but the row remains REVIEW because another review code remains."
            ),
            "not_apply_ready": "All outputs remain AI-only preview; human/admin approval is absent.",
        },
        "generated_files": [str(path) for path in paths.values()],
    }
    write_json(paths["summary_json"], summary_rows)
    write_csv(paths["summary_csv"], summary_rows, SUMMARY_FIELDS)
    write_json(paths["prefix_json"], prefix_rows)
    write_csv(paths["prefix_csv"], prefix_rows, PREFIX_FIELDS)
    write_json(paths["guardrail_holds_json"], hold_rows)
    write_csv(paths["guardrail_holds_csv"], hold_rows, HOLD_FIELDS)
    paths["dashboard"].write_text(build_html(summary_rows, manifest), encoding="utf-8")
    write_json(paths["manifest"], manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build aggregate-only shadow preview for AI provisional review reduction candidates.")
    parser.add_argument("--rule-engine-csv", type=Path, default=RULE_ENGINE_RESULTS_CSV)
    parser.add_argument("--candidates-json", type=Path, default=AI_CANDIDATES_JSON)
    parser.add_argument("--decisions-json", type=Path, default=AI_DECISIONS_JSON)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_preview(
        rule_engine_csv=args.rule_engine_csv,
        candidates_json=args.candidates_json,
        decisions_json=args.decisions_json,
        out_dir=args.out_dir,
    )
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"AI provisional shadow preview written: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
