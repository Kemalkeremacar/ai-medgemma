from __future__ import annotations

import csv
import html
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
CSV_PATH = ROOT / "SUT/generated/dgx_handoff/rule_engine_results.csv"
LOOKUP_PATH = ROOT / "SUT/generated/sut_diagnosis_rules/ek2b/runtime/sut_diagnosis_runtime_lookup.json"
OUT_DIR = ROOT / "SUT/generated/shadow_quality_gate/full_historical_empirical_rule_intelligence_20260709"

STATUS_FIELDS = {
    "FAIL": "fail_codes",
    "REVIEW": "review_codes",
    "PASS": "pass_codes",
    "NO_RULE": "no_rule_codes",
}
ALL_CODE_FIELDS = [*STATUS_FIELDS.values(), "tzh_codes", "unknown_codes"]


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def split_values(value: Any) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"[;|,\s]+", str(value or ""))
        if part.strip()
    ]


def split_diagnoses(value: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for diagnosis in split_values(value):
        normalized = diagnosis.strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def normalize_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    if code.startswith("SUT::"):
        code = code.split("::", 1)[1].strip()
    return code


def code_type(code: str) -> str:
    code = normalize_code(code)
    if code.startswith("TZH."):
        return "tzh"
    if re.fullmatch(r"[1-9][0-9]{5}", code):
        return "sut_6_digit"
    if re.fullmatch(r"[0-9]{2}\.[0-9].*", code):
        return "huv_or_local_dotted"
    if re.fullmatch(r"[0-9]{6}", code):
        return "six_digit_leading_zero_or_unknown"
    return "unknown_pattern"


def diagnosis_chapter(diagnosis: str) -> str:
    diagnosis = str(diagnosis or "").strip().upper()
    if not diagnosis:
        return "UNKNOWN"
    letter = diagnosis[0]
    if letter in {"A", "B"}:
        return "A-B Infectious"
    if letter == "C" or diagnosis.startswith("D0") or diagnosis.startswith("D1") or diagnosis.startswith("D2") or diagnosis.startswith("D3") or diagnosis.startswith("D4"):
        return "C-D48 Neoplasms"
    if letter == "D":
        return "D50-D89 Blood/immune"
    return {
        "E": "E Endocrine/metabolic",
        "F": "F Mental/behavioral",
        "G": "G Nervous system",
        "H": "H Eye/ear",
        "I": "I Circulatory",
        "J": "J Respiratory",
        "K": "K Digestive",
        "L": "L Skin",
        "M": "M Musculoskeletal",
        "N": "N Genitourinary",
        "O": "O Pregnancy/childbirth",
        "P": "P Perinatal",
        "Q": "Q Congenital",
        "R": "R Symptoms/findings",
        "S": "S-T Injury/poisoning",
        "T": "S-T Injury/poisoning",
        "V": "V-Y External causes",
        "W": "V-Y External causes",
        "X": "V-Y External causes",
        "Y": "V-Y External causes",
        "Z": "Z Factors/status",
        "U": "U Special purposes",
    }.get(letter, "UNKNOWN")


def csv_value(value: Any) -> str:
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "" if value is None else str(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field, "")) for field in fieldnames})


def load_lookup() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not LOOKUP_PATH.exists():
        return {}, {}, {}
    data = json.loads(LOOKUP_PATH.read_text(encoding="utf-8-sig"))
    return data.get("rules_by_sut_code") or {}, data.get("rules_by_procedure_key") or {}, data.get("aliases") or {}


def lookup_status(code: str, rules_by_sut_code: dict[str, Any], rules_by_procedure_key: dict[str, Any], aliases: dict[str, Any]) -> str:
    code = normalize_code(code)
    if code in rules_by_sut_code:
        return "present_in_rules_by_sut_code"
    if code in rules_by_procedure_key:
        return "present_in_rules_by_procedure_key"
    alias_target = aliases.get(code) or aliases.get(f"SUT::{code}")
    if alias_target and (alias_target in rules_by_sut_code or alias_target in rules_by_procedure_key):
        return "present_via_alias"
    return "missing_from_runtime_lookup"


def blank_code_stat() -> dict[str, Any]:
    return {
        "occurrences": 0,
        "rows": 0,
        "status_occurrences": Counter(),
        "status_rows": Counter(),
        "overall_decision_rows": Counter(),
        "diagnoses": Counter(),
        "diagnosis_chapters": Counter(),
        "diagnoses_by_status": defaultdict(Counter),
        "diagnosis_chapters_by_status": defaultdict(Counter),
        "rows_with_diagnosis": 0,
    }


def increment_code_stats(
    stats: dict[str, dict[str, Any]],
    *,
    code: str,
    status: str,
    occurrence_count: int,
    diagnoses: list[str],
    overall_decision: str,
    counted_total_row: bool,
) -> None:
    item = stats.setdefault(code, blank_code_stat())
    item["occurrences"] += occurrence_count
    item["status_occurrences"][status] += occurrence_count
    item["status_rows"][status] += 1
    if counted_total_row:
        item["rows"] += 1
        item["overall_decision_rows"][overall_decision] += 1
        if diagnoses:
            item["rows_with_diagnosis"] += 1
        for diagnosis in diagnoses:
            item["diagnoses"][diagnosis] += 1
            item["diagnosis_chapters"][diagnosis_chapter(diagnosis)] += 1
    for diagnosis in diagnoses:
        item["diagnoses_by_status"][status][diagnosis] += 1
        item["diagnosis_chapters_by_status"][status][diagnosis_chapter(diagnosis)] += 1


def share(part: int | float, total: int | float) -> float:
    return round((float(part) / float(total)) if total else 0.0, 8)


def entropy(counter: Counter[str]) -> float:
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    value = 0.0
    for count in counter.values():
        probability = count / total
        value -= probability * math.log(probability)
    return value


def compact_counter(counter: Counter[str], limit: int = 10) -> list[dict[str, Any]]:
    total = sum(counter.values())
    return [
        {"value": key, "count": count, "share": share(count, total)}
        for key, count in counter.most_common(limit)
    ]


def top_values(counter: Counter[str], limit: int = 5) -> str:
    return "; ".join(f"{key}:{count}" for key, count in counter.most_common(limit))


def empirical_priority_score(rows: int, no_rule_rate: float, review_rate: float, fail_rate: float, top3_diag_share: float) -> float:
    volume_score = min(math.log10(rows + 1) / 5.0, 1.0) * 35.0
    concentration_score = top3_diag_share * 25.0
    no_rule_score = no_rule_rate * 25.0
    review_score = review_rate * 12.0
    fail_score = min(fail_rate * 200.0, 20.0)
    return round(volume_score + concentration_score + no_rule_score + review_score + fail_score, 4)


def confidence_label(rows: int, top3_diag_share: float, fail_rate: float) -> str:
    if rows >= 1000 and top3_diag_share >= 0.65 and fail_rate == 0:
        return "high"
    if rows >= 100 and top3_diag_share >= 0.45 and fail_rate <= 0.001:
        return "medium"
    return "low"


def recommended_category(
    *,
    rows: int,
    code_type_value: str,
    lookup_status_value: str,
    no_rule_rows: int,
    review_rows: int,
    fail_rows: int,
    pass_rows: int,
    top3_diag_share: float,
) -> str:
    if rows < 20:
        return "insufficient_evidence"
    if fail_rows > 0:
        return "fail_investigation_candidate"
    if no_rule_rows > 0 and lookup_status_value == "missing_from_runtime_lookup":
        if code_type_value == "sut_6_digit":
            return "high_priority_sut_rule_backfill" if no_rule_rows >= 100 else "sut_rule_backfill_candidate"
        if code_type_value == "huv_or_local_dotted":
            return "local_huv_alias_or_rule_layer_candidate"
        return "source_code_system_investigation_candidate"
    if review_rows >= 100 and top3_diag_share >= 0.50:
        return "review_reduction_candidate"
    if review_rows >= 100:
        return "manual_review_policy_candidate"
    if pass_rows >= 1000 and pass_rows / max(rows, 1) >= 0.90:
        return "stable_pass_rule_validation"
    return "evidence_observation_only"


def analyze() -> dict[str, Any]:
    rules_by_sut_code, rules_by_procedure_key, aliases = load_lookup()
    stats: dict[str, dict[str, Any]] = {}
    decision_counts: Counter[str] = Counter()
    row_field_presence: Counter[str] = Counter()
    diagnosis_counts: Counter[str] = Counter()
    diagnosis_chapter_counts: Counter[str] = Counter()
    code_type_occurrences: Counter[str] = Counter()
    code_type_rows: Counter[str] = Counter()
    status_code_occurrences: Counter[str] = Counter()
    status_code_rows: Counter[str] = Counter()
    tzh_code_occurrences: Counter[str] = Counter()
    unknown_code_occurrences: Counter[str] = Counter()
    total_rows = 0
    rows_with_any_status_code = 0
    rows_with_any_diagnosis = 0

    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        input_columns = reader.fieldnames or []
        for row in reader:
            total_rows += 1
            overall_decision = str(row.get("overall_decision") or "UNKNOWN").strip().upper() or "UNKNOWN"
            decision_counts[overall_decision] += 1
            diagnoses = split_diagnoses(row.get("diagnoses"))
            if diagnoses:
                rows_with_any_diagnosis += 1
            for diagnosis in diagnoses:
                diagnosis_counts[diagnosis] += 1
                diagnosis_chapter_counts[diagnosis_chapter(diagnosis)] += 1

            row_code_seen: set[str] = set()
            row_had_status_code = False
            for status, field in STATUS_FIELDS.items():
                raw_codes = [normalize_code(code) for code in split_values(row.get(field))]
                if raw_codes:
                    row_field_presence[field] += 1
                    row_had_status_code = True
                occurrence_counter = Counter(code for code in raw_codes if code)
                for code, occurrence_count in occurrence_counter.items():
                    ctype = code_type(code)
                    counted_total_row = code not in row_code_seen
                    if counted_total_row:
                        row_code_seen.add(code)
                        code_type_rows[ctype] += 1
                    code_type_occurrences[ctype] += occurrence_count
                    status_code_occurrences[status] += occurrence_count
                    status_code_rows[status] += 1
                    increment_code_stats(
                        stats,
                        code=code,
                        status=status,
                        occurrence_count=occurrence_count,
                        diagnoses=diagnoses,
                        overall_decision=overall_decision,
                        counted_total_row=counted_total_row,
                    )
            if row_had_status_code:
                rows_with_any_status_code += 1
            for field, target in (("tzh_codes", tzh_code_occurrences), ("unknown_codes", unknown_code_occurrences)):
                raw_codes = [normalize_code(code) for code in split_values(row.get(field))]
                if raw_codes:
                    row_field_presence[field] += 1
                for code in raw_codes:
                    target[code] += 1

    procedure_rows: list[dict[str, Any]] = []
    diagnosis_matrix_rows: list[dict[str, Any]] = []
    chapter_matrix_rows: list[dict[str, Any]] = []
    for code, item in stats.items():
        rows = int(item["rows"])
        status_rows = item["status_rows"]
        pass_rows = int(status_rows.get("PASS", 0))
        review_rows = int(status_rows.get("REVIEW", 0))
        fail_rows = int(status_rows.get("FAIL", 0))
        no_rule_rows = int(status_rows.get("NO_RULE", 0))
        diag_counter = item["diagnoses"]
        chapter_counter = item["diagnosis_chapters"]
        diag_total = sum(diag_counter.values())
        unique_diag = len(diag_counter)
        top1_diag_count = diag_counter.most_common(1)[0][1] if diag_counter else 0
        top3_diag_count = sum(count for _, count in diag_counter.most_common(3))
        top1_diag_share = share(top1_diag_count, diag_total)
        top3_diag_share = share(top3_diag_count, diag_total)
        entropy_value = entropy(diag_counter)
        normalized_entropy = round(entropy_value / math.log(unique_diag), 8) if unique_diag > 1 else 0.0
        ctype = code_type(code)
        lstatus = lookup_status(code, rules_by_sut_code, rules_by_procedure_key, aliases)
        no_rule_rate = share(no_rule_rows, rows)
        review_rate = share(review_rows, rows)
        fail_rate = share(fail_rows, rows)
        pass_rate = share(pass_rows, rows)
        category = recommended_category(
            rows=rows,
            code_type_value=ctype,
            lookup_status_value=lstatus,
            no_rule_rows=no_rule_rows,
            review_rows=review_rows,
            fail_rows=fail_rows,
            pass_rows=pass_rows,
            top3_diag_share=top3_diag_share,
        )
        procedure_rows.append(
            {
                "code": code,
                "code_type": ctype,
                "lookup_status": lstatus,
                "recommended_category": category,
                "empirical_confidence": confidence_label(rows, top3_diag_share, fail_rate),
                "empirical_priority_score": empirical_priority_score(rows, no_rule_rate, review_rate, fail_rate, top3_diag_share),
                "provision_rows": rows,
                "occurrences": int(item["occurrences"]),
                "pass_rows": pass_rows,
                "review_rows": review_rows,
                "fail_rows": fail_rows,
                "no_rule_rows": no_rule_rows,
                "pass_rate": pass_rate,
                "review_rate": review_rate,
                "fail_rate": fail_rate,
                "no_rule_rate": no_rule_rate,
                "rows_with_diagnosis": int(item["rows_with_diagnosis"]),
                "diagnosis_observations": diag_total,
                "unique_diagnoses": unique_diag,
                "top1_diagnosis_share": top1_diag_share,
                "top3_diagnosis_share": top3_diag_share,
                "diagnosis_entropy": round(entropy_value, 8),
                "diagnosis_normalized_entropy": normalized_entropy,
                "top_diagnoses": top_values(diag_counter, 10),
                "top_diagnosis_chapters": top_values(chapter_counter, 8),
                "overall_decision_rows": dict(item["overall_decision_rows"]),
            }
        )
        for diagnosis, count in diag_counter.most_common(25):
            diagnosis_matrix_rows.append(
                {
                    "code": code,
                    "code_type": ctype,
                    "diagnosis": diagnosis,
                    "diagnosis_chapter": diagnosis_chapter(diagnosis),
                    "cooccurrence_rows": count,
                    "share_within_code_diagnosis_observations": share(count, diag_total),
                    "code_provision_rows": rows,
                    "recommended_category": category,
                }
            )
        for chapter, count in chapter_counter.most_common(15):
            chapter_matrix_rows.append(
                {
                    "code": code,
                    "code_type": ctype,
                    "diagnosis_chapter": chapter,
                    "cooccurrence_rows": count,
                    "share_within_code_diagnosis_observations": share(count, diag_total),
                    "code_provision_rows": rows,
                    "recommended_category": category,
                }
            )

    procedure_rows.sort(key=lambda row: (float(row["empirical_priority_score"]), int(row["provision_rows"])), reverse=True)
    diagnosis_matrix_rows.sort(key=lambda row: (int(row["code_provision_rows"]), int(row["cooccurrence_rows"])), reverse=True)
    chapter_matrix_rows.sort(key=lambda row: (int(row["code_provision_rows"]), int(row["cooccurrence_rows"])), reverse=True)

    no_rule_candidates = [
        row for row in procedure_rows
        if int(row["no_rule_rows"]) > 0
    ]
    no_rule_candidates.sort(key=lambda row: (int(row["no_rule_rows"]), float(row["empirical_priority_score"])), reverse=True)

    review_candidates = [
        row for row in procedure_rows
        if row["recommended_category"] in {"review_reduction_candidate", "manual_review_policy_candidate"}
    ]
    review_candidates.sort(key=lambda row: (row["recommended_category"] != "review_reduction_candidate", int(row["review_rows"]) * -1))
    review_candidates = sorted(review_candidates, key=lambda row: (row["recommended_category"] == "review_reduction_candidate", int(row["review_rows"]), float(row["top3_diagnosis_share"])), reverse=True)

    fail_candidates = [row for row in procedure_rows if int(row["fail_rows"]) > 0]
    fail_candidates.sort(key=lambda row: (int(row["fail_rows"]), int(row["provision_rows"])), reverse=True)

    stable_pass_candidates = [
        row for row in procedure_rows
        if row["recommended_category"] == "stable_pass_rule_validation"
    ]
    stable_pass_candidates.sort(key=lambda row: int(row["pass_rows"]), reverse=True)

    category_counts = Counter(str(row["recommended_category"]) for row in procedure_rows)
    lookup_status_counts = Counter(str(row["lookup_status"]) for row in procedure_rows)
    code_type_counts = Counter(str(row["code_type"]) for row in procedure_rows)
    dashboard = {
        "schema_version": "full_historical_empirical_rule_intelligence.v1",
        "generated_at": now_iso(),
        "source_csv": str(CSV_PATH),
        "lookup_path": str(LOOKUP_PATH),
        "output_dir": str(OUT_DIR),
        "counts": {
            "total_provision_rows": total_rows,
            "rows_with_any_diagnosis": rows_with_any_diagnosis,
            "rows_with_any_evaluated_status_code": rows_with_any_status_code,
            "unique_evaluated_procedure_codes": len(procedure_rows),
            "unique_tzh_codes": len(tzh_code_occurrences),
            "unique_unknown_codes": len(unknown_code_occurrences),
            "decision_counts": dict(decision_counts),
            "row_field_presence": dict(row_field_presence),
            "status_code_occurrences": dict(status_code_occurrences),
            "status_code_rows": dict(status_code_rows),
            "code_type_occurrences": dict(code_type_occurrences),
            "code_type_rows": dict(code_type_rows),
            "recommended_category_counts": dict(category_counts),
            "lookup_status_code_counts": dict(lookup_status_counts),
            "code_type_unique_counts": dict(code_type_counts),
        },
        "lookup_summary": {
            "rules_by_sut_code": len(rules_by_sut_code),
            "rules_by_procedure_key": len(rules_by_procedure_key),
            "aliases": len(aliases),
        },
        "top_global_diagnoses": compact_counter(diagnosis_counts, 30),
        "top_global_diagnosis_chapters": compact_counter(diagnosis_chapter_counts, 30),
        "top_no_rule_candidates": no_rule_candidates[:50],
        "top_review_reduction_candidates": [
            row for row in review_candidates if row["recommended_category"] == "review_reduction_candidate"
        ][:50],
        "top_fail_investigation_candidates": fail_candidates[:50],
        "top_stable_pass_validation_candidates": stable_pass_candidates[:50],
        "limitations": [
            "This CSV does not contain facility, branch, physician, amount, contract, or period fields; institution/time anomaly work needs a richer DB/view export.",
            "Empirical diagnosis distributions are evidence-generation signals, not automatic clinical approval.",
            "MedGemma output must remain shadow metadata; deterministic rules and human/admin gates remain authoritative.",
        ],
        "safety": {
            "writes_to_production_db": False,
            "writes_to_qdrant": False,
            "live_runtime_override": False,
            "auto_apply": False,
            "exports_case_level_rows": False,
        },
    }

    generated_files = write_outputs(
        dashboard,
        procedure_rows,
        diagnosis_matrix_rows,
        chapter_matrix_rows,
        no_rule_candidates,
        review_candidates,
        fail_candidates,
        stable_pass_candidates,
        tzh_code_occurrences,
        unknown_code_occurrences,
    )
    dashboard["generated_files"] = generated_files
    write_json(OUT_DIR / "rule_coverage_dashboard.json", dashboard)
    return dashboard


def write_outputs(
    dashboard: dict[str, Any],
    procedure_rows: list[dict[str, Any]],
    diagnosis_matrix_rows: list[dict[str, Any]],
    chapter_matrix_rows: list[dict[str, Any]],
    no_rule_candidates: list[dict[str, Any]],
    review_candidates: list[dict[str, Any]],
    fail_candidates: list[dict[str, Any]],
    stable_pass_candidates: list[dict[str, Any]],
    tzh_code_occurrences: Counter[str],
    unknown_code_occurrences: Counter[str],
) -> list[str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    procedure_fields = [
        "code",
        "code_type",
        "lookup_status",
        "recommended_category",
        "empirical_confidence",
        "empirical_priority_score",
        "provision_rows",
        "occurrences",
        "pass_rows",
        "review_rows",
        "fail_rows",
        "no_rule_rows",
        "pass_rate",
        "review_rate",
        "fail_rate",
        "no_rule_rate",
        "rows_with_diagnosis",
        "diagnosis_observations",
        "unique_diagnoses",
        "top1_diagnosis_share",
        "top3_diagnosis_share",
        "diagnosis_entropy",
        "diagnosis_normalized_entropy",
        "top_diagnoses",
        "top_diagnosis_chapters",
        "overall_decision_rows",
    ]
    matrix_fields = [
        "code",
        "code_type",
        "diagnosis",
        "diagnosis_chapter",
        "cooccurrence_rows",
        "share_within_code_diagnosis_observations",
        "code_provision_rows",
        "recommended_category",
    ]
    chapter_fields = [
        "code",
        "code_type",
        "diagnosis_chapter",
        "cooccurrence_rows",
        "share_within_code_diagnosis_observations",
        "code_provision_rows",
        "recommended_category",
    ]
    code_counter_fields = ["code", "occurrences", "share"]

    generated = []
    targets = [
        ("procedure_code_summary.csv", procedure_rows, procedure_fields),
        ("empirical_rule_candidate_scores.csv", procedure_rows, procedure_fields),
        ("procedure_diagnosis_matrix_top25.csv", diagnosis_matrix_rows, matrix_fields),
        ("procedure_diagnosis_chapter_matrix_top15.csv", chapter_matrix_rows, chapter_fields),
        ("top_no_rule_rule_candidates.csv", no_rule_candidates, procedure_fields),
        ("review_reduction_candidates.csv", review_candidates, procedure_fields),
        ("fail_case_aggregate_report.csv", fail_candidates, procedure_fields),
        ("stable_pass_rule_validation_candidates.csv", stable_pass_candidates, procedure_fields),
        (
            "tzh_code_aggregate.csv",
            counter_rows(tzh_code_occurrences),
            code_counter_fields,
        ),
        (
            "unknown_code_aggregate.csv",
            counter_rows(unknown_code_occurrences),
            code_counter_fields,
        ),
    ]
    for filename, rows, fields in targets:
        path = OUT_DIR / filename
        write_csv(path, rows, fields)
        generated.append(str(path))

    json_targets = [
        ("procedure_code_summary_top1000.json", procedure_rows[:1000]),
        ("top_no_rule_rule_candidates.json", no_rule_candidates[:500]),
        ("review_reduction_candidates.json", review_candidates[:500]),
        ("fail_case_aggregate_report.json", fail_candidates[:500]),
        ("stable_pass_rule_validation_candidates.json", stable_pass_candidates[:500]),
    ]
    for filename, payload in json_targets:
        path = OUT_DIR / filename
        write_json(path, payload)
        generated.append(str(path))

    html_path = OUT_DIR / "rule_coverage_dashboard.html"
    html_path.write_text(build_html_dashboard(dashboard), encoding="utf-8")
    generated.append(str(html_path))
    return generated


def counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    total = sum(counter.values())
    return [
        {"code": code, "occurrences": count, "share": share(count, total)}
        for code, count in counter.most_common()
    ]


def build_html_dashboard(dashboard: dict[str, Any]) -> str:
    counts = dashboard.get("counts", {})
    category_counts = counts.get("recommended_category_counts") or {}
    decision_counts = counts.get("decision_counts") or {}
    top_no_rule = dashboard.get("top_no_rule_candidates") or []
    top_review = dashboard.get("top_review_reduction_candidates") or []
    top_fail = dashboard.get("top_fail_investigation_candidates") or []

    def table(rows: list[dict[str, Any]], columns: list[str]) -> str:
        header = "".join(f"<th>{html.escape(col)}</th>" for col in columns)
        body = []
        for row in rows[:25]:
            cells = "".join(html.escape(str(row.get(col, ""))) for col in columns)
            cells = "".join(f"<td>{html.escape(str(row.get(col, '')))}</td>" for col in columns)
            body.append(f"<tr>{cells}</tr>")
        return f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body)}</tbody></table>"

    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Full Historical Empirical Rule Intelligence</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse;margin:12px 0}"
        "td,th{border:1px solid #ccc;padding:4px 8px;font-size:12px}th{background:#f3f3f3}"
        "code{background:#f5f5f5;padding:2px 4px}</style></head><body>"
        "<h1>Full Historical Empirical Rule Intelligence</h1>"
        f"<p>Generated at: {html.escape(str(dashboard.get('generated_at')))}</p>"
        "<h2>Scope</h2>"
        f"<p>Total provision rows: <strong>{html.escape(str(counts.get('total_provision_rows')))}</strong></p>"
        f"<p>Unique evaluated procedure codes: <strong>{html.escape(str(counts.get('unique_evaluated_procedure_codes')))}</strong></p>"
        "<h2>Decision Counts</h2>"
        f"{table([{'decision': k, 'count': v} for k, v in decision_counts.items()], ['decision', 'count'])}"
        "<h2>Recommended Category Counts</h2>"
        f"{table([{'category': k, 'count': v} for k, v in category_counts.items()], ['category', 'count'])}"
        "<h2>Top NO_RULE Candidates</h2>"
        f"{table(top_no_rule, ['code','code_type','no_rule_rows','provision_rows','recommended_category','top_diagnoses'])}"
        "<h2>Top Review Reduction Candidates</h2>"
        f"{table(top_review, ['code','review_rows','provision_rows','top3_diagnosis_share','top_diagnoses'])}"
        "<h2>Top Fail Investigation Candidates</h2>"
        f"{table(top_fail, ['code','fail_rows','provision_rows','top_diagnoses'])}"
        "<h2>Limitations</h2><ul>"
        + "".join(f"<li>{html.escape(item)}</li>" for item in dashboard.get("limitations", []))
        + "</ul>"
        "<h2>Safety</h2>"
        f"{table([dashboard.get('safety', {})], ['writes_to_production_db','writes_to_qdrant','live_runtime_override','auto_apply','exports_case_level_rows'])}"
        "</body></html>"
    )


def main() -> int:
    dashboard = analyze()
    print(
        json.dumps(
            {
                "out_dir": str(OUT_DIR),
                "total_provision_rows": dashboard["counts"]["total_provision_rows"],
                "unique_evaluated_procedure_codes": dashboard["counts"]["unique_evaluated_procedure_codes"],
                "decision_counts": dashboard["counts"]["decision_counts"],
                "recommended_category_counts": dashboard["counts"]["recommended_category_counts"],
                "top_no_rule_candidates": [
                    {
                        "code": row["code"],
                        "no_rule_rows": row["no_rule_rows"],
                        "provision_rows": row["provision_rows"],
                        "recommended_category": row["recommended_category"],
                        "top_diagnoses": row["top_diagnoses"],
                    }
                    for row in dashboard["top_no_rule_candidates"][:10]
                ],
                "top_review_reduction_candidates": [
                    {
                        "code": row["code"],
                        "review_rows": row["review_rows"],
                        "provision_rows": row["provision_rows"],
                        "top3_diagnosis_share": row["top3_diagnosis_share"],
                        "top_diagnoses": row["top_diagnoses"],
                    }
                    for row in dashboard["top_review_reduction_candidates"][:10]
                ],
                "top_fail_investigation_candidates": [
                    {
                        "code": row["code"],
                        "fail_rows": row["fail_rows"],
                        "provision_rows": row["provision_rows"],
                        "top_diagnoses": row["top_diagnoses"],
                    }
                    for row in dashboard["top_fail_investigation_candidates"][:10]
                ],
                "safety": dashboard["safety"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
