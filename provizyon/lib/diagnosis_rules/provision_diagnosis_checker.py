"""HUV Tani Kural Motoru (provision_diagnosis_checker).

HUV islem kodlarina karsi ICD-10 tani uygunlugunu kontrol eder.
Runtime lookup JSON dosyasindan 8,050+ kural yukler ve her bir
islem kodu icin diagnosis_policy'ye gore karar verir:

  - not_required: Tani gerektirmez, otomatik PASS.
  - required_any: En az bir ICD-10 paterni eslesmelidir.
  - required_all: Tum zorunlu paternler eslesmelidir.
  - excluded_any: Dislama listesiyle carpisma kontrolu.
  - review_required / conditional: Manuel inceleme gerektirir.

Kullanim (CLI):
    python provision_diagnosis_checker.py --huv-code 02.16321 --diagnosis M54.5

Kullanim (Python):
    lookup = load_runtime_lookup()
    result = evaluate_huv_diagnoses(lookup, "02.16321", ["M54.5"])
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


_LIB_ROOT = Path(__file__).resolve().parent.parent
_PROVIZYON_ROOT = _LIB_ROOT.parent
DEFAULT_LOOKUP_JSON = (
    _PROVIZYON_ROOT
    / "data"
    / "generated"
    / "diagnosis_rules"
    / "runtime"
    / "huv_diagnosis_runtime_lookup.json"
)

REVIEW_POLICIES = {"review_required", "conditional"}
SUPPORTED_POLICIES = {
    "not_required",
    "required_any",
    "required_all",
    "excluded_any",
    "review_required",
    "conditional",
}


def normalize_huv_code(value: str) -> str:
    code = str(value or "").strip()
    if code.upper().startswith("HUV::"):
        return code.split("::", 1)[1].strip()
    return code


def normalize_icd10_code(value: str) -> str:
    code = str(value or "").strip().upper()
    code = code.replace(",", ".")
    code = re.sub(r"[^A-Z0-9.]", "", code)
    if "." not in code and re.match(r"^[A-Z][0-9]{2}[A-Z0-9]+$", code):
        code = f"{code[:3]}.{code[3:]}"
    return code


def _icd_category_key(value: str) -> tuple[int, int] | None:
    code = normalize_icd10_code(value)
    match = re.match(r"^([A-Z])([0-9]{2})", code)
    if not match:
        return None
    return (ord(match.group(1)) - ord("A"), int(match.group(2)))


def _match_range(pattern: str, diagnosis_code: str) -> bool:
    parts = pattern.split("-", 1)
    if len(parts) != 2:
        return False
    start_key = _icd_category_key(parts[0])
    end_key = _icd_category_key(parts[1])
    diagnosis_key = _icd_category_key(diagnosis_code)
    if start_key is None or end_key is None or diagnosis_key is None:
        return False
    return start_key <= diagnosis_key <= end_key


def icd10_pattern_matches(pattern: str, diagnosis_code: str) -> bool:
    pattern = str(pattern or "").strip().upper()
    diagnosis_code = normalize_icd10_code(diagnosis_code)
    if not pattern or not diagnosis_code:
        return False

    if "-" in pattern:
        return _match_range(pattern, diagnosis_code)

    if pattern.endswith(".*"):
        base = normalize_icd10_code(pattern[:-2])
        return diagnosis_code == base or diagnosis_code.startswith(f"{base}.")

    normalized_pattern = normalize_icd10_code(pattern)
    if diagnosis_code == normalized_pattern:
        return True
    if "." not in normalized_pattern and diagnosis_code.startswith(
        f"{normalized_pattern}."
    ):
        return True
    return False


def find_pattern_matches(
    patterns: list[str],
    diagnosis_codes: list[str],
) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    normalized_diagnoses = [normalize_icd10_code(code) for code in diagnosis_codes]
    for pattern in patterns:
        for diagnosis_code in normalized_diagnoses:
            if icd10_pattern_matches(pattern, diagnosis_code):
                matches.append({"pattern": pattern, "diagnosis_code": diagnosis_code})
    return matches


def load_runtime_lookup(path: Path = DEFAULT_LOOKUP_JSON) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_rule(lookup: dict[str, Any], huv_code: str) -> dict[str, Any] | None:
    code = normalize_huv_code(huv_code)
    rules_by_huv_code = lookup.get("rules_by_huv_code") or {}
    aliases = lookup.get("aliases") or {}
    if code in rules_by_huv_code:
        return rules_by_huv_code[code]
    alias_code = aliases.get(str(huv_code).strip()) or aliases.get(f"HUV::{code}")
    if alias_code:
        return rules_by_huv_code.get(alias_code)
    return None


def _automatic_decision(
    rule: dict[str, Any],
    diagnosis_codes: list[str],
) -> dict[str, Any]:
    policy = rule.get("diagnosis_policy")
    required_patterns = list(rule.get("required_icd10_patterns") or [])
    excluded_patterns = list(rule.get("excluded_icd10_patterns") or [])
    normalized_diagnoses = [
        normalize_icd10_code(code) for code in diagnosis_codes if normalize_icd10_code(code)
    ]

    excluded_matches = find_pattern_matches(excluded_patterns, normalized_diagnoses)
    required_matches = find_pattern_matches(required_patterns, normalized_diagnoses)

    if excluded_matches:
        return {
            "status": "diagnosis_excluded",
            "allowed": False,
            "diagnosis_required": bool(required_patterns),
            "matched_diagnoses": required_matches,
            "excluded_matches": excluded_matches,
            "message": "Girilen tanı bu işlem için dışlama kuralıyla eşleşiyor.",
        }

    if policy == "not_required":
        return {
            "status": "allowed_no_diagnosis_required",
            "allowed": True,
            "diagnosis_required": False,
            "matched_diagnoses": [],
            "excluded_matches": [],
            "message": "Bu HUV işlemi için tanı zorunluluğu yok.",
        }

    if policy == "required_any":
        if not normalized_diagnoses:
            return {
                "status": "missing_diagnosis",
                "allowed": False,
                "diagnosis_required": True,
                "matched_diagnoses": [],
                "excluded_matches": [],
                "message": "Bu HUV işlemi için tanı gerekli ancak tanı girilmemiş.",
            }
        if required_matches:
            return {
                "status": "allowed_by_diagnosis",
                "allowed": True,
                "diagnosis_required": True,
                "matched_diagnoses": required_matches,
                "excluded_matches": [],
                "message": "Girilen tanılardan en az biri işlem için beklenen ICD-10 paterniyle eşleşiyor.",
            }
        return {
            "status": "diagnosis_mismatch",
            "allowed": False,
            "diagnosis_required": True,
            "matched_diagnoses": [],
            "excluded_matches": [],
            "message": "Girilen tanılar işlem için beklenen ICD-10 paternleriyle eşleşmiyor.",
        }

    if policy == "required_all":
        if not normalized_diagnoses:
            return {
                "status": "missing_diagnosis",
                "allowed": False,
                "diagnosis_required": True,
                "matched_diagnoses": [],
                "excluded_matches": [],
                "message": "Bu HUV işlemi için tanı gerekli ancak tanı girilmemiş.",
            }
        missing_patterns = [
            pattern
            for pattern in required_patterns
            if not any(
                icd10_pattern_matches(pattern, diagnosis_code)
                for diagnosis_code in normalized_diagnoses
            )
        ]
        return {
            "status": "allowed_by_diagnosis"
            if not missing_patterns
            else "diagnosis_mismatch",
            "allowed": not missing_patterns,
            "diagnosis_required": True,
            "matched_diagnoses": required_matches,
            "excluded_matches": [],
            "missing_patterns": missing_patterns,
            "message": "Tüm zorunlu ICD-10 paternleri eşleşti."
            if not missing_patterns
            else "Bazı zorunlu ICD-10 paternleri girilen tanılarla eşleşmedi.",
        }

    if policy == "excluded_any":
        return {
            "status": "allowed_no_excluded_diagnosis",
            "allowed": True,
            "diagnosis_required": False,
            "matched_diagnoses": [],
            "excluded_matches": [],
            "message": "Girilen tanılarda dışlama paterni saptanmadı.",
        }

    return {
        "status": "unsupported_policy",
        "allowed": None,
        "diagnosis_required": bool(required_patterns),
        "matched_diagnoses": required_matches,
        "excluded_matches": excluded_matches,
        "message": "Bu kural tipi otomatik karar için desteklenmiyor; manuel inceleme gerekir.",
    }


def evaluate_huv_diagnoses(
    lookup: dict[str, Any],
    huv_code: str,
    diagnosis_codes: list[str] | None = None,
) -> dict[str, Any]:
    diagnosis_codes = diagnosis_codes or []
    rule = resolve_rule(lookup, huv_code)
    if rule is None:
        return {
            "huv_code": normalize_huv_code(huv_code),
            "procedure_key": f"HUV::{normalize_huv_code(huv_code)}",
            "status": "unknown_huv",
            "allowed": None,
            "diagnosis_required": None,
            "requires_manual_review": True,
            "input_diagnoses": [normalize_icd10_code(code) for code in diagnosis_codes],
            "message": "HUV kodu diagnosis rule lookup içinde bulunamadı.",
        }

    tentative_decision = _automatic_decision(rule, diagnosis_codes)
    review_required = bool(rule.get("review_required")) or rule.get(
        "diagnosis_policy"
    ) in REVIEW_POLICIES

    result = {
        "huv_code": rule.get("huv_code"),
        "procedure_key": rule.get("procedure_key"),
        "procedure_name": rule.get("procedure_name"),
        "diagnosis_policy": rule.get("diagnosis_policy"),
        "required_icd10_patterns": rule.get("required_icd10_patterns") or [],
        "excluded_icd10_patterns": rule.get("excluded_icd10_patterns") or [],
        "confidence": rule.get("confidence"),
        "review_required": review_required,
        "requires_manual_review": review_required,
        "input_diagnoses": [
            normalize_icd10_code(code) for code in diagnosis_codes if normalize_icd10_code(code)
        ],
        "reason": rule.get("reason"),
        "source_evidence": rule.get("source_evidence"),
        "quality_flags": rule.get("quality_flags") or [],
    }

    if review_required:
        result.update(
            {
                "status": "review_required",
                "allowed": None,
                "diagnosis_required": tentative_decision.get("diagnosis_required"),
                "tentative_status": tentative_decision.get("status"),
                "matched_diagnoses": tentative_decision.get("matched_diagnoses", []),
                "excluded_matches": tentative_decision.get("excluded_matches", []),
                "message": "Bu HUV işlem-tanı kuralı manuel/uzman incelemesi gerektiriyor; otomatik provizyon kararı verilmemeli.",
            }
        )
        return result

    result.update(tentative_decision)
    result["requires_manual_review"] = False
    return result


def evaluate_provision(
    lookup: dict[str, Any],
    huv_codes: list[str],
    diagnosis_codes: list[str] | None = None,
) -> dict[str, Any]:
    items = [
        evaluate_huv_diagnoses(lookup, huv_code, diagnosis_codes)
        for huv_code in huv_codes
    ]
    if any(item.get("allowed") is False for item in items):
        overall_status = "not_payable_by_diagnosis"
        overall_allowed = False
    elif any(item.get("requires_manual_review") for item in items):
        overall_status = "review_required"
        overall_allowed = None
    else:
        overall_status = "allowed"
        overall_allowed = True
    return {
        "overall_status": overall_status,
        "overall_allowed": overall_allowed,
        "diagnosis_codes": [
            normalize_icd10_code(code) for code in (diagnosis_codes or [])
        ],
        "items": items,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate HUV procedure diagnosis eligibility for online provision checks."
    )
    parser.add_argument("--lookup-json", type=Path, default=DEFAULT_LOOKUP_JSON)
    parser.add_argument(
        "--huv-code",
        action="append",
        required=True,
        help="HUV code. Can be repeated. Accepts 02.16321 or HUV::02.16321.",
    )
    parser.add_argument(
        "--diagnosis",
        action="append",
        default=[],
        help="ICD-10 diagnosis code. Can be repeated.",
    )
    parser.add_argument(
        "--diagnoses",
        default="",
        help="Comma/space/semicolon separated ICD-10 diagnosis code list.",
    )
    parser.add_argument("--compact", action="store_true", help="Print compact JSON.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    lookup = load_runtime_lookup(args.lookup_json)
    diagnosis_codes = list(args.diagnosis)
    if args.diagnoses:
        diagnosis_codes.extend(
            code for code in re.split(r"[;,\s]+", args.diagnoses) if code
        )
    result = evaluate_provision(lookup, args.huv_code, diagnosis_codes)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
    )


if __name__ == "__main__":
    main()
