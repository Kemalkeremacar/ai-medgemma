from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .engine import load_index
from .pipeline_state import read_jsonl
from .rule_model import (
    SUTRule,
    extract_codes,
    normalize_code,
    normalize_facility_level,
    parse_period_and_limit,
    rule_severity,
    stable_rule_id,
)


RULE_TYPE_ALIASES = {
    "cannot_bill_with": "cannot_bill_with",
    "cannot_bill_with_any": "cannot_bill_with_any",
    "cannot_bill_with_category": "cannot_bill_with_category",
    "cannot_bill_with_context": "cannot_bill_with_context",
    "birlikte_odenmez": "cannot_bill_with",
    "birlikte ödenmez": "cannot_bill_with",
    "birlikte faturalandırılmaz": "cannot_bill_with",
    "max_frequency": "max_frequency",
    "frequency_limit": "max_frequency",
    "frequency_limits": "max_frequency",
    "max_per_day": "max_frequency",
    "max_per_year": "max_frequency",
    "not_billable_separately": "not_billable_separately",
    "quantity_constraint": "quantity_constraint",
    "duration_requirement": "duration_requirement",
    "ayrıca ödenmez": "not_billable_separately",
    "ayrıca faturalandırılmaz": "not_billable_separately",
    "included_in_service": "included_in_service",
    "included_services": "included_in_service",
    "included_in_package": "included_in_service",
    "facility_level_required": "facility_level_required",
    "requires_facility_level": "facility_level_required",
    "clinical_condition_required": "clinical_condition_required",
    "required_clinical_evidence": "required_clinical_evidence",
    "required_document": "required_document",
    "requires_document": "required_document",
    "diagnosis_constraint": "diagnosis_constraint",
    "requires_diagnosis": "diagnosis_constraint",
    "description": "other",
    "age_constraint": "age_constraint",
}


def normalize_rule_type(value: str | None) -> str:
    if not value:
        return "other"
    key = " ".join(str(value).casefold().split())
    return RULE_TYPE_ALIASES.get(key, key.replace(" ", "_"))


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _candidate_source_quote(candidate: dict) -> str | None:
    for key in ("source_quote", "text_quote", "quote"):
        if candidate.get(key):
            return str(candidate[key])
    return None


def _candidate_condition(candidate: dict) -> str | None:
    for key in ("condition", "condition_text", "requires_diagnosis_or_condition", "action", "description"):
        if candidate.get(key):
            return str(candidate[key])
    return None


def _target_codes(candidate: dict, source_code: str | None, quote: str | None) -> list[str]:
    values = []
    for key in ("target_codes", "target_code", "cannot_bill_with"):
        for item in _as_list(candidate.get(key)):
            if isinstance(item, dict):
                code = normalize_code(item.get("code") or item.get("target_code"))
            else:
                code = normalize_code(str(item))
            if code:
                values.append(code)

    for code in extract_codes(quote):
        if source_code and code == source_code:
            continue
        values.append(code)

    return sorted(set(values))


def compile_candidate(
    candidate: dict,
    source_code: str | None,
    source_name: str | None,
    source_list: str | None,
    source_file: str | None,
    source_row: int | str | None,
) -> SUTRule | None:
    rule_type = normalize_rule_type(candidate.get("rule_type"))
    quote = _candidate_source_quote(candidate)
    condition = _candidate_condition(candidate)
    period = candidate.get("period")
    limit = candidate.get("limit")

    if rule_type == "max_frequency":
        parsed_period, parsed_limit = parse_period_and_limit(quote or condition)
        period = period or parsed_period
        limit = limit or parsed_limit
        if isinstance(limit, str) and limit.isdigit():
            limit = int(limit)

    facility_level = normalize_facility_level(
        candidate.get("facility_level") or condition or quote
    ) if rule_type == "facility_level_required" else None

    targets = _target_codes(candidate, source_code, quote)
    required_document = None
    if rule_type == "required_document":
        required_document = (
            candidate.get("required_document")
            or candidate.get("document_type")
            or condition
        )

    confidence = candidate.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except Exception:
        confidence = None

    if rule_type == "other" and not quote and not condition:
        return None

    rule_id = stable_rule_id(
        [
            source_code,
            rule_type,
            ",".join(targets),
            period,
            limit,
            facility_level,
            condition,
            quote,
            source_list,
            source_row,
        ]
    )

    return SUTRule(
        rule_id=rule_id,
        source_code=source_code,
        source_name=source_name,
        rule_type=rule_type,
        severity=rule_severity(rule_type),
        target_codes=targets,
        period=period,
        limit=limit,
        facility_level=facility_level,
        condition=condition,
        required_document=required_document,
        source_quote=quote,
        confidence=confidence,
        source_list=source_list,
        source_file=source_file,
        source_row=source_row,
        raw=candidate,
    )


def compile_from_analysis_row(row: dict) -> list[SUTRule]:
    payload = row.get("payload", {})
    structured = payload.get("structured", {})
    source_code = normalize_code(payload.get("code"))
    source_name = payload.get("name")
    source_list = payload.get("source_list")
    source_file = payload.get("source_file")
    source_row = payload.get("source_row")

    candidates: list[dict] = []
    for key in (
        "rule_candidates",
        "frequency_limits",
        "cannot_bill_with",
        "required_documents",
        "required_clinical_evidence",
        "diagnosis_constraints",
        "included_services",
        "billing_constraints",
    ):
        for value in structured.get(key, []) or []:
            if isinstance(value, dict):
                candidates.append(value)

    rules = []
    for candidate in candidates:
        rule = compile_candidate(
            candidate=candidate,
            source_code=source_code,
            source_name=source_name,
            source_list=source_list,
            source_file=source_file,
            source_row=source_row,
        )
        if rule:
            rules.append(rule)
    return rules


def compile_from_index_record(record: dict) -> list[SUTRule]:
    source_code = normalize_code(record.get("code"))
    source_name = record.get("name")
    source_list = record.get("source_list")
    source_file = record.get("source_file")
    source_row = record.get("source_row")
    rules = []
    for candidate in record.get("rules", []) or []:
        rule = compile_candidate(
            candidate=candidate,
            source_code=source_code,
            source_name=source_name,
            source_list=source_list,
            source_file=source_file,
            source_row=source_row,
        )
        if rule:
            rules.append(rule)
    return rules


def dedupe_rules(rules: list[SUTRule]) -> list[SUTRule]:
    seen: set[str] = set()
    out: list[SUTRule] = []
    for rule in rules:
        if rule.rule_id in seen:
            continue
        seen.add(rule.rule_id)
        out.append(rule)
    return out


def compile_rules_from_analyses(path: Path) -> list[SUTRule]:
    rows = read_jsonl(path)
    rules: list[SUTRule] = []
    for row in rows:
        rules.extend(compile_from_analysis_row(row))
    return dedupe_rules(rules)


def compile_rules_from_index(path: Path) -> list[SUTRule]:
    index = load_index(path)
    rules: list[SUTRule] = []
    for record in index.get("records", []):
        rules.extend(compile_from_index_record(record))
    return dedupe_rules(rules)


def save_rules(rules: list[SUTRule], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "metadata": {
            "rule_count": len(rules),
        },
        "rules": [rule.to_dict() for rule in rules],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_rules(path: Path) -> list[SUTRule]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [SUTRule.from_dict(item) for item in data.get("rules", [])]
