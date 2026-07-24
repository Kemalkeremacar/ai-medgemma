"""Review-reduction DGX handoff bundle — read-only aggregate loader.

Reads curated shadow artifacts under GemmaApp/data/handoffs/...
Does not write to Qdrant, DB, or live rule runtime.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any

from . import settings

DEFAULT_BUNDLE_NAME = "review_reduction_dgx_transfer_bundle_20260709"
PROPOSAL_DIR_REL = (
    "artifacts/review_reduction_703790_shadow_policy_proposal_20260720"
)
REGISTER_CSV_REL = (
    "artifacts/review_reduction_final_handoff_20260709/final_decision_register.csv"
)
FINAL_SUMMARY_REL = (
    "artifacts/review_reduction_final_handoff_20260709/FINAL_EXECUTIVE_SUMMARY.txt"
)
POLICY_PACK_MANIFEST_REL = (
    "artifacts/review_reduction_policy_pack_20260709/TASK_MANIFEST.json"
)
MEDGEMMA_TOP50_MANIFEST_REL = (
    "artifacts/review_reduction_medgemma_shadow_merge_top50_combined_20260709/"
    "TASK_MANIFEST.json"
)
AI_PROVISIONAL_MANIFEST_REL = (
    "artifacts/review_reduction_ai_provisional_expert_review_20260709/TASK_MANIFEST.json"
)
MANIFEST_REL = "DGX_TRANSFER_MANIFEST.json"
PROPOSAL_JSON_REL = f"{PROPOSAL_DIR_REL}/703790_SHADOW_POLICY_PROPOSAL.json"
PREFIX_CSV_REL = f"{PROPOSAL_DIR_REL}/703790_prefix_decision_register.csv"
MONITORING_JSON_REL = f"{PROPOSAL_DIR_REL}/703790_SHADOW_MONITORING_PLAN.json"
ROLLBACK_JSON_REL = f"{PROPOSAL_DIR_REL}/703790_SHADOW_ROLLBACK_MANIFEST.json"
INTEGRITY_JSON_REL = "DGX_BUNDLE_INTEGRITY_CHECK.json"

REQUIRED_FALSE_FLAGS = (
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


class ShadowHandoffError(Exception):
    """Bundle missing or unreadable."""

    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def resolve_bundle_root() -> Path:
    """Resolve handoff bundle root (env override or default under GemmaApp)."""
    override = os.environ.get("PROVIZYON_SHADOW_HANDOFF_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (settings.GEMMA_ROOT / "data" / "handoffs" / DEFAULT_BUNDLE_NAME).resolve()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_csv_dicts(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _require_file(root: Path, rel: str) -> Path:
    path = root / rel
    if not path.is_file():
        raise ShadowHandoffError(
            f"Handoff dosyası bulunamadı: {rel} (kök: {root})",
            status_code=404,
        )
    return path


def ensure_bundle() -> Path:
    root = resolve_bundle_root()
    if not root.is_dir():
        raise ShadowHandoffError(
            f"Shadow handoff bundle yok: {root}. "
            "Zip'i data/handoffs/ altına açın veya "
            "PROVIZYON_SHADOW_HANDOFF_ROOT ayarlayın.",
            status_code=503,
        )
    return root


def _corrected_h40(proposal: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    core = proposal.get("historical_core_scenario") or {}
    corrected = manifest.get("corrected_703790_shadow_proposal") or {}
    return {
        "sut_code": proposal.get("sut_code", "703790"),
        "procedure_name": proposal.get("procedure_name"),
        "candidate_prefixes": list(
            (proposal.get("shadow_overlay_preview") or {}).get(
                "candidate_diagnosis_prefixes"
            )
            or corrected.get("candidate_prefixes")
            or ["H40"]
        ),
        "historical_review_rows": int(
            core.get("historical_703790_review_rows")
            or corrected.get("historical_review_rows")
            or 0
        ),
        "h40_matched_rows": int(
            core.get("matched_candidate_rows")
            or corrected.get("historical_shadow_candidate_rows")
            or 0
        ),
        "matched_share": core.get("matched_share"),
        "full_release_counterfactual": int(
            core.get("row_level_full_release_counterfactual")
            or corrected.get("historical_full_release_counterfactual")
            or 0
        ),
        "partial_resolution_counterfactual": int(
            core.get("row_level_partial_resolution_counterfactual") or 0
        ),
        "unmatched_rows": int(core.get("unmatched_rows") or 0),
        "overlay_enabled": bool(
            (proposal.get("shadow_overlay_preview") or {}).get("enabled")
        ),
        "runtime_decision_mode": (proposal.get("current_runtime_rule") or {}).get(
            "runtime_decision_mode"
        ),
        "diagnosis_policy": (proposal.get("current_runtime_rule") or {}).get(
            "diagnosis_policy"
        ),
        "decision_if_missing": (proposal.get("current_runtime_rule") or {}).get(
            "decision_if_missing"
        ),
        "apply_ready": False,
        "note": "Düzeltilmiş H40-only cohort; canlı REVIEW_REQUIRED değişmedi.",
    }


def _broad_counterfactual_from_register(rows: list[dict[str, str]]) -> dict[str, Any]:
    """Older AI broad counts from final register (superseded for 703790)."""
    for row in rows:
        if (row.get("code") or "").strip() == "703790":
            return {
                "superseded": True,
                "label": "original_broad_ai_prefix_preview_counterfactual",
                "candidate_shadow_pass_occurrence_rows": _int_or_none(
                    row.get("candidate_shadow_pass_occurrence_rows")
                ),
                "row_level_full_release_rows": _int_or_none(
                    row.get("row_level_full_release_rows")
                ),
                "row_level_partial_resolution_rows": _int_or_none(
                    row.get("row_level_partial_resolution_rows")
                ),
                "note": (
                    "Eski geniş AI prefix sonucu; governance için önerilmez. "
                    "Düzeltilmiş H40 cohort kullanılır."
                ),
            }
    return {"superseded": True, "available": False}


def _int_or_none(raw: str | None) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def _normalize_register_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        "decision_group": (row.get("decision_group") or "").strip(),
        "code": (row.get("code") or "").strip(),
        "clinical_theme": (row.get("clinical_theme") or "").strip(),
        "recommended_disposition": (row.get("recommended_disposition") or "").strip(),
        "rationale": (row.get("rationale") or "").strip(),
        "review_rows": _int_or_none(row.get("review_rows")),
        "ai_expert_decision": (row.get("ai_expert_decision") or "").strip(),
        "confidence": (row.get("confidence") or "").strip(),
        "confidence_score": _float_or_none(row.get("confidence_score")),
        "candidate_shadow_pass_occurrence_rows": _int_or_none(
            row.get("candidate_shadow_pass_occurrence_rows")
        ),
        "row_level_full_release_rows": _int_or_none(
            row.get("row_level_full_release_rows")
        ),
        "row_level_partial_resolution_rows": _int_or_none(
            row.get("row_level_partial_resolution_rows")
        ),
        "human_admin_approval_present": _boolish(
            row.get("human_admin_approval_present"), default=False
        ),
        "apply_ready": _boolish(row.get("apply_ready"), default=False),
        "auto_apply": _boolish(row.get("auto_apply"), default=False),
    }


def _float_or_none(raw: str | None) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _boolish(raw: str | None, *, default: bool = False) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def load_decision_register() -> dict[str, Any]:
    root = ensure_bundle()
    rows_raw = _load_csv_dicts(_require_file(root, REGISTER_CSV_REL))
    rows = [_normalize_register_row(r) for r in rows_raw]
    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_group.setdefault(row["decision_group"] or "unknown", []).append(row)
    counts = {g: len(items) for g, items in by_group.items()}
    return {
        "bundle_root": str(root),
        "source": REGISTER_CSV_REL,
        "row_count": len(rows),
        "by_decision_group_counts": counts,
        "by_decision_group": by_group,
        "rows": rows,
        "apply_ready": False,
        "human_admin_approval_present": False,
        "shadow_only": True,
    }


def load_703790_detail() -> dict[str, Any]:
    root = ensure_bundle()
    proposal = _load_json(_require_file(root, PROPOSAL_JSON_REL))
    monitoring = _load_json(_require_file(root, MONITORING_JSON_REL))
    rollback = _load_json(_require_file(root, ROLLBACK_JSON_REL))
    prefix_rows = [
        {
            "diagnosis_prefix": (r.get("diagnosis_prefix") or "").strip(),
            "historical_703790_review_rows_with_prefix": _int_or_none(
                r.get("historical_703790_review_rows_with_prefix")
            ),
            "share_of_703790_review_rows": _float_or_none(
                r.get("share_of_703790_review_rows")
            ),
            "proposal_class": (r.get("proposal_class") or "").strip(),
            "proposal_reason": (r.get("proposal_reason") or "").strip(),
        }
        for r in _load_csv_dicts(_require_file(root, PREFIX_CSV_REL))
    ]
    manifest = _load_json(_require_file(root, MANIFEST_REL))
    register = load_decision_register()
    overlay = proposal.get("shadow_overlay_preview") or {}
    return {
        "bundle_root": str(root),
        "proposal_id": proposal.get("proposal_id"),
        "proposal_mode": proposal.get("proposal_mode"),
        "corrected_h40": _corrected_h40(proposal, manifest),
        "broad_ai_counterfactual": _broad_counterfactual_from_register(register["rows"]),
        "clinical_quality_correction": proposal.get("clinical_quality_correction"),
        "current_runtime_rule": proposal.get("current_runtime_rule"),
        "shadow_overlay_preview": {
            "enabled": bool(overlay.get("enabled")),
            "candidate_diagnosis_prefixes": overlay.get("candidate_diagnosis_prefixes"),
            "shadow_event_on_match": overlay.get("shadow_event_on_match"),
            "actual_runtime_decision_on_match": overlay.get(
                "actual_runtime_decision_on_match"
            ),
            "fallback_actual_runtime_decision": overlay.get(
                "fallback_actual_runtime_decision"
            ),
            "human_admin_approval_present": False,
            "apply_ready": False,
        },
        "prefix_decision_register": prefix_rows,
        "monitoring": {
            "mode": monitoring.get("mode"),
            "current_status": monitoring.get("current_status"),
            "minimum_observation_window": monitoring.get("minimum_observation_window"),
            "metrics": monitoring.get("metrics"),
            "promotion_gates": monitoring.get("promotion_gates"),
            "baseline": monitoring.get("baseline"),
            "safety": monitoring.get("safety"),
        },
        "rollback": {
            "rollback_type": rollback.get("rollback_type"),
            "live_runtime_changed": rollback.get("live_runtime_changed"),
            "production_rollback_required": rollback.get("production_rollback_required"),
            "rollback_triggers": rollback.get("rollback_triggers"),
            "expected_post_rollback_state": rollback.get("expected_post_rollback_state"),
            "safety": rollback.get("safety"),
        },
        "safety": proposal.get("safety") or {},
        "required_next_gate": proposal.get("required_next_gate"),
        "not_apply_ready": True,
        "shadow_only": True,
    }


def _optional_json(root: Path, rel: str) -> dict[str, Any] | None:
    path = root / rel
    if not path.is_file():
        return None
    data = _load_json(path)
    return data if isinstance(data, dict) else None


def _parse_final_summary_counts(root: Path) -> dict[str, int]:
    """Best-effort parse of FINAL_EXECUTIVE_SUMMARY.txt key metrics."""
    path = root / FINAL_SUMMARY_REL
    out: dict[str, int] = {}
    if not path.is_file():
        return out
    key_map = {
        "Historical rows": "historical_rows",
        "Baseline REVIEW rows": "baseline_review_rows",
        "Initial top50 policy candidates": "top50_policy_candidates",
        "MedGemma top50 fast-track to expert": "medgemma_fast_track",
        "Mapping backlog before policy": "mapping_backlog",
        "Manual-review/invalid hold": "manual_or_invalid_hold",
        "AI-only shadow candidates after guardrails": "ai_shadow_candidates",
        "Shadow preview candidate-code occurrences": "shadow_preview_occurrences",
        "Shadow preview full REVIEW-row release candidates": "shadow_preview_full_release",
        "Shadow preview partial-resolution rows still REVIEW": "shadow_preview_partial",
    }
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if ":" not in line:
            continue
        label, _, raw = line.partition(":")
        label = label.strip()
        key = key_map.get(label)
        if not key:
            continue
        try:
            out[key] = int(str(raw).strip().replace(",", ""))
        except ValueError:
            continue
    return out


def load_funnel() -> dict[str, Any]:
    """Aggregate review-reduction funnel for Ürün Hikâyesi (read-only)."""
    root = ensure_bundle()
    manifest = _load_json(_require_file(root, MANIFEST_REL))
    proposal = _load_json(_require_file(root, PROPOSAL_JSON_REL))
    register = load_decision_register()
    summary_counts = _parse_final_summary_counts(root)
    policy = _optional_json(root, POLICY_PACK_MANIFEST_REL) or {}
    top50 = _optional_json(root, MEDGEMMA_TOP50_MANIFEST_REL) or {}
    ai_prov = _optional_json(root, AI_PROVISIONAL_MANIFEST_REL) or {}

    policy_ctx = policy.get("source_context") or {}
    policy_counts = policy.get("counts") or {}
    top50_counts = top50.get("counts") or {}
    top50_triage = top50_counts.get("by_merged_triage_category") or {}
    ai_counts = ai_prov.get("counts") or {}
    headline = manifest.get("headline_result") or {}
    h40 = _corrected_h40(proposal, manifest)
    group_counts = register["by_decision_group_counts"]

    historical = int(
        policy_ctx.get("total_provision_rows")
        or summary_counts.get("historical_rows")
        or 0
    )
    baseline_review = int(
        policy_ctx.get("total_review_rows")
        or summary_counts.get("baseline_review_rows")
        or 0
    )
    top50_n = int(
        policy_counts.get("candidates")
        or top50_counts.get("total_rows")
        or summary_counts.get("top50_policy_candidates")
        or 50
    )
    fast_track = int(
        top50_triage.get("fast_track_to_human_expert_review")
        or summary_counts.get("medgemma_fast_track")
        or 0
    )
    mapping_backlog = int(
        top50_triage.get("mapping_backlog_before_policy")
        or summary_counts.get("mapping_backlog")
        or 0
    )
    manual_hold = int(
        (
            int(top50_triage.get("keep_manual_review_observation") or 0)
            + int(top50_triage.get("response_invalid_manual_review_required") or 0)
        )
        or summary_counts.get("manual_or_invalid_hold")
        or 0
    )
    ai_requests = int(ai_counts.get("requests") or 0)
    ai_valid = int(ai_counts.get("valid_responses") or 0)
    ai_shadow = int(
        ai_counts.get("shadow_candidate_rows")
        or summary_counts.get("ai_shadow_candidates")
        or group_counts.get("recommended_shadow_pilot")
        or 0
    )

    steps = [
        {
            "id": "historical_scan",
            "label": "Geçmiş provizyon tarandı",
            "value": historical,
            "note": "Deterministik / aggregate analiz — tamamı Qdrant veya MedGemma'ya gitmedi.",
        },
        {
            "id": "baseline_review",
            "label": "Temel REVIEW satırı",
            "value": baseline_review,
            "note": "İnceleme kuyruğu yoğunluğu; azaltma fırsatı buradan arandı.",
        },
        {
            "id": "top50_candidates",
            "label": "Öncelikli politika adayı",
            "value": top50_n,
            "note": "Milyonlarca satır yerine deterministik katmanla küçültülmüş Top-50.",
        },
        {
            "id": "medgemma_triage",
            "label": "MedGemma Top-50 triyaj",
            "value": top50_n,
            "note": (
                f"Uzman hızlı iz: {fast_track} · eşleştirme bekleyen: {mapping_backlog} · "
                f"manuel/geçersiz: {manual_hold}."
            ),
            "detail": {
                "fast_track_to_expert": fast_track,
                "mapping_backlog": mapping_backlog,
                "manual_or_invalid_hold": manual_hold,
            },
        },
        {
            "id": "ai_provisional",
            "label": "AI geçici uzman incelemesi",
            "value": ai_shadow,
            "note": (
                f"{ai_requests} istek, {ai_valid} geçerli sözleşme; "
                f"guardrail sonrası {ai_shadow} gölge aday. İnsan onayı değildir."
            ),
            "detail": {
                "requests": ai_requests,
                "valid_responses": ai_valid,
                "shadow_candidates": ai_shadow,
            },
        },
        {
            "id": "pilot_703790_h40",
            "label": "Pilot 703790 · yalnız H40",
            "value": int(h40.get("h40_matched_rows") or 0),
            "note": (
                f"{h40.get('historical_review_rows') or 0} geçmiş 703790 REVIEW → "
                f"{h40.get('h40_matched_rows') or 0} H40 eşleşmesi; "
                "canlı karar değişmedi (manuel inceleme gerekli)."
            ),
            "detail": {
                "historical_review_rows": h40.get("historical_review_rows"),
                "h40_matched_rows": h40.get("h40_matched_rows"),
                "full_release_counterfactual": h40.get("full_release_counterfactual"),
                "partial_resolution_counterfactual": h40.get(
                    "partial_resolution_counterfactual"
                ),
                "unmatched_rows": h40.get("unmatched_rows"),
            },
        },
    ]

    return {
        "title": "İnceleme azaltma hunisi",
        "subtitle": (
            "2,36M satır tarandı; LLM'e yalnızca küçültülmüş adaylar gitti. "
            "Canlı kural motoru değişmedi."
        ),
        "clarification": (
            "Bu sayılar geçmiş aggregate çalışmadan gelir. "
            "Tamamı vektörleştirilmedi ve tamamı MedGemma'ya gönderilmedi."
        ),
        "steps": steps,
        "headline_preview": {
            "recommended_shadow_pilot_code": headline.get(
                "recommended_shadow_pilot_code"
            ),
            "candidate_shadow_pass_occurrence_rows": headline.get(
                "candidate_shadow_pass_occurrence_rows"
            ),
            "row_level_full_release_rows": headline.get("row_level_full_release_rows"),
            "row_level_partial_resolution_rows": headline.get(
                "row_level_partial_resolution_rows"
            ),
            "note": (
                "Geniş AI prefix önizleme toplamı; 703790 için düzeltilmiş H40 kohortu "
                "yönetim kararına esas alınır."
            ),
        },
        "decision_register_counts": group_counts,
        "not_all_in_qdrant": True,
        "not_all_sent_to_medgemma": True,
        "apply_ready": False,
        "shadow_only": True,
    }


def load_summary() -> dict[str, Any]:
    root = ensure_bundle()
    manifest = _load_json(_require_file(root, MANIFEST_REL))
    proposal = _load_json(_require_file(root, PROPOSAL_JSON_REL))
    register = load_decision_register()
    funnel = load_funnel()
    integrity: dict[str, Any] | None = None
    integrity_path = root / INTEGRITY_JSON_REL
    if integrity_path.is_file():
        integrity = _load_json(integrity_path)

    safety = dict(manifest.get("safety") or {})
    # Force expected semantics for API consumers
    for flag in REQUIRED_FALSE_FLAGS:
        if flag in safety:
            safety[flag] = False
    safety.setdefault("shadow_only", True)
    safety["apply_ready"] = False
    safety["human_admin_approval_present"] = False

    return {
        "bundle_root": str(root),
        "bundle_name": DEFAULT_BUNDLE_NAME,
        "manifest_schema_version": manifest.get("schema_version"),
        "manifest_generated_at": manifest.get("generated_at"),
        "purpose": manifest.get("purpose"),
        "headline_result": manifest.get("headline_result"),
        "funnel": funnel,
        "corrected_h40": _corrected_h40(proposal, manifest),
        "broad_ai_counterfactual": _broad_counterfactual_from_register(register["rows"]),
        "decision_register_counts": register["by_decision_group_counts"],
        "safety": safety,
        "qdrant": {
            "production_write_allowed": False,
            "payload_preview_only": True,
            "suggested_shadow_collection": (manifest.get("qdrant") or {}).get(
                "suggested_shadow_collection"
            ),
            "upsert_performed": False,
        },
        "integrity_check_present": integrity is not None,
        "integrity_overall_pass_for_shadow_analysis": (
            None
            if integrity is None
            else integrity.get("overall_pass_for_shadow_analysis")
        ),
        "validate_command": (
            "python provizyon/scripts/validate_shadow_handoff.py"
        ),
        "not_apply_ready": True,
        "not_human_admin_approval": True,
        "shadow_only": True,
        "badges": [
            "Gölge gözlem kapalı",
            "Yayına hazır değil",
            "İnsan onayı yok",
        ],
    }


def _norm_code(raw: str | None) -> str:
    return (raw or "").strip().upper().replace(" ", "")


def _diag_matches_prefix(diagnosis: str, prefix: str) -> bool:
    """ICD önek eşleşmesi: H40 → H40, H40.1, H401."""
    d = _norm_code(diagnosis).replace(".", "")
    p = _norm_code(prefix).replace(".", "")
    if not d or not p:
        return False
    return d.startswith(p)


def _matched_diagnosis_prefixes(
    diagnoses: list[str], prefixes: list[str]
) -> list[str]:
    matched: list[str] = []
    seen: set[str] = set()
    for diag in diagnoses:
        for prefix in prefixes:
            if _diag_matches_prefix(diag, prefix):
                key = _norm_code(diag)
                if key and key not in seen:
                    seen.add(key)
                    matched.append(diag.strip())
                break
    return matched


def evaluate_shadow_advice(
    *,
    sut_codes: list[str] | None = None,
    huv_codes: list[str] | None = None,
    diagnoses: list[str] | None = None,
    nihai_karar: str | None = None,
) -> dict[str, Any]:
    """Read-only shadow advice for a job (does not change live decision).

    Pilot: 703790 + H40* from the corrected handoff proposal.
    """
    sut = [_norm_code(c) for c in (sut_codes or []) if _norm_code(c)]
    huv = [_norm_code(c) for c in (huv_codes or []) if _norm_code(c)]
    diags = [str(d).strip() for d in (diagnoses or []) if str(d).strip()]
    all_proc = set(sut) | set(huv)

    base: dict[str, Any] = {
        "mode": "read_only_overlay",
        "proposal_id": None,
        "sut_code": None,
        "candidate_prefixes": [],
        "matched_procedure": False,
        "matched_diagnosis_prefixes": [],
        "status": "not_applicable",
        "label": "Gölge aday değil",
        "message": "Bu iş, açık gölge pilot kapsamında değil.",
        "live_decision_unchanged": True,
        "apply_ready": False,
        "human_admin_approval_present": False,
        "auto_apply": False,
        "shadow_only": True,
        "nihai_karar": nihai_karar,
    }

    try:
        root = ensure_bundle()
        proposal = _load_json(_require_file(root, PROPOSAL_JSON_REL))
        manifest = _load_json(_require_file(root, MANIFEST_REL))
    except ShadowHandoffError as exc:
        base["status"] = "bundle_unavailable"
        base["label"] = "Gölge paket yok"
        base["message"] = str(exc)
        return base

    h40 = _corrected_h40(proposal, manifest)
    sut_code = _norm_code(str(h40.get("sut_code") or proposal.get("sut_code") or "703790"))
    prefixes = [str(p) for p in (h40.get("candidate_prefixes") or ["H40"])]
    overlay = proposal.get("shadow_overlay_preview") or {}

    base.update(
        {
            "proposal_id": proposal.get("proposal_id"),
            "sut_code": sut_code,
            "candidate_prefixes": prefixes,
            "procedure_name": h40.get("procedure_name") or proposal.get("procedure_name"),
            "overlay_enabled_in_proposal": bool(overlay.get("enabled")),
            "historical_review_rows": h40.get("historical_review_rows"),
            "h40_matched_rows": h40.get("h40_matched_rows"),
        }
    )

    if sut_code not in all_proc:
        return base

    base["matched_procedure"] = True
    matched_diags = _matched_diagnosis_prefixes(diags, prefixes)
    base["matched_diagnosis_prefixes"] = matched_diags

    if matched_diags:
        base["status"] = "matched_candidate"
        base["label"] = f"Gölge aday · {sut_code} + {'/'.join(prefixes)}*"
        base["message"] = (
            f"{sut_code} işlemi ve {', '.join(matched_diags)} tanı(ları) düzeltilmiş "
            f"gölge kohortuna ({'/'.join(prefixes)}*) uyuyor. Bu yalnızca tavsiye/"
            "karşıolgusal işarettir; canlı karar değişmedi, otomatik onay yok, "
            "yayına hazır değil."
        )
        base["counterfactual"] = {
            "shadow_event": overlay.get("shadow_event_on_match")
            or "AI_PROVISIONAL_SHADOW_PASS_CANDIDATE",
            "actual_runtime_decision": "UNCHANGED",
            "note": "Uzman onayı ve yayın kapısı olmadan allow uygulanmaz.",
        }
        return base

    base["status"] = "procedure_only"
    base["label"] = f"Gölge kapsam dışı · {sut_code} (önek yok)"
    base["message"] = (
        f"{sut_code} işlemi gölge pilot kodudur ancak tanılarda "
        f"{'/'.join(prefixes)}* öneki yok; gölge aday sayılmaz. Canlı karar değişmedi."
    )
    return base


def evaluate_shadow_advice_for_job(job: Any) -> dict[str, Any]:
    """Convenience wrapper for ProvizyonJob-like objects."""
    sut_fn = getattr(job, "all_sut_codes", None)
    huv_fn = getattr(job, "all_huv_codes", None)
    return evaluate_shadow_advice(
        sut_codes=list(sut_fn() if callable(sut_fn) else []),
        huv_codes=list(huv_fn() if callable(huv_fn) else []),
        diagnoses=list(getattr(job, "diagnoses", None) or []),
        nihai_karar=None,
    )


def attach_shadow_advice_to_result(result: dict[str, Any]) -> dict[str, Any]:
    """Ensure result.raw.shadow_advice exists (compute from job_meta if missing)."""
    if not isinstance(result, dict):
        return result
    raw = result.get("raw")
    if not isinstance(raw, dict):
        raw = {}
        result["raw"] = raw
    existing = raw.get("shadow_advice")
    if isinstance(existing, dict) and existing.get("status"):
        # Keep stored advice; refresh live karar pointer only.
        existing["nihai_karar"] = result.get("nihai_karar")
        existing["live_decision_unchanged"] = True
        return result
    meta = raw.get("job_meta") or {}
    advice = evaluate_shadow_advice(
        sut_codes=list(meta.get("sut_codes") or []),
        huv_codes=list(meta.get("huv_codes") or []),
        diagnoses=list(meta.get("diagnoses") or []),
        nihai_karar=result.get("nihai_karar"),
    )
    raw["shadow_advice"] = advice
    return result
