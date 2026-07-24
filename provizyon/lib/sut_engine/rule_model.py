from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any


CODE_RE = re.compile(r"\b(?:P?\d{5,6}|[A-Z]\d{5,6})\b", re.IGNORECASE)


@dataclass
class SUTRule:
    rule_id: str
    source_code: str | None
    source_name: str | None
    rule_type: str
    severity: str = "warning"
    target_codes: list[str] = field(default_factory=list)
    period: str | None = None
    limit: int | None = None
    facility_level: str | None = None
    condition: str | None = None
    required_document: str | None = None
    source_quote: str | None = None
    confidence: float | None = None
    source_list: str | None = None
    source_file: str | None = None
    source_row: int | str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(value: dict) -> "SUTRule":
        allowed = set(SUTRule.__dataclass_fields__.keys())
        return SUTRule(**{key: value.get(key) for key in allowed})


def normalize_code(code: str | None) -> str | None:
    if code is None:
        return None
    code = str(code).strip().upper()
    return code or None


def normalize_text(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


def extract_codes(text: str | None) -> list[str]:
    if not text:
        return []
    return sorted({match.group(0).upper() for match in CODE_RE.finditer(text)})


def stable_rule_id(parts: list[Any]) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sut-rule-{digest[:24]}"


def rule_severity(rule_type: str) -> str:
    if rule_type in {
        "cannot_bill_with",
        "cannot_bill_with_any",
        "cannot_bill_with_category",
        "max_frequency",
        "facility_level_required",
        "diagnosis_constraint",
        "age_constraint",
        "quantity_constraint",
        "duration_requirement",
    }:
        return "fail"
    if rule_type in {
        "required_document",
        "required_clinical_evidence",
        "clinical_condition_required",
        "not_billable_separately",
        "cannot_bill_with_context",
    }:
        return "warning"
    return "info"


def normalize_facility_level(value: str | None) -> str | None:
    if not value:
        return None
    text = normalize_text(value)
    if "3" in text or "üç" in text or "uc" in text:
        return "third"
    if "2" in text or "iki" in text:
        return "second"
    if "1" in text or "bir" in text:
        return "first"
    return text or None


def parse_period_and_limit(text: str | None) -> tuple[str | None, int | None]:
    normalized = normalize_text(text)
    if not normalized:
        return None, None

    number_words = {
        "bir": 1,
        "iki": 2,
        "üç": 3,
        "uc": 3,
        "dört": 4,
        "dort": 4,
        "beş": 5,
        "bes": 5,
        "altı": 6,
        "alti": 6,
        "yedi": 7,
        "sekiz": 8,
        "dokuz": 9,
        "on": 10,
    }
    period = None
    if "aynı gün" in normalized or "ayni gun" in normalized:
        period = "day"
    elif "gün" in normalized or "gun" in normalized:
        period = "day"
    elif "hafta" in normalized:
        period = "week"
    elif "ay" in normalized:
        period = "month"
    elif "yıl" in normalized or "yil" in normalized:
        period = "year"
    elif "tedavi süresince" in normalized or "tedavi suresince" in normalized:
        period = "treatment"
    elif "yatış süresince" in normalized or "yatis suresince" in normalized:
        period = "admission"
    elif "gebelik boyunca" in normalized:
        period = "pregnancy"
    elif "aynı seansta" in normalized or "ayni seansta" in normalized or "her seans" in normalized:
        period = "session"
    elif "her bir" in normalized or "her biri" in normalized or "her uygulama" in normalized or "ilave her" in normalized or "her işlem" in normalized or "her islem" in normalized or "her girişim" in normalized or "her girisim" in normalized:
        period = "procedure"
    elif "ömür boyu" in normalized or "omur boyu" in normalized:
        period = "lifetime"
    elif "bir defa" in normalized or "bir kez" in normalized or "1 kez" in normalized:
        period = "treatment"

    limit = None
    number_text = CODE_RE.sub(" ", normalized)
    match = re.search(r"\b(\d+|bir|iki|üç|uc|dört|dort|beş|bes|altı|alti|yedi|sekiz|dokuz|on)\b", number_text)
    if match:
        token = match.group(1)
        limit = int(token) if token.isdigit() else number_words.get(token)

    return period, limit
