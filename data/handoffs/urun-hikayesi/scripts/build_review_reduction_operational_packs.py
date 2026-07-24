from __future__ import annotations

import csv
import html
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
TRIAGE_PATH = (
    ROOT
    / "SUT/generated/shadow_quality_gate/review_reduction_medgemma_shadow_merge_top50_combined_20260709"
    / "medgemma_review_reduction_shadow_triage_top50_combined.csv"
)
POLICY_CANDIDATES_PATH = (
    ROOT
    / "SUT/generated/shadow_quality_gate/review_reduction_policy_pack_20260709"
    / "review_reduction_policy_candidates_top50.json"
)
OUT_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_operational_packs_20260709"
EXPERT_DIR = OUT_DIR / "expert_fast_track_review_pack"
MAPPING_DIR = OUT_DIR / "mapping_backlog_pack"
HOLD_DIR = OUT_DIR / "manual_review_hold_pack"

SCHEMA_VERSION = "review_reduction_operational_packs.v1"

EXPERT_CSV_FIELDS = [
    "priority_order",
    "priority_tier",
    "rank",
    "code",
    "code_type",
    "clinical_theme",
    "review_rows",
    "top3_diagnosis_share",
    "review_reduction_potential",
    "source_risk_level",
    "source_recommended_action",
    "medgemma_confidence",
    "diagnosis_cohort_safety",
    "supported_prefixes_for_expert_review",
    "prefixes_medgemma_says_keep_review",
    "top_diagnoses",
    "current_runtime_rule_context",
    "medgemma_missing_evidence",
    "medgemma_risk_notes",
    "medgemma_reasoning_summary",
    "expert_review_question",
    "expert_decision",
    "approved_prefixes_if_any",
    "rejected_prefixes_if_any",
    "official_source_or_committee_reference",
    "expert_reviewer",
    "expert_notes",
    "human_admin_approval_present",
    "shadow_staging_allowed_after_expert_approval",
    "apply_ready",
    "auto_apply",
]

MAPPING_CSV_FIELDS = [
    "mapping_priority_order",
    "mapping_priority_tier",
    "rank",
    "code",
    "code_type",
    "clinical_theme",
    "review_rows",
    "top3_diagnosis_share",
    "source_risk_level",
    "review_reduction_potential",
    "supported_prefixes",
    "top_diagnoses",
    "medgemma_confidence",
    "medgemma_reasoning_summary",
    "mapping_question",
    "mapping_status",
    "canonical_sut_code",
    "canonical_procedure_name",
    "source_table_evidence",
    "mapping_confidence",
    "mapping_reviewer",
    "mapping_notes",
    "policy_refinement_allowed_after_mapping",
    "human_admin_approval_present",
    "apply_ready",
    "auto_apply",
]

HOLD_CSV_FIELDS = [
    "rank",
    "code",
    "clinical_theme",
    "review_rows",
    "merged_triage_category",
    "source_risk_level",
    "review_reduction_potential",
    "medgemma_status",
    "medgemma_confidence",
    "hold_reason",
    "prefixes_to_keep_review",
    "medgemma_reasoning_summary",
    "apply_ready",
    "auto_apply",
]


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


def split_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def parse_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", ".")))
    except ValueError:
        return 0


def parse_float(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", "."))
    except ValueError:
        return 0.0


def safety_block() -> dict[str, bool]:
    return {
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "live_runtime_override": False,
        "auto_apply": False,
        "exports_case_level_rows": False,
        "claims_human_admin_approval": False,
    }


def top_diagnoses_text(candidate: dict[str, Any], limit: int = 8) -> str:
    output = []
    for item in list(candidate.get("top_diagnoses") or [])[:limit]:
        output.append(f"{item.get('value')}:{item.get('count')}")
    return ";".join(output)


def policy_by_code_and_rank(candidates: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(str(item.get("code") or ""), parse_int(item.get("rank"))): item for item in candidates}


def priority_tier_for_expert(row: dict[str, Any]) -> str:
    review_rows = parse_int(row.get("review_rows"))
    potential = str(row.get("review_reduction_potential") or "")
    source_action = str(row.get("source_recommended_action") or "")
    safety = str(row.get("diagnosis_cohort_safety") or "")
    theme = str(row.get("clinical_theme") or "")
    if source_action in {
        "expert_review_for_cohort_auto_pass_candidate",
        "expert_review_for_conditional_review_refinement",
    }:
        return "tier_1_policy_candidate"
    if review_rows >= 1000 and safety == "conditional_only":
        return "tier_2_high_volume_conditional"
    if review_rows >= 1000:
        return "tier_2_high_volume_observation_to_expert"
    if theme in {"non_specific_symptom_dominant", "cardiology_symptom_or_monitoring"}:
        return "tier_3_conditional_or_safety_review"
    if potential in {"high", "medium_high"}:
        return "tier_2_medium_high_potential"
    return "tier_4_lower_volume_expert_queue"


def expert_priority_sort_key(row: dict[str, Any]) -> tuple[int, int, int, int]:
    tier_weight = {
        "tier_1_policy_candidate": 5,
        "tier_2_high_volume_conditional": 4,
        "tier_2_high_volume_observation_to_expert": 3,
        "tier_2_medium_high_potential": 3,
        "tier_3_conditional_or_safety_review": 2,
        "tier_4_lower_volume_expert_queue": 1,
    }.get(str(row.get("priority_tier") or ""), 0)
    potential_weight = {"high": 4, "medium_high": 3, "medium": 2, "low": 1}.get(
        str(row.get("review_reduction_potential") or ""),
        0,
    )
    return (tier_weight, potential_weight, parse_int(row.get("review_rows")), -parse_int(row.get("rank")))


def mapping_priority_tier(row: dict[str, Any]) -> str:
    review_rows = parse_int(row.get("review_rows"))
    if review_rows >= 50000:
        return "tier_1_extreme_volume_mapping"
    if review_rows >= 5000:
        return "tier_1_high_volume_mapping"
    if review_rows >= 1000:
        return "tier_2_medium_volume_mapping"
    return "tier_3_targeted_mapping"


def mapping_sort_key(row: dict[str, Any]) -> tuple[int, int, int]:
    tier_weight = {
        "tier_1_extreme_volume_mapping": 4,
        "tier_1_high_volume_mapping": 3,
        "tier_2_medium_volume_mapping": 2,
        "tier_3_targeted_mapping": 1,
    }.get(str(row.get("mapping_priority_tier") or ""), 0)
    return (tier_weight, parse_int(row.get("review_rows")), -parse_int(row.get("rank")))


def expert_question(row: dict[str, Any]) -> str:
    prefixes = ", ".join(split_list(row.get("supported_prefixes")))
    safety = str(row.get("diagnosis_cohort_safety") or "")
    if safety == "conditional_only":
        return (
            f"Bu işlem kodu için yalnızca dar ve belgelenmiş kohortta ({prefixes}) manual REVIEW azaltımı "
            "tıbben ve policy açısından uygun olabilir mi? Hangi prefix'ler mutlaka REVIEW kalmalı?"
        )
    return (
        f"Bu işlem kodunda MedGemma'nın desteklediği prefix kohortu ({prefixes}) domain expert onayıyla "
        "shadow staging adayına dönüştürülebilir mi?"
    )


def mapping_question(row: dict[str, Any]) -> str:
    prefixes = ", ".join(split_list(row.get("supported_prefixes")))
    return (
        f"{row.get('code')} local/HUV kodunun canonical SUT/procedure karşılığı nedir? "
        f"Tanı dağılımı ({prefixes}) klinik olarak hangi prosedür kimliğine işaret ediyor?"
    )


def enrich_common(row: dict[str, str], policy_lookup: dict[tuple[str, int], dict[str, Any]]) -> dict[str, Any]:
    code = str(row.get("code") or "")
    rank = parse_int(row.get("rank"))
    policy = policy_lookup.get((code, rank), {})
    return {
        **row,
        "rank": rank,
        "review_rows": parse_int(row.get("review_rows")),
        "top3_diagnosis_share": parse_float(row.get("top3_diagnosis_share")),
        "medgemma_confidence": parse_float(row.get("confidence")),
        "supported_prefixes": split_list(row.get("supported_prefixes")),
        "prefixes_to_keep_review": split_list(row.get("prefixes_to_keep_review")),
        "missing_evidence": split_list(row.get("missing_evidence")),
        "risk_notes": split_list(row.get("risk_notes")),
        "top_diagnoses": top_diagnoses_text(policy),
        "current_runtime_rule_context": policy.get("current_runtime_rule_context") or {},
        "policy_source_candidate": policy,
        "safety": safety_block(),
    }


def build_expert_rows(triage_rows: list[dict[str, str]], policy_lookup: dict[tuple[str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        enrich_common(row, policy_lookup)
        for row in triage_rows
        if row.get("merged_triage_category") == "fast_track_to_human_expert_review"
    ]
    for row in rows:
        row["priority_tier"] = priority_tier_for_expert(row)
        row["expert_review_question"] = expert_question(row)
        row["expert_decision"] = ""
        row["approved_prefixes_if_any"] = ""
        row["rejected_prefixes_if_any"] = ""
        row["official_source_or_committee_reference"] = ""
        row["expert_reviewer"] = ""
        row["expert_notes"] = ""
        row["human_admin_approval_present"] = False
        row["shadow_staging_allowed_after_expert_approval"] = False
        row["apply_ready"] = False
        row["auto_apply"] = False
    rows.sort(key=expert_priority_sort_key, reverse=True)
    for index, row in enumerate(rows, start=1):
        row["priority_order"] = index
        row["supported_prefixes_for_expert_review"] = row.get("supported_prefixes")
        row["prefixes_medgemma_says_keep_review"] = row.get("prefixes_to_keep_review")
        row["medgemma_missing_evidence"] = row.get("missing_evidence")
        row["medgemma_risk_notes"] = row.get("risk_notes")
        row["medgemma_reasoning_summary"] = row.get("reasoning_summary")
    return rows


def build_mapping_rows(triage_rows: list[dict[str, str]], policy_lookup: dict[tuple[str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        enrich_common(row, policy_lookup)
        for row in triage_rows
        if row.get("merged_triage_category") == "mapping_backlog_before_policy"
    ]
    for row in rows:
        row["mapping_priority_tier"] = mapping_priority_tier(row)
        row["mapping_question"] = mapping_question(row)
        row["mapping_status"] = ""
        row["canonical_sut_code"] = ""
        row["canonical_procedure_name"] = ""
        row["source_table_evidence"] = ""
        row["mapping_confidence"] = ""
        row["mapping_reviewer"] = ""
        row["mapping_notes"] = ""
        row["policy_refinement_allowed_after_mapping"] = False
        row["human_admin_approval_present"] = False
        row["apply_ready"] = False
        row["auto_apply"] = False
        row["medgemma_reasoning_summary"] = row.get("reasoning_summary")
    rows.sort(key=mapping_sort_key, reverse=True)
    for index, row in enumerate(rows, start=1):
        row["mapping_priority_order"] = index
    return rows


def build_hold_rows(triage_rows: list[dict[str, str]], policy_lookup: dict[tuple[str, int], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        enrich_common(row, policy_lookup)
        for row in triage_rows
        if row.get("merged_triage_category")
        in {"keep_manual_review_observation", "response_invalid_manual_review_required"}
    ]
    for row in rows:
        if row.get("merged_triage_category") == "response_invalid_manual_review_required":
            row["hold_reason"] = "MedGemma response contract invalid or internally inconsistent; keep manual REVIEW."
        elif row.get("medgemma_status") == "error":
            row["hold_reason"] = "MedGemma inference/parse error; keep manual REVIEW."
        else:
            row["hold_reason"] = "Aggregate evidence is plausible but too broad/non-specific for review reduction."
        row["medgemma_reasoning_summary"] = row.get("reasoning_summary")
        row["apply_ready"] = False
        row["auto_apply"] = False
    rows.sort(key=lambda item: (parse_int(item.get("rank"))))
    return rows


def build_expert_prompt(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows[:12]:
        lines.append(
            f"- #{row['priority_order']} rank={row['rank']} code={row['code']} "
            f"review_rows={row['review_rows']} theme={row['clinical_theme']} "
            f"tier={row['priority_tier']} prefixes={','.join(row['supported_prefixes_for_expert_review'])}"
        )
    return """REVIEW REDUCTION — DOMAIN EXPERT FAST-TRACK REVIEW

Purpose:
Evaluate MedGemma-shadow-triaged aggregate historical candidates for possible narrow deterministic review-policy refinement.

Important constraints:
- This is not payment approval.
- This is not a live runtime change.
- MedGemma output is shadow metadata only and is not human/admin/expert approval.
- Any candidate accepted by an expert may only move to shadow staging, deterministic batch preview, decision-drift/business-impact gates, and rollback planning.
- No live apply, Qdrant write, runtime override, or automatic review reduction is authorized by this package.

Priority candidates:
""" + "\n".join(lines) + """

Expert decision options per row:
1. keep_manual_review
2. approve_shadow_staging_for_specific_prefixes
3. conditional_review_refinement_only
4. reject_review_reduction_due_to_risk
5. request_more_evidence

Required fields:
- code
- expert_decision
- approved_prefixes_if_any
- rejected_prefixes_if_any
- official_source_or_committee_reference
- expert_reviewer
- expert_notes
"""


def build_mapping_prompt(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows[:12]:
        lines.append(
            f"- #{row['mapping_priority_order']} rank={row['rank']} code={row['code']} "
            f"review_rows={row['review_rows']} tier={row['mapping_priority_tier']} "
            f"theme={row['clinical_theme']} prefixes={','.join(row['supported_prefixes'])}"
        )
    return """REVIEW REDUCTION — LOCAL/HUV CANONICAL MAPPING BACKLOG

Purpose:
Resolve local/HUV/dotted procedure-code identity before any review-reduction policy refinement is considered.

Important constraints:
- Read-only catalog/procedure/code lookup only.
- Do not change live rules, runtime lookup, Qdrant, or production databases.
- Do not export case-level rows or patient identifiers.
- Mapping evidence is not review-reduction approval.
- After mapping, any policy refinement still requires domain expert/admin validation and deterministic preview gates.

Priority mapping candidates:
""" + "\n".join(lines) + """

Mapping decision options per row:
1. mapped_to_existing_sut_code
2. local_alias_of_existing_catalog_item
3. legacy_code_requires_catalog_backfill
4. unmapped_needs_business_owner
5. exclude_from_review_reduction_scope

Required fields:
- code
- mapping_status
- canonical_sut_code
- canonical_procedure_name
- source_table_evidence
- mapping_confidence
- mapping_reviewer
- mapping_notes
"""


def build_mapping_sql_template() -> str:
    return """-- READ-ONLY mapping research template.
-- Replace {{CODE}} with a single local/HUV/dotted candidate code.
-- Do not run UPDATE/INSERT/DELETE/MERGE/EXEC apply commands.

-- 1) Direct procedure definition lookup
SELECT TOP 50
    att.Kod,
    att.Ad,
    att.HuvKodu,
    att.*
FROM AYAKTA_TEDAVI_TANIM att
WHERE att.Kod = '{{CODE}}'
   OR att.HuvKodu = '{{CODE}}';

-- 2) If direct lookup fails, search by normalized dotted/legacy variants.
-- Add local normalization rules manually and keep output aggregate/catalog-only.
SELECT TOP 50
    att.Kod,
    att.Ad,
    att.HuvKodu
FROM AYAKTA_TEDAVI_TANIM att
WHERE REPLACE(att.Kod, '.', '') = REPLACE('{{CODE}}', '.', '')
   OR REPLACE(att.HuvKodu, '.', '') = REPLACE('{{CODE}}', '.', '');

-- 3) Optional: count historical references only; do not export case rows.
SELECT
    att.Kod,
    att.Ad,
    COUNT_BIG(*) AS provision_row_count
FROM PROVIZYON_FATURA_AYAKTA_TEDAVI pfat
JOIN AYAKTA_TEDAVI_TANIM att ON att.Id = pfat.AyaktaTedaviTanimId
WHERE att.Kod = '{{CODE}}'
   OR att.HuvKodu = '{{CODE}}'
GROUP BY att.Kod, att.Ad;
"""


def build_html(title: str, rows: list[dict[str, Any]], columns: list[str]) -> str:
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
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ccc;padding:4px 8px;font-size:12px}th{background:#f3f3f3}</style>"
        "</head><body>"
        f"<h1>{html.escape(title)}</h1>"
        "<p>This is shadow/admin-review evidence only. It does not approve, auto-apply, or override live decisions.</p>"
        f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
        "</body></html>"
    )


def write_pack_outputs() -> dict[str, Any]:
    triage_rows = load_csv(TRIAGE_PATH)
    policy_candidates = load_json(POLICY_CANDIDATES_PATH)
    policy_lookup = policy_by_code_and_rank(policy_candidates)
    expert_rows = build_expert_rows(triage_rows, policy_lookup)
    mapping_rows = build_mapping_rows(triage_rows, policy_lookup)
    hold_rows = build_hold_rows(triage_rows, policy_lookup)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    EXPERT_DIR.mkdir(parents=True, exist_ok=True)
    MAPPING_DIR.mkdir(parents=True, exist_ok=True)
    HOLD_DIR.mkdir(parents=True, exist_ok=True)

    write_json(EXPERT_DIR / "expert_fast_track_candidates_all19.json", expert_rows)
    write_csv(EXPERT_DIR / "expert_fast_track_candidates_all19.csv", expert_rows, EXPERT_CSV_FIELDS)
    write_json(EXPERT_DIR / "expert_fast_track_candidates_priority10.json", expert_rows[:10])
    write_csv(EXPERT_DIR / "expert_fast_track_candidates_priority10.csv", expert_rows[:10], EXPERT_CSV_FIELDS)
    write_csv(EXPERT_DIR / "EXPERT_DECISION_TEMPLATE.csv", expert_rows, EXPERT_CSV_FIELDS)
    (EXPERT_DIR / "DOMAIN_EXPERT_FAST_TRACK_REVIEW_PROMPT_COPY_PASTE.txt").write_text(
        build_expert_prompt(expert_rows),
        encoding="utf-8",
    )
    (EXPERT_DIR / "expert_fast_track_dashboard.html").write_text(
        build_html(
            "Expert Fast-Track Review Pack",
            expert_rows,
            [
                "priority_order",
                "priority_tier",
                "rank",
                "code",
                "clinical_theme",
                "review_rows",
                "medgemma_confidence",
                "supported_prefixes_for_expert_review",
            ],
        ),
        encoding="utf-8",
    )

    write_json(MAPPING_DIR / "mapping_backlog_candidates_all16.json", mapping_rows)
    write_csv(MAPPING_DIR / "mapping_backlog_candidates_all16.csv", mapping_rows, MAPPING_CSV_FIELDS)
    write_json(MAPPING_DIR / "mapping_backlog_priority10.json", mapping_rows[:10])
    write_csv(MAPPING_DIR / "mapping_backlog_priority10.csv", mapping_rows[:10], MAPPING_CSV_FIELDS)
    write_csv(MAPPING_DIR / "MAPPING_RESEARCH_TEMPLATE.csv", mapping_rows, MAPPING_CSV_FIELDS)
    write_json(
        MAPPING_DIR / "MAPPING_OUTPUT_CONTRACT.json",
        {
            "schema_version": "review_reduction_mapping_output_contract.v1",
            "required_fields": [
                "code",
                "mapping_status",
                "canonical_sut_code",
                "canonical_procedure_name",
                "source_table_evidence",
                "mapping_confidence",
                "mapping_reviewer",
                "mapping_notes",
                "no_live_write_ack",
                "no_qdrant_write_ack",
                "no_case_level_export_ack",
            ],
            "allowed_mapping_status": [
                "mapped_to_existing_sut_code",
                "local_alias_of_existing_catalog_item",
                "legacy_code_requires_catalog_backfill",
                "unmapped_needs_business_owner",
                "exclude_from_review_reduction_scope",
            ],
        },
    )
    (MAPPING_DIR / "MAPPING_AGENT_PROMPT_COPY_PASTE.txt").write_text(build_mapping_prompt(mapping_rows), encoding="utf-8")
    (MAPPING_DIR / "READ_ONLY_MAPPING_SQL_TEMPLATE.sql").write_text(build_mapping_sql_template(), encoding="utf-8")
    (MAPPING_DIR / "mapping_backlog_dashboard.html").write_text(
        build_html(
            "Mapping Backlog Pack",
            mapping_rows,
            [
                "mapping_priority_order",
                "mapping_priority_tier",
                "rank",
                "code",
                "clinical_theme",
                "review_rows",
                "supported_prefixes",
            ],
        ),
        encoding="utf-8",
    )

    write_json(HOLD_DIR / "manual_review_hold_candidates.json", hold_rows)
    write_csv(HOLD_DIR / "manual_review_hold_candidates.csv", hold_rows, HOLD_CSV_FIELDS)
    (HOLD_DIR / "manual_review_hold_dashboard.html").write_text(
        build_html(
            "Manual Review Hold Pack",
            hold_rows,
            ["rank", "code", "clinical_theme", "review_rows", "merged_triage_category", "hold_reason"],
        ),
        encoding="utf-8",
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "source_triage_path": str(TRIAGE_PATH),
        "source_policy_candidates_path": str(POLICY_CANDIDATES_PATH),
        "out_dir": str(OUT_DIR),
        "counts": {
            "source_triage_rows": len(triage_rows),
            "expert_fast_track_rows": len(expert_rows),
            "expert_priority10_rows": len(expert_rows[:10]),
            "mapping_backlog_rows": len(mapping_rows),
            "mapping_priority10_rows": len(mapping_rows[:10]),
            "manual_review_hold_rows": len(hold_rows),
            "expert_by_priority_tier": dict(Counter(str(row.get("priority_tier") or "") for row in expert_rows)),
            "mapping_by_priority_tier": dict(Counter(str(row.get("mapping_priority_tier") or "") for row in mapping_rows)),
            "hold_by_category": dict(Counter(str(row.get("merged_triage_category") or "") for row in hold_rows)),
        },
        "safety": safety_block(),
        "generated_files": [
            str(EXPERT_DIR / "expert_fast_track_candidates_all19.json"),
            str(EXPERT_DIR / "expert_fast_track_candidates_all19.csv"),
            str(EXPERT_DIR / "expert_fast_track_candidates_priority10.json"),
            str(EXPERT_DIR / "expert_fast_track_candidates_priority10.csv"),
            str(EXPERT_DIR / "EXPERT_DECISION_TEMPLATE.csv"),
            str(EXPERT_DIR / "DOMAIN_EXPERT_FAST_TRACK_REVIEW_PROMPT_COPY_PASTE.txt"),
            str(EXPERT_DIR / "expert_fast_track_dashboard.html"),
            str(MAPPING_DIR / "mapping_backlog_candidates_all16.json"),
            str(MAPPING_DIR / "mapping_backlog_candidates_all16.csv"),
            str(MAPPING_DIR / "mapping_backlog_priority10.json"),
            str(MAPPING_DIR / "mapping_backlog_priority10.csv"),
            str(MAPPING_DIR / "MAPPING_RESEARCH_TEMPLATE.csv"),
            str(MAPPING_DIR / "MAPPING_OUTPUT_CONTRACT.json"),
            str(MAPPING_DIR / "MAPPING_AGENT_PROMPT_COPY_PASTE.txt"),
            str(MAPPING_DIR / "READ_ONLY_MAPPING_SQL_TEMPLATE.sql"),
            str(MAPPING_DIR / "mapping_backlog_dashboard.html"),
            str(HOLD_DIR / "manual_review_hold_candidates.json"),
            str(HOLD_DIR / "manual_review_hold_candidates.csv"),
            str(HOLD_DIR / "manual_review_hold_dashboard.html"),
            str(OUT_DIR / "TASK_MANIFEST.json"),
        ],
        "next_steps": [
            "Send expert_fast_track_candidates_priority10.csv and DOMAIN_EXPERT_FAST_TRACK_REVIEW_PROMPT_COPY_PASTE.txt to domain experts.",
            "Send mapping_backlog_priority10.csv and MAPPING_AGENT_PROMPT_COPY_PASTE.txt to mapping/catalog owners.",
            "Do not stage or apply any policy until expert/admin decisions and mapping outputs are returned and validated.",
        ],
    }
    write_json(OUT_DIR / "TASK_MANIFEST.json", manifest)
    (OUT_DIR / "RUN_NEXT_STEPS.txt").write_text(
        "\n".join(f"{index}. {step}" for index, step in enumerate(manifest["next_steps"], start=1)) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "out_dir": str(OUT_DIR),
                "counts": manifest["counts"],
                "safety": manifest["safety"],
                "expert_priority10": [
                    {
                        "priority_order": row["priority_order"],
                        "code": row["code"],
                        "review_rows": row["review_rows"],
                        "tier": row["priority_tier"],
                        "prefixes": row["supported_prefixes_for_expert_review"],
                    }
                    for row in expert_rows[:10]
                ],
                "mapping_priority10": [
                    {
                        "mapping_priority_order": row["mapping_priority_order"],
                        "code": row["code"],
                        "review_rows": row["review_rows"],
                        "tier": row["mapping_priority_tier"],
                        "prefixes": row["supported_prefixes"],
                    }
                    for row in mapping_rows[:10]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return manifest


def main() -> int:
    write_pack_outputs()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
