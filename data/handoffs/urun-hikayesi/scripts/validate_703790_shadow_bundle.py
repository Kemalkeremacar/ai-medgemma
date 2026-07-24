from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
DEFAULT_BUNDLE = Path(__file__).resolve().parents[1]
DEFAULT_ZIP = None  # zip optional; opened bundle is DEFAULT_BUNDLE
DEFAULT_PROPOSAL_DIR = (
    ROOT
    / "SUT/generated/shadow_quality_gate/review_reduction_703790_shadow_policy_proposal_20260720"
)
RUNTIME_LOOKUP = ROOT / "SUT/generated/sut_diagnosis_rules/ek2b/runtime/sut_diagnosis_runtime_lookup.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def validate(bundle: Path, zip_path: Path | None, proposal_dir: Path) -> dict[str, Any]:
    errors: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    manifest_path = bundle / "DGX_TRANSFER_MANIFEST.json"
    check(bundle.is_dir(), f"Bundle directory missing: {bundle}")
    check(manifest_path.is_file(), f"Manifest missing: {manifest_path}")
    if zip_path is not None:
        check(zip_path.is_file(), f"Bundle ZIP missing: {zip_path}")
    if errors:
        return {"status": "FAIL", "errors": errors}

    manifest = load_json(manifest_path)
    required = {
        "BUNDLE_STRUCTURE.md",
        "scripts/build_703790_shadow_policy_proposal.py",
        "scripts/validate_703790_shadow_bundle.py",
        (
            "artifacts/review_reduction_703790_shadow_policy_proposal_20260720/"
            "703790_SHADOW_POLICY_PROPOSAL.json"
        ),
        (
            "artifacts/review_reduction_703790_shadow_policy_proposal_20260720/"
            "703790_SHADOW_MONITORING_PLAN.json"
        ),
        (
            "artifacts/review_reduction_703790_shadow_policy_proposal_20260720/"
            "703790_SHADOW_ROLLBACK_MANIFEST.json"
        ),
        (
            "artifacts/review_reduction_703790_shadow_policy_proposal_20260720/"
            "703790_GOVERNANCE_REVIEW.txt"
        ),
        (
            "artifacts/review_reduction_703790_shadow_policy_proposal_20260720/"
            "TASK_MANIFEST.json"
        ),
        "qdrant_shadow/QDRANT_SHADOW_PAYLOAD_PREVIEW.jsonl",
    }
    missing = sorted(rel for rel in required if not (bundle / rel).is_file())
    check(not missing, f"Missing bundle paths: {missing}")
    check(
        not list(bundle.rglob("rule_engine_results.csv")),
        "Case-level source rule_engine_results.csv is present in bundle",
    )

    if zip_path is not None and zip_path.is_file():
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            check(archive.testzip() is None, "ZIP CRC failure")
            check(
                not any(Path(name).name == "rule_engine_results.csv" for name in names),
                "Case-level source rule_engine_results.csv is present in ZIP",
            )
            check(required <= names, f"Missing ZIP paths: {sorted(required - names)}")

    qdrant_preview = bundle / "qdrant_shadow/QDRANT_SHADOW_PAYLOAD_PREVIEW.jsonl"
    qdrant_rows = [
        json.loads(line)
        for line in qdrant_preview.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    check(len(qdrant_rows) == 20, f"Qdrant record count is {len(qdrant_rows)}, expected 20")
    record_703790 = next(
        (
            row
            for row in qdrant_rows
            if row.get("payload", {}).get("code") == "703790"
        ),
        None,
    )
    check(record_703790 is not None, "703790 Qdrant record missing")
    if record_703790:
        payload = record_703790["payload"]
        expected_payload = {
            "candidate_diagnosis_prefixes": ["H40"],
            "candidate_shadow_pass_occurrence_rows": 146,
            "row_level_full_release_rows": 7,
            "row_level_partial_resolution_rows": 139,
            "shadow_overlay_enabled": False,
            "supersedes_broad_ai_prefix_preview": True,
            "runtime_decision_changed": False,
            "apply_ready": False,
            "human_admin_approval_present": False,
        }
        for key, expected in expected_payload.items():
            check(
                payload.get(key) == expected,
                f"703790 payload {key}={payload.get(key)!r}, expected {expected!r}",
            )
        broad = payload.get("original_broad_counterfactual") or {}
        check(
            broad.get("matched_candidate_rows") == 199,
            "Broad counterfactual matched rows mismatch",
        )
        check(
            broad.get("row_level_full_release_counterfactual") == 16,
            "Broad counterfactual full-release rows mismatch",
        )
        check(
            broad.get("row_level_partial_resolution_counterfactual") == 183,
            "Broad counterfactual partial-resolution rows mismatch",
        )

    hash_entries = list(manifest.get("copied_artifacts") or []) + list(
        manifest.get("copied_scripts") or []
    )
    for key in ("structure_document", "readme", "dgx_agent_prompt"):
        if manifest.get(key):
            hash_entries.append(manifest[key])
    hash_entries.extend(manifest.get("qdrant", {}).get("files") or [])
    for entry in hash_entries:
        path = bundle / entry["bundle_path"]
        check(path.exists(), f"Manifest file missing: {entry['bundle_path']}")
        if path.exists():
            check(
                sha256_file(path) == entry["sha256"],
                f"Manifest hash mismatch: {entry['bundle_path']}",
            )

    check(
        sha256_file(zip_path) == manifest.get("zip", {}).get("sha256"),
        "ZIP SHA256 does not match manifest",
    )
    proposal_files = [path for path in proposal_dir.iterdir() if path.is_file()]
    check(len(proposal_files) == 10, "Proposal package must contain exactly 10 files")
    check(
        not any("case_id" in path.name.lower() for path in proposal_files),
        "Proposal package has a case_id-named file",
    )
    proposal = load_json(proposal_dir / "703790_SHADOW_POLICY_PROPOSAL.json")
    expected_safety = {
        "human_admin_approval_present": False,
        "apply_ready": False,
        "auto_apply": False,
        "runtime_decision_changed": False,
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "exports_case_level_rows": False,
        "exports_case_ids": False,
        "shadow_only": True,
    }
    for key, expected in expected_safety.items():
        check(
            proposal["safety"].get(key) == expected,
            f"Proposal safety {key} mismatch",
        )
    check(
        proposal["shadow_overlay_preview"].get("enabled") is False,
        "Shadow overlay is unexpectedly enabled",
    )

    runtime = load_json(RUNTIME_LOOKUP)["rules_by_sut_code"]["703790"]
    expected_runtime = {
        "diagnosis_policy": "review_required",
        "decision_if_missing": "REVIEW_REQUIRED",
        "runtime_decision_mode": "manual_review",
        "review_required": True,
    }
    for key, expected in expected_runtime.items():
        check(runtime.get(key) == expected, f"Runtime {key} changed")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "zip_file_count": len(names),
        "zip_size_bytes": zip_path.stat().st_size,
        "zip_sha256": sha256_file(zip_path),
        "manifest_hash_entries_verified": len(hash_entries),
        "proposal_file_count": len(proposal_files),
        "qdrant_record_count": len(qdrant_rows),
        "qdrant_703790": {
            "candidate_diagnosis_prefixes": (
                record_703790 or {}
            ).get("payload", {}).get("candidate_diagnosis_prefixes"),
            "candidate_shadow_pass_occurrence_rows": (
                record_703790 or {}
            ).get("payload", {}).get("candidate_shadow_pass_occurrence_rows"),
            "row_level_full_release_rows": (
                record_703790 or {}
            ).get("payload", {}).get("row_level_full_release_rows"),
            "row_level_partial_resolution_rows": (
                record_703790 or {}
            ).get("payload", {}).get("row_level_partial_resolution_rows"),
        },
        "runtime_703790": {
            key: runtime.get(key)
            for key in expected_runtime
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the 703790 shadow proposal and DGX bundle.")
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--zip", dest="zip_path", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--proposal-dir", type=Path, default=DEFAULT_PROPOSAL_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = validate(args.bundle, args.zip_path, args.proposal_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
