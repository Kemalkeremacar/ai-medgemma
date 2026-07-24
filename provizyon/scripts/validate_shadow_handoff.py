#!/usr/bin/env python3
"""Portable validation for the review-reduction DGX handoff bundle (SG-1 light).

Does not require Windows SUT paths or runtime lookup.
Does not write to Qdrant/DB or apply live rules.

Usage:
  python provizyon/scripts/validate_shadow_handoff.py
  python provizyon/scripts/validate_shadow_handoff.py --bundle /path/to/bundle
  python provizyon/scripts/validate_shadow_handoff.py --schema-check \\
      --expert-decision provizyon/config/expert_decision.703790.example.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROVIZYON_ROOT = SCRIPT_DIR.parent
GEMMA_ROOT = PROVIZYON_ROOT.parent
DEFAULT_BUNDLE = (
    GEMMA_ROOT / "data" / "handoffs" / "review_reduction_dgx_transfer_bundle_20260709"
)
CONFIG_DIR = PROVIZYON_ROOT / "config"

PROPOSAL_REL = (
    "artifacts/review_reduction_703790_shadow_policy_proposal_20260720/"
    "703790_SHADOW_POLICY_PROPOSAL.json"
)
MONITORING_REL = (
    "artifacts/review_reduction_703790_shadow_policy_proposal_20260720/"
    "703790_SHADOW_MONITORING_PLAN.json"
)
ROLLBACK_REL = (
    "artifacts/review_reduction_703790_shadow_policy_proposal_20260720/"
    "703790_SHADOW_ROLLBACK_MANIFEST.json"
)
REGISTER_REL = (
    "artifacts/review_reduction_final_handoff_20260709/final_decision_register.csv"
)
MANIFEST_REL = "DGX_TRANSFER_MANIFEST.json"
QDRANT_PREVIEW_REL = "qdrant_shadow/QDRANT_SHADOW_PAYLOAD_PREVIEW.jsonl"
QDRANT_CONTRACT_REL = "qdrant_shadow/QDRANT_SHADOW_COLLECTION_CONTRACT.json"

REQUIRED_FILES = (
    MANIFEST_REL,
    "DGX_TRANSFER_README.txt",
    "BUNDLE_STRUCTURE.md",
    PROPOSAL_REL,
    MONITORING_REL,
    ROLLBACK_REL,
    REGISTER_REL,
    QDRANT_PREVIEW_REL,
    QDRANT_CONTRACT_REL,
    "qdrant_shadow/DO_NOT_WRITE_TO_PRODUCTION_QDRANT.txt",
)

FALSE_FLAGS = (
    "human_admin_approval_present",
    "apply_ready",
    "auto_apply",
    "live_runtime_override",
    "runtime_decision_changed",
    "writes_to_production_db",
    "writes_to_qdrant",
    "exports_case_level_rows",
    "exports_case_ids",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def check_false_flags(obj: dict[str, Any], label: str, errors: list[str]) -> None:
    for flag in FALSE_FLAGS:
        if flag in obj and obj[flag] is True:
            errors.append(f"{label}: {flag}=true (expected false)")


def validate_bundle(bundle: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if not bundle.is_dir():
        return {
            "status": "FAIL",
            "bundle": str(bundle),
            "errors": [f"Bundle directory missing: {bundle}"],
            "warnings": [],
        }

    for rel in REQUIRED_FILES:
        if not (bundle / rel).is_file():
            errors.append(f"Missing required file: {rel}")

    hits = list(bundle.rglob("rule_engine_results.csv"))
    if hits:
        errors.append(
            "rule_engine_results.csv must be absent; found: "
            + ", ".join(str(p.relative_to(bundle)) for p in hits)
        )

    manifest: dict[str, Any] = {}
    proposal: dict[str, Any] = {}
    monitoring: dict[str, Any] = {}

    manifest_path = bundle / MANIFEST_REL
    if manifest_path.is_file():
        manifest = load_json(manifest_path)
        check_false_flags(manifest.get("safety") or {}, "manifest.safety", errors)
        if (manifest.get("qdrant") or {}).get("production_write_allowed") is True:
            errors.append("manifest.qdrant.production_write_allowed=true")

    proposal_path = bundle / PROPOSAL_REL
    if proposal_path.is_file():
        proposal = load_json(proposal_path)
        check_false_flags(proposal.get("safety") or {}, "proposal.safety", errors)
        overlay = proposal.get("shadow_overlay_preview") or {}
        if overlay.get("enabled") is True:
            errors.append("proposal.shadow_overlay_preview.enabled=true (expected false)")
        prefixes = overlay.get("candidate_diagnosis_prefixes") or []
        if prefixes != ["H40"]:
            errors.append(
                f"proposal candidate prefixes expected ['H40'], got {prefixes!r}"
            )
        runtime = proposal.get("current_runtime_rule") or {}
        if runtime.get("decision_if_missing") not in (None, "REVIEW_REQUIRED"):
            if runtime.get("decision_if_missing") != "REVIEW_REQUIRED":
                errors.append(
                    "runtime decision_if_missing expected REVIEW_REQUIRED, got "
                    f"{runtime.get('decision_if_missing')!r}"
                )
        if runtime.get("diagnosis_policy") not in (None, "review_required"):
            errors.append(
                f"runtime diagnosis_policy expected review_required, got "
                f"{runtime.get('diagnosis_policy')!r}"
            )
        if runtime.get("runtime_decision_mode") not in (None, "manual_review"):
            errors.append(
                f"runtime_decision_mode expected manual_review, got "
                f"{runtime.get('runtime_decision_mode')!r}"
            )

    monitoring_path = bundle / MONITORING_REL
    if monitoring_path.is_file():
        monitoring = load_json(monitoring_path)
        check_false_flags(monitoring.get("safety") or {}, "monitoring.safety", errors)
        if monitoring.get("current_status") not in (
            None,
            "not_approved_shadow_only",
        ):
            warnings.append(
                f"monitoring.current_status={monitoring.get('current_status')!r}"
            )

    # Preview payloads: no case_id, safety flags false
    preview_path = bundle / QDRANT_PREVIEW_REL
    if preview_path.is_file():
        for i, line in enumerate(preview_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            obj = json.loads(line)
            payload = obj.get("payload") or {}
            if "case_id" in payload or "case_ids" in payload:
                errors.append(f"qdrant preview line {i}: case_id field present")
            for flag in FALSE_FLAGS:
                if payload.get(flag) is True:
                    errors.append(f"qdrant preview line {i}: {flag}=true")
            if payload.get("shadow_only") is False:
                errors.append(f"qdrant preview line {i}: shadow_only=false")

    status = "PASS" if not errors else "FAIL"
    return {
        "status": status,
        "bundle": str(bundle.resolve()),
        "manifest_schema_version": manifest.get("schema_version"),
        "recommended_shadow_pilot_code": (manifest.get("headline_result") or {}).get(
            "recommended_shadow_pilot_code"
        ),
        "proposal_id": proposal.get("proposal_id"),
        "overlay_enabled": (proposal.get("shadow_overlay_preview") or {}).get("enabled"),
        "apply_ready": False,
        "human_admin_approval_present": False,
        "production_write_authorized": False,
        "errors": errors,
        "warnings": warnings,
        "not_apply_ready": True,
        "shadow_only": True,
    }


def schema_check(path: Path, schema_path: Path, errors: list[str]) -> None:
    try:
        import jsonschema  # type: ignore
    except ImportError:
        errors.append(
            "jsonschema paketi yok; --schema-check için: pip install jsonschema"
        )
        return
    instance = load_json(path)
    schema = load_json(schema_path)
    try:
        jsonschema.validate(instance, schema)
    except jsonschema.ValidationError as exc:
        errors.append(f"schema {schema_path.name}: {exc.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate review-reduction shadow handoff bundle (portable)."
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        help="Bundle directory (default: GemmaApp/data/handoffs/... or env)",
    )
    parser.add_argument(
        "--schema-check",
        action="store_true",
        help="Also validate optional expert_decision / rule_draft JSON files",
    )
    parser.add_argument(
        "--expert-decision",
        type=Path,
        default=None,
        help="Path to expert_decision JSON (with --schema-check)",
    )
    parser.add_argument(
        "--rule-draft",
        type=Path,
        default=None,
        help="Path to rule_draft JSON (with --schema-check)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    args = parser.parse_args(argv)

    bundle = args.bundle
    if bundle is None:
        env = os.environ.get("PROVIZYON_SHADOW_HANDOFF_ROOT", "").strip()
        bundle = Path(env) if env else DEFAULT_BUNDLE

    result = validate_bundle(bundle)

    schema_errors: list[str] = []
    if args.schema_check:
        if args.expert_decision:
            schema_check(
                args.expert_decision,
                CONFIG_DIR / "expert_decision.schema.json",
                schema_errors,
            )
        if args.rule_draft:
            schema_check(
                args.rule_draft,
                CONFIG_DIR / "rule_draft.schema.json",
                schema_errors,
            )
        if not args.expert_decision and not args.rule_draft:
            # Default: check the 703790 example form
            example = CONFIG_DIR / "expert_decision.703790.example.json"
            if example.is_file():
                schema_check(example, CONFIG_DIR / "expert_decision.schema.json", schema_errors)
            else:
                schema_errors.append(f"Default example missing: {example}")
        if schema_errors:
            result["errors"] = list(result.get("errors") or []) + schema_errors
            result["status"] = "FAIL"
        result["schema_check"] = True

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"status: {result['status']}")
        print(f"bundle: {result['bundle']}")
        print(f"recommended_shadow_pilot_code: {result.get('recommended_shadow_pilot_code')}")
        print(f"proposal_id: {result.get('proposal_id')}")
        print(f"overlay_enabled: {result.get('overlay_enabled')}")
        print("apply_ready: false")
        print("production_write_authorized: false")
        for w in result.get("warnings") or []:
            print(f"WARNING: {w}")
        for e in result.get("errors") or []:
            print(f"ERROR: {e}")
        if result["status"] == "PASS":
            print("Bundle SAFE for DGX-side shadow analysis / visibility. NOT apply-ready.")

    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
