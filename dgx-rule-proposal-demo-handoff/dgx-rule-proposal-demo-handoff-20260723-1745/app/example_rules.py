"""Deterministic plain-language example rule drafts from proposal fields.

No model calls. Output is always labeled as draft / not an approved rule.
"""

from __future__ import annotations

from typing import Any

PERIOD_LABELS = {
    "G": "gün",
    "H": "hafta",
    "M": "ay",
    "Y": "yıl",
}

CRITICAL_FLAGS = {
    "official_source_locator_or_quote_missing",
    "source_crosswalk_not_trusted",
    "canonical_together_target_not_found",
    "frequency_period_or_limit_incomplete",
    "explicit_frequency_fields_not_parsed",
    "explicit_age_bounds_not_parsed",
}


def _flag_base(flag: str) -> str:
    if flag.startswith("unresolved_target_sut_codes"):
        return "unresolved_target_sut_codes"
    if flag.startswith("official_source_verification_failed"):
        return "official_source_verification_failed"
    if flag.startswith("ambiguous_target_sut_codes"):
        return "ambiguous_target_sut_codes"
    return flag


def _merge_fields(proposal: dict[str, Any], signals: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for sig in signals:
        fields = sig.get("fields") or {}
        if isinstance(fields, dict):
            for k, v in fields.items():
                if k == "procedureRefs":
                    continue
                if v is not None and v != "" and v != []:
                    merged.setdefault(k, v)
    pf = proposal.get("proposedFields") or {}
    if isinstance(pf, dict):
        for k, v in pf.items():
            if v is not None and v != "" and v != []:
                merged[k] = v
    return merged


def _consistency(
    proposal: dict[str, Any],
    *,
    evidence_count: int,
    fields: dict[str, Any],
) -> dict[str, Any]:
    flags = [_flag_base(f) for f in (proposal.get("qualityFlags") or [])]
    critical = sorted({f for f in flags if f in CRITICAL_FLAGS or f.startswith("unresolved_")})
    completeness = proposal.get("completeness")
    priority = proposal.get("priority")
    rule_type = proposal.get("targetRuleType")

    missing: list[str] = []
    if rule_type == "sure":
        for key, label in (
            ("adet", "adet/limit"),
            ("periyotDeger", "periyot değeri"),
            ("surePeriyot", "periyot birimi (G/M/Y)"),
        ):
            if fields.get(key) in (None, "", []):
                missing.append(label)
    elif rule_type == "birlikteOdenmez":
        if not fields.get("targetSutCodes"):
            missing.append("birlikte ödenmeyecek hedef SUT kodları")
    elif rule_type == "yas":
        if fields.get("yasBaslangic") in (None, "") and fields.get("yasBitis") in (None, ""):
            missing.append("yaş alt/üst sınırları")

    score = 0
    reasons: list[str] = []
    if priority == "A":
        score += 2
        reasons.append("Öncelik A")
    elif priority == "B":
        score += 1
        reasons.append("Öncelik B")
    if completeness == "complete":
        score += 2
        reasons.append("Completeness: complete")
    elif completeness == "partial":
        reasons.append("Completeness: partial")
    if evidence_count >= 1:
        score += 2
        reasons.append(f"Evidence: {evidence_count}")
    else:
        reasons.append("Evidence: 0")
    if not critical:
        score += 2
        reasons.append("Kritik quality flag yok")
    else:
        reasons.append("Kritik flag var: " + ", ".join(critical[:4]))
    if not missing:
        score += 2
        reasons.append("Örnek kural için gerekli alanlar dolu")
    else:
        reasons.append("Eksik alan: " + ", ".join(missing))

    if score >= 8 and not missing and evidence_count >= 1 and not critical:
        level = "high"
        level_label = "Yüksek tutarlılık adayı"
    elif score >= 5 and evidence_count >= 1:
        level = "medium"
        level_label = "Orta tutarlılık — dikkatli kullanın"
    else:
        level = "low"
        level_label = "Düşük tutarlılık — yalnızca taslak / ek doğrulama gerekir"

    return {
        "score": score,
        "maxScore": 10,
        "level": level,
        "levelLabel": level_label,
        "reasons": reasons,
        "criticalFlags": critical,
        "missingFields": missing,
        "canDraftStrongly": level == "high",
    }


def _period_phrase(fields: dict[str, Any]) -> str | None:
    unit = fields.get("surePeriyot")
    value = fields.get("periyotDeger")
    if unit is None or value is None:
        return None
    label = PERIOD_LABELS.get(str(unit), str(unit))
    try:
        n = int(value)
    except (TypeError, ValueError):
        return f"{value} {label}"
    if n == 1:
        return f"1 {label}"
    return f"{n} {label}"


def _sure_rules(proc: dict[str, Any], fields: dict[str, Any]) -> list[dict[str, Any]]:
    kod = proc.get("kod") or "?"
    ad = proc.get("ad") or "işlem"
    liste = proc.get("listeTipi") or "HUV"
    adet = fields.get("adet")
    period = _period_phrase(fields)
    sut = fields.get("sourceSutCode")
    group = fields.get("islemlerGrupMu")

    rules: list[dict[str, Any]] = []
    if adet is not None and period:
        text = (
            f"{liste} {kod} ({ad}) işlemi için {period} içinde en fazla {adet} adet "
            f"uygulama/ödeme sınırı tanımlanabilir."
        )
        if sut:
            text += f" Kaynak SUT kodu referansı: {sut}."
        if group is True:
            text += " İşlemler grup olarak değerlendirilebilir."
        elif group is False:
            text += " İşlemler grup değil, tekil işlem bazında değerlendirilir."
        rules.append(
            {
                "title": "Süre / frekans limiti",
                "text": text,
                "kind": "sure_limit",
            }
        )
    elif adet is not None:
        text = (
            f"{liste} {kod} ({ad}) için adet limiti {adet} olarak önerilmiş; "
            f"periyot bilgisi kayıtta eksik — periyodu evidence’dan netleştirmeden kural yazmayın."
        )
        rules.append({"title": "Eksik periyotlu frekans taslağı", "text": text, "kind": "sure_partial"})
    elif sut:
        rules.append(
            {
                "title": "Kaynak referanslı süre kuralı (alanlar eksik)",
                "text": (
                    f"{liste} {kod} ({ad}) için süre/frekans kuralı adayı var; "
                    f"kaynak SUT {sut}. Adet ve periyot alanları bu kayıtta tam değil — "
                    f"resmî evidence okunarak limit cümlesi uzman tarafından yazılmalıdır."
                ),
                "kind": "sure_stub",
            }
        )
    else:
        rules.append(
            {
                "title": "Süre kuralı iskeleti",
                "text": (
                    f"{liste} {kod} ({ad}) için süre/frekans tipi aday üretilmiş ancak "
                    f"adet/periyot/SUT alanları yetersiz. Önce evidence ve quality flag’lere bakın."
                ),
                "kind": "sure_empty",
            }
        )
    return rules


def _birlikte_rules(proc: dict[str, Any], fields: dict[str, Any]) -> list[dict[str, Any]]:
    kod = proc.get("kod") or "?"
    ad = proc.get("ad") or "işlem"
    liste = proc.get("listeTipi") or "HUV"
    targets = fields.get("targetSutCodes") or []
    if not isinstance(targets, list):
        targets = [targets]
    targets = [str(t) for t in targets if t]
    sut = fields.get("sourceSutCode")
    evrak = fields.get("evrakBazliMi")

    rules: list[dict[str, Any]] = []
    if targets:
        hedef = ", ".join(targets)
        text = (
            f"{liste} {kod} ({ad}) işlemi; SUT kodları {hedef} ile aynı evrak/başvuru kapsamında "
            f"birlikte ödenmez kuralı olarak tanımlanabilir."
        )
        if sut:
            text += f" Kaynak SUT referansı: {sut}."
        if evrak is True:
            text += " Kontrol evrak bazlı uygulanabilir."
        elif evrak is False:
            text += " Kontrol evrak bazlı değil şeklinde işaretlenmiş."
        rules.append(
            {
                "title": "Birlikte ödenmez",
                "text": text,
                "kind": "birlikte",
            }
        )
    else:
        text = (
            f"{liste} {kod} ({ad}) için birlikte-ödenmez tipi aday var; "
            f"hedef SUT kodları kayıtta net değil. Hedef işlemler evidence’dan doğrulanmadan "
            f"kural tanımlanmamalıdır."
        )
        if sut:
            text += f" Kaynak SUT referansı: {sut}."
        if evrak is True:
            text += " Evrak bazlı kontrol önerilmiş."
        rules.append({"title": "Birlikte ödenmez (hedef eksik)", "text": text, "kind": "birlikte_partial"})
    return rules


def _yas_rules(proc: dict[str, Any], fields: dict[str, Any]) -> list[dict[str, Any]]:
    kod = proc.get("kod") or "?"
    ad = proc.get("ad") or "işlem"
    liste = proc.get("listeTipi") or "HUV"
    lo = fields.get("yasBaslangic")
    hi = fields.get("yasBitis")
    sut = fields.get("sourceSutCode")

    if lo is not None or hi is not None:
        lo_s = "?" if lo is None else str(lo)
        hi_s = "?" if hi is None else str(hi)
        text = (
            f"{liste} {kod} ({ad}) işlemi yalnızca {lo_s}–{hi_s} yaş aralığında "
            f"uygulanabilir / ödenebilir yaş kuralı olarak tanımlanabilir."
        )
        if sut:
            text += f" Kaynak SUT referansı: {sut}."
        return [{"title": "Yaş aralığı", "text": text, "kind": "yas"}]

    text = (
        f"{liste} {kod} ({ad}) için yaş kuralı adayı üretilmiş ancak yaş sınırları parse edilmemiş. "
        f"Evidence’daki yaş ifadesi okunarak alt/üst sınır uzman tarafından yazılmalıdır."
    )
    if sut:
        text += f" Kaynak SUT referansı: {sut}."
    return [{"title": "Yaş kuralı (sınır eksik)", "text": text, "kind": "yas_partial"}]


def build_example_rules(
    proposal: dict[str, Any],
    *,
    signals: list[dict[str, Any]] | None = None,
    evidence_count: int | None = None,
) -> dict[str, Any]:
    signals = signals or []
    if evidence_count is None:
        evidence_count = len(proposal.get("officialEvidenceIds") or [])
    fields = _merge_fields(proposal, signals)
    proc = proposal.get("primaryProcedure") or {}
    rule_type = proposal.get("targetRuleType")
    consistency = _consistency(proposal, evidence_count=evidence_count, fields=fields)

    if rule_type == "sure":
        rules = _sure_rules(proc, fields)
    elif rule_type == "birlikteOdenmez":
        rules = _birlikte_rules(proc, fields)
    elif rule_type == "yas":
        rules = _yas_rules(proc, fields)
    else:
        rules = [
            {
                "title": "Bilinmeyen kural tipi",
                "text": f"Kural tipi ({rule_type}) için henüz örnek cümle şablonu yok.",
                "kind": "unknown",
            }
        ]

    disclaimer = (
        "Bu metinler otomatik taslaktır; onaylı kural değildir. "
        "Resmî evidence ve kurum politikanız doğrulanmadan canlı kurala yazılmamalıdır."
    )

    return {
        "proposalId": proposal.get("proposalId"),
        "ruleType": rule_type,
        "disclaimer": disclaimer,
        "consistency": consistency,
        "usedFields": {k: fields[k] for k in sorted(fields.keys())},
        "examples": rules,
        "howGenerated": (
            "Deterministik şablon: proposedFields + engine signal alanları birleştirilerek "
            "Türkçe örnek cümle üretilir. Model çağrısı yoktur."
        ),
    }
