from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from .rule_model import SUTRule, normalize_code, normalize_facility_level, normalize_text, parse_period_and_limit


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None


def _quantity(service: dict) -> int:
    for key in ("quantity", "adet", "count"):
        value = service.get(key)
        if value is not None:
            try:
                return max(1, int(float(value)))
            except Exception:
                return 1
    return 1

def _frequency_period_and_limit(rule: SUTRule) -> tuple[str | None, int | None]:
    parsed_period, parsed_limit = parse_period_and_limit(rule.source_quote or rule.condition)
    period = rule.period or parsed_period
    if rule.limit is None:
        return period, parsed_limit
    try:
        limit = int(float(rule.limit))
    except Exception:
        return period, parsed_limit
    if limit <= 0:
        return period, parsed_limit
    return period, limit


def _service_key(service: dict) -> str | None:
    return normalize_code(service.get("code") or service.get("service_code") or service.get("islem_kodu"))


def _service_name(service: dict) -> str | None:
    return service.get("name") or service.get("service_name") or service.get("islem_adi")


def _all_text(provizyon: dict) -> str:
    parts: list[str] = []
    for key in ("diagnoses", "documents", "clinical_evidence", "notes"):
        value = provizyon.get(key, [])
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.extend(str(v) for v in item.values() if v is not None)
    return normalize_text(" ".join(parts))


def _condition_supported(condition: str | None, corpus: str) -> bool:
    if not condition:
        return False
    terms = [
        term for term in normalize_text(condition).split()
        if len(term) >= 5 and term not in {"hastalar", "faturalandirilir", "faturalandırılır", "icin", "için"}
    ]
    if not terms:
        return False
    return any(term in corpus for term in terms[:8])


def _period_bucket(service: dict, period: str | None) -> str:
    if period in {"treatment", "admission", "episode", "pregnancy"}:
        return str(service.get("treatment_id") or service.get("admission_id") or service.get("episode_id") or service.get("provizyon_id") or "current")
    if period == "session":
        return str(service.get("session_id") or service.get("date") or service.get("tarih") or "current")
    if period == "procedure":
        return str(service.get("procedure_id") or service.get("line_id") or id(service))
    if period == "lifetime":
        return "lifetime"
    service_date = _parse_date(service.get("date") or service.get("tarih"))
    if not service_date:
        return "unknown"
    if period == "day":
        return service_date.isoformat()
    if period == "month":
        return f"{service_date.year:04d}-{service_date.month:02d}"
    if period == "year":
        return f"{service_date.year:04d}"
    if period == "week":
        year, week, _ = service_date.isocalendar()
        return f"{year:04d}-W{week:02d}"
    return service_date.isoformat()


def _same_day(left: dict, right: dict) -> bool:
    left_date = _parse_date(left.get("date") or left.get("tarih"))
    right_date = _parse_date(right.get("date") or right.get("tarih"))
    if left_date is None or right_date is None:
        return True
    return left_date == right_date


def _service_text(service: dict) -> str:
    return normalize_text(" ".join(str(value) for value in service.values() if value is not None))


def _category_terms(rule: SUTRule) -> list[str]:
    raw = rule.raw if isinstance(rule.raw, dict) else {}
    terms = raw.get("target_category_terms") or raw.get("target_category") or []
    if isinstance(terms, str):
        terms = [terms]
    return [normalize_text(str(term)) for term in terms if normalize_text(str(term))]


def _make_finding(
    status: str,
    rule: SUTRule,
    message: str,
    service: dict,
    related_services: list[dict] | None = None,
) -> dict:
    return {
        "status": status,
        "severity": rule.severity,
        "rule_id": rule.rule_id,
        "rule_type": rule.rule_type,
        "service_code": _service_key(service),
        "message": message,
        "source_quote": rule.source_quote,
        "source_list": rule.source_list,
        "source_file": rule.source_file,
        "source_row": rule.source_row,
        "related_services": related_services or [],
    }


class SUTEvaluator:
    def __init__(self, rules: list[SUTRule]):
        self.rules_by_code: dict[str, list[SUTRule]] = defaultdict(list)
        for rule in rules:
            if rule.source_code:
                self.rules_by_code[normalize_code(rule.source_code)].append(rule)

    def evaluate(self, provizyon: dict) -> dict:
        services = provizyon.get("services", []) or []
        history = provizyon.get("history", []) or []
        all_services = services + history
        services_by_code: dict[str, list[dict]] = defaultdict(list)
        for service in all_services:
            code = _service_key(service)
            if code:
                services_by_code[code].append(service)

        corpus = _all_text(provizyon)
        service_results = []
        all_findings = []

        for service in services:
            code = _service_key(service)
            if not code:
                continue
            findings = []
            for rule in self.rules_by_code.get(code, []):
                finding = self._evaluate_rule(rule, service, services, all_services, services_by_code, provizyon, corpus)
                if finding:
                    findings.append(finding)
                    all_findings.append(finding)

            service_results.append(
                {
                    "service_code": code,
                    "service_name": _service_name(service),
                    "date": service.get("date") or service.get("tarih"),
                    "quantity": _quantity(service),
                    "status": _status_from_findings(findings),
                    "findings": findings,
                }
            )

        return {
            "provizyon_id": provizyon.get("provizyon_id"),
            "hasta_id": provizyon.get("hasta_id"),
            "overall_status": _status_from_findings(all_findings),
            "summary": _summary(all_findings),
            "service_results": service_results,
            "rule_set_suggestions": _rule_set_suggestions(all_findings),
        }

    def _evaluate_rule(
        self,
        rule: SUTRule,
        service: dict,
        current_services: list[dict],
        all_services: list[dict],
        services_by_code: dict[str, list[dict]],
        provizyon: dict,
        corpus: str,
    ) -> dict | None:
        if rule.rule_type == "cannot_bill_with":
            related = []
            for target_code in rule.target_codes:
                related.extend(
                    item for item in current_services
                    if _service_key(item) == normalize_code(target_code)
                )
            if related:
                targets = ", ".join(sorted({str(_service_key(item)) for item in related}))
                return _make_finding(
                    "FAIL",
                    rule,
                    f"{_service_key(service)} kodu {targets} ile birlikte faturalandırılamaz/ödenemez.",
                    service,
                    related,
                )
            return None

        if rule.rule_type == "cannot_bill_with_any":
            related = [
                item for item in current_services
                if item is not service and (rule.period != "day" or _same_day(service, item))
            ]
            if related:
                return _make_finding(
                    "FAIL",
                    rule,
                    f"{_service_key(service)} kodu diğer işlemlerle birlikte faturalandırılamaz/ödenemez.",
                    service,
                    related,
                )
            return None

        if rule.rule_type == "cannot_bill_with_category":
            terms = _category_terms(rule)
            related = [
                item for item in current_services
                if item is not service
                and (rule.period != "day" or _same_day(service, item))
                and any(term in _service_text(item) for term in terms)
            ]
            if related:
                label = ", ".join(terms) if terms else "ilgili kategori"
                return _make_finding(
                    "FAIL",
                    rule,
                    f"{_service_key(service)} kodu {label} kapsamındaki işlemlerle birlikte faturalandırılamaz/ödenemez.",
                    service,
                    related,
                )
            return None

        if rule.rule_type == "cannot_bill_with_context":
            return _make_finding(
                "WARNING",
                rule,
                "SUT birlikte faturalandırma yasağı bağlam/kod grubu içeriyor; hedef kodlar otomatik çözümlenemediği için manuel kontrol gerekli.",
                service,
            )

        if rule.rule_type == "max_frequency":
            period, limit = _frequency_period_and_limit(rule)
            if not period or limit is None:
                return _make_finding(
                    "INSUFFICIENT_INFO",
                    rule,
                    "Frekans kuralı var ancak period/limit normalize edilemedi; manuel kontrol gerekli.",
                    service,
                )
            bucket = _period_bucket(service, period)
            total = 0
            related = []
            for item in services_by_code.get(_service_key(service), []):
                if _period_bucket(item, period) == bucket:
                    total += _quantity(item)
                    related.append(item)
            if total > limit:
                return _make_finding(
                    "FAIL",
                    rule,
                    f"{_service_key(service)} için {period} bazında limit {limit}, mevcut adet {total}.",
                    service,
                    related,
                )
            return None

        if rule.rule_type == "facility_level_required":
            required = normalize_facility_level(rule.facility_level)
            actual = normalize_facility_level(
                service.get("facility_level")
                or provizyon.get("facility_level")
                or provizyon.get("kurum_basamagi")
            )
            if not actual:
                return _make_finding(
                    "INSUFFICIENT_INFO",
                    rule,
                    "Kurum basamağı bilgisi yok; SUT basamak şartı doğrulanamadı.",
                    service,
                )
            if required and actual != required:
                return _make_finding(
                    "FAIL",
                    rule,
                    f"İşlem için kurum basamağı şartı {required}, mevcut {actual}.",
                    service,
                )
            return None

        if rule.rule_type in {"clinical_condition_required", "required_clinical_evidence", "diagnosis_constraint"}:
            condition = rule.condition or rule.source_quote
            if _condition_supported(condition, corpus):
                return None
            return _make_finding(
                "WARNING",
                rule,
                "SUT kuralındaki klinik/tanı koşulunu destekleyen açık kanıt input içinde bulunamadı.",
                service,
            )

        if rule.rule_type == "required_document":
            doc_text = normalize_text(" ".join(str(doc) for doc in provizyon.get("documents", [])))
            required = normalize_text(rule.required_document or rule.condition or rule.source_quote)
            if required and _condition_supported(required, doc_text):
                return None
            return _make_finding(
                "WARNING",
                rule,
                "SUT kuralındaki belge şartı input belgeleriyle doğrulanamadı.",
                service,
            )
        if rule.rule_type == "age_constraint":
            age = service.get("age") or provizyon.get("age") or provizyon.get("patient_age")
            if age is None:
                return _make_finding(
                    "INSUFFICIENT_INFO",
                    rule,
                    "SUT yaş koşulu var ancak hasta yaşı input içinde bulunamadı.",
                    service,
                )
            return _make_finding(
                "WARNING",
                rule,
                "SUT yaş koşulu otomatik yorumlanamadı; manuel doğrulama gerekli.",
                service,
            )

        if rule.rule_type == "quantity_constraint":
            return _make_finding(
                "WARNING",
                rule,
                "SUT miktar/seviye/alan kısıtı otomatik yorumlanamadı; işlem detayı manuel doğrulanmalı.",
                service,
            )

        if rule.rule_type == "duration_requirement":
            return _make_finding(
                "WARNING",
                rule,
                "SUT süre şartı otomatik yorumlanamadı; işlem süresi/manual detay doğrulanmalı.",
                service,
            )

        if rule.rule_type == "not_billable_separately":
            return _make_finding(
                "WARNING",
                rule,
                "Bu işlem/unsur ayrıca faturalandırılamaz olabilir; ana işlem/paket ilişkisi manuel veya paket kuralıyla kontrol edilmeli.",
                service,
            )

        if rule.rule_type == "included_in_service":
            return _make_finding(
                "INFO",
                rule,
                "Bu işlem bazı hizmet/unsurları kapsıyor; aynı unsurların ayrıca faturalandırılmadığı kontrol edilmeli.",
                service,
            )

        return None


def _status_from_findings(findings: list[dict]) -> str:
    statuses = {finding.get("status") for finding in findings}
    if "FAIL" in statuses:
        return "FAIL"
    if "WARNING" in statuses:
        return "WARNING"
    if "INSUFFICIENT_INFO" in statuses:
        return "INSUFFICIENT_INFO"
    return "PASS"


def _summary(findings: list[dict]) -> dict:
    counts = defaultdict(int)
    for finding in findings:
        counts[finding.get("status") or "UNKNOWN"] += 1
    return {
        "fail_count": counts["FAIL"],
        "warning_count": counts["WARNING"],
        "insufficient_info_count": counts["INSUFFICIENT_INFO"],
        "info_count": counts["INFO"],
        "finding_count": len(findings),
    }


def _rule_set_suggestions(findings: list[dict]) -> list[dict]:
    suggestions = []
    seen = set()
    for finding in findings:
        key = (finding.get("rule_type"), finding.get("service_code"), finding.get("source_quote"))
        if key in seen:
            continue
        seen.add(key)
        severity = "fail" if finding.get("status") == "FAIL" else "warning"
        suggestions.append(
            {
                "name": f"{finding.get('service_code')} {finding.get('rule_type')} kontrolü",
                "severity": severity,
                "description": finding.get("message"),
                "pseudo_logic": _pseudo_logic(finding),
                "source_quote": finding.get("source_quote"),
                "source": {
                    "source_list": finding.get("source_list"),
                    "source_file": finding.get("source_file"),
                    "source_row": finding.get("source_row"),
                },
            }
        )
    return suggestions


def _pseudo_logic(finding: dict) -> str:
    rule_type = finding.get("rule_type")
    code = finding.get("service_code")
    if rule_type == "cannot_bill_with":
        related = ", ".join(
            sorted({str(item.get("code") or item.get("service_code") or item.get("islem_kodu")) for item in finding.get("related_services", [])})
        )
        return f"IF service_code == {code} AND any_service_code IN ({related}) THEN FAIL"
    if rule_type == "max_frequency":
        return f"IF service_code == {code} AND count_in_period > SUT_LIMIT THEN FAIL"
    if rule_type == "facility_level_required":
        return f"IF service_code == {code} AND facility_level != required_level THEN FAIL"
    return f"IF service_code == {code} AND {rule_type} condition not verified THEN REVIEW"
