"""SUT Tani Kural Motoru (sut_provision_diagnosis_checker).

SUT (EK-2B) islem kodlarina karsi ICD-10 tani uygunlugunu kontrol eder.
HUV checker ile ayni diagnosis_policy mantigini kullanir, ek olarak
ozel kisitlamalari (yas, cinsiyet, zorunlu belge, klinik kanit) degerlendirir.

Runtime lookup JSON dosyasindan 7,058 SUT kurali yukler.

Ozel kisitlamalar (special_constraints):
  - age_min / age_max: Hasta yasi siniri
  - allowed_sexes: Cinsiyet filtresi
  - required_documents: Zorunlu belge terimleri
  - required_clinical_evidence_terms: Klinik kanit terimleri

Kullanim (CLI):
    python sut_provision_diagnosis_checker.py --sut-code 530320 --diagnosis K35.8

Kullanim (Python):
    lookup = load_runtime_lookup()
    result = evaluate_sut_diagnoses(lookup, "530320", ["K35.8"], age=45, sex="erkek")
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


_LIB_ROOT = Path(__file__).resolve().parent.parent
_PROVIZYON_ROOT = _LIB_ROOT.parent

if str(_LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIB_ROOT))

from diagnosis_rules.provision_diagnosis_checker import (
    find_pattern_matches,
    icd10_pattern_matches,
    normalize_icd10_code,
)


DEFAULT_LOOKUP_JSON = (
    _PROVIZYON_ROOT
    / "data"
    / "generated"
    / "sut_diagnosis_rules"
    / "ek2b"
    / "runtime"
    / "sut_diagnosis_runtime_lookup.json"
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


def normalize_sut_code(value: str) -> str:
    code = str(value or "").strip().upper()
    if code.startswith("SUT::"):
        code = code.split("::", 1)[1].strip()
    return code


def load_runtime_lookup(path: Path = DEFAULT_LOOKUP_JSON) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_rule(lookup: dict[str, Any], sut_code: str) -> dict[str, Any] | None:
    code = normalize_sut_code(sut_code)
    rules_by_sut_code = lookup.get("rules_by_sut_code") or {}
    aliases = lookup.get("aliases") or {}
    if code in rules_by_sut_code:
        return rules_by_sut_code[code]
    alias_code = aliases.get(str(sut_code).strip()) or aliases.get(f"SUT::{code}")
    if alias_code:
        return rules_by_sut_code.get(alias_code)
    return None


def _automatic_diagnosis_decision(
    rule: dict[str, Any],
    diagnosis_codes: list[str],
) -> dict[str, Any]:
    policy = str(rule.get("diagnosis_policy") or "review_required")
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
            "message": "Girilen tanı bu SUT kodu için dışlama kuralıyla eşleşiyor.",
        }

    if policy == "not_required":
        return {
            "status": "allowed_no_diagnosis_required",
            "allowed": True,
            "diagnosis_required": False,
            "matched_diagnoses": [],
            "excluded_matches": [],
            "message": "Bu SUT kodu için tanı zorunluluğu yok.",
        }

    if policy == "required_any":
        if not normalized_diagnoses:
            return {
                "status": "missing_diagnosis",
                "allowed": False,
                "diagnosis_required": True,
                "matched_diagnoses": [],
                "excluded_matches": [],
                "message": "Bu SUT kodu için tanı gerekli ancak tanı girilmemiş.",
            }
        if required_matches:
            return {
                "status": "allowed_by_diagnosis",
                "allowed": True,
                "diagnosis_required": True,
                "matched_diagnoses": required_matches,
                "excluded_matches": [],
                "message": "Girilen tanılardan en az biri SUT kodu için beklenen ICD-10 paterniyle eşleşiyor.",
            }
        return {
            "status": "diagnosis_mismatch",
            "allowed": False,
            "diagnosis_required": True,
            "matched_diagnoses": [],
            "excluded_matches": [],
            "message": "Girilen tanılar SUT kodu için beklenen ICD-10 paternleriyle eşleşmiyor.",
        }

    if policy == "required_all":
        if not normalized_diagnoses:
            return {
                "status": "missing_diagnosis",
                "allowed": False,
                "diagnosis_required": True,
                "matched_diagnoses": [],
                "excluded_matches": [],
                "message": "Bu SUT kodu için tanı gerekli ancak tanı girilmemiş.",
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
        "message": "Bu SUT tanı kural tipi otomatik karar için desteklenmiyor; manuel inceleme gerekir.",
    }


def _fold(text: str | None) -> str:
    value = (text or "").casefold().replace("ı", "i").replace("İ", "i")
    replacements = str.maketrans("çğıöşüâîû", "cgiosuaiu")
    value = value.translate(replacements)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def _text_corpus(values: list[str | dict[str, Any]] | None) -> str:
    parts: list[str] = []
    for value in values or []:
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value.values() if item is not None)
    return _fold(" ".join(parts))


def _contains_any(corpus: str, terms: list[str]) -> bool:
    return any(_fold(term) and _fold(term) in corpus for term in terms)


def _normalize_sex(value: str | None) -> str:
    folded = _fold(value)
    if folded in {"k", "kadin", "female", "f"}:
        return "F"
    if folded in {"e", "erkek", "male", "m"}:
        return "M"
    return folded.upper()


def evaluate_special_constraints(
    rule: dict[str, Any],
    *,
    age: int | None = None,
    sex: str | None = None,
    documents: list[str | dict[str, Any]] | None = None,
    clinical_evidence: list[str | dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    constraints = rule.get("special_constraints") or {}
    results: list[dict[str, Any]] = []

    age_min = constraints.get("age_min")
    age_max = constraints.get("age_max")
    if age_min is not None or age_max is not None:
        if age is None:
            results.append(
                {
                    "constraint": "age",
                    "status": "unknown",
                    "passed": None,
                    "requires_review": True,
                    "blocking": False,
                    "message": "SUT kuralında yaş şartı var ancak hasta yaşı verilmemiş.",
                }
            )
        else:
            failed = (age_min is not None and age < int(age_min)) or (
                age_max is not None and age > int(age_max)
            )
            results.append(
                {
                    "constraint": "age",
                    "status": "failed" if failed else "passed",
                    "passed": not failed,
                    "requires_review": False,
                    "blocking": failed,
                    "message": "Hasta yaşı SUT yaş şartıyla uyumsuz."
                    if failed
                    else "Hasta yaşı SUT yaş şartıyla uyumlu.",
                    "expected": {"age_min": age_min, "age_max": age_max},
                    "actual": age,
                }
            )

    allowed_sexes = [
        _normalize_sex(item) for item in constraints.get("allowed_sexes", []) or []
    ]
    if allowed_sexes:
        actual_sex = _normalize_sex(sex)
        if not actual_sex:
            results.append(
                {
                    "constraint": "sex",
                    "status": "unknown",
                    "passed": None,
                    "requires_review": True,
                    "blocking": False,
                    "message": "SUT kuralında cinsiyet şartı var ancak cinsiyet verilmemiş.",
                }
            )
        else:
            failed = actual_sex not in allowed_sexes
            results.append(
                {
                    "constraint": "sex",
                    "status": "failed" if failed else "passed",
                    "passed": not failed,
                    "requires_review": False,
                    "blocking": failed,
                    "message": "Hasta cinsiyeti SUT şartıyla uyumsuz."
                    if failed
                    else "Hasta cinsiyeti SUT şartıyla uyumlu.",
                    "expected": allowed_sexes,
                    "actual": actual_sex,
                }
            )

    required_documents = list(constraints.get("required_documents") or [])
    if required_documents:
        corpus = _text_corpus(documents)
        matched = _contains_any(corpus, required_documents)
        results.append(
            {
                "constraint": "required_document",
                "status": "passed" if matched else "unknown",
                "passed": True if matched else None,
                "requires_review": not matched,
                "blocking": False,
                "message": "SUT belge şartı input belgelerinde görüldü."
                if matched
                else "SUT belge şartı otomatik doğrulanamadı; belge/manual kontrol gerekli.",
                "expected_terms": required_documents,
            }
        )

    evidence_terms = list(constraints.get("required_clinical_evidence_terms") or [])
    if evidence_terms:
        corpus = _text_corpus(clinical_evidence)
        matched = _contains_any(corpus, evidence_terms)
        results.append(
            {
                "constraint": "clinical_evidence",
                "status": "passed" if matched else "unknown",
                "passed": True if matched else None,
                "requires_review": not matched,
                "blocking": False,
                "message": "SUT klinik kanıt şartı input içinde destekleniyor."
                if matched
                else "SUT klinik kanıt şartı otomatik doğrulanamadı; manuel/AI kanıt kontrolü gerekli.",
                "expected_terms": evidence_terms,
            }
        )

    return results


def evaluate_sut_diagnoses(
    lookup: dict[str, Any],
    sut_code: str,
    diagnosis_codes: list[str] | None = None,
    *,
    age: int | None = None,
    sex: str | None = None,
    documents: list[str | dict[str, Any]] | None = None,
    clinical_evidence: list[str | dict[str, Any]] | None = None,
) -> dict[str, Any]:
    diagnosis_codes = diagnosis_codes or []
    rule = resolve_rule(lookup, sut_code)
    normalized_diagnoses = [
        normalize_icd10_code(code) for code in diagnosis_codes if normalize_icd10_code(code)
    ]
    if rule is None:
        return {
            "sut_code": normalize_sut_code(sut_code),
            "procedure_key": f"SUT::{normalize_sut_code(sut_code)}",
            "status": "unknown_sut",
            "allowed": None,
            "diagnosis_required": None,
            "requires_manual_review": True,
            "input_diagnoses": normalized_diagnoses,
            "message": "SUT kodu diagnosis rule lookup içinde bulunamadı.",
        }

    tentative_decision = _automatic_diagnosis_decision(rule, diagnosis_codes)
    constraint_results = evaluate_special_constraints(
        rule,
        age=age,
        sex=sex,
        documents=documents,
        clinical_evidence=clinical_evidence,
    )
    constraint_failed = any(item.get("blocking") and item.get("passed") is False for item in constraint_results)
    constraint_review = any(item.get("requires_review") for item in constraint_results)
    rule_review_required = bool(rule.get("review_required")) or rule.get(
        "diagnosis_policy"
    ) in REVIEW_POLICIES
    review_required = rule_review_required or constraint_review

    result = {
        "sut_code": rule.get("sut_code"),
        "procedure_key": rule.get("procedure_key"),
        "procedure_name": rule.get("procedure_name"),
        "source_list": rule.get("source_list"),
        "source_file": rule.get("source_file"),
        "source_row": rule.get("source_row"),
        "diagnosis_policy": rule.get("diagnosis_policy"),
        "required_icd10_patterns": rule.get("required_icd10_patterns") or [],
        "excluded_icd10_patterns": rule.get("excluded_icd10_patterns") or [],
        "required_diagnosis_groups": rule.get("required_diagnosis_groups") or [],
        "special_constraints": rule.get("special_constraints") or {},
        "constraint_results": constraint_results,
        "confidence": rule.get("confidence"),
        "review_required": review_required,
        "requires_manual_review": review_required,
        "input_diagnoses": normalized_diagnoses,
        "reason": rule.get("reason"),
        "source_evidence": rule.get("source_evidence"),
        "quality_flags": rule.get("quality_flags") or [],
    }

    if constraint_failed:
        result.update(
            {
                "status": "sut_constraint_failed",
                "allowed": False,
                "diagnosis_required": tentative_decision.get("diagnosis_required"),
                "tentative_status": tentative_decision.get("status"),
                "matched_diagnoses": tentative_decision.get("matched_diagnoses", []),
                "excluded_matches": tentative_decision.get("excluded_matches", []),
                "message": "SUT özel şartlarından en az biri bloklayıcı şekilde karşılanmadı.",
            }
        )
        return result

    if rule_review_required:
        result.update(
            {
                "status": "review_required",
                "allowed": None,
                "diagnosis_required": tentative_decision.get("diagnosis_required"),
                "tentative_status": tentative_decision.get("status"),
                "matched_diagnoses": tentative_decision.get("matched_diagnoses", []),
                "excluded_matches": tentative_decision.get("excluded_matches", []),
                "message": "Bu SUT tanı kuralı manuel/uzman incelemesi gerektiriyor; otomatik provizyon kararı verilmemeli.",
            }
        )
        return result

    if constraint_review and tentative_decision.get("allowed") is True:
        result.update(
            {
                "status": "review_required",
                "allowed": None,
                "diagnosis_required": tentative_decision.get("diagnosis_required"),
                "tentative_status": tentative_decision.get("status"),
                "matched_diagnoses": tentative_decision.get("matched_diagnoses", []),
                "excluded_matches": tentative_decision.get("excluded_matches", []),
                "message": "Tanı kuralı olumlu ancak SUT özel belge/klinik şartları otomatik doğrulanamadı.",
            }
        )
        return result

    result.update(tentative_decision)
    result["requires_manual_review"] = constraint_review
    result["review_required"] = constraint_review
    return result


def evaluate_sut_provision(
    lookup: dict[str, Any],
    sut_codes: list[str],
    diagnosis_codes: list[str] | None = None,
    *,
    age: int | None = None,
    sex: str | None = None,
    documents: list[str | dict[str, Any]] | None = None,
    clinical_evidence: list[str | dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items = [
        evaluate_sut_diagnoses(
            lookup,
            sut_code,
            diagnosis_codes,
            age=age,
            sex=sex,
            documents=documents,
            clinical_evidence=clinical_evidence,
        )
        for sut_code in sut_codes
    ]
    if any(item.get("allowed") is False for item in items):
        overall_status = "not_payable_by_sut_diagnosis"
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
        description="Evaluate SUT procedure diagnosis eligibility for online provision checks."
    )
    parser.add_argument("--lookup-json", type=Path, default=DEFAULT_LOOKUP_JSON)
    parser.add_argument(
        "--sut-code",
        action="append",
        required=True,
        help="SUT code. Can be repeated. Accepts 530320 or SUT::530320.",
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
    parser.add_argument("--age", type=int, default=None)
    parser.add_argument("--sex", default=None)
    parser.add_argument("--document", action="append", default=[])
    parser.add_argument("--clinical-evidence", action="append", default=[])
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
    result = evaluate_sut_provision(
        lookup,
        args.sut_code,
        diagnosis_codes,
        age=args.age,
        sex=args.sex,
        documents=args.document,
        clinical_evidence=args.clinical_evidence,
    )
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
