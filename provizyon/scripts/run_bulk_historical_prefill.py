"""2.36M Bulk Rule-Engine Test.

Paginated read-only extraction from S_VW_PROVIZYON_AI (~2.36M rows).
Each provision's procedure codes are evaluated against the deterministic
HUV and SUT diagnosis rule lookups. No MedGemma calls. No writes to
production DB, Qdrant, or runtime.

Outputs:
    rule_engine_results.csv   — one row per provision with overall decision
    rule_engine_summary.json  — aggregate status distributions

Usage:
    python -m scripts.run_bulk_historical_prefill [--page-size 50000] [--resume]
"""
from __future__ import annotations

import csv
import json
import logging
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent

TASK_DIR = _PROJECT_ROOT / "data" / "handoffs" / "bulk_historical_prefill_3_2m_task"
OUTPUT_DIR = TASK_DIR / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)

PROVIZYON_ROOT = _SCRIPT_DIR.parent
LIB_ROOT = PROVIZYON_ROOT / "lib"
sys.path.insert(0, str(PROVIZYON_ROOT))
sys.path.insert(0, str(LIB_ROOT))

from provizyon_engine.deidentify import init_salt, pseudonymize

from diagnosis_rules.provision_diagnosis_checker import (
    evaluate_huv_diagnoses,
    load_runtime_lookup as load_huv_lookup,
)
from diagnosis_rules.sut_provision_diagnosis_checker import (
    evaluate_sut_diagnoses,
    load_runtime_lookup as load_sut_lookup,
)

PAGE_SIZE_DEFAULT = 50000
CHECKPOINT_PATH = OUTPUT_DIR / "checkpoint.json"

_HUV_CODE_RE = re.compile(r"^\d{2}\.\d")
_SUT_CODE_RE = re.compile(r"^\d{6}$")
_TZH_CODE_RE = re.compile(r"^TZH\.", re.IGNORECASE)

# ── SQL ──────────────────────────────────────────────────────────────────

_COUNT_SQL = """
SELECT COUNT(*) AS total_rows,
       MIN(ProvizyonId) AS min_id,
       MAX(ProvizyonId) AS max_id
FROM dbo.S_VW_PROVIZYON_AI
"""

_PAGE_SQL = """
SELECT TOP (?)
    v.ProvizyonId,
    v.HastaYas,
    v.Cinsiyet,
    v.ProvizyonDurumu,
    v.TaniBilgileri,
    v.IslemBilgileri
FROM dbo.S_VW_PROVIZYON_AI v
WHERE v.ProvizyonId > ?
ORDER BY v.ProvizyonId ASC
"""


# ── Parsers ──────────────────────────────────────────────────────────────

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


def _parse_procedures(islem_bilgileri: str | None) -> list[dict[str, str]]:
    if not islem_bilgileri:
        return []
    procs: list[dict[str, str]] = []
    for entry in islem_bilgileri.split("<~>"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|")
        if len(parts) < 2:
            continue
        code = parts[0].strip()
        liste_tip = parts[2].strip() if len(parts) >= 3 else ""
        if liste_tip == "HUV":
            code_type = "HUV"
        elif "SUT" in liste_tip:
            code_type = "SUT"
        elif _SUT_CODE_RE.match(code) and not code.startswith("0"):
            code_type = "SUT"
        elif _HUV_CODE_RE.match(code) or _TZH_CODE_RE.match(code):
            code_type = "HUV"
        else:
            code_type = "unknown"
        procs.append({"code": code, "code_type": code_type})
    return procs


def _sex_value(cinsiyet: str | None) -> str | None:
    if not cinsiyet:
        return None
    c = cinsiyet.strip().upper()
    if c in ("E", "ERKEK"):
        return "erkek"
    if c in ("K", "KADIN"):
        return "kadin"
    return None


# ── Rule evaluation per provision ────────────────────────────────────────

def _evaluate_provision(
    procs: list[dict[str, str]],
    diag_codes: list[str],
    huv_lookup: dict[str, Any],
    sut_lookup: dict[str, Any],
    age: int | None,
    sex: str | None,
) -> dict[str, Any]:
    """Evaluate all procedure codes for a single provision."""

    huv_codes = [p["code"] for p in procs if p["code_type"] == "HUV" and not _TZH_CODE_RE.match(p["code"])]
    sut_codes = [p["code"] for p in procs if p["code_type"] == "SUT"]
    tzh_codes = [p["code"] for p in procs if _TZH_CODE_RE.match(p["code"])]
    unk_codes = [p["code"] for p in procs if p["code_type"] == "unknown"]

    huv_codes = list(dict.fromkeys(huv_codes))
    sut_codes = list(dict.fromkeys(sut_codes))

    statuses: list[str] = []
    fail_codes: list[str] = []
    review_codes: list[str] = []
    pass_codes: list[str] = []
    no_rule_codes: list[str] = []

    for code in huv_codes:
        result = evaluate_huv_diagnoses(huv_lookup, code, diag_codes)
        status = result.get("status", "unknown")
        allowed = result.get("allowed")
        statuses.append(status)
        if allowed is False:
            fail_codes.append(code)
        elif allowed is True:
            pass_codes.append(code)
        elif status == "unknown_huv":
            no_rule_codes.append(code)
        else:
            review_codes.append(code)

    for code in sut_codes:
        result = evaluate_sut_diagnoses(
            sut_lookup, code, diag_codes, age=age, sex=sex,
        )
        status = result.get("status", "unknown")
        allowed = result.get("allowed")
        statuses.append(status)
        if allowed is False:
            fail_codes.append(code)
        elif allowed is True:
            pass_codes.append(code)
        elif status == "unknown_sut":
            no_rule_codes.append(code)
        else:
            review_codes.append(code)

    if fail_codes:
        overall = "FAIL"
    elif review_codes:
        overall = "REVIEW"
    elif pass_codes:
        overall = "PASS"
    elif no_rule_codes:
        overall = "NO_RULE"
    elif not procs:
        overall = "NO_PROCEDURE"
    else:
        overall = "SKIPPED"

    return {
        "overall": overall,
        "fail_codes": fail_codes,
        "review_codes": review_codes,
        "pass_codes": pass_codes,
        "no_rule_codes": no_rule_codes,
        "tzh_codes": tzh_codes,
        "unk_codes": unk_codes,
        "huv_evaluated": len(huv_codes),
        "sut_evaluated": len(sut_codes),
    }


# ── Connection ───────────────────────────────────────────────────────────

def _get_connection():
    from provizyon_engine.db import get_connection
    return get_connection()


# ── Checkpoint ───────────────────────────────────────────────────────────

def _save_checkpoint(last_id: int, rows_so_far: int, page_num: int) -> None:
    data = {
        "last_provizyon_id": last_id,
        "rows_processed": rows_so_far,
        "page_number": page_num,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    CHECKPOINT_PATH.write_text(json.dumps(data, indent=2))


def _load_checkpoint() -> dict | None:
    if CHECKPOINT_PATH.exists():
        return json.loads(CHECKPOINT_PATH.read_text())
    return None


# ── Main ─────────────────────────────────────────────────────────────────

def main(page_size: int = PAGE_SIZE_DEFAULT, resume: bool = False) -> int:
    log.info("=== 2.36M Bulk Rule-Engine Test başlıyor ===")
    start = time.monotonic()

    salt = init_salt()

    huv_lookup = load_huv_lookup()
    huv_rule_count = len(huv_lookup.get("rules_by_huv_code", {}))
    log.info("HUV tanı kuralları yüklendi: %d kural", huv_rule_count)

    sut_lookup = load_sut_lookup()
    sut_rule_count = len(sut_lookup.get("rules_by_sut_code", {}))
    log.info("SUT tanı kuralları yüklendi: %d kural", sut_rule_count)

    # ── Counters ─────────────────────────────────────────────────────────
    total_rows = 0
    overall_counter: Counter[str] = Counter()
    status_counter: Counter[str] = Counter()
    huv_evaluated_total = 0
    sut_evaluated_total = 0
    total_fail_codes = 0
    total_review_codes = 0
    total_pass_codes = 0
    total_no_rule_codes = 0

    # ── Resume from checkpoint? ──────────────────────────────────────────
    last_id = 0
    page_num = 0
    if resume:
        cp = _load_checkpoint()
        if cp:
            last_id = cp["last_provizyon_id"]
            total_rows = cp["rows_processed"]
            page_num = cp["page_number"]
            log.info("Checkpoint'ten devam: last_id=%d, rows=%d, page=%d",
                     last_id, total_rows, page_num)

    # ── Get total count ──────────────────────────────────────────────────
    with _get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(_COUNT_SQL)
        count_row = cursor.fetchone()
        source_total = count_row[0]
        source_min_id = count_row[1]
        source_max_id = count_row[2]
    log.info("Kaynak: %d satır, ID aralığı [%s, %s]",
             source_total, source_min_id, source_max_id)

    # ── Open CSV writer ──────────────────────────────────────────────────
    csv_path = OUTPUT_DIR / "rule_engine_results.csv"
    csv_fields = [
        "case_id", "overall_decision", "diagnoses",
        "huv_evaluated", "sut_evaluated",
        "fail_codes", "review_codes", "pass_codes", "no_rule_codes",
        "tzh_codes", "unknown_codes",
    ]

    write_header = not resume or not csv_path.exists()
    csv_file = open(csv_path, "a" if resume else "w", newline="", encoding="utf-8")
    csv_writer = csv.DictWriter(csv_file, fieldnames=csv_fields)
    if write_header:
        csv_writer.writeheader()

    # ── Paginated extraction + evaluation ────────────────────────────────
    page_fetch_start = time.monotonic()

    try:
        while True:
            page_num += 1
            with _get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(_PAGE_SQL, (page_size, last_id))
                cols = [c[0] for c in cursor.description]
                rows = [dict(zip(cols, r)) for r in cursor.fetchall()]

            if not rows:
                log.info("Sayfa %d: boş — extraction tamamlandı.", page_num)
                break

            for row in rows:
                total_rows += 1
                pid = row["ProvizyonId"]
                last_id = pid

                case_id = pseudonymize(pid, salt=salt)

                procs = _parse_procedures(row.get("IslemBilgileri"))
                diag_codes = _parse_diagnoses(row.get("TaniBilgileri"))

                age = int(row["HastaYas"]) if row.get("HastaYas") is not None else None
                sex = _sex_value(row.get("Cinsiyet"))

                result = _evaluate_provision(
                    procs, diag_codes, huv_lookup, sut_lookup, age, sex,
                )

                overall = result["overall"]
                overall_counter[overall] += 1
                huv_evaluated_total += result["huv_evaluated"]
                sut_evaluated_total += result["sut_evaluated"]
                total_fail_codes += len(result["fail_codes"])
                total_review_codes += len(result["review_codes"])
                total_pass_codes += len(result["pass_codes"])
                total_no_rule_codes += len(result["no_rule_codes"])

                csv_writer.writerow({
                    "case_id": case_id,
                    "overall_decision": overall,
                    "diagnoses": ";".join(diag_codes),
                    "huv_evaluated": result["huv_evaluated"],
                    "sut_evaluated": result["sut_evaluated"],
                    "fail_codes": ";".join(result["fail_codes"]),
                    "review_codes": ";".join(result["review_codes"]),
                    "pass_codes": ";".join(result["pass_codes"]),
                    "no_rule_codes": ";".join(result["no_rule_codes"]),
                    "tzh_codes": ";".join(result["tzh_codes"]),
                    "unknown_codes": ";".join(result["unk_codes"]),
                })

            elapsed_page = time.monotonic() - page_fetch_start
            rows_per_sec = total_rows / elapsed_page if elapsed_page > 0 else 0
            pct = (total_rows / source_total * 100) if source_total > 0 else 0
            log.info(
                "Sayfa %d: +%d satır (toplam %d / %d = %.1f%%, %.0f satır/sn) | "
                "PASS=%d FAIL=%d REVIEW=%d NO_RULE=%d",
                page_num, len(rows), total_rows, source_total, pct, rows_per_sec,
                overall_counter["PASS"], overall_counter["FAIL"],
                overall_counter["REVIEW"], overall_counter["NO_RULE"],
            )

            _save_checkpoint(last_id, total_rows, page_num)
            csv_file.flush()

            if len(rows) < page_size:
                log.info("Son sayfa (kısmi) — extraction tamamlandı.")
                break
    finally:
        csv_file.close()

    elapsed_total = time.monotonic() - start

    # ── Summary report ───────────────────────────────────────────────────
    summary = {
        "schema_version": "rule_engine_test_summary.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(elapsed_total, 2),
        "source_total_rows": source_total,
        "rows_processed": total_rows,
        "pages_fetched": page_num,
        "page_size": page_size,
        "rule_lookups": {
            "huv_rules_loaded": huv_rule_count,
            "sut_rules_loaded": sut_rule_count,
        },
        "overall_decision_distribution": dict(overall_counter),
        "procedure_evaluation_totals": {
            "huv_codes_evaluated": huv_evaluated_total,
            "sut_codes_evaluated": sut_evaluated_total,
            "total_fail_code_hits": total_fail_codes,
            "total_review_code_hits": total_review_codes,
            "total_pass_code_hits": total_pass_codes,
            "total_no_rule_code_hits": total_no_rule_codes,
        },
        "output_files": {
            "csv_results": str(csv_path.name),
            "csv_row_count": total_rows,
        },
        "safety": {
            "source_system_read_only": True,
            "writes_to_production_db": False,
            "writes_to_qdrant": False,
            "calls_medgemma": False,
        },
    }

    summary_path = OUTPUT_DIR / "rule_engine_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info("Summary yazıldı: %s", summary_path.name)

    if CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        log.info("Checkpoint temizlendi (başarılı tamamlanma).")

    log.info("=== Tamamlandı: %d satır, %.1f saniye ===", total_rows, elapsed_total)
    log.info("Karar dağılımı: %s", dict(overall_counter))
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="2.36M Bulk Rule-Engine Test")
    ap.add_argument("--page-size", type=int, default=PAGE_SIZE_DEFAULT,
                    help=f"Sayfa başına satır (varsayılan: {PAGE_SIZE_DEFAULT})")
    ap.add_argument("--resume", action="store_true",
                    help="Önceki checkpoint'ten devam et")
    args = ap.parse_args()
    raise SystemExit(main(page_size=args.page_size, resume=args.resume))
