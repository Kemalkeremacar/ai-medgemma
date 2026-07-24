from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
SHADOW_ROOT = ROOT / "SUT/generated/shadow_quality_gate"
SCRIPT_ROOT = ROOT / "SUT/generated/dgx_handoff"
BUNDLE_ROOT = SCRIPT_ROOT / "review_reduction_dgx_transfer_bundle_20260709"
ZIP_PATH = SCRIPT_ROOT / "review_reduction_dgx_transfer_bundle_20260709.zip"
PROPOSAL_703790_DIR = SHADOW_ROOT / "review_reduction_703790_shadow_policy_proposal_20260720"
PROPOSAL_703790 = PROPOSAL_703790_DIR / "703790_SHADOW_POLICY_PROPOSAL.json"
SCENARIOS_703790 = PROPOSAL_703790_DIR / "703790_shadow_policy_scenarios.json"
STRUCTURE_DOCUMENT = SCRIPT_ROOT / "REVIEW_REDUCTION_DGX_BUNDLE_STRUCTURE.md"

SCHEMA_VERSION = "review_reduction_dgx_transfer_bundle.v1"
SUGGESTED_SHADOW_COLLECTION = "sut_policy_shadow_review_reduction_20260709"

CURATED_ARTIFACT_DIRS = [
    PROPOSAL_703790_DIR,
    SHADOW_ROOT / "review_reduction_final_handoff_20260709",
    SHADOW_ROOT / "review_reduction_ai_provisional_shadow_preview_20260709",
    SHADOW_ROOT / "review_reduction_ai_provisional_expert_review_20260709",
    SHADOW_ROOT / "review_reduction_operational_packs_20260709",
    SHADOW_ROOT / "review_reduction_medgemma_shadow_merge_top50_combined_20260709",
    SHADOW_ROOT / "review_reduction_policy_pack_20260709",
]

SCRIPT_FILES = [
    SCRIPT_ROOT / "build_703790_shadow_policy_proposal.py",
    SCRIPT_ROOT / "validate_703790_shadow_bundle.py",
    SCRIPT_ROOT / "analyze_full_historical_empirical_rule_intelligence.py",
    SCRIPT_ROOT / "build_review_reduction_policy_pack.py",
    SCRIPT_ROOT / "build_review_reduction_medgemma_shadow_handoff.py",
    SCRIPT_ROOT / "run_review_reduction_medgemma_shadow_inference.py",
    SCRIPT_ROOT / "merge_review_reduction_medgemma_shadow_responses.py",
    SCRIPT_ROOT / "build_review_reduction_operational_packs.py",
    SCRIPT_ROOT / "run_review_reduction_medgemma_ai_provisional_expert_review.py",
    SCRIPT_ROOT / "build_ai_provisional_review_reduction_shadow_preview.py",
    SCRIPT_ROOT / "build_review_reduction_final_handoff.py",
    SCRIPT_ROOT / "build_review_reduction_dgx_transfer_bundle.py",
]

FINAL_DECISION_REGISTER = SHADOW_ROOT / "review_reduction_final_handoff_20260709/final_decision_register.csv"
FINAL_EXECUTIVE_SUMMARY = SHADOW_ROOT / "review_reduction_final_handoff_20260709/FINAL_EXECUTIVE_SUMMARY.txt"
FINAL_MANIFEST = SHADOW_ROOT / "review_reduction_final_handoff_20260709/TASK_MANIFEST.json"

EXCLUDED_FILENAMES = {
    "rule_engine_results.csv",
}


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


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(path: Path, base: Path) -> str:
    return str(path.relative_to(base)).replace("\\", "/")


def should_exclude(path: Path) -> bool:
    return path.name in EXCLUDED_FILENAMES


def copy_tree_curated(source: Path, destination: Path) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    if not source.exists():
        return copied
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        if should_exclude(path):
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(
            {
                "source": str(path),
                "bundle_path": safe_relative(target, BUNDLE_ROOT),
                "size_bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
    return copied


def copy_scripts(destination: Path) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    destination.mkdir(parents=True, exist_ok=True)
    for script in SCRIPT_FILES:
        if not script.exists():
            continue
        target = destination / script.name
        shutil.copy2(script, target)
        copied.append(
            {
                "source": str(script),
                "bundle_path": safe_relative(target, BUNDLE_ROOT),
                "size_bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
    return copied


def copy_structure_document(bundle_root: Path) -> dict[str, Any] | None:
    if not STRUCTURE_DOCUMENT.exists():
        return None
    target = bundle_root / "BUNDLE_STRUCTURE.md"
    shutil.copy2(STRUCTURE_DOCUMENT, target)
    return {
        "source": str(STRUCTURE_DOCUMENT),
        "bundle_path": safe_relative(target, bundle_root),
        "size_bytes": target.stat().st_size,
        "sha256": sha256_file(target),
    }


def safety_payload() -> dict[str, bool]:
    return {
        "human_admin_approval_present": False,
        "apply_ready": False,
        "auto_apply": False,
        "live_rule_candidate": False,
        "live_runtime_override": False,
        "runtime_decision_changed": False,
        "shadow_only": True,
        "production_qdrant_write_allowed": False,
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "exports_case_level_rows": False,
        "exports_case_ids": False,
    }


def qdrant_record_text(row: dict[str, str]) -> str:
    return (
        f"Review-reduction shadow decision for {row.get('code')}: "
        f"group={row.get('decision_group')}; "
        f"disposition={row.get('recommended_disposition')}; "
        f"theme={row.get('clinical_theme')}; "
        f"confidence={row.get('confidence')} ({row.get('confidence_score')}); "
        f"candidate_shadow_pass_occurrence_rows={row.get('candidate_shadow_pass_occurrence_rows')}; "
        f"row_level_full_release_rows={row.get('row_level_full_release_rows')}; "
        f"rationale={row.get('rationale')}."
    )


def build_qdrant_shadow_preview(qdrant_dir: Path) -> dict[str, Any]:
    decisions = load_csv(FINAL_DECISION_REGISTER)
    proposal_703790 = load_json(PROPOSAL_703790) if PROPOSAL_703790.exists() else {}
    scenarios_703790 = load_json(SCENARIOS_703790) if SCENARIOS_703790.exists() else []
    scenario_by_id = {
        str(row.get("scenario_id") or ""): row
        for row in scenarios_703790
        if isinstance(row, dict)
    }
    core_703790 = scenario_by_id.get("clinical_core_h40_only") or {}
    broad_703790 = scenario_by_id.get("original_ai_broad_counterfactual") or {}
    records: list[dict[str, Any]] = []
    for index, row in enumerate(decisions, start=1):
        code = row.get("code") or f"row_{index}"
        point_key = f"review_reduction_shadow_{code}_{index}"
        payload = {
            "artifact_type": "review_reduction_shadow_decision",
            "source_bundle_schema_version": SCHEMA_VERSION,
            "source": "dgx_transfer_bundle_preview",
            "governance_status": "not_approved",
            "decision_group": row.get("decision_group"),
            "code": row.get("code"),
            "clinical_theme": row.get("clinical_theme"),
            "recommended_disposition": row.get("recommended_disposition"),
            "ai_expert_decision": row.get("ai_expert_decision"),
            "confidence": row.get("confidence"),
            "confidence_score": row.get("confidence_score"),
            "candidate_shadow_pass_occurrence_rows": row.get("candidate_shadow_pass_occurrence_rows"),
            "row_level_full_release_rows": row.get("row_level_full_release_rows"),
            "row_level_partial_resolution_rows": row.get("row_level_partial_resolution_rows"),
            "rationale": row.get("rationale"),
            **safety_payload(),
        }
        document = qdrant_record_text(row)
        if code == "703790" and proposal_703790:
            payload.update(
                {
                    "governance_status": "corrected_shadow_proposal_not_approved",
                    "recommended_disposition": "shadow_observation_h40_only",
                    "candidate_diagnosis_prefixes": ["H40"],
                    "candidate_shadow_pass_occurrence_rows": core_703790.get("matched_candidate_rows"),
                    "row_level_full_release_rows": core_703790.get(
                        "row_level_full_release_counterfactual"
                    ),
                    "row_level_partial_resolution_rows": core_703790.get(
                        "row_level_partial_resolution_counterfactual"
                    ),
                    "shadow_overlay_enabled": False,
                    "supersedes_broad_ai_prefix_preview": True,
                    "model_quality_warning": (
                        "Prior MedGemma reasoning incorrectly characterized H52/H40/H04/H43 as "
                        "ear/hearing diagnoses; do not use the broad prefix list as policy."
                    ),
                    "original_broad_counterfactual": {
                        "prefixes": broad_703790.get("prefixes") or [],
                        "matched_candidate_rows": broad_703790.get("matched_candidate_rows"),
                        "row_level_full_release_counterfactual": broad_703790.get(
                            "row_level_full_release_counterfactual"
                        ),
                        "row_level_partial_resolution_counterfactual": broad_703790.get(
                            "row_level_partial_resolution_counterfactual"
                        ),
                    },
                    "corrected_proposal_source": safe_relative(PROPOSAL_703790, ROOT),
                }
            )
            document = (
                "Corrected 703790 shadow-only proposal: use H40 only as an "
                "AI_PROVISIONAL_SHADOW_PASS_CANDIDATE observation cohort; keep the deterministic "
                "runtime decision unchanged as REVIEW_REQUIRED. The original broad AI prefix set "
                "is counterfactual only and is superseded because its clinical rationale contained "
                "an ICD-category interpretation error."
            )
        records.append(
            {
                "preview_only": True,
                "not_qdrant_upsert_request": True,
                "suggested_collection": SUGGESTED_SHADOW_COLLECTION,
                "point_id": hashlib.sha1(point_key.encode("utf-8")).hexdigest(),
                "document": document,
                "payload": payload,
            }
        )
    qdrant_dir.mkdir(parents=True, exist_ok=True)
    preview_path = qdrant_dir / "QDRANT_SHADOW_PAYLOAD_PREVIEW.jsonl"
    write_jsonl(preview_path, records)
    contract = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "suggested_collection": SUGGESTED_SHADOW_COLLECTION,
        "purpose": "Preview-only payloads for a separate shadow Qdrant collection. This is not a production upsert request.",
        "minimum_required_payload_flags": safety_payload(),
        "hard_rules": [
            "Do not write these records to production Qdrant collections.",
            "If indexed, use only a separate shadow collection.",
            "Do not treat any record as human/admin/expert approval.",
            "Do not use these records for live rule lookup, runtime override, payment approval, or auto-apply.",
            "Do not index case-level historical rows or case IDs.",
        ],
        "record_count": len(records),
        "preview_jsonl": str(preview_path),
    }
    contract_path = qdrant_dir / "QDRANT_SHADOW_COLLECTION_CONTRACT.json"
    write_json(contract_path, contract)
    warning_path = qdrant_dir / "DO_NOT_WRITE_TO_PRODUCTION_QDRANT.txt"
    warning_path.write_text(
        "\n".join(
            [
                "DO NOT WRITE THIS BUNDLE TO PRODUCTION QDRANT.",
                "",
                "The JSONL file in this folder is a shadow payload preview only.",
                "If Qdrant indexing is needed, use a separate shadow collection:",
                SUGGESTED_SHADOW_COLLECTION,
                "",
                "Required semantics:",
                "human_admin_approval_present=false",
                "apply_ready=false",
                "auto_apply=false",
                "live_rule_candidate=false",
                "shadow_only=true",
                "production_qdrant_write_allowed=false",
                "",
                "No live rule lookup, runtime override, production DB write, payment approval, or auto-apply is authorized.",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "record_count": len(records),
        "files": [
            {
                "bundle_path": safe_relative(preview_path, BUNDLE_ROOT),
                "size_bytes": preview_path.stat().st_size,
                "sha256": sha256_file(preview_path),
            },
            {
                "bundle_path": safe_relative(contract_path, BUNDLE_ROOT),
                "size_bytes": contract_path.stat().st_size,
                "sha256": sha256_file(contract_path),
            },
            {
                "bundle_path": safe_relative(warning_path, BUNDLE_ROOT),
                "size_bytes": warning_path.stat().st_size,
                "sha256": sha256_file(warning_path),
            },
        ],
    }


def write_readme(bundle_root: Path) -> Path:
    readme = bundle_root / "DGX_TRANSFER_README.txt"
    summary = FINAL_EXECUTIVE_SUMMARY.read_text(encoding="utf-8") if FINAL_EXECUTIVE_SUMMARY.exists() else ""
    readme.write_text(
        "\n".join(
            [
                "DGX TRANSFER BUNDLE - REVIEW REDUCTION SHADOW WORK",
                "=================================================",
                "",
                "This bundle is curated for DGX-side analysis and handoff.",
                "It is not a live apply package and does not authorize Qdrant writes.",
                "",
                "Important directories:",
                "- artifacts/: curated aggregate/shadow outputs",
                "- scripts/: reproducibility scripts",
                "- qdrant_shadow/: preview-only JSONL and collection contract",
                "- BUNDLE_STRUCTURE.md: bundle inventory, precedence, and safety semantics",
                "",
                "703790 correction:",
                "- Read artifacts/review_reduction_703790_shadow_policy_proposal_20260720 first.",
                "- The recommended cohort is H40-only shadow observation: 146/212 historical REVIEW rows.",
                "- The earlier 199-row broad AI result is counterfactual only and must not be promoted.",
                "- H52/H04/H18/H43 remain manual-review; H46/H47.5/H35.3 remain observation-only.",
                "- The runtime result remains REVIEW_REQUIRED.",
                "",
                "Excluded on purpose:",
                "- SUT/generated/dgx_handoff/rule_engine_results.csv",
                "- any case-level export or case_id list",
                "",
                "Headline:",
                summary,
            ]
        ),
        encoding="utf-8",
    )
    return readme


def write_dgx_agent_prompt(bundle_root: Path) -> Path:
    prompt = bundle_root / "DGX_AGENT_PROMPT_COPY_PASTE.txt"
    prompt.write_text(
        "\n".join(
            [
                "DGX AGENT PROMPT - REVIEW REDUCTION SHADOW BUNDLE",
                "=================================================",
                "",
                "You are the DGX-side agent receiving a curated review-reduction shadow bundle.",
                "Your task is to inspect, validate, and prepare DGX-side shadow analysis outputs from the bundle.",
                "You must not perform any production write, live rule change, or production Qdrant upsert.",
                "",
                "INPUT BUNDLE",
                "------------",
                "The user will provide or place this zip/directory on DGX:",
                "review_reduction_dgx_transfer_bundle_20260709.zip",
                "",
                "After unpacking, expected top-level files/directories are:",
                "- DGX_TRANSFER_MANIFEST.json",
                "- DGX_TRANSFER_README.txt",
                "- DGX_AGENT_PROMPT_COPY_PASTE.txt",
                "- BUNDLE_STRUCTURE.md",
                "- artifacts/",
                "- scripts/",
                "- qdrant_shadow/",
                "",
                "MANDATORY FIRST STEPS",
                "---------------------",
                "1. Unpack the bundle into a dedicated working directory.",
                "2. Read these files before doing anything else:",
                "   - DGX_TRANSFER_MANIFEST.json",
                "   - DGX_TRANSFER_README.txt",
                "   - qdrant_shadow/QDRANT_SHADOW_COLLECTION_CONTRACT.json",
                "   - qdrant_shadow/DO_NOT_WRITE_TO_PRODUCTION_QDRANT.txt",
                "   - artifacts/review_reduction_final_handoff_20260709/FINAL_EXECUTIVE_SUMMARY.txt",
                "   - artifacts/review_reduction_final_handoff_20260709/final_decision_register.csv",
                "   - artifacts/review_reduction_703790_shadow_policy_proposal_20260720/703790_GOVERNANCE_REVIEW.txt",
                "   - artifacts/review_reduction_703790_shadow_policy_proposal_20260720/703790_SHADOW_POLICY_PROPOSAL.json",
                "   - artifacts/review_reduction_703790_shadow_policy_proposal_20260720/703790_SHADOW_MONITORING_PLAN.json",
                "   - artifacts/review_reduction_703790_shadow_policy_proposal_20260720/703790_SHADOW_ROLLBACK_MANIFEST.json",
                "3. Verify that the bundle is curated and does not include rule_engine_results.csv.",
                "4. Verify that no included final/shadow artifact contains case_id exports intended for indexing.",
                "5. Verify all safety flags remain false where expected:",
                "   - human_admin_approval_present=false",
                "   - apply_ready=false",
                "   - auto_apply=false",
                "   - live_rule_candidate=false",
                "   - live_runtime_override=false",
                "   - runtime_decision_changed=false",
                "   - production_qdrant_write_allowed=false",
                "   - writes_to_production_db=false",
                "   - writes_to_qdrant=false",
                "   - exports_case_level_rows=false",
                "   - exports_case_ids=false",
                "",
                "NON-NEGOTIABLE GUARDRAILS",
                "-------------------------",
                "- Do not write to production Qdrant collections.",
                "- Do not write to any production database.",
                "- Do not modify live rule lookup/runtime files.",
                "- Do not create live apply plans.",
                "- Do not treat MedGemma or AI-only provisional output as human/admin/expert approval.",
                "- Do not index raw case-level historical data or case IDs.",
                "- Do not infer approval from the presence of this bundle.",
                "- If there is any ambiguity, stop and report the ambiguity instead of writing.",
                "",
                "WHAT THIS BUNDLE MEANS",
                "----------------------",
                "This bundle summarizes a review-reduction shadow study over 2,358,495 historical provision rows.",
                "The deterministic rule engine remains the only live decision layer.",
                "MedGemma was used only as shadow reviewer, triage assistant, and AI-only provisional medical-policy reviewer.",
                "For 703790, the later corrected proposal supersedes the earlier broad AI prefix preview.",
                "",
                "Headline result:",
                "- recommended_shadow_pilot_code: 703790",
                "- candidate_shadow_pass_occurrence_rows: 505 across the 3 AI-only candidates",
                "- row_level_full_release_rows: 32",
                "- row_level_partial_resolution_rows: 473",
                "- guardrail_hold_rows: 16",
                "- corrected_703790_h40_shadow_candidate_rows: 146",
                "- corrected_703790_h40_full_release_counterfactual: 7",
                "- corrected_703790_h40_partial_resolution_counterfactual: 139",
                "",
                "Final disposition summary:",
                "- 703790: H40-only shadow observation proposal; runtime stays REVIEW_REQUIRED.",
                "- The earlier 703790 broad 199/16 result is counterfactual only and not recommended.",
                "- H52/H04/H18/H43 are excluded from shadow pass pending real medical-policy review.",
                "- 907440 and 906510: conditional shadow backlog; not live-ready.",
                "- 908115 and 704530: blocked by confidence guardrail despite AI provisional approval.",
                "- 16 mapping candidates: resolve mapping before any policy refinement.",
                "- manual-review hold group: keep REVIEW.",
                "",
                "QDRANT-SPECIFIC INSTRUCTIONS",
                "----------------------------",
                "There is a folder named qdrant_shadow/ containing:",
                "- QDRANT_SHADOW_PAYLOAD_PREVIEW.jsonl",
                "- QDRANT_SHADOW_COLLECTION_CONTRACT.json",
                "- DO_NOT_WRITE_TO_PRODUCTION_QDRANT.txt",
                "",
                "The JSONL file is preview-only. It is not a Qdrant upsert request.",
                "If, and only if, the user explicitly asks you in the DGX session to index it, use only a separate shadow collection.",
                "Suggested collection name:",
                "sut_policy_shadow_review_reduction_20260709",
                "",
                "Every indexed payload must preserve these metadata values:",
                "- artifact_type=review_reduction_shadow_decision",
                "- governance_status must remain a not-approved status; the corrected 703790 value is corrected_shadow_proposal_not_approved",
                "- human_admin_approval_present=false",
                "- apply_ready=false",
                "- auto_apply=false",
                "- live_rule_candidate=false",
                "- live_runtime_override=false",
                "- runtime_decision_changed=false",
                "- shadow_only=true",
                "- production_qdrant_write_allowed=false",
                "- exports_case_level_rows=false",
                "- exports_case_ids=false",
                "",
                "If a local DGX embedding/Qdrant ingestion pipeline exists:",
                "- Use the JSONL 'document' field as text to embed.",
                "- Preserve the JSONL 'payload' object exactly, except for adding DGX-side audit fields such as indexed_at, bundle_sha256, or ingestion_run_id.",
                "- Upsert only into the shadow collection.",
                "- Produce a dry-run summary before any actual shadow upsert.",
                "",
                "If no approved shadow Qdrant ingestion pipeline exists:",
                "- Do not improvise production writes.",
                "- Produce a plan file named DGX_SHADOW_QDRANT_INGESTION_PLAN.txt.",
                "- Include the exact collection name, payload schema, and safety checks needed for later human review.",
                "",
                "EXPECTED OUTPUTS FROM YOU",
                "-------------------------",
                "Create DGX-side output files next to the unpacked bundle, not inside production application directories:",
                "- DGX_AGENT_REVIEW_REPORT.txt",
                "- DGX_BUNDLE_INTEGRITY_CHECK.json",
                "- DGX_SHADOW_QDRANT_INGESTION_PLAN.txt",
                "- Optional only if explicitly approved and actually run: DGX_SHADOW_QDRANT_DRY_RUN_OR_UPSERT_RESULT.json",
                "",
                "Your report must include:",
                "1. Bundle path and manifest schema version.",
                "2. Whether rule_engine_results.csv is absent from the bundle.",
                "3. Whether case_id/case-level indexing risk was found.",
                "4. Final headline result and recommended code 703790.",
                "5. Decision register counts by decision_group.",
                "6. Qdrant recommendation: no production write; shadow collection only.",
                "7. Any blockers or missing files.",
                "",
                "FINAL RESPONSE STYLE",
                "--------------------",
                "Respond in Turkish.",
                "Be explicit that this is not apply-ready and not human/admin approval.",
                "If all checks pass, say the bundle is safe for DGX-side shadow analysis.",
                "Do not say it is safe for production apply.",
                "",
                "START NOW",
                "---------",
                "Begin by unpacking/reading the bundle manifest and safety contract, then perform the validation steps above.",
            ]
        ),
        encoding="utf-8",
    )
    return prompt

def zip_directory(source_dir: Path, zip_path: Path) -> dict[str, Any]:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in source_dir.rglob("*"):
            if path.is_file():
                archive.write(path, arcname=safe_relative(path, source_dir))
    return {
        "path": str(zip_path),
        "size_bytes": zip_path.stat().st_size,
        "sha256": sha256_file(zip_path),
    }


def build_bundle(bundle_root: Path = BUNDLE_ROOT, zip_path: Path = ZIP_PATH) -> dict[str, Any]:
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle_root.mkdir(parents=True, exist_ok=True)

    copied_artifacts: list[dict[str, Any]] = []
    artifacts_dir = bundle_root / "artifacts"
    for source_dir in CURATED_ARTIFACT_DIRS:
        destination = artifacts_dir / source_dir.name
        copied_artifacts.extend(copy_tree_curated(source_dir, destination))

    copied_scripts = copy_scripts(bundle_root / "scripts")
    copied_structure_document = copy_structure_document(bundle_root)
    qdrant_preview = build_qdrant_shadow_preview(bundle_root / "qdrant_shadow")
    readme = write_readme(bundle_root)
    dgx_agent_prompt = write_dgx_agent_prompt(bundle_root)

    final_manifest = load_json(FINAL_MANIFEST)
    excluded_sources = [
        {
            "path": str(SCRIPT_ROOT / "rule_engine_results.csv"),
            "reason": "case-level historical source; too broad for curated DGX transfer bundle and must not be indexed into Qdrant",
        }
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "bundle_root": str(bundle_root),
        "suggested_zip_path": str(zip_path),
        "purpose": "Curated DGX handoff bundle for review-reduction shadow work.",
        "headline_result": final_manifest.get("headline_result") or {},
        "included": {
            "artifact_dirs": [str(path) for path in CURATED_ARTIFACT_DIRS],
            "artifact_files_copied": len(copied_artifacts),
            "script_files_copied": len(copied_scripts),
            "structure_document_copied": copied_structure_document is not None,
            "qdrant_shadow_preview_records": qdrant_preview["record_count"],
        },
        "corrected_703790_shadow_proposal": {
            "source": str(PROPOSAL_703790),
            "proposal_present": PROPOSAL_703790.exists(),
            "candidate_prefixes": ["H40"],
            "historical_review_rows": 212,
            "historical_shadow_candidate_rows": 146,
            "historical_full_release_counterfactual": 7,
            "runtime_decision_changed": False,
            "apply_ready": False,
        },
        "excluded_sources": excluded_sources,
        "safety": safety_payload(),
        "qdrant": {
            "production_write_allowed": False,
            "suggested_shadow_collection": SUGGESTED_SHADOW_COLLECTION,
            "payload_preview_only": True,
            "files": qdrant_preview["files"],
        },
        "copied_artifacts": copied_artifacts,
        "copied_scripts": copied_scripts,
        "structure_document": copied_structure_document,
        "readme": {
            "bundle_path": safe_relative(readme, bundle_root),
            "size_bytes": readme.stat().st_size,
            "sha256": sha256_file(readme),
        },
        "dgx_agent_prompt": {
            "bundle_path": safe_relative(dgx_agent_prompt, bundle_root),
            "size_bytes": dgx_agent_prompt.stat().st_size,
            "sha256": sha256_file(dgx_agent_prompt),
        },
    }
    manifest_path = bundle_root / "DGX_TRANSFER_MANIFEST.json"
    write_json(manifest_path, manifest)
    zip_info = zip_directory(bundle_root, zip_path)
    manifest["zip"] = zip_info
    write_json(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build DGX transfer bundle for review-reduction shadow work.")
    parser.add_argument("--bundle-root", type=Path, default=BUNDLE_ROOT)
    parser.add_argument("--zip-path", type=Path, default=ZIP_PATH)
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = build_bundle(bundle_root=args.bundle_root, zip_path=args.zip_path)
    if args.print_summary:
        printable = {
            "schema_version": manifest["schema_version"],
            "bundle_root": manifest["bundle_root"],
            "zip": manifest["zip"],
            "headline_result": manifest["headline_result"],
            "included": manifest["included"],
            "excluded_sources": manifest["excluded_sources"],
            "safety": manifest["safety"],
            "qdrant": {
                "production_write_allowed": manifest["qdrant"]["production_write_allowed"],
                "suggested_shadow_collection": manifest["qdrant"]["suggested_shadow_collection"],
                "payload_preview_only": manifest["qdrant"]["payload_preview_only"],
            },
        }
        print(json.dumps(printable, ensure_ascii=False, indent=2))
    else:
        print(f"DGX transfer bundle written: {args.bundle_root}")
        print(f"ZIP written: {args.zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
