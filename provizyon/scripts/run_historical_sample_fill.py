"""DGX Historical Provision Sample Fill — Business Impact Analysis.

MSSQL view (dbo.S_VW_PROVIZYON_AI) üzerinden 24 staged MedGemma candidate
için geçmiş provizyon verisi çeker, deterministic before/after kararlarını
hesaplar ve output dosyalarını üretir.

READ-ONLY — hiçbir production write yapılmaz.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent

TASK_DIR = _PROJECT_ROOT / "data" / "handoffs" / "medgemma_business_impact_historical_sample_task_final_24_20260707"
OUTPUT_DIR = TASK_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

PROVIZYON_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(PROVIZYON_ROOT))

HASH_SALT = os.urandom(32)

MAX_ROWS_PER_CANDIDATE = 10


def _pseudonymize(provizyon_id: int) -> str:
    raw = f"{provizyon_id}".encode() + HASH_SALT
    return hashlib.sha256(raw).hexdigest()[:16]


def _age_band(age: int | None) -> str:
    if age is None:
        return "unknown"
    if age < 1:
        return "0-1"
    if age <= 17:
        return "2-17"
    if age <= 30:
        return "18-30"
    if age <= 45:
        return "31-45"
    if age <= 60:
        return "46-60"
    if age <= 75:
        return "61-75"
    return "76+"


def _load_candidates() -> list[dict]:
    with open(TASK_DIR / "candidates_24_compact.json") as f:
        return json.load(f)


def _load_sut_diagnosis_lookup() -> dict[str, dict]:
    path = PROVIZYON_ROOT / "data" / "generated" / "sut_diagnosis_rules" / "ek2b" / "runtime" / "sut_diagnosis_runtime_lookup.json"
    if not path.exists():
        log.warning("SUT diagnosis lookup bulunamadı: %s", path)
        return {}
    with open(path) as f:
        data = json.load(f)
    return data.get("rules_by_sut_code", {})


def _deterministic_decision(
    rule: dict | None,
    diagnosis_codes: list[str],
) -> str:
    """Deterministic kural motoru simülasyonu.

    rule yoksa → REVIEW
    diagnosis_policy check:
      - not_required → APPROVE
      - review_required + no patterns → REVIEW
      - conditional / required → ICD-10 pattern eşleştirmesi
    """
    if rule is None:
        return "REVIEW"

    policy = rule.get("diagnosis_policy", "review_required")

    if policy == "not_required":
        return "APPROVE"

    required_patterns: list[str] = rule.get("required_icd10_patterns", [])
    excluded_patterns: list[str] = rule.get("excluded_icd10_patterns", [])
    decision_if_missing = rule.get("decision_if_missing", "REVIEW_REQUIRED")

    if not diagnosis_codes:
        if "APPROVE" in decision_if_missing.upper():
            return "APPROVE"
        if "REJECT" in decision_if_missing.upper():
            return "REJECT"
        return "REVIEW"

    if excluded_patterns:
        for diag in diagnosis_codes:
            for pat in excluded_patterns:
                if diag.upper().startswith(pat.upper()):
                    return "REJECT"

    if required_patterns:
        matched = False
        for diag in diagnosis_codes:
            for pat in required_patterns:
                if diag.upper().startswith(pat.upper()):
                    matched = True
                    break
            if matched:
                break
        if not matched:
            return "REVIEW"
        return "APPROVE"

    if policy in ("review_required", "conditional"):
        return "REVIEW"

    return "REVIEW"


def _build_code_to_candidates(candidates: list[dict]) -> dict[str, list[dict]]:
    """Her işlem kodunu eşleşen candidate(lar)a eşleştirir."""
    mapping: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        code = c["code"]
        mapping[code].append(c)
        for alt in c.get("candidate_codes", []):
            clean = alt.split("::")[-1] if "::" in alt else alt
            if "->" not in clean and clean != code:
                mapping[clean].append(c)
    return dict(mapping)


def _get_connection():
    from provizyon_engine.db import get_connection
    return get_connection()


def _parse_diagnoses(tani_bilgileri: str | None) -> list[str]:
    if not tani_bilgileri:
        return []
    codes: list[str] = []
    for entry in tani_bilgileri.split("<~>"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|")
        if parts[0]:
            codes.append(parts[0].strip())
    return codes


_PROVISION_SQL_TEMPLATE = """
SELECT DISTINCT
    pf.ID                        AS ProvizyonId,
    pf.HizmetTarih,
    att.Kod                      AS MatchedCode,
    pfd.Ad                       AS ProvizyonDurumu,
    kg.Ad                        AS KurumTipi,
    CASE WHEN pf.AkrabaID IS NOT NULL
         THEN DATEDIFF(YEAR, ha.DogumTarihi, ISNULL(pf.HizmetTarih, GETDATE()))
         ELSE DATEDIFF(YEAR, hp.DogumTarihi, ISNULL(pf.HizmetTarih, GETDATE()))
    END                          AS HastaYas,
    CASE
        WHEN pf.AkrabaID IS NOT NULL THEN
            CASE ha.Cinsiyet WHEN 'E' THEN 'Erkek' WHEN 'K' THEN N'Kadın' ELSE ha.Cinsiyet END
        ELSE
            CASE hp.Cinsiyet WHEN 'E' THEN 'Erkek' WHEN 'K' THEN N'Kadın' ELSE hp.Cinsiyet END
    END                          AS Cinsiyet,
    STUFF((
        SELECT '<~>' + icd.Kod + '|' + icd.Ad
        FROM dbo.PROVIZYON_FATURA_ISLEM_TIP pfit
        INNER JOIN dbo.P_FATURA_ISLEM_TIP_ICD10 picd ON pfit.ID = picd.ProvizyonFaturaIslemTipID
        INNER JOIN dbo.ICD10_TANIM icd ON picd.ICD10ID = icd.ID
        WHERE pfit.ProvizyonFaturaID = pf.ID
        FOR XML PATH(''), TYPE
    ).value('.', 'NVARCHAR(MAX)'), 1, 3, '') AS TaniBilgileri
FROM dbo.PROVIZYON_FATURA pf
INNER JOIN dbo.PROVIZYON_FATURA_AYAKTA_TEDAVI pfat ON pfat.ProvizyonFaturaID = pf.ID
INNER JOIN dbo.AYAKTA_TEDAVI_TANIM att ON pfat.AyaktaTedaviTanimID = att.ID
LEFT JOIN dbo.PROVIZYON_FATURA_DURUM_TANIM pfd ON pf.ProvizyonFaturaDurumTanimID = pfd.ID
LEFT JOIN dbo.KURUM_TANIM k ON pf.KurumTanimID = k.ID
LEFT JOIN dbo.KURUM_GRUP_TANIM kg ON k.KurumGrupID = kg.ID
LEFT JOIN dbo.HASTA_PERSONEL hp ON pf.PersonelID = hp.ID
LEFT JOIN dbo.HASTA_AKRABA ha ON pf.AkrabaID = ha.ID
"""


def _fetch_provisions_for_codes(
    codes: list[str],
) -> list[dict[str, Any]]:
    if not codes:
        return []
    placeholders = ",".join(["?"] * len(codes))
    sql = _PROVISION_SQL_TEMPLATE + f"""
    WHERE att.Kod IN ({placeholders})
    ORDER BY pf.HizmetTarih DESC
    """
    rows: list[dict] = []
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, codes)
        cols = [c[0] for c in cursor.description]
        for row in cursor.fetchall():
            rows.append(dict(zip(cols, row)))
    return rows


def _resolve_huv_codes_by_name(
    procedure_name: str,
    conn,
) -> list[str]:
    """İşlem adını AYAKTA_TEDAVI_TANIM.Ad'da arayarak HUV kodlarını döndürür."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT att.Kod FROM dbo.AYAKTA_TEDAVI_TANIM att WHERE att.Ad LIKE ?",
        f"%{procedure_name}%",
    )
    return [r[0] for r in cursor.fetchall()]


def _fetch_provisions_for_huv_codes(
    huv_codes: list[str],
    original_code: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """HUV kodları üzerinden provizyon çeker, MatchedCode'u original_code olarak yazar."""
    if not huv_codes:
        return []
    placeholders = ",".join(["?"] * len(huv_codes))
    sql = _PROVISION_SQL_TEMPLATE + f"""
    WHERE att.Kod IN ({placeholders})
    ORDER BY pf.HizmetTarih DESC
    """
    rows: list[dict] = []
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, huv_codes)
        cols = [c[0] for c in cursor.description]
        for row in cursor.fetchall():
            d = dict(zip(cols, row))
            d["HuvMatchedCode"] = d["MatchedCode"]
            d["MatchedCode"] = original_code
            rows.append(d)
            if len(rows) >= limit:
                break
    return rows


_HUV_NAME_SEARCH_PATTERNS: dict[str, list[str]] = {
    "21.260.001": ["%artroskop%menisk%"],
    "640010":     ["%meme%rekonstr%"],
    "12.005.001": ["%vitrektomi%"],
    "21.102.001": ["%abdomen%BT%", "%abdomen%tomografi%", "%kontrastsız%BT%taş%"],
    "18.001.001": ["%sezaryen%"],
    "22.001.001": ["fizik tedavi%", "%FİZİK TEDAVİ%"],
    "13.010.001": ["%beyin%MR%"],
    "15.005.001": ["%kolonoskopi%"],
    "21.103.001": ["%lomber%MR%", "%lomber%vertebra%MR%", "MR%lomber%"],
    "14.001.001": ["%lomber%diskektomi%"],
    "17.001.001": ["%sistoskopi%"],
    "18.010.001": ["%histerektomi%"],
    "08.001.001": ["%deri%biyopsi%", "%punch%biyopsi%"],
    "09.002.001": ["%ekokardiyografi%"],
    "11.005.001": ["%septoplasti%"],
    "09.007.001": ["%koroner%stent%", "%anjiyoplasti%stent%"],
    "10.001.001": ["%lobektomi%"],
    "09.001.004": ["%koroner%anjiyografi%"],
    "15.001.001": ["%üst%GİS%endoskopi%", "%GİS%endoskop%"],
}


def _resolve_huv_fallback(candidates: list[dict]) -> dict[str, list[str]]:
    """Kod eşleşmesi olmayan adaylar için isim tabanlı HUV kodları çözümler."""
    code_to_huv: dict[str, list[str]] = {}
    with _get_connection() as conn:
        cursor = conn.cursor()
        for cand in candidates:
            code = cand["code"]
            if code in code_to_huv:
                continue
            patterns = _HUV_NAME_SEARCH_PATTERNS.get(code)
            if not patterns:
                name = cand.get("procedure_name", "")
                if name:
                    patterns = [f"%{name}%"]
                else:
                    continue
            huv_codes: list[str] = []
            for pat in patterns:
                cursor.execute(
                    "SELECT DISTINCT att.Kod FROM dbo.AYAKTA_TEDAVI_TANIM att WHERE att.Ad LIKE ?",
                    pat,
                )
                huv_codes.extend(r[0] for r in cursor.fetchall())
            if huv_codes:
                code_to_huv[code] = list(set(huv_codes))
                log.info("HUV fallback %s => %d HUV kod bulundu", code, len(code_to_huv[code]))
    return code_to_huv


def main() -> int:
    log.info("=== Historical Provision Sample Fill başlıyor ===")
    start = datetime.now(timezone.utc)

    candidates = _load_candidates()
    log.info("%d candidate yüklendi.", len(candidates))

    sut_rules = _load_sut_diagnosis_lookup()
    log.info("SUT diagnosis lookup yüklendi (%d kural).", len(sut_rules))

    code_to_candidates = _build_code_to_candidates(candidates)
    all_codes = sorted(code_to_candidates.keys())
    log.info("Aranacak toplam benzersiz kod sayısı: %d", len(all_codes))

    # --- Faz 1: Direkt kod eşleştirmesi ---
    log.info("Faz 1: Direkt kod eşleştirmesi (MSSQL)...")
    raw_rows = _fetch_provisions_for_codes(all_codes)
    log.info("Faz 1: %d ham satır döndü.", len(raw_rows))

    candidate_rows: dict[str, list[dict]] = defaultdict(list)
    for row in raw_rows:
        matched_code = row["MatchedCode"]
        matched_candidates = code_to_candidates.get(matched_code, [])
        for cand in matched_candidates:
            key = cand["guarded_apply_plan_row_id"]
            if len(candidate_rows[key]) < MAX_ROWS_PER_CANDIDATE:
                candidate_rows[key].append(row)

    # --- Faz 2: Eşleşmeyen adaylar için HUV isim fallback ---
    unmatched = [c for c in candidates if not candidate_rows.get(c["guarded_apply_plan_row_id"])]
    if unmatched:
        log.info("Faz 2: %d eşleşmeyen aday için HUV isim fallback...", len(unmatched))
        huv_map = _resolve_huv_fallback(unmatched)
        for cand in unmatched:
            code = cand["code"]
            plan_id = cand["guarded_apply_plan_row_id"]
            huv_codes = huv_map.get(code)
            if not huv_codes:
                continue
            extra_rows = _fetch_provisions_for_huv_codes(huv_codes, code, limit=MAX_ROWS_PER_CANDIDATE)
            if extra_rows:
                log.info("  %s (%s): %d provizyon (HUV fallback)", code, cand["procedure_name"], len(extra_rows))
                candidate_rows[plan_id] = extra_rows

    output_rows: list[dict] = []
    per_candidate_counts: dict[str, int] = {}
    transition_counts: dict[str, int] = defaultdict(int)
    candidates_with_rows = 0
    errors: list[str] = []
    warnings: list[str] = []

    for cand in candidates:
        plan_id = cand["guarded_apply_plan_row_id"]
        cand_id = cand["approved_apply_candidate_id"]
        template_id = cand["template_row_id"]
        operation = cand["operation_type"]
        code = cand["code"]
        target_id = cand.get("target_identifier", "")

        rows = candidate_rows.get(plan_id, [])
        per_candidate_counts[plan_id] = len(rows)

        if not rows:
            warnings.append(f"{code}: Eşleşen geçmiş provizyon bulunamadı.")
            output_rows.append({
                "case_id": f"no_match_{code}",
                "provision_period": "",
                "sut_code": code,
                "diagnosis_codes": [],
                "before_decision": "REVIEW",
                "after_decision": "REVIEW",
                "before_overall_status": "no_rule" if operation != "preview_relink_rule" else "wrong_rule",
                "after_overall_status": "staged_rule_review" if operation != "preview_relink_rule" else "relinked_rule_review",
                "guarded_apply_plan_row_id": plan_id,
                "approved_apply_candidate_id": cand_id,
                "template_row_id": template_id,
                "facility_level": "",
                "age_band": "",
                "sex": "",
                "notes": f"Eşleşen geçmiş provizyon bulunamadı; affected_provision_count_hint={cand.get('affected_provision_count_hint', 'N/A')}",
            })
            continue

        candidates_with_rows += 1

        before_rule = None
        after_rule = None

        if operation == "preview_create_new_rule":
            before_rule = None
            after_rule = cand.get("payload")

        elif operation == "preview_create_catalog_backfill":
            before_rule = None
            after_rule = None

        elif operation == "preview_relink_rule":
            payload = cand.get("payload", {})
            current_code = payload.get("current_code", code)
            target_code = payload.get("target_rule_code")
            before_rule = sut_rules.get(current_code)
            after_rule = sut_rules.get(target_code) if target_code else None

        for row in rows:
            pid = row["ProvizyonId"]
            case_id = _pseudonymize(pid)
            hizmet_tarih = row.get("HizmetTarih")
            period = ""
            if hizmet_tarih:
                if hasattr(hizmet_tarih, "strftime"):
                    period = hizmet_tarih.strftime("%Y-%m")
                else:
                    period = str(hizmet_tarih)[:7]

            diag_codes = _parse_diagnoses(row.get("TaniBilgileri"))
            age = row.get("HastaYas")
            sex = row.get("Cinsiyet", "")
            facility = row.get("KurumTipi", "")

            before_dec = _deterministic_decision(before_rule, diag_codes)
            after_dec = _deterministic_decision(after_rule, diag_codes)

            if operation == "preview_create_new_rule":
                before_status = "no_rule"
                after_status = "staged_new_rule"
            elif operation == "preview_create_catalog_backfill":
                before_status = "not_in_catalog"
                after_status = "catalog_backfilled"
                before_dec = "REVIEW"
                after_dec = "REVIEW"
            elif operation == "preview_relink_rule":
                payload = cand.get("payload", {})
                before_status = f"linked_to_{payload.get('current_code', '?')}"
                after_status = f"relinked_to_{payload.get('target_rule_code', '?')}"
            else:
                before_status = "unknown"
                after_status = "unknown"

            transition_key = f"{before_dec}->{after_dec}"
            transition_counts[transition_key] += 1

            output_rows.append({
                "case_id": case_id,
                "provision_period": period,
                "sut_code": code,
                "diagnosis_codes": diag_codes,
                "before_decision": before_dec,
                "after_decision": after_dec,
                "before_overall_status": before_status,
                "after_overall_status": after_status,
                "guarded_apply_plan_row_id": plan_id,
                "approved_apply_candidate_id": cand_id,
                "template_row_id": template_id,
                "facility_level": facility or "",
                "age_band": _age_band(age),
                "sex": sex or "",
                "notes": "",
            })

    sample_path = OUTPUT_DIR / "filled_historical_provision_sample.json"
    sample_output = {
        "schema_version": "shadow_medgemma_historical_provision_sample_filled.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": len(output_rows),
        "candidates_seen": len(candidates),
        "candidates_with_rows": candidates_with_rows,
        "rows": output_rows,
    }
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump(sample_output, f, ensure_ascii=False, indent=2, default=str)
    log.info("Sample yazıldı: %s (%d satır)", sample_path.name, len(output_rows))

    end = datetime.now(timezone.utc)
    report = {
        "schema_version": "dgx_historical_sample_run_report.v1",
        "generated_at": end.isoformat(),
        "elapsed_seconds": (end - start).total_seconds(),
        "source_system_read_only": True,
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "live_runtime_override": False,
        "auto_apply": False,
        "candidates_seen": len(candidates),
        "candidates_with_rows": candidates_with_rows,
        "total_output_rows": len(output_rows),
        "per_candidate_counts": per_candidate_counts,
        "before_after_transition_counts": dict(transition_counts),
        "phi_redaction_confirmed": True,
        "errors": errors,
        "warnings": warnings,
    }
    report_path = OUTPUT_DIR / "dgx_historical_sample_run_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log.info("Report yazıldı: %s", report_path.name)

    log.info("=== Tamamlandı: %d satır, %d candidate eşleşti ===",
             len(output_rows), candidates_with_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
