from __future__ import annotations

import argparse
import csv
import html
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
RULE_ENGINE_RESULTS = ROOT / "SUT/generated/dgx_handoff/rule_engine_results.csv"
RUNTIME_LOOKUP = ROOT / "SUT/generated/sut_diagnosis_rules/ek2b/runtime/sut_diagnosis_runtime_lookup.json"
AI_CANDIDATE = (
    ROOT
    / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_expert_review_20260709"
    / "ai_provisional_shadow_staging_candidates.json"
)
OUT_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_703790_shadow_policy_proposal_20260720"

SCHEMA_VERSION = "review_reduction_703790_shadow_policy_proposal.v1"
TARGET_CODE = "703790"

SCENARIOS = [
    {
        "scenario_id": "clinical_core_h40_only",
        "description": "Conservative glaucoma-only cohort for shadow observation.",
        "prefixes": ["H40"],
        "recommended": True,
        "reason": (
            "Nerve Fiber Analyzer is directly coherent with glaucoma evaluation. This avoids promoting "
            "the prior AI review's incorrect interpretation of eye ICD codes."
        ),
    },
    {
        "scenario_id": "structural_eye_observation",
        "description": "Glaucoma plus observed optic-nerve/retina codes; observation only.",
        "prefixes": ["H40", "H46", "H47.5", "H35.3"],
        "recommended": False,
        "reason": "Potentially coherent with retinal nerve-fiber assessment, but requires human medical-policy validation.",
    },
    {
        "scenario_id": "original_ai_broad_counterfactual",
        "description": "Original AI broad prefix set retained only as a counterfactual benchmark.",
        "prefixes": ["H52", "H40", "H04", "H43", "H47.5", "H35.3", "H18.3"],
        "recommended": False,
        "reason": (
            "Not recommended: the MedGemma rationale misclassified eye ICD codes as ear/hearing disorders, "
            "and several prefixes are not specific NFA indications."
        ),
    },
]

SCENARIO_FIELDS = [
    "scenario_id",
    "description",
    "prefixes",
    "recommended",
    "historical_703790_review_rows",
    "matched_candidate_rows",
    "matched_share",
    "row_level_full_release_counterfactual",
    "row_level_partial_resolution_counterfactual",
    "unmatched_rows",
    "reason",
    "runtime_decision_changed",
    "apply_ready",
]

PREFIX_FIELDS = [
    "diagnosis_prefix",
    "historical_703790_review_rows_with_prefix",
    "share_of_703790_review_rows",
    "proposal_class",
    "proposal_reason",
]
PREFIX_CLASSIFICATIONS = [
    ("H40", "shadow_candidate_core", "Glaucoma is the conservative NFA-coherent shadow cohort."),
    ("H46", "observation_only", "Optic neuritis may be relevant, but requires human medical validation."),
    ("H47.5", "observation_only", "Observed visual-pathway code; retain for analysis only."),
    ("H35.3", "observation_only", "Retinal disease code; clinical NFA specificity is not established here."),
    ("H43", "manual_review", "Vitreous disorders are not sufficiently specific for NFA shadow pass."),
    ("H52", "manual_review", "Refraction/accommodation disorders are common but not specific NFA indications."),
    ("H04", "manual_review", "Lacrimal-system disorders are not a specific NFA indication."),
    ("H18", "manual_review", "Corneal disorders are not a specific NFA indication."),
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


def matches_prefix(diagnosis: str, prefix: str) -> bool:
    return str(diagnosis or "").strip().upper().startswith(str(prefix or "").strip().upper())


def any_prefix_match(diagnoses: list[str], prefixes: list[str]) -> bool:
    return any(matches_prefix(diagnosis, prefix) for diagnosis in diagnoses for prefix in prefixes)


def safety_block() -> dict[str, bool]:
    return {
        "human_admin_approval_present": False,
        "apply_ready": False,
        "auto_apply": False,
        "live_runtime_override": False,
        "runtime_decision_changed": False,
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "exports_case_level_rows": False,
        "exports_case_ids": False,
        "shadow_only": True,
    }


def find_ai_candidate() -> dict[str, Any]:
    candidates = load_json(AI_CANDIDATE)
    for candidate in candidates:
        if str(candidate.get("code") or "") == TARGET_CODE:
            return candidate
    raise ValueError(f"AI candidate not found: {TARGET_CODE}")


def runtime_rule() -> dict[str, Any]:
    lookup = load_json(RUNTIME_LOOKUP)
    rules = lookup.get("rules_by_sut_code") or {}
    rule = rules.get(TARGET_CODE)
    if not isinstance(rule, dict):
        raise ValueError(f"Runtime rule not found: {TARGET_CODE}")
    return rule


def analyze_historical() -> tuple[list[dict[str, Any]], Counter[str], Counter[str], int]:
    scenario_stats = {
        scenario["scenario_id"]: {
            **scenario,
            "historical_703790_review_rows": 0,
            "matched_candidate_rows": 0,
            "row_level_full_release_counterfactual": 0,
            "row_level_partial_resolution_counterfactual": 0,
            "unmatched_rows": 0,
        }
        for scenario in SCENARIOS
    }
    prefix_row_counts: Counter[str] = Counter()
    overall_code_status: Counter[str] = Counter()
    rows_scanned = 0

    with RULE_ENGINE_RESULTS.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows_scanned += 1
            status = str(row.get("overall_decision") or "").strip().upper()
            status_fields = {
                "REVIEW": split_values(row.get("review_codes")),
                "PASS": split_values(row.get("pass_codes")),
                "FAIL": split_values(row.get("fail_codes")),
                "NO_RULE": split_values(row.get("no_rule_codes")),
            }
            for code_status, codes in status_fields.items():
                if TARGET_CODE in codes:
                    overall_code_status[code_status] += 1

            review_codes = status_fields["REVIEW"]
            if status != "REVIEW" or TARGET_CODE not in review_codes:
                continue
            diagnoses = split_values(row.get("diagnoses"))
            for prefix, _proposal_class, _reason in PREFIX_CLASSIFICATIONS:
                if any_prefix_match(diagnoses, [prefix]):
                    prefix_row_counts[prefix] += 1

            for stats in scenario_stats.values():
                stats["historical_703790_review_rows"] += 1
                if any_prefix_match(diagnoses, stats["prefixes"]):
                    stats["matched_candidate_rows"] += 1
                    if len(review_codes) == 1:
                        stats["row_level_full_release_counterfactual"] += 1
                    else:
                        stats["row_level_partial_resolution_counterfactual"] += 1
                else:
                    stats["unmatched_rows"] += 1

    scenario_rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        stats = scenario_stats[scenario["scenario_id"]]
        total = int(stats["historical_703790_review_rows"])
        matched = int(stats["matched_candidate_rows"])
        scenario_rows.append(
            {
                **stats,
                "matched_share": round(matched / total, 8) if total else 0.0,
                "runtime_decision_changed": False,
                "apply_ready": False,
            }
        )
    return scenario_rows, prefix_row_counts, overall_code_status, rows_scanned


def prefix_register(prefix_row_counts: Counter[str], total_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for prefix, proposal_class, reason in PREFIX_CLASSIFICATIONS:
        count = int(prefix_row_counts[prefix])
        rows.append(
            {
                "diagnosis_prefix": prefix,
                "historical_703790_review_rows_with_prefix": count,
                "share_of_703790_review_rows": round(count / total_rows, 8) if total_rows else 0.0,
                "proposal_class": proposal_class,
                "proposal_reason": reason,
            }
        )
    return rows


def build_monitoring_plan(core_scenario: dict[str, Any]) -> dict[str, Any]:
    baseline_rows = int(core_scenario["historical_703790_review_rows"])
    baseline_matches = int(core_scenario["matched_candidate_rows"])
    baseline_share = float(core_scenario["matched_share"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "proposal_id": "shadow_703790_h40_v1",
        "mode": "shadow_observation_only",
        "baseline": {
            "historical_703790_review_rows": baseline_rows,
            "h40_shadow_candidate_rows": baseline_matches,
            "h40_shadow_candidate_share": baseline_share,
            "row_level_full_release_counterfactual": core_scenario["row_level_full_release_counterfactual"],
            "row_level_partial_resolution_counterfactual": core_scenario["row_level_partial_resolution_counterfactual"],
        },
        "minimum_observation_window": {
            "days": 30,
            "minimum_703790_review_rows": 100,
            "minimum_h40_matches": 50,
        },
        "metrics": [
            {
                "metric": "h40_shadow_candidate_share",
                "alert_if": "absolute change from historical baseline exceeds 10 percentage points",
            },
            {
                "metric": "new_diagnosis_prefix_share",
                "alert_if": "previously unseen diagnosis prefixes exceed 5% of 703790 rows",
            },
            {
                "metric": "manual_review_disagreement_rate",
                "alert_if": "human/manual outcome disagrees with shadow candidate in more than 2% of labeled rows",
            },
            {
                "metric": "candidate_with_other_review_codes_share",
                "alert_if": "increase exceeds 10 percentage points from historical partial-resolution baseline",
            },
            {
                "metric": "fail_or_reject_signal",
                "alert_if": "any deterministic FAIL/reject signal occurs for an H40 shadow candidate",
            },
        ],
        "promotion_gates": [
            "human medical-policy review completed",
            "human_admin_approval_present=true from a real authorized reviewer",
            "official source or internal committee rationale attached",
            "minimum observation window completed",
            "manual_review_disagreement_rate <= 0.02",
            "fail_or_reject_signal_count == 0",
            "separate guarded apply and rollback review completed",
        ],
        "current_status": "not_approved_shadow_only",
        "safety": safety_block(),
    }


def build_rollback_manifest() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "proposal_id": "shadow_703790_h40_v1",
        "rollback_type": "shadow_overlay_deactivation",
        "live_runtime_changed": False,
        "production_rollback_required": False,
        "rollback_triggers": [
            "any deterministic FAIL/reject signal for a shadow candidate",
            "manual-review disagreement rate above 2%",
            "unexpected diagnosis-prefix drift above monitoring threshold",
            "payload safety flag mutation",
            "accidental use by a live decision path",
        ],
        "rollback_steps": [
            "disable the 703790 shadow observer configuration",
            "stop emitting AI_PROVISIONAL_SHADOW_PASS_CANDIDATE events",
            "remove only 703790 shadow points from the dedicated shadow collection if such indexing was separately approved",
            "preserve audit logs and monitoring aggregates",
            "verify runtime rule remains diagnosis_policy=review_required and runtime_decision_mode=manual_review",
        ],
        "expected_post_rollback_state": {
            "sut_code": TARGET_CODE,
            "diagnosis_policy": "review_required",
            "decision_if_missing": "REVIEW_REQUIRED",
            "review_required": True,
            "runtime_decision_mode": "manual_review",
        },
        "safety": safety_block(),
    }


def governance_text(
    rule: dict[str, Any],
    ai_candidate: dict[str, Any],
    scenario_rows: list[dict[str, Any]],
) -> str:
    core = next(row for row in scenario_rows if row["scenario_id"] == "clinical_core_h40_only")
    return "\n".join(
        [
            "703790 SHADOW-ONLY POLICY PROPOSAL",
            "==================================",
            "",
            f"Procedure: {rule.get('procedure_name')} ({TARGET_CODE})",
            f"Current runtime policy: {rule.get('diagnosis_policy')}",
            f"Current runtime decision mode: {rule.get('runtime_decision_mode')}",
            "",
            "PROPOSAL",
            "--------",
            "Observe H40* diagnoses as AI_PROVISIONAL_SHADOW_PASS_CANDIDATE.",
            "Do not change the actual runtime decision; every row remains governed by the deterministic rule engine.",
            "All non-H40 diagnoses remain REVIEW_REQUIRED.",
            "",
            "WHY THE EARLIER AI PREFIX SET WAS NARROWED",
            "-------------------------------------------",
            "The prior MedGemma response incorrectly described H52/H40/H04/H43 as hearing/ear diagnoses.",
            "They are eye ICD codes, and several are not specific indications for Nerve Fiber Analyzer.",
            "Therefore the broad AI prefix list is retained only as a counterfactual, not as the recommended policy.",
            "",
            "HISTORICAL CORE-SCENARIO IMPACT",
            "-------------------------------",
            f"703790 REVIEW rows: {core.get('historical_703790_review_rows')}",
            f"H40 shadow-candidate rows: {core.get('matched_candidate_rows')}",
            f"H40 matched share: {core.get('matched_share')}",
            f"Full-row release counterfactual: {core.get('row_level_full_release_counterfactual')}",
            f"Partial-resolution counterfactual: {core.get('row_level_partial_resolution_counterfactual')}",
            "",
            "AI SOURCE STATUS",
            "----------------",
            f"AI confidence score: {ai_candidate.get('confidence_score')}",
            "AI output is advisory and is not human/admin/expert approval.",
            "",
            "CURRENT GOVERNANCE STATUS",
            "-------------------------",
            "human_admin_approval_present=false",
            "apply_ready=false",
            "auto_apply=false",
            "runtime_decision_changed=false",
            "shadow_only=true",
        ]
    )


def build_dashboard(scenario_rows: list[dict[str, Any]], prefix_rows: list[dict[str, Any]]) -> str:
    scenario_body = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(csv_value(row.get(field, '')))}</td>"
            for field in [
                "scenario_id",
                "prefixes",
                "matched_candidate_rows",
                "matched_share",
                "row_level_full_release_counterfactual",
                "recommended",
            ]
        )
        + "</tr>"
        for row in scenario_rows
    )
    prefix_body = "".join(
        "<tr>"
        + "".join(
            f"<td>{html.escape(csv_value(row.get(field, '')))}</td>"
            for field in [
                "diagnosis_prefix",
                "historical_703790_review_rows_with_prefix",
                "share_of_703790_review_rows",
                "proposal_class",
            ]
        )
        + "</tr>"
        for row in prefix_rows
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>703790 Shadow Proposal</title>"
        "<style>body{font-family:Arial;margin:24px}table{border-collapse:collapse;margin-bottom:24px}"
        "td,th{border:1px solid #ccc;padding:5px 8px;font-size:12px}th{background:#eee}"
        ".warn{color:#8a4b00;font-weight:bold}</style></head><body>"
        "<h1>703790 Shadow-Only Policy Proposal</h1>"
        "<p class='warn'>No live rule change. No human/admin approval. Not apply-ready.</p>"
        "<h2>Scenarios</h2><table><tr><th>scenario</th><th>prefixes</th><th>matched</th>"
        "<th>share</th><th>full release counterfactual</th><th>recommended</th></tr>"
        f"{scenario_body}</table>"
        "<h2>Prefix Register</h2><table><tr><th>prefix</th><th>rows</th><th>share</th><th>class</th></tr>"
        f"{prefix_body}</table></body></html>"
    )


def build_proposal(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rule = runtime_rule()
    ai_candidate = find_ai_candidate()
    scenario_rows, prefix_row_counts, overall_code_status, rows_scanned = analyze_historical()
    total_review_rows = next(
        row["historical_703790_review_rows"]
        for row in scenario_rows
        if row["scenario_id"] == "clinical_core_h40_only"
    )
    prefix_rows = prefix_register(prefix_row_counts, int(total_review_rows))
    core = next(row for row in scenario_rows if row["scenario_id"] == "clinical_core_h40_only")

    proposal = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "proposal_id": "shadow_703790_h40_v1",
        "sut_code": TARGET_CODE,
        "procedure_name": rule.get("procedure_name"),
        "proposal_mode": "shadow_observation_only",
        "current_runtime_rule": {
            "procedure_key": rule.get("procedure_key"),
            "diagnosis_policy": rule.get("diagnosis_policy"),
            "decision_if_missing": rule.get("decision_if_missing"),
            "review_required": rule.get("review_required"),
            "runtime_decision_mode": rule.get("runtime_decision_mode"),
            "confidence": rule.get("confidence"),
            "source_evidence": rule.get("source_evidence"),
        },
        "shadow_overlay_preview": {
            "enabled": False,
            "candidate_diagnosis_prefixes": ["H40"],
            "shadow_event_on_match": "AI_PROVISIONAL_SHADOW_PASS_CANDIDATE",
            "actual_runtime_decision_on_match": "UNCHANGED",
            "fallback_actual_runtime_decision": "REVIEW_REQUIRED",
            "human_admin_approval_present": False,
            "apply_ready": False,
        },
        "clinical_quality_correction": {
            "issue": (
                "Prior MedGemma reasoning incorrectly characterized H52/H40/H04/H43 as ear/hearing diagnoses."
            ),
            "action": "Do not promote the broad AI prefix set; use H40 only for the proposed shadow cohort.",
            "prefixes_observation_only": ["H46", "H47.5", "H35.3"],
            "prefixes_keep_manual_review": ["H52", "H04", "H18", "H43"],
        },
        "historical_core_scenario": core,
        "historical_code_status_counts": dict(overall_code_status),
        "required_next_gate": "real human medical-policy and admin governance review",
        "safety": safety_block(),
    }

    paths = {
        "proposal": out_dir / "703790_SHADOW_POLICY_PROPOSAL.json",
        "scenario_json": out_dir / "703790_shadow_policy_scenarios.json",
        "scenario_csv": out_dir / "703790_shadow_policy_scenarios.csv",
        "prefix_json": out_dir / "703790_prefix_decision_register.json",
        "prefix_csv": out_dir / "703790_prefix_decision_register.csv",
        "monitoring": out_dir / "703790_SHADOW_MONITORING_PLAN.json",
        "rollback": out_dir / "703790_SHADOW_ROLLBACK_MANIFEST.json",
        "governance": out_dir / "703790_GOVERNANCE_REVIEW.txt",
        "dashboard": out_dir / "703790_shadow_policy_dashboard.html",
        "manifest": out_dir / "TASK_MANIFEST.json",
    }
    write_json(paths["proposal"], proposal)
    write_json(paths["scenario_json"], scenario_rows)
    write_csv(paths["scenario_csv"], scenario_rows, SCENARIO_FIELDS)
    write_json(paths["prefix_json"], prefix_rows)
    write_csv(paths["prefix_csv"], prefix_rows, PREFIX_FIELDS)
    write_json(paths["monitoring"], build_monitoring_plan(core))
    write_json(paths["rollback"], build_rollback_manifest())
    paths["governance"].write_text(
        governance_text(rule, ai_candidate, scenario_rows),
        encoding="utf-8",
    )
    paths["dashboard"].write_text(build_dashboard(scenario_rows, prefix_rows), encoding="utf-8")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "mode": "703790_shadow_only_policy_proposal",
        "inputs": {
            "rule_engine_results": str(RULE_ENGINE_RESULTS),
            "runtime_lookup": str(RUNTIME_LOOKUP),
            "ai_candidate": str(AI_CANDIDATE),
        },
        "counts": {
            "historical_rows_scanned": rows_scanned,
            "historical_703790_status_counts": dict(overall_code_status),
            "scenario_count": len(scenario_rows),
            "prefix_register_rows": len(prefix_rows),
            "recommended_h40_matched_rows": core["matched_candidate_rows"],
            "recommended_h40_full_release_counterfactual": core["row_level_full_release_counterfactual"],
            "recommended_h40_partial_resolution_counterfactual": core[
                "row_level_partial_resolution_counterfactual"
            ],
        },
        "critical_correction": proposal["clinical_quality_correction"],
        "safety": safety_block(),
        "generated_files": [str(path) for path in paths.values()],
    }
    write_json(paths["manifest"], manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build conservative 703790 shadow-only policy proposal.")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_proposal(out_dir=args.out_dir)
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"703790 shadow proposal written: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
