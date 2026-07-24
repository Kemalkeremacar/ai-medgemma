from __future__ import annotations

import argparse
import csv
import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
OUT_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_final_handoff_20260709"

POLICY_MANIFEST = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_policy_pack_20260709/TASK_MANIFEST.json"
OPERATIONAL_MANIFEST = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_operational_packs_20260709/TASK_MANIFEST.json"
TOP50_MANIFEST = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_medgemma_shadow_merge_top50_combined_20260709/TASK_MANIFEST.json"
AI_EXPERT_MANIFEST = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_expert_review_20260709/TASK_MANIFEST.json"
SHADOW_PREVIEW_MANIFEST = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_shadow_preview_20260709/TASK_MANIFEST.json"
SHADOW_PREVIEW_SUMMARY = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_shadow_preview_20260709/ai_provisional_shadow_preview_summary.csv"
SHADOW_PREVIEW_HOLDS = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_shadow_preview_20260709/ai_provisional_shadow_preview_guardrail_holds.csv"

SCHEMA_VERSION = "review_reduction_final_handoff.v1"

DECISION_FIELDS = [
    "decision_group",
    "code",
    "clinical_theme",
    "recommended_disposition",
    "rationale",
    "review_rows",
    "ai_expert_decision",
    "confidence",
    "confidence_score",
    "candidate_shadow_pass_occurrence_rows",
    "row_level_full_release_rows",
    "row_level_partial_resolution_rows",
    "human_admin_approval_present",
    "apply_ready",
    "auto_apply",
]

ARTIFACT_FIELDS = [
    "artifact_group",
    "artifact_name",
    "path",
    "purpose",
    "contains_case_level_rows",
    "apply_ready",
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


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


def int_value(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def safety_block() -> dict[str, bool]:
    return {
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "live_runtime_override": False,
        "auto_apply": False,
        "exports_case_level_rows": False,
        "exports_case_ids": False,
        "claims_human_admin_approval": False,
        "human_admin_approval_present": False,
        "apply_ready": False,
    }


def build_decision_register(preview_rows: list[dict[str, str]], hold_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for row in preview_rows:
        code = row.get("code")
        full_release = int_value(row.get("row_level_full_release_rows"))
        occurrence_rows = int_value(row.get("candidate_shadow_pass_occurrence_rows"))
        if code == "703790":
            decision_group = "recommended_shadow_pilot"
            disposition = "prepare_shadow_only_policy_proposal_for_governance_review"
            rationale = (
                "Strongest AI-only signal: high confidence, focused H-prefix cohort, no manual-prefix blocker, "
                "and measurable historical shadow-pass occurrence."
            )
        else:
            decision_group = "conditional_shadow_backlog"
            disposition = "keep_as_conditional_shadow_backlog_until_human_or_policy_governance_review"
            rationale = (
                "AI-only conditional signal exists, but confidence is medium and most rows remain REVIEW because "
                "other review reasons still exist."
            )
        decisions.append(
            {
                "decision_group": decision_group,
                "code": code,
                "clinical_theme": row.get("clinical_theme"),
                "recommended_disposition": disposition,
                "rationale": rationale,
                "review_rows": row.get("historical_review_code_rows"),
                "ai_expert_decision": row.get("ai_expert_decision"),
                "confidence": row.get("confidence"),
                "confidence_score": row.get("confidence_score"),
                "candidate_shadow_pass_occurrence_rows": occurrence_rows,
                "row_level_full_release_rows": full_release,
                "row_level_partial_resolution_rows": int_value(row.get("row_level_partial_resolution_rows")),
                "human_admin_approval_present": False,
                "apply_ready": False,
                "auto_apply": False,
            }
        )
    for row in hold_rows:
        hold_reason = row.get("hold_reason") or ""
        if "ai_provisional_approval_requires_confidence" in hold_reason:
            decision_group = "guardrail_blocked_ai_approval"
            disposition = "do_not_shadow_stage_without_stronger_evidence_or_human_governance"
        elif row.get("ai_expert_decision") == "ai_keep_manual_review":
            decision_group = "keep_manual_review"
            disposition = "keep_manual_review"
        else:
            decision_group = "not_selected_for_shadow_preview"
            disposition = "hold_for_human_or_policy_review"
        decisions.append(
            {
                "decision_group": decision_group,
                "code": row.get("code"),
                "clinical_theme": row.get("clinical_theme"),
                "recommended_disposition": disposition,
                "rationale": hold_reason,
                "review_rows": row.get("review_rows"),
                "ai_expert_decision": row.get("ai_expert_decision"),
                "confidence": row.get("confidence"),
                "confidence_score": row.get("confidence_score"),
                "candidate_shadow_pass_occurrence_rows": 0,
                "row_level_full_release_rows": 0,
                "row_level_partial_resolution_rows": 0,
                "human_admin_approval_present": False,
                "apply_ready": False,
                "auto_apply": False,
            }
        )
    decisions.append(
        {
            "decision_group": "mapping_backlog",
            "code": "16_mapping_candidates",
            "clinical_theme": "local_huv_mapping",
            "recommended_disposition": "resolve_mapping_before_any_review_reduction_policy",
            "rationale": "Large local/HUV dotted-code volume requires canonical mapping before medical policy refinement.",
            "review_rows": "",
            "ai_expert_decision": "",
            "confidence": "",
            "confidence_score": "",
            "candidate_shadow_pass_occurrence_rows": 0,
            "row_level_full_release_rows": 0,
            "row_level_partial_resolution_rows": 0,
            "human_admin_approval_present": False,
            "apply_ready": False,
            "auto_apply": False,
        }
    )
    return decisions


def build_artifact_index() -> list[dict[str, Any]]:
    return [
        {
            "artifact_group": "source",
            "artifact_name": "Historical rule engine results",
            "path": str(ROOT / "SUT/generated/dgx_handoff/rule_engine_results.csv"),
            "purpose": "Source aggregate/historical decision stream used for mining and shadow preview.",
            "contains_case_level_rows": True,
            "apply_ready": False,
        },
        {
            "artifact_group": "policy_pack",
            "artifact_name": "Review reduction policy candidates top50",
            "path": str(ROOT / "SUT/generated/shadow_quality_gate/review_reduction_policy_pack_20260709/review_reduction_policy_candidates_top50.csv"),
            "purpose": "Empirical review-reduction candidate list from full historical rule-engine output.",
            "contains_case_level_rows": False,
            "apply_ready": False,
        },
        {
            "artifact_group": "medgemma_shadow",
            "artifact_name": "MedGemma top50 triage",
            "path": str(ROOT / "SUT/generated/shadow_quality_gate/review_reduction_medgemma_shadow_merge_top50_combined_20260709/medgemma_review_reduction_shadow_triage_top50_combined.csv"),
            "purpose": "AI shadow triage into expert fast-track, mapping backlog, manual-review hold.",
            "contains_case_level_rows": False,
            "apply_ready": False,
        },
        {
            "artifact_group": "operational_packs",
            "artifact_name": "Expert fast-track all19",
            "path": str(ROOT / "SUT/generated/shadow_quality_gate/review_reduction_operational_packs_20260709/expert_fast_track_review_pack/expert_fast_track_candidates_all19.csv"),
            "purpose": "Expert-review worklist; later used for AI-only provisional review because no expert was available.",
            "contains_case_level_rows": False,
            "apply_ready": False,
        },
        {
            "artifact_group": "operational_packs",
            "artifact_name": "Mapping backlog all16",
            "path": str(ROOT / "SUT/generated/shadow_quality_gate/review_reduction_operational_packs_20260709/mapping_backlog_pack/mapping_backlog_candidates_all16.csv"),
            "purpose": "Local/HUV mapping candidates that must be resolved before policy refinement.",
            "contains_case_level_rows": False,
            "apply_ready": False,
        },
        {
            "artifact_group": "ai_provisional_expert_review",
            "artifact_name": "AI provisional expert decisions all19",
            "path": str(ROOT / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_expert_review_20260709/ai_provisional_expert_decisions_all19.csv"),
            "purpose": "MedGemma expert-surrogate output; not human/admin approval.",
            "contains_case_level_rows": False,
            "apply_ready": False,
        },
        {
            "artifact_group": "ai_provisional_shadow_preview",
            "artifact_name": "AI provisional shadow preview summary",
            "path": str(ROOT / "SUT/generated/shadow_quality_gate/review_reduction_ai_provisional_shadow_preview_20260709/ai_provisional_shadow_preview_summary.csv"),
            "purpose": "Aggregate deterministic preview of AI-only candidate-code impact.",
            "contains_case_level_rows": False,
            "apply_ready": False,
        },
        {
            "artifact_group": "final_handoff",
            "artifact_name": "Final handoff package",
            "path": str(OUT_DIR),
            "purpose": "Final consolidated summary, decision register, next steps, and artifact index.",
            "contains_case_level_rows": False,
            "apply_ready": False,
        },
    ]


def section(title: str, lines: list[str]) -> str:
    return title + "\n" + ("=" * len(title)) + "\n" + "\n".join(lines) + "\n"


def build_executive_summary(
    *,
    policy_manifest: dict[str, Any],
    operational_manifest: dict[str, Any],
    top50_manifest: dict[str, Any],
    ai_manifest: dict[str, Any],
    preview_manifest: dict[str, Any],
    preview_rows: list[dict[str, str]],
) -> str:
    policy_counts = policy_manifest.get("counts") or {}
    source_context = policy_manifest.get("source_context") or {}
    top50_counts = top50_manifest.get("counts") or {}
    top50_by_category = top50_counts.get("by_merged_triage_category") or {}
    operational_counts = operational_manifest.get("counts") or {}
    ai_counts = ai_manifest.get("counts") or {}
    preview_counts = preview_manifest.get("counts") or {}
    lines: list[str] = []
    lines.append(
        section(
            "YONETIM OZETI",
            [
                "Calisma, 2.36M historical provision rule-engine sonucundan review-reduction firsatlarini bulmak icin yapildi.",
                "Canli karar katmani degistirilmedi; deterministik rule engine halen tek karar katmani olarak kalmali.",
                "MedGemma sadece shadow reviewer, triage assistant ve AI-only provisional medical-policy reviewer olarak kullanildi.",
                "AI ciktilari insan/admin/expert onayi degildir; final paket apply-ready degildir.",
            ],
        )
    )
    lines.append(
        section(
            "ANA SAYILAR",
            [
                f"Historical rows: {source_context.get('total_provision_rows')}",
                f"Baseline REVIEW rows: {source_context.get('total_review_rows')}",
                f"Initial top50 policy candidates: {policy_counts.get('candidates')}",
                f"MedGemma top50 fast-track to expert: {top50_by_category.get('fast_track_to_human_expert_review')}",
                f"Mapping backlog before policy: {top50_by_category.get('mapping_backlog_before_policy')}",
                f"Manual-review/invalid hold: {top50_by_category.get('keep_manual_review_observation', 0) + top50_by_category.get('response_invalid_manual_review_required', 0)}",
                f"Operational expert fast-track rows: {operational_counts.get('expert_fast_track_rows')}",
                f"AI provisional expert responses: {ai_counts.get('responses')} responses, {ai_counts.get('valid_responses')} valid",
                f"AI-only shadow candidates after guardrails: {ai_counts.get('shadow_candidate_rows')}",
                f"Shadow preview candidate-code occurrences: {preview_counts.get('candidate_shadow_pass_occurrence_rows')}",
                f"Shadow preview full REVIEW-row release candidates: {preview_counts.get('row_level_full_release_rows')}",
                f"Shadow preview partial-resolution rows still REVIEW: {preview_counts.get('row_level_partial_resolution_rows')}",
            ],
        )
    )
    strongest = next((row for row in preview_rows if row.get("code") == "703790"), None)
    if strongest:
        lines.append(
            section(
                "EN NET ADAY",
                [
                    "703790, eye_ear temasi icinde en guclu AI-only shadow pilot adayidir.",
                    f"Confidence score: {strongest.get('confidence_score')}",
                    f"Historical review-code rows: {strongest.get('historical_review_code_rows')}",
                    f"AI provisional shadow-pass occurrence rows: {strongest.get('candidate_shadow_pass_occurrence_rows')}",
                    f"Full row-level release candidates: {strongest.get('row_level_full_release_rows')}",
                    "Bu aday bile apply-ready degildir; sadece human/admin governance review icin hazir shadow-only pilot onerisi sayilmalidir.",
                ],
            )
        )
    lines.append(
        section(
            "SON KARAR",
            [
                "Canli kurala alinacak bir sonuc yok.",
                "703790 icin shadow-only policy proposal hazirlanabilir ve insan/admin governance onayina sunulabilir.",
                "907440 ve 906510 conditional shadow backlog olarak tutulmali.",
                "908115 ve 704530, MedGemma approve demesine ragmen confidence guardrail nedeniyle bloklandi.",
                "Mapping backlog cozulmeden lokal/HUV dotted code adaylari icin policy refinement yapilmamali.",
            ],
        )
    )
    lines.append(
        section(
            "GUVENLIK SINIRLARI",
            [
                "human_admin_approval_present=false",
                "apply_ready=false",
                "auto_apply=false",
                "writes_to_production_db=false",
                "writes_to_qdrant=false",
                "live_runtime_override=false",
                "exports_case_level_rows=false",
                "MedGemma output is not human/admin/expert approval.",
            ],
        )
    )
    return "\n".join(lines)


def build_next_steps() -> str:
    return "\n".join(
        [
            "ONERILEN SONRAKI ADIMLAR",
            "========================",
            "1. 703790 icin sadece shadow-only policy proposal hazirla; human/admin governance onayi olmadan live apply yapma.",
            "2. 703790 proposal icin deterministic diff, rollback manifest ve monitoring kriterlerini ayrica uret.",
            "3. 907440 ve 906510'u conditional backlog'da tut; daha fazla klinik kaynak veya human review gelirse tekrar degerlendir.",
            "4. 908115 ve 704530 icin confidence guardrail'i asmak icin ya daha guclu kanit ya da human/admin governance gerektir.",
            "5. 16 mapping backlog adayini katalog/mapping owner surecine ver; mapping cozulmeden review reduction yapma.",
            "6. Manual-review hold grubunu canli sistemde aynen REVIEW tut.",
            "",
            "NOT: Bu dosya uygulama talimati degildir; governance/handoff ozetidir.",
        ]
    )


def build_dashboard(summary_text: str, decisions: list[dict[str, Any]]) -> str:
    columns = [
        "decision_group",
        "code",
        "recommended_disposition",
        "confidence_score",
        "candidate_shadow_pass_occurrence_rows",
        "row_level_full_release_rows",
    ]
    header = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
    rows = []
    for decision in decisions:
        rows.append(
            "<tr>"
            + "".join(f"<td>{html.escape(csv_value(decision.get(column, '')))}</td>" for column in columns)
            + "</tr>"
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Review Reduction Final Handoff</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px;line-height:1.35}"
        "pre{white-space:pre-wrap;background:#f7f7f7;padding:12px;border:1px solid #ddd}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px;font-size:12px}"
        "th{background:#f3f3f3}.warn{color:#8a4b00;font-weight:bold}</style>"
        "</head><body>"
        "<h1>Review Reduction Final Handoff</h1>"
        "<p class='warn'>Not apply-ready. No human/admin approval is claimed.</p>"
        f"<pre>{html.escape(summary_text)}</pre>"
        f"<h2>Decision Register</h2><table><thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        "</body></html>"
    )


def build_final_handoff(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    policy_manifest = load_json(POLICY_MANIFEST)
    operational_manifest = load_json(OPERATIONAL_MANIFEST)
    top50_manifest = load_json(TOP50_MANIFEST)
    ai_manifest = load_json(AI_EXPERT_MANIFEST)
    preview_manifest = load_json(SHADOW_PREVIEW_MANIFEST)
    preview_rows = load_csv(SHADOW_PREVIEW_SUMMARY)
    hold_rows = load_csv(SHADOW_PREVIEW_HOLDS)

    decisions = build_decision_register(preview_rows, hold_rows)
    artifacts = build_artifact_index()
    summary_text = build_executive_summary(
        policy_manifest=policy_manifest,
        operational_manifest=operational_manifest,
        top50_manifest=top50_manifest,
        ai_manifest=ai_manifest,
        preview_manifest=preview_manifest,
        preview_rows=preview_rows,
    )
    next_steps = build_next_steps()

    paths = {
        "executive_summary": out_dir / "FINAL_EXECUTIVE_SUMMARY.txt",
        "next_steps": out_dir / "NEXT_STEPS.txt",
        "decision_register_json": out_dir / "final_decision_register.json",
        "decision_register_csv": out_dir / "final_decision_register.csv",
        "artifact_index_json": out_dir / "final_artifact_index.json",
        "artifact_index_csv": out_dir / "final_artifact_index.csv",
        "dashboard": out_dir / "final_handoff_dashboard.html",
        "manifest": out_dir / "TASK_MANIFEST.json",
    }
    paths["executive_summary"].write_text(summary_text, encoding="utf-8")
    paths["next_steps"].write_text(next_steps, encoding="utf-8")
    write_json(paths["decision_register_json"], decisions)
    write_csv(paths["decision_register_csv"], decisions, DECISION_FIELDS)
    write_json(paths["artifact_index_json"], artifacts)
    write_csv(paths["artifact_index_csv"], artifacts, ARTIFACT_FIELDS)
    paths["dashboard"].write_text(build_dashboard(summary_text, decisions), encoding="utf-8")

    preview_counts = preview_manifest.get("counts") or {}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "mode": "final_handoff_no_live_apply",
        "out_dir": str(out_dir),
        "inputs": {
            "policy_manifest": str(POLICY_MANIFEST),
            "operational_manifest": str(OPERATIONAL_MANIFEST),
            "top50_medgemma_manifest": str(TOP50_MANIFEST),
            "ai_provisional_expert_manifest": str(AI_EXPERT_MANIFEST),
            "ai_provisional_shadow_preview_manifest": str(SHADOW_PREVIEW_MANIFEST),
        },
        "headline_result": {
            "recommended_shadow_pilot_code": "703790",
            "candidate_shadow_pass_occurrence_rows": preview_counts.get("candidate_shadow_pass_occurrence_rows"),
            "row_level_full_release_rows": preview_counts.get("row_level_full_release_rows"),
            "row_level_partial_resolution_rows": preview_counts.get("row_level_partial_resolution_rows"),
            "guardrail_hold_rows": preview_counts.get("guardrail_hold_rows"),
        },
        "safety": safety_block(),
        "generated_files": [str(path) for path in paths.values()],
    }
    write_json(paths["manifest"], manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build final handoff package for review-reduction shadow work.")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_final_handoff(out_dir=args.out_dir)
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"Review reduction final handoff written: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
