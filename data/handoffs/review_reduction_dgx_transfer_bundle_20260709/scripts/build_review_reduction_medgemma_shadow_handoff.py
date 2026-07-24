from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
POLICY_PACK_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_policy_pack_20260709"
DEFAULT_CANDIDATES_PATH = POLICY_PACK_DIR / "review_reduction_policy_candidates_top50.json"
DEFAULT_POLICY_MANIFEST_PATH = POLICY_PACK_DIR / "TASK_MANIFEST.json"
DEFAULT_OUT_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_medgemma_shadow_handoff_20260709"

SCHEMA_VERSION = "review_reduction_medgemma_shadow_handoff.v1"
REQUEST_SCHEMA_VERSION = "review_reduction_medgemma_shadow_request.v1"
RESPONSE_SCHEMA_VERSION = "review_reduction_medgemma_shadow_response.v1"

REQUEST_CSV_FIELDS = [
    "request_id",
    "rank",
    "code",
    "code_type",
    "lookup_status",
    "clinical_theme",
    "review_rows",
    "top3_diagnosis_share",
    "review_reduction_potential",
    "risk_level",
    "recommended_action",
    "review_focus",
    "top_diagnosis_prefixes_for_expert_review",
]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


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


def slug(value: Any) -> str:
    cleaned = re.sub(r"[^0-9A-Za-zığüşöçİĞÜŞÖÇ]+", "_", str(value or ""), flags=re.IGNORECASE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:80] or "row"


def safety_block(*, calls_medgemma: bool = False) -> dict[str, bool]:
    return {
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "live_runtime_override": False,
        "auto_apply": False,
        "exports_case_level_rows": False,
        "calls_medgemma": calls_medgemma,
        "claims_human_admin_approval": False,
    }


def response_schema() -> dict[str, Any]:
    return {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "format": "jsonl",
        "one_json_object_per_request": True,
        "required_fields": [
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
        ],
        "allowed_values": {
            "schema_version": [RESPONSE_SCHEMA_VERSION],
            "medgemma_status": ["completed", "blocked", "error"],
            "clinical_plausibility": [
                "plausible",
                "partially_plausible",
                "not_plausible",
                "insufficient_evidence",
            ],
            "diagnosis_cohort_safety": [
                "narrow_candidate",
                "conditional_only",
                "keep_manual_review",
                "mapping_required_first",
                "insufficient_evidence",
            ],
            "mapping_assessment": [
                "not_needed",
                "mapping_required_first",
                "canonical_identity_uncertain",
                "candidate_for_mapping_review",
            ],
            "confidence_label": ["very_low", "low", "medium", "high"],
            "recommended_triage": [
                "send_to_expert_priority_1",
                "send_to_expert_priority_2",
                "mapping_backlog_first",
                "keep_manual_review_observation",
                "reject_for_review_reduction",
                "blocked_no_inference",
            ],
        },
        "hard_constraints": [
            "Return exactly one JSON object per input request line.",
            "Do not wrap responses in markdown or code fences.",
            "confidence must be a number between 0.0 and 1.0.",
            "eligible_for_human_expert_fast_track is not approval; it only prioritizes human expert review.",
            "eligible_for_human_expert_fast_track=true requires medgemma_status=completed, confidence >= 0.85, clinical_plausibility=plausible, and diagnosis_cohort_safety in narrow_candidate or conditional_only.",
            "eligible_for_human_expert_fast_track=true is forbidden when mapping_assessment is mapping_required_first or canonical_identity_uncertain.",
            "For lookup_status=missing_from_runtime_lookup, prefer mapping_required_first or candidate_for_mapping_review; do not suggest direct policy staging.",
            "Never claim human/admin/expert approval; set no_human_approval_claim_ack=true.",
            "Never write to Qdrant, runtime systems, production databases, or live decision layers; set no_live_write_ack=true.",
            "Set shadow_only_ack=true to acknowledge that this is only AI shadow metadata.",
            "If MedGemma inference cannot be run, return medgemma_status=blocked, confidence=0.0, recommended_triage=blocked_no_inference, eligible_for_human_expert_fast_track=false.",
        ],
    }


def compact_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": candidate.get("rank"),
        "code": candidate.get("code"),
        "code_type": candidate.get("code_type"),
        "lookup_status": candidate.get("lookup_status"),
        "clinical_theme": candidate.get("clinical_theme"),
        "specificity_level": candidate.get("specificity_level"),
        "review_reduction_potential": candidate.get("review_reduction_potential"),
        "risk_level": candidate.get("risk_level"),
        "recommended_action": candidate.get("recommended_action"),
        "review_rows": candidate.get("review_rows"),
        "provision_rows": candidate.get("provision_rows"),
        "top3_diagnosis_share": candidate.get("top3_diagnosis_share"),
        "top1_diagnosis_share": candidate.get("top1_diagnosis_share"),
        "unique_diagnoses": candidate.get("unique_diagnoses"),
        "diagnosis_normalized_entropy": candidate.get("diagnosis_normalized_entropy"),
        "top_diagnoses": list(candidate.get("top_diagnoses") or [])[:15],
        "top_diagnosis_prefixes_for_expert_review": list(
            candidate.get("top_diagnosis_prefixes_for_expert_review") or []
        ),
        "top_diagnosis_chapters": list(candidate.get("top_diagnosis_chapters") or [])[:8],
        "current_runtime_rule_context": candidate.get("current_runtime_rule_context") or {},
        "suggested_policy_hypothesis": candidate.get("suggested_policy_hypothesis"),
        "required_safety_gates": candidate.get("required_safety_gates") or [],
        "safety": candidate.get("safety") or safety_block(),
    }


def review_focus(candidate: dict[str, Any]) -> str:
    action = str(candidate.get("recommended_action") or "")
    lookup_status = str(candidate.get("lookup_status") or "")
    theme = str(candidate.get("clinical_theme") or "")
    if lookup_status == "missing_from_runtime_lookup":
        return (
            "Assess whether the diagnosis distribution is clinically coherent enough to prioritize local/HUV "
            "mapping work. Do not recommend review reduction before canonical mapping is resolved."
        )
    if action == "expert_review_for_cohort_auto_pass_candidate":
        return (
            "Assess whether the observed top diagnosis prefixes form a narrow, clinically plausible cohort "
            "that should be fast-tracked to human expert review for possible deterministic policy refinement."
        )
    if action == "expert_review_for_conditional_review_refinement":
        return (
            "Assess whether a conditional policy could reduce review only for a narrow documented cohort while "
            "keeping broader/manual-review diagnoses under review."
        )
    if theme == "non_specific_symptom_dominant":
        return (
            "Assess whether non-specific symptom dominance makes review reduction unsafe and which prefixes "
            "should remain manual-review only."
        )
    return (
        "Assess aggregate clinical plausibility, evidence gaps, and whether this candidate should stay as "
        "manual-review observation."
    )


def render_request_prompt(request_payload: dict[str, Any]) -> str:
    payload = {
        "response_schema": response_schema(),
        "request": request_payload,
        "global_constraints": {
            "role": "MedGemma shadow clinical plausibility reviewer and evidence triage assistant",
            "aggregate_only": True,
            "no_case_level_rows": True,
            "must_not_claim_payment_or_policy_approval": True,
            "must_not_claim_human_admin_expert_approval": True,
            "must_not_apply_or_write_live_systems": True,
            "must_not_override_deterministic_validators": True,
            "official_source_or_internal_committee_validation_still_required": True,
        },
    }
    return (
        "Bu review-reduction adayı için MedGemma shadow klinik plausibility ve kanıt-triage değerlendirmesi yap.\n"
        "Sadece verilen aggregate tanı dağılımını kullan; vaka düzeyi veri isteme veya uydurma.\n"
        "Ödeme onayı, canlı kural onayı, insan/admin/uzman onayı veya auto-pass kararı üretme.\n"
        "Sadece strict JSON object döndür; markdown/code fence kullanma.\n\n"
        "Değerlendirilecek başlıklar:\n"
        "1. Tanı prefix kohortu klinik olarak dar ve makul mü?\n"
        "2. Hangi prefix'ler uzman incelemesine taşınabilir, hangileri manual REVIEW kalmalı?\n"
        "3. Kod runtime lookup'ta yoksa mapping çözülmeden policy refinement yapılmamalı mı?\n"
        "4. Eksik kanıtlar ve klinik/policy risk notları nelerdir?\n\n"
        f"INPUT_JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}"
    )


def build_request(candidate: dict[str, Any], *, index: int, generated_at: str) -> dict[str, Any]:
    compact = compact_candidate(candidate)
    request_id = f"rr_medgemma_{index + 1:04d}_{slug(compact.get('code'))}"
    request_payload = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "generated_at": generated_at,
        "request_id": request_id,
        "task": "review_reduction_medgemma_shadow_clinical_plausibility_triage",
        "response_schema_version": RESPONSE_SCHEMA_VERSION,
        "review_focus": review_focus(compact),
        "candidate": compact,
        "safety": safety_block(calls_medgemma=False),
        "non_goals": [
            "do_not_auto_apply",
            "do_not_write_qdrant",
            "do_not_write_production_db",
            "do_not_export_case_level_rows",
            "do_not_claim_human_admin_expert_approval",
            "do_not_represent_ai_as_payment_policy_approval",
        ],
    }
    return {
        **request_payload,
        "prompt": render_request_prompt(request_payload),
    }


def build_requests(
    candidates: list[dict[str, Any]],
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    generated_at = now_iso()
    selected = candidates[offset:]
    if limit:
        selected = selected[:limit]
    return [build_request(candidate, index=index, generated_at=generated_at) for index, candidate in enumerate(selected)]


def request_csv_row(request: dict[str, Any]) -> dict[str, Any]:
    candidate = request.get("candidate") or {}
    return {
        "request_id": request.get("request_id"),
        "rank": candidate.get("rank"),
        "code": candidate.get("code"),
        "code_type": candidate.get("code_type"),
        "lookup_status": candidate.get("lookup_status"),
        "clinical_theme": candidate.get("clinical_theme"),
        "review_rows": candidate.get("review_rows"),
        "top3_diagnosis_share": candidate.get("top3_diagnosis_share"),
        "review_reduction_potential": candidate.get("review_reduction_potential"),
        "risk_level": candidate.get("risk_level"),
        "recommended_action": candidate.get("recommended_action"),
        "review_focus": request.get("review_focus"),
        "top_diagnosis_prefixes_for_expert_review": candidate.get("top_diagnosis_prefixes_for_expert_review"),
    }


def blocked_response_template(request: dict[str, Any]) -> dict[str, Any]:
    candidate = request.get("candidate") or {}
    return {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "request_id": request.get("request_id"),
        "code": candidate.get("code"),
        "medgemma_status": "blocked",
        "clinical_plausibility": "insufficient_evidence",
        "diagnosis_cohort_safety": "insufficient_evidence",
        "mapping_assessment": "canonical_identity_uncertain",
        "confidence": 0.0,
        "confidence_label": "very_low",
        "eligible_for_human_expert_fast_track": False,
        "recommended_triage": "blocked_no_inference",
        "supported_prefixes": [],
        "prefixes_to_keep_review": list(candidate.get("top_diagnosis_prefixes_for_expert_review") or []),
        "missing_evidence": ["medgemma_inference_not_run"],
        "risk_notes": ["blocked_template_only"],
        "reasoning_summary": "MedGemma inference çalıştırılmadığı için bu satır sadece blocked şablondur.",
        "no_live_write_ack": True,
        "no_human_approval_claim_ack": True,
        "shadow_only_ack": True,
    }


def agent_prompt_text() -> str:
    schema = json.dumps(response_schema(), ensure_ascii=False, indent=2, sort_keys=True)
    return f"""MEDGEMMA SHADOW REVIEW TASK — Review Reduction Clinical Plausibility Triage

AMAÇ
Bu klasördeki aggregate-only review-reduction adaylarını MedGemma ile klinik plausibility ve risk/kanıt triage açısından değerlendir.

GİRDİLER
- medgemma_review_reduction_shadow_requests.jsonl: işlenecek ana istek dosyası, her satır bir JSON request.
- medgemma_review_reduction_shadow_requests.json: aynı isteklerin JSON array hali.
- MEDGEMMA_RESPONSE_SCHEMA.json: zorunlu yanıt sözleşmesi.
- medgemma_review_reduction_shadow_responses_BLOCKED_TEMPLATE.jsonl: MedGemma inference çalıştırılamazsa kullanılabilecek blocked şablon.

ÜRETİLECEK ÇIKTI
- medgemma_review_reduction_shadow_responses.jsonl

KATI KURALLAR
- Her request için tam bir JSON object döndür.
- Markdown, açıklama metni veya code fence kullanma.
- Hasta/vaka/provizyon satırı isteme veya yazma; girdi aggregate-only.
- Production DB, Qdrant, runtime, rule engine, canlı karar katmanı veya dosyalara live apply/write yapma.
- Ödeme onayı, canlı kural onayı, insan/admin/uzman onayı iddia etme.
- MedGemma çıktısı sadece shadow metadata ve domain expert önceliklendirme girdisidir.
- Runtime lookup'ta eksik local/HUV kodlarda mapping çözülmeden review reduction önermemelisin.

YANIT SÖZLEŞMESİ
{schema}

DEĞERLENDİRME REHBERİ
1. Top tanı prefix dağılımının klinik olarak dar ve makul olup olmadığını değerlendir.
2. supported_prefixes alanına yalnızca girdi adayındaki prefix listesinden, uzman incelemesine taşınabilecek olanları koy.
3. prefixes_to_keep_review alanına geniş, non-specific, riskli veya belirsiz kalması gereken prefix'leri koy.
4. missing_evidence alanında resmi SUT kaynak, prosedür adı, mapping, yaş/cinsiyet bandı, dönem etkisi gibi eksikleri belirt.
5. eligible_for_human_expert_fast_track=true yalnızca insan uzman incelemesine hızlı taşıma anlamına gelir; onay değildir.
6. Kısa Türkçe reasoning_summary yaz; gizli chain-of-thought verme.
"""


def run_after_text(out_dir: Path) -> str:
    responses_path = out_dir / "medgemma_review_reduction_shadow_responses.jsonl"
    merge_script = ROOT / "SUT/generated/dgx_handoff/merge_review_reduction_medgemma_shadow_responses.py"
    return f"""MedGemma response return steps:
1. Put the returned JSONL at:
   {responses_path}
2. Validate and merge it with:
   python {merge_script} --requests-path {out_dir / "medgemma_review_reduction_shadow_requests.jsonl"} --responses-path {responses_path}
3. Review the generated merge/triage output before any expert/admin package update.

Safety reminder:
- This merge is shadow-only.
- It does not apply rules.
- It does not write Qdrant.
- It does not create human/admin/expert approval.
- Any actual policy refinement still requires official source or internal medical committee validation, human/admin approval, deterministic shadow batch preview, business-impact drift gate, and rollback planning.
"""


def build_manifest(
    *,
    candidates_path: Path,
    policy_manifest_path: Path,
    out_dir: Path,
    requests: list[dict[str, Any]],
    generated_files: list[str],
) -> dict[str, Any]:
    candidates = [request.get("candidate") or {} for request in requests]
    source_manifest = load_json(policy_manifest_path) if policy_manifest_path.exists() else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "mode": "review_reduction_medgemma_shadow_handoff",
        "source_candidates_path": str(candidates_path),
        "source_policy_manifest_path": str(policy_manifest_path),
        "out_dir": str(out_dir),
        "generated_files": generated_files,
        "expected_medgemma_output": str(out_dir / "medgemma_review_reduction_shadow_responses.jsonl"),
        "counts": {
            "requests": len(requests),
            "by_recommended_action": dict(Counter(str(item.get("recommended_action") or "") for item in candidates)),
            "by_risk_level": dict(Counter(str(item.get("risk_level") or "") for item in candidates)),
            "by_review_reduction_potential": dict(
                Counter(str(item.get("review_reduction_potential") or "") for item in candidates)
            ),
            "by_lookup_status": dict(Counter(str(item.get("lookup_status") or "") for item in candidates)),
            "by_clinical_theme": dict(Counter(str(item.get("clinical_theme") or "") for item in candidates)),
        },
        "source_policy_context": source_manifest.get("source_context") or {},
        "safety": safety_block(calls_medgemma=False),
        "instructions": {
            "does_not_call_medgemma_locally": True,
            "does_not_apply": True,
            "does_not_write_qdrant": True,
            "does_not_export_case_level_rows": True,
            "medgemma_side_runs_shadow_review": True,
            "response_must_not_claim_human_approval": True,
            "local_next_gate": "Validate MedGemma responses and use the merged triage only as domain expert/admin review input.",
        },
    }


def write_handoff_outputs(
    *,
    candidates_path: Path = DEFAULT_CANDIDATES_PATH,
    policy_manifest_path: Path = DEFAULT_POLICY_MANIFEST_PATH,
    out_dir: Path = DEFAULT_OUT_DIR,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    candidates = list(load_json(candidates_path) or [])
    requests = build_requests(candidates, limit=limit, offset=offset)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out_dir / "TASK_MANIFEST.json"
    requests_json_path = out_dir / "medgemma_review_reduction_shadow_requests.json"
    requests_jsonl_path = out_dir / "medgemma_review_reduction_shadow_requests.jsonl"
    requests_csv_path = out_dir / "medgemma_review_reduction_shadow_requests.csv"
    schema_path = out_dir / "MEDGEMMA_RESPONSE_SCHEMA.json"
    prompt_path = out_dir / "MEDGEMMA_AGENT_PROMPT_COPY_PASTE.txt"
    blocked_template_path = out_dir / "medgemma_review_reduction_shadow_responses_BLOCKED_TEMPLATE.jsonl"
    output_contract_path = out_dir / "OUTPUT_CONTRACT.json"
    run_after_path = out_dir / "RUN_AFTER_MEDGEMMA_RETURN.txt"

    generated_files = [
        str(manifest_path),
        str(requests_json_path),
        str(requests_jsonl_path),
        str(requests_csv_path),
        str(schema_path),
        str(prompt_path),
        str(blocked_template_path),
        str(output_contract_path),
        str(run_after_path),
    ]
    manifest = build_manifest(
        candidates_path=candidates_path,
        policy_manifest_path=policy_manifest_path,
        out_dir=out_dir,
        requests=requests,
        generated_files=generated_files,
    )

    write_json(manifest_path, manifest)
    write_json(requests_json_path, requests)
    write_jsonl(requests_jsonl_path, requests)
    write_csv(requests_csv_path, [request_csv_row(request) for request in requests], REQUEST_CSV_FIELDS)
    write_json(schema_path, response_schema())
    write_json(output_contract_path, response_schema())
    write_jsonl(blocked_template_path, [blocked_response_template(request) for request in requests])
    prompt_path.write_text(agent_prompt_text(), encoding="utf-8")
    run_after_path.write_text(run_after_text(out_dir), encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a MedGemma shadow-review handoff package for review-reduction candidates."
    )
    parser.add_argument("--candidates-path", type=Path, default=DEFAULT_CANDIDATES_PATH)
    parser.add_argument("--policy-manifest-path", type=Path, default=DEFAULT_POLICY_MANIFEST_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--limit", type=int, default=0, help="Optional request limit; 0 means all candidates.")
    parser.add_argument("--offset", type=int, default=0, help="Optional zero-based candidate offset before limit.")
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = write_handoff_outputs(
        candidates_path=args.candidates_path,
        policy_manifest_path=args.policy_manifest_path,
        out_dir=args.out_dir,
        limit=args.limit or None,
        offset=args.offset,
    )
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print(f"MedGemma review-reduction shadow handoff written: {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
