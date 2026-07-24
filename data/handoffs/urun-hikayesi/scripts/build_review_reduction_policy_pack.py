from __future__ import annotations

import csv
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
SOURCE_DIR = ROOT / "SUT/generated/shadow_quality_gate/full_historical_empirical_rule_intelligence_20260709"
REVIEW_CANDIDATES_PATH = SOURCE_DIR / "review_reduction_candidates.csv"
DIAGNOSIS_MATRIX_PATH = SOURCE_DIR / "procedure_diagnosis_matrix_top25.csv"
DASHBOARD_PATH = SOURCE_DIR / "rule_coverage_dashboard.json"
LOOKUP_PATH = ROOT / "SUT/generated/sut_diagnosis_rules/ek2b/runtime/sut_diagnosis_runtime_lookup.json"
OUT_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_policy_pack_20260709"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


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


def parse_float(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", "."))
    except ValueError:
        return 0.0


def parse_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def parse_top_values(value: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for part in str(value or "").split(";"):
        if ":" not in part:
            continue
        key, count = part.rsplit(":", 1)
        output.append({"value": key.strip(), "count": parse_int(count)})
    return output


def diagnosis_prefixes(top_diagnoses: list[dict[str, Any]], limit: int = 5) -> list[str]:
    prefixes: list[str] = []
    for item in top_diagnoses[:limit]:
        diagnosis = str(item.get("value") or "").upper()
        match = re.match(r"([A-Z][0-9]{2})", diagnosis)
        if match:
            prefixes.append(match.group(1))
        elif diagnosis:
            prefixes.append(diagnosis)
    return list(dict.fromkeys(prefixes))


def clinical_theme(top_diagnoses: list[dict[str, Any]], top_chapters_text: str) -> str:
    values = {str(item.get("value") or "").upper() for item in top_diagnoses[:10]}
    chapters = str(top_chapters_text or "")
    if any(value.startswith("Z33") or value.startswith("Z34") or value.startswith("Z35") or value.startswith("Z36") for value in values) or "Pregnancy" in chapters:
        return "obstetric_pregnancy"
    if any(value.startswith("B34.2") or value.startswith("U07") for value in values):
        return "covid_or_viral_infection"
    if any(value.startswith("R53") or value.startswith("R68") for value in values):
        return "non_specific_symptom_dominant"
    if any(value.startswith("R07") or value.startswith("R00") or value.startswith("I10") for value in values):
        return "cardiology_symptom_or_monitoring"
    if any(value.startswith("H") for value in values) or "Eye/ear" in chapters:
        return "eye_ear"
    if any(value.startswith("N") for value in values) or "Genitourinary" in chapters:
        return "genitourinary"
    if any(value.startswith("M") for value in values) or "Musculoskeletal" in chapters:
        return "musculoskeletal"
    if any(value.startswith("K") for value in values) or "Digestive" in chapters:
        return "digestive"
    if any(value.startswith("J") for value in values) or "Respiratory" in chapters:
        return "respiratory"
    if any(value.startswith("S") or value.startswith("T") for value in values) or "Injury" in chapters:
        return "injury_trauma"
    if any(value.startswith("F") for value in values) or "Mental" in chapters:
        return "mental_behavioral"
    return "mixed_or_unclear"


def specificity_level(top3_share: float, normalized_entropy: float, unique_diagnoses: int) -> str:
    if top3_share >= 0.70 and normalized_entropy <= 0.50:
        return "high"
    if top3_share >= 0.50 and normalized_entropy <= 0.70:
        return "medium"
    if unique_diagnoses <= 20 and top3_share >= 0.45:
        return "medium"
    return "low"


def review_reduction_potential(review_rows: int, top3_share: float, specificity: str) -> str:
    if review_rows >= 1000 and top3_share >= 0.65 and specificity in {"high", "medium"}:
        return "high"
    if review_rows >= 500 and top3_share >= 0.55:
        return "medium_high"
    if review_rows >= 100 and top3_share >= 0.50:
        return "medium"
    return "low"


def risk_level(row: dict[str, Any], theme: str, lookup_status: str) -> str:
    review_rows = parse_int(row.get("review_rows"))
    top3_share = parse_float(row.get("top3_diagnosis_share"))
    fail_rows = parse_int(row.get("fail_rows"))
    if fail_rows > 0:
        return "high"
    if lookup_status == "missing_from_runtime_lookup":
        return "high" if review_rows >= 1000 else "medium_high"
    if theme == "non_specific_symptom_dominant" and top3_share < 0.75:
        return "medium_high"
    if review_rows >= 5000:
        return "medium_high"
    return "medium"


def recommended_action(row: dict[str, Any], theme: str, lookup_status: str, potential: str, risk: str) -> str:
    code_type = str(row.get("code_type") or "")
    if lookup_status == "missing_from_runtime_lookup" and code_type == "huv_or_local_dotted":
        return "resolve_local_huv_mapping_then_policy_refinement"
    if lookup_status == "missing_from_runtime_lookup":
        return "catalog_or_rule_backfill_before_review_reduction"
    if risk == "high":
        return "expert_review_only_no_reduction_until_fail_review"
    if potential in {"high", "medium_high"} and theme not in {"non_specific_symptom_dominant", "mixed_or_unclear"}:
        return "expert_review_for_cohort_auto_pass_candidate"
    if potential in {"high", "medium_high"}:
        return "expert_review_for_conditional_review_refinement"
    return "manual_review_policy_observation"


def build_candidate(row: dict[str, str], rank: int, diagnosis_rows: list[dict[str, str]], lookup_rules: dict[str, Any]) -> dict[str, Any]:
    code = str(row.get("code") or "")
    top_diagnoses = parse_top_values(row.get("top_diagnoses", ""))
    top3_share = parse_float(row.get("top3_diagnosis_share"))
    normalized_entropy = parse_float(row.get("diagnosis_normalized_entropy"))
    unique_diagnoses = parse_int(row.get("unique_diagnoses"))
    review_rows = parse_int(row.get("review_rows"))
    provision_rows = parse_int(row.get("provision_rows"))
    lookup_status = str(row.get("lookup_status") or "")
    theme = clinical_theme(top_diagnoses, row.get("top_diagnosis_chapters", ""))
    specificity = specificity_level(top3_share, normalized_entropy, unique_diagnoses)
    potential = review_reduction_potential(review_rows, top3_share, specificity)
    risk = risk_level(row, theme, lookup_status)
    action = recommended_action(row, theme, lookup_status, potential, risk)
    rule = lookup_rules.get(code) if isinstance(lookup_rules.get(code), dict) else {}
    current_policy = (
        rule.get("diagnosis_policy")
        or rule.get("policy")
        or rule.get("diagnosis_rule_policy")
        or ""
    )
    current_runtime_mode = rule.get("runtime_decision_mode") or ""
    current_review_required = rule.get("review_required")
    top_prefixes = diagnosis_prefixes(top_diagnoses, limit=5)
    candidate = {
        "rank": rank,
        "code": code,
        "code_type": row.get("code_type"),
        "lookup_status": lookup_status,
        "clinical_theme": theme,
        "specificity_level": specificity,
        "review_reduction_potential": potential,
        "risk_level": risk,
        "recommended_action": action,
        "review_rows": review_rows,
        "provision_rows": provision_rows,
        "top3_diagnosis_share": top3_share,
        "top1_diagnosis_share": parse_float(row.get("top1_diagnosis_share")),
        "unique_diagnoses": unique_diagnoses,
        "diagnosis_normalized_entropy": normalized_entropy,
        "top_diagnoses": top_diagnoses,
        "top_diagnosis_prefixes_for_expert_review": top_prefixes,
        "top_diagnosis_chapters": parse_top_values(row.get("top_diagnosis_chapters", "")),
        "current_runtime_rule_context": {
            "present_in_rules_by_sut_code": bool(rule),
            "diagnosis_policy": current_policy,
            "runtime_decision_mode": current_runtime_mode,
            "review_required": current_review_required,
            "required_icd10_patterns_count": len(rule.get("required_icd10_patterns") or []),
            "excluded_icd10_patterns_count": len(rule.get("excluded_icd10_patterns") or []),
            "confidence": rule.get("confidence", ""),
        },
        "diagnosis_evidence_top25": diagnosis_rows,
        "suggested_policy_hypothesis": build_policy_hypothesis(action, top_prefixes, theme),
        "required_safety_gates": [
            "official_source_or_internal_medical_committee_validation",
            "human_admin_approval_required",
            "shadow_only_batch_preview_before_any_live_apply",
            "approve_to_reject_drift_must_be_zero",
            "rollback_manifest_required",
            "post_deployment_monitoring_required_if_later_approved",
        ],
        "safety": {
            "auto_apply": False,
            "live_runtime_override": False,
            "writes_to_qdrant": False,
            "exports_case_level_rows": False,
        },
    }
    return candidate


def build_policy_hypothesis(action: str, top_prefixes: list[str], theme: str) -> str:
    if action == "resolve_local_huv_mapping_then_policy_refinement":
        return "First map the local/HUV code to a canonical catalog/rule layer; do not reduce review solely from empirical data."
    if action == "catalog_or_rule_backfill_before_review_reduction":
        return "Backfill the missing catalog/rule context first; review reduction can be considered only after deterministic rule identity is resolved."
    if action == "expert_review_for_cohort_auto_pass_candidate":
        return (
            "Expert may evaluate whether these diagnosis prefixes can be allowed without manual review for this procedure: "
            + ", ".join(top_prefixes)
        )
    if action == "expert_review_for_conditional_review_refinement":
        return (
            "Expert may evaluate a conditional policy that keeps manual review except for a narrow, documented diagnosis cohort: "
            + ", ".join(top_prefixes)
            + f" (theme={theme})"
        )
    return "Keep manual review for now; use this aggregate evidence for observation and future policy refinement."


def build_html(candidates: list[dict[str, Any]], manifest: dict[str, Any]) -> str:
    columns = [
        "rank",
        "code",
        "clinical_theme",
        "review_rows",
        "top3_diagnosis_share",
        "review_reduction_potential",
        "risk_level",
        "recommended_action",
        "top_diag",
    ]
    header = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
    body = []
    for item in candidates:
        row = {
            **item,
            "top_diag": "; ".join(
                f"{diag['value']}:{diag['count']}" for diag in item.get("top_diagnoses", [])[:5]
            ),
        }
        body.append(
            "<tr>"
            + "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in columns)
            + "</tr>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Review Reduction Policy Pack</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 8px;font-size:12px}th{background:#f3f3f3}</style>"
        "</head><body>"
        "<h1>Review Reduction Policy Pack</h1>"
        f"<p>Generated at: {html.escape(str(manifest.get('generated_at')))}</p>"
        f"<p>Candidates: {html.escape(str(manifest.get('counts', {}).get('candidates')))}</p>"
        "<p>This is an expert-review worklist. It does not approve, auto-apply, or override live decisions.</p>"
        f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        "</body></html>"
    )


def build_prompt(candidates: list[dict[str, Any]]) -> str:
    top_lines = []
    for item in candidates[:20]:
        top_diagnoses = ", ".join(
            f"{diag['value']}({diag['count']})" for diag in item.get("top_diagnoses", [])[:5]
        )
        top_lines.append(
            f"- {item['rank']:02d}. {item['code']} | theme={item['clinical_theme']} | "
            f"review_rows={item['review_rows']} | top3_share={item['top3_diagnosis_share']} | "
            f"action={item['recommended_action']} | top={top_diagnoses}"
        )
    return """REVIEW REDUCTION POLICY REVIEW TASK

Purpose:
Review aggregate historical diagnosis-procedure evidence for high-volume manual REVIEW procedure codes and decide whether any narrow deterministic policy refinement is appropriate.

Important:
- This is not an approval to pay.
- This is not a live runtime change.
- MedGemma/AI output must remain shadow metadata only.
- Any reduction of manual review requires official source or internal medical committee validation, human/admin approval, shadow batch preview, and rollback planning.

Candidate list:
""" + "\n".join(top_lines) + """

Expert decision options per candidate:
1. keep_manual_review
2. refine_review_policy_for_specific_diagnosis_cohort
3. convert_to_known_no_diagnosis_rule_required_only_if_officially_supported
4. resolve_local_huv_mapping_first
5. reject_candidate_due_to_clinical_or_policy_risk

Required response fields per candidate:
- code
- expert_decision
- approved_diagnosis_prefixes_if_any
- rejected_diagnosis_prefixes_if_any
- official_source_or_committee_reference
- notes
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    review_rows = load_csv(REVIEW_CANDIDATES_PATH)
    diagnosis_matrix_rows = load_csv(DIAGNOSIS_MATRIX_PATH)
    dashboard = load_json(DASHBOARD_PATH)
    lookup = load_json(LOOKUP_PATH)
    lookup_rules = lookup.get("rules_by_sut_code") or {}
    diagnosis_by_code: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in diagnosis_matrix_rows:
        diagnosis_by_code[str(row.get("code") or "")].append(row)

    selected_source_rows = review_rows[:50]
    candidates = [
        build_candidate(
            row,
            rank=index,
            diagnosis_rows=diagnosis_by_code.get(str(row.get("code") or ""), [])[:25],
            lookup_rules=lookup_rules,
        )
        for index, row in enumerate(selected_source_rows, start=1)
    ]
    candidates.sort(
        key=lambda item: (
            {"high": 4, "medium_high": 3, "medium": 2, "low": 1}.get(item["review_reduction_potential"], 0),
            item["review_rows"],
            item["top3_diagnosis_share"],
        ),
        reverse=True,
    )
    for index, item in enumerate(candidates, start=1):
        item["rank"] = index

    counts = {
        "candidates": len(candidates),
        "by_potential": dict(Counter(item["review_reduction_potential"] for item in candidates)),
        "by_risk": dict(Counter(item["risk_level"] for item in candidates)),
        "by_action": dict(Counter(item["recommended_action"] for item in candidates)),
        "by_theme": dict(Counter(item["clinical_theme"] for item in candidates)),
    }
    manifest = {
        "schema_version": "review_reduction_policy_pack.v1",
        "generated_at": now_iso(),
        "source_review_candidates": str(REVIEW_CANDIDATES_PATH),
        "source_rule_dashboard": str(DASHBOARD_PATH),
        "source_lookup": str(LOOKUP_PATH),
        "out_dir": str(OUT_DIR),
        "counts": counts,
        "source_context": {
            "total_provision_rows": dashboard.get("counts", {}).get("total_provision_rows"),
            "total_review_rows": dashboard.get("counts", {}).get("decision_counts", {}).get("REVIEW"),
            "review_reduction_candidate_count": dashboard.get("counts", {}).get("recommended_category_counts", {}).get("review_reduction_candidate"),
            "manual_review_policy_candidate_count": dashboard.get("counts", {}).get("recommended_category_counts", {}).get("manual_review_policy_candidate"),
        },
        "safety": {
            "writes_to_production_db": False,
            "writes_to_qdrant": False,
            "live_runtime_override": False,
            "auto_apply": False,
            "exports_case_level_rows": False,
        },
    }

    flat_fields = [
        "rank",
        "code",
        "code_type",
        "lookup_status",
        "clinical_theme",
        "specificity_level",
        "review_reduction_potential",
        "risk_level",
        "recommended_action",
        "review_rows",
        "provision_rows",
        "top3_diagnosis_share",
        "top1_diagnosis_share",
        "unique_diagnoses",
        "diagnosis_normalized_entropy",
        "top_diagnosis_prefixes_for_expert_review",
        "suggested_policy_hypothesis",
        "top_diagnoses",
        "current_runtime_rule_context",
        "required_safety_gates",
    ]
    write_json(OUT_DIR / "review_reduction_policy_candidates_top50.json", candidates)
    write_csv(OUT_DIR / "review_reduction_policy_candidates_top50.csv", candidates, flat_fields)
    write_json(OUT_DIR / "review_reduction_policy_candidates_top20_for_admin.json", candidates[:20])
    write_csv(OUT_DIR / "review_reduction_policy_candidates_top20_for_admin.csv", candidates[:20], flat_fields)
    write_json(OUT_DIR / "TASK_MANIFEST.json", manifest)
    (OUT_DIR / "DOMAIN_EXPERT_REVIEW_PROMPT_COPY_PASTE.txt").write_text(build_prompt(candidates), encoding="utf-8")
    (OUT_DIR / "review_reduction_policy_dashboard.html").write_text(build_html(candidates, manifest), encoding="utf-8")
    (OUT_DIR / "RUN_NEXT_STEPS.txt").write_text(
        """Recommended next steps:
1. Send DOMAIN_EXPERT_REVIEW_PROMPT_COPY_PASTE.txt and review_reduction_policy_candidates_top20_for_admin.csv to medical/policy reviewers.
2. Do not change live rules from empirical evidence alone.
3. If experts approve a narrow cohort, create shadow staging candidates only.
4. Run deterministic batch preview and business impact/rollback gates before any live apply discussion.
""",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "out_dir": str(OUT_DIR),
                "counts": counts,
                "top10": [
                    {
                        "code": item["code"],
                        "review_rows": item["review_rows"],
                        "theme": item["clinical_theme"],
                        "potential": item["review_reduction_potential"],
                        "risk": item["risk_level"],
                        "action": item["recommended_action"],
                        "top_prefixes": item["top_diagnosis_prefixes_for_expert_review"],
                    }
                    for item in candidates[:10]
                ],
                "safety": manifest["safety"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
