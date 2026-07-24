from __future__ import annotations

import csv
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
OUT_DIR = ROOT / "SUT/generated/dgx_handoff/medgemma_business_impact_historical_sample_task_final_24_20260707"
SOURCE_PLAN = ROOT / "SUT/generated/shadow_quality_gate/medgemma_dgx_final_guarded_apply_plan_final_24_big_medgemma_prefill/medgemma_final_guarded_apply_plan_rows.json"
SOURCE_STAGING_DIR = ROOT / "SUT/generated/shadow_quality_gate/medgemma_dgx_final_guarded_apply_executor_shadow_write_final_24_big_medgemma_prefill/shadow_staging_writes"
SOURCE_BUSINESS_DIR = ROOT / "SUT/generated/shadow_quality_gate/medgemma_dgx_final_business_impact_analysis_final_24_big_medgemma_prefill"
SOURCE_SCRIPT = ROOT / "SUT/diagnosis_rules/shadow_medgemma_final_business_impact_analysis.py"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def csv_value(value: Any) -> Any:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field, "")) for field in fields})


def normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("SUT::"):
        text = text.split("::", 1)[1].strip()
    return text


def int_or_zero(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return max(0, int(float(str(value).replace(",", ".").strip())))
    except (TypeError, ValueError):
        return 0


def first_nested_key(obj: Any, key: str) -> Any:
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if key in current and current.get(key) not in (None, ""):
                return current.get(key)
            stack.extend(value for value in current.values() if isinstance(value, (dict, list)))
        elif isinstance(current, list):
            stack.extend(value for value in current if isinstance(value, (dict, list)))
    return None


def all_nested_values(obj: Any, keys: set[str]) -> list[Any]:
    values: list[Any] = []
    stack = [obj]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key in keys and value not in (None, ""):
                    values.append(value)
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(value for value in current if isinstance(value, (dict, list)))
    return values


def candidate_codes(row: dict[str, Any]) -> list[str]:
    operation = row.get("operation") if isinstance(row.get("operation"), dict) else {}
    planned_write = row.get("planned_write") if isinstance(row.get("planned_write"), dict) else {}
    payload = planned_write.get("payload") if isinstance(planned_write.get("payload"), dict) else {}
    values: list[Any] = [
        operation.get("code"),
        operation.get("target_identifier"),
        payload.get("rule_code"),
        payload.get("source_code"),
        payload.get("catalog_code"),
        payload.get("canonical_code"),
        payload.get("current_code"),
        payload.get("target_rule_code"),
        payload.get("target_catalog_code"),
        payload.get("procedure_key"),
        payload.get("target_procedure_key"),
    ]
    values.extend(
        all_nested_values(
            row,
            {
                "code",
                "current_code",
                "target_code",
                "target_rule_code",
                "target_catalog_code",
                "canonical_code",
                "catalog_code",
                "source_code",
                "procedure_key",
                "target_procedure_key",
            },
        )
    )
    codes = {normalize_code(value) for value in values if normalize_code(value)}
    expanded = set(codes)
    for code in codes:
        if "->" in code:
            expanded.update(normalize_code(part) for part in code.split("->") if normalize_code(part))
        if "::" in code:
            expanded.add(normalize_code(code.split("::", 1)[1]))
    return sorted(code for code in expanded if code)


def build_compact_candidates(plan_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact_rows: list[dict[str, Any]] = []
    for index, row in enumerate(plan_rows, 1):
        operation = row.get("operation") if isinstance(row.get("operation"), dict) else {}
        refs = row.get("source_refs") if isinstance(row.get("source_refs"), dict) else {}
        planned_write = row.get("planned_write") if isinstance(row.get("planned_write"), dict) else {}
        payload = planned_write.get("payload") if isinstance(planned_write.get("payload"), dict) else {}
        approval = row.get("human_admin_approval") if isinstance(row.get("human_admin_approval"), dict) else {}
        compact_rows.append(
            {
                "index": index,
                "guarded_apply_plan_row_id": row.get("guarded_apply_plan_row_id"),
                "template_row_id": first_nested_key(row, "template_row_id") or "",
                "approved_apply_candidate_id": refs.get("approved_apply_candidate_id"),
                "human_admin_decision_id": refs.get("human_admin_decision_id"),
                "dry_run_diff_id": refs.get("dry_run_diff_id"),
                "operation_type": operation.get("operation_type"),
                "target_identifier": operation.get("target_identifier"),
                "namespace": operation.get("namespace"),
                "code": operation.get("code"),
                "procedure_name": operation.get("procedure_name"),
                "target_runtime": planned_write.get("target_runtime"),
                "write_kind": planned_write.get("write_kind"),
                "write_mode": planned_write.get("write_mode"),
                "affected_provision_count_hint": int_or_zero(first_nested_key(row, "record_count")),
                "candidate_codes": candidate_codes(row),
                "payload": payload,
                "admin_reviewer": approval.get("reviewer"),
                "admin_reviewer_type": approval.get("reviewer_type"),
                "official_source_reference": approval.get("official_source_reference"),
            }
        )
    return compact_rows


def prompt_text() -> str:
    return f"""DGX AGENT TASK — MedGemma Business Impact Historical Provision Sample Fill

AMAÇ
Bu klasördeki 24 staged MedGemma apply candidate için sağlık provizyon sisteminden READ-ONLY geçmiş provizyon verisi çek ve Business Impact / Decision Drift raporu için historical sample dosyasını doldur.

ÇALIŞMA KLASÖRÜ
{OUT_DIR}

KULLANILACAK GİRDİLER
1. candidates_24_compact.json
   - 24 adayın kompakt listesi.
   - Her satırda guarded_apply_plan_row_id, template_row_id, approved_apply_candidate_id, operation_type, code, target_identifier, candidate_codes ve staged payload var.
2. historical_provision_sample_template.csv veya .json
   - Doldurulacak şablon.
3. source_artifacts/shadow_staging_writes/*.json
   - 24 adayın staged shadow write payloadları.
4. OUTPUT_CONTRACT.json
   - Üretilecek dosya sözleşmesi.

ÇIKTI DOSYALARI
- outputs/filled_historical_provision_sample.csv
veya
- outputs/filled_historical_provision_sample.json

Ayrıca run raporu üret:
- outputs/dgx_historical_sample_run_report.json

KATI GÜVENLİK KURALLARI
- Production DB, Qdrant, live runtime, rule engine veya provizyon karar sistemine WRITE yapma.
- Sadece read-only query çalıştır.
- Live apply, promote, runtime override, Qdrant upsert/delete yapma.
- MedGemma kararı üretme; after_decision deterministic/rule-based batch-preview sonucu olmalı.
- Hasta adı, TCKN, telefon, adres, e-posta, açık hasta ID, açık kurum içi kişi ID yazma.
- case_id/provision_id alanını pseudonymous/hashlenmiş yaz. Hash salt/secret değerini output klasörüne yazma.

HER OUTPUT SATIRI İÇİN ZORUNLU ALANLAR
- case_id: pseudonymous historical provision/case id.
- provision_period: örn. 2026-06 veya ilgili dönem.
- sut_code: geçmiş provizyondaki işlem/SUT/kod.
- diagnosis_codes: ICD-10 tanı kodları; CSV'de ; ile ayrılabilir.
- before_decision: mevcut canlı deterministic kuralla karar. İzinli değerler: APPROVE, REJECT, REVIEW.
- after_decision: 24 staged candidate in-memory preview uygulanmış gibi deterministic karar. İzinli değerler: APPROVE, REJECT, REVIEW.
- guarded_apply_plan_row_id: eşleşen aday row id.
- approved_apply_candidate_id: eşleşen candidate id.
- template_row_id: eşleşen source template row id.

ÖNERİLEN OPSİYONEL ALANLAR
- before_overall_status
- after_overall_status
- before_allowed
- after_allowed
- facility_level
- age_band, doğum tarihi değil yaş bandı
- sex, gerekiyorsa
- document_flags, ham belge değil flag listesi
- clinical_evidence_flags, ham klinik not değil flag listesi
- notes

EŞLEŞTİRME STRATEJİSİ
- Her candidate için candidates_24_compact.json içindeki candidate_codes, code ve target_identifier alanlarını kullan.
- preview_create_new_rule: ilgili rule_code/procedure_key/source code ile geçmiş provizyonları bul.
- preview_create_catalog_backfill: source_code/catalog_code/canonical_code ile geçmiş provizyonları bul.
- preview_relink_rule: current_code ve target_rule_code çevresindeki geçmiş provizyonları bul; before mevcut current mapping, after staged target mapping preview sonucu olmalı.

BEFORE / AFTER KARAR TANIMI
- before_decision = mevcut üretim/canlı deterministic rule engine sonucu.
- after_decision = hiçbir canlı write yapmadan, staged candidate payloadları in-memory preview olarak uygulanmış deterministic rule engine sonucu.
- Eğer karar otomatik verilemiyorsa REVIEW kullan.
- Eğer before APPROVE iken after REJECT olursa bu kritik stop point sayılır; doğru hesapla.

ÖNERİLEN ÇALIŞMA SIRASI
1. candidates_24_compact.json dosyasını oku.
2. Her candidate için geçmiş provizyon sisteminden read-only matching kayıtları çek.
3. Ham PHI alanlarını at; sadece pseudonymous case_id ve gerekli karar/klinik flag alanlarını tut.
4. Mevcut deterministic sonucu before_* alanlarına yaz.
5. Staged candidate in-memory preview sonucunu after_* alanlarına yaz.
6. outputs/filled_historical_provision_sample.csv veya .json üret.
7. outputs/dgx_historical_sample_run_report.json üret.

RUN REPORT İÇERİĞİ
outputs/dgx_historical_sample_run_report.json içinde şunlar olsun:
- schema_version: dgx_historical_sample_run_report.v1
- generated_at
- source_system_read_only: true
- writes_to_production_db: false
- writes_to_qdrant: false
- live_runtime_override: false
- auto_apply: false
- candidates_seen: 24
- candidates_with_rows
- total_output_rows
- per_candidate_counts
- before_after_transition_counts
- phi_redaction_confirmed: true
- errors
- warnings

BU GÖREVİN BAŞARI KRİTERİ
- 24 candidate okunmuş olmalı.
- En azından matching geçmiş provizyonu olan candidate'lar için output row üretilmeli.
- Output şema OUTPUT_CONTRACT.json ile uyumlu olmalı.
- PHI içermemeli.
- Tüm kaynak erişimleri read-only olmalı.
- Canlı sistemde hiçbir write/apply yapılmamış olmalı.
"""


def main() -> int:
    source_artifacts_dir = OUT_DIR / "source_artifacts"
    outputs_dir = OUT_DIR / "outputs"
    reference_scripts_dir = OUT_DIR / "reference_scripts"
    for directory in (OUT_DIR, source_artifacts_dir, outputs_dir, reference_scripts_dir):
        directory.mkdir(parents=True, exist_ok=True)

    plan_rows = read_json(SOURCE_PLAN)
    compact_rows = build_compact_candidates(plan_rows)
    write_json(OUT_DIR / "candidates_24_compact.json", compact_rows)
    compact_fields = [
        "index",
        "guarded_apply_plan_row_id",
        "template_row_id",
        "approved_apply_candidate_id",
        "operation_type",
        "target_identifier",
        "namespace",
        "code",
        "procedure_name",
        "target_runtime",
        "write_kind",
        "write_mode",
        "affected_provision_count_hint",
        "candidate_codes",
        "admin_reviewer",
        "official_source_reference",
    ]
    write_csv(OUT_DIR / "candidates_24_compact.csv", compact_rows, compact_fields)

    shutil.copy2(SOURCE_PLAN, source_artifacts_dir / "medgemma_final_guarded_apply_plan_rows_FULL_SOURCE.json")
    shutil.copy2(SOURCE_BUSINESS_DIR / "historical_provision_sample_template.json", OUT_DIR / "historical_provision_sample_template.json")
    shutil.copy2(SOURCE_BUSINESS_DIR / "historical_provision_sample_template.csv", OUT_DIR / "historical_provision_sample_template.csv")
    for name in ("business_impact_report.json", "impact_heatmap.json", "decision_drift_matrix.json", "rollback_manifest.json"):
        source = SOURCE_BUSINESS_DIR / name
        if source.exists():
            shutil.copy2(source, source_artifacts_dir / name)
    if SOURCE_STAGING_DIR.exists():
        target_staging = source_artifacts_dir / "shadow_staging_writes"
        if target_staging.exists():
            shutil.rmtree(target_staging)
        shutil.copytree(SOURCE_STAGING_DIR, target_staging)
    if SOURCE_SCRIPT.exists():
        shutil.copy2(SOURCE_SCRIPT, reference_scripts_dir / SOURCE_SCRIPT.name)

    output_contract = {
        "schema_version": "dgx_historical_business_impact_output_contract.v1",
        "required_output_files": [
            "outputs/filled_historical_provision_sample.csv or outputs/filled_historical_provision_sample.json",
            "outputs/dgx_historical_sample_run_report.json",
        ],
        "required_columns": [
            "case_id",
            "provision_period",
            "sut_code",
            "diagnosis_codes",
            "before_decision",
            "after_decision",
            "guarded_apply_plan_row_id",
            "approved_apply_candidate_id",
            "template_row_id",
        ],
        "allowed_decisions": ["APPROVE", "REJECT", "REVIEW"],
        "optional_columns": [
            "before_overall_status",
            "after_overall_status",
            "before_allowed",
            "after_allowed",
            "facility_level",
            "age_band",
            "sex",
            "document_flags",
            "clinical_evidence_flags",
            "notes",
        ],
        "privacy_rules": {
            "no_patient_name": True,
            "no_tc_identity_number": True,
            "no_phone_address_email": True,
            "case_id_must_be_pseudonymous": True,
            "do_not_write_hash_salt_or_secret": True,
        },
        "safety_rules": {
            "read_only_source_queries": True,
            "writes_to_production_db": False,
            "writes_to_qdrant": False,
            "live_runtime_override": False,
            "auto_apply": False,
        },
    }
    write_json(OUT_DIR / "OUTPUT_CONTRACT.json", output_contract)
    (OUT_DIR / "DGX_AGENT_PROMPT_COPY_PASTE.txt").write_text(prompt_text(), encoding="utf-8")
    (OUT_DIR / "RUN_AFTER_DGX_RETURN.txt").write_text(
        "After DGX returns outputs/filled_historical_provision_sample.csv or .json, run locally:\n\n"
        "python C:\\Projects\\ADDQ\\SUT\\diagnosis_rules\\shadow_medgemma_final_business_impact_analysis.py --historical-sample-rows-path <PATH_TO_FILLED_SAMPLE>\n",
        encoding="utf-8",
    )
    (outputs_dir / "PUT_FILLED_HISTORICAL_SAMPLE_HERE.txt").write_text(
        "DGX agent should write filled_historical_provision_sample.csv or .json in this directory.\n",
        encoding="utf-8",
    )

    template_rows = read_json(OUT_DIR / "historical_provision_sample_template.json").get("rows", [])
    files = sorted(str(path.relative_to(OUT_DIR)) for path in OUT_DIR.rglob("*") if path.is_file())
    manifest = {
        "schema_version": "dgx_handoff_medgemma_business_impact_historical_sample_task.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "task_folder": str(OUT_DIR),
        "purpose": "DGX read-only historical provision extraction and deterministic before/after batch-preview sample generation for 24 staged MedGemma candidates.",
        "counts": {
            "candidates": len(compact_rows),
            "historical_template_rows": len(template_rows),
            "source_staging_files": len(list((source_artifacts_dir / "shadow_staging_writes").glob("*.json"))),
        },
        "files": files,
        "safety": output_contract["safety_rules"],
    }
    write_json(OUT_DIR / "TASK_MANIFEST.json", manifest)

    checksums = []
    for path in sorted(OUT_DIR.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(OUT_DIR).as_posix()}")
    (OUT_DIR / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    print(json.dumps({"task_folder": str(OUT_DIR), "counts": manifest["counts"], "files": len(files) + 1}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
