"""Deterministic plain-language example rule drafts from proposal fields.

No model calls. Output is always labeled as draft / not an approved rule.

Presentation contract (strict list separation):
- Birlikte ödenmez is same-list only: HUV↔HUV or SUT↔SUT (same contract space).
- Never project SUT target codes into a HUV together-rule (no reverse crosswalk).
- HUV primary with only targetSutCodes is not approvable as an HUV rule; SUT
  targets may be noted as a separate SUT-side candidate only.
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
    if flag.startswith("cross_list_together_targets_blocked"):
        return "cross_list_together_targets_blocked"
    return flag


def _as_code_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    out: list[str] = []
    for item in value:
        if item is None or item == "":
            continue
        out.append(str(item))
    return out


def collect_procedure_refs(
    proposal: dict[str, Any],
    signals: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge procedureRefs from signals + proposal; dedupe by listeTipi::kod."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(ref: Any) -> None:
        if not isinstance(ref, dict):
            return
        kod = ref.get("kod")
        if not kod:
            return
        key = f"{ref.get('listeTipi') or ''}::{kod}"
        if key in seen:
            return
        seen.add(key)
        merged.append(ref)

    for sig in signals or []:
        fields = sig.get("fields") or {}
        if isinstance(fields, dict):
            for ref in fields.get("procedureRefs") or []:
                _add(ref)
    for ref in proposal.get("procedureRefs") or []:
        _add(ref)
    return merged


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
            if k == "procedureRefs":
                continue
            if v is not None and v != "" and v != []:
                merged[k] = v
    refs = collect_procedure_refs(proposal, signals)
    if refs:
        merged["procedureRefs"] = refs
    return merged


def same_list_peers(
    primary: dict[str, Any],
    refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    primary_kod = primary.get("kod")
    liste = primary.get("listeTipi") or "HUV"
    peers: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("kod") == primary_kod:
            continue
        if (ref.get("listeTipi") or liste) != liste:
            continue
        peers.append(ref)
    return peers


def resolve_birlikte_targets(
    primary: dict[str, Any],
    fields: dict[str, Any],
) -> dict[str, Any]:
    """Resolve same-list together-targets only (no HUV↔SUT projection)."""
    liste = primary.get("listeTipi") or "HUV"
    refs = fields.get("procedureRefs") or []
    if not isinstance(refs, list):
        refs = []
    peers = same_list_peers(primary, refs)
    sut_targets = _as_code_list(fields.get("targetSutCodes"))

    result: dict[str, Any] = {
        "targetListeTipi": liste,
        "targetCodes": [],
        "targetRefs": [],
        "resolution": "none",
        "provenanceSutTargets": sut_targets,
        "canApproveSameList": False,
        "separateSutCandidate": False,
    }

    if peers:
        result["targetRefs"] = peers
        result["targetCodes"] = [str(p.get("kod")) for p in peers if p.get("kod")]
        result["resolution"] = "procedureRefs"
        result["canApproveSameList"] = True
        return result

    if liste == "SUT":
        result["targetCodes"] = sut_targets
        result["targetRefs"] = [{"kod": c, "listeTipi": "SUT"} for c in sut_targets]
        result["resolution"] = "targetSutCodes" if sut_targets else "none"
        result["canApproveSameList"] = bool(sut_targets)
        return result

    # HUV primary without HUV peers: SUT targets must not become HUV peers.
    if sut_targets:
        result["resolution"] = "sut_targets_cross_list_blocked"
        result["separateSutCandidate"] = True
        result["canApproveSameList"] = False
        return result

    result["resolution"] = "none"
    result["canApproveSameList"] = False
    return result


def _consistency(
    proposal: dict[str, Any],
    *,
    evidence_count: int,
    fields: dict[str, Any],
    birlikte: dict[str, Any] | None = None,
) -> dict[str, Any]:
    flags = [_flag_base(f) for f in (proposal.get("qualityFlags") or [])]
    critical = sorted({f for f in flags if f in CRITICAL_FLAGS or f.startswith("unresolved_")})
    completeness = proposal.get("completeness")
    priority = proposal.get("priority")
    rule_type = proposal.get("targetRuleType")
    liste = (proposal.get("primaryProcedure") or {}).get("listeTipi") or "HUV"

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
        targets = (birlikte or {}).get("targetCodes") or []
        resolution = (birlikte or {}).get("resolution")
        if not targets:
            if resolution == "sut_targets_cross_list_blocked":
                missing.append(
                    f"birlikte ödenmeyecek hedef {liste} kodları "
                    "(yalnız SUT hedefleri var; HUV–SUT karıştırılmaz)"
                )
                critical = sorted(set(critical) | {"cross_list_together_targets_blocked"})
            else:
                missing.append(f"birlikte ödenmeyecek hedef {liste} kodları")
        if birlikte and not birlikte.get("canApproveSameList"):
            # Never score as strongly draftable without same-list peers.
            pass
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
        reasons.append("Tamamlanma: Tam")
    elif completeness == "partial":
        reasons.append("Tamamlanma: Kısmi")
    if evidence_count >= 1:
        score += 2
        reasons.append(f"Kanıt: {evidence_count}")
    else:
        reasons.append("Kanıt: 0")
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

    can_approve = True
    if rule_type == "birlikteOdenmez" and birlikte is not None:
        can_approve = bool(birlikte.get("canApproveSameList"))
        if not can_approve:
            reasons.append("Aynı liste tipinde onaylanabilir hedef yok (HUV–SUT karıştırılmaz)")

    if (
        score >= 8
        and not missing
        and evidence_count >= 1
        and not critical
        and can_approve
    ):
        level = "high"
        level_label = "Yüksek tutarlılık adayı"
    elif score >= 5 and evidence_count >= 1 and can_approve:
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
        "canDraftStrongly": level == "high" and can_approve,
        "canApproveSameList": can_approve if rule_type == "birlikteOdenmez" else None,
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


def _provenance_sut_note(fields: dict[str, Any]) -> str:
    sut = fields.get("sourceSutCode")
    if not sut:
        return ""
    return f" Kaynak SUT (eşleme) referansı: {sut}."


def _sure_rules(proc: dict[str, Any], fields: dict[str, Any]) -> list[dict[str, Any]]:
    kod = proc.get("kod") or "?"
    ad = proc.get("ad") or "işlem"
    liste = proc.get("listeTipi") or "HUV"
    adet = fields.get("adet")
    period = _period_phrase(fields)
    group = fields.get("islemlerGrupMu")

    rules: list[dict[str, Any]] = []
    if adet is not None and period:
        text = (
            f"{liste} {kod} ({ad}) işlemi için {period} içinde en fazla {adet} adet "
            f"uygulama/ödeme sınırı tanımlanabilir."
        )
        text += _provenance_sut_note(fields)
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
        text += _provenance_sut_note(fields)
        rules.append({"title": "Eksik periyotlu frekans taslağı", "text": text, "kind": "sure_partial"})
    elif fields.get("sourceSutCode"):
        rules.append(
            {
                "title": "Kaynak referanslı süre kuralı (alanlar eksik)",
                "text": (
                    f"{liste} {kod} ({ad}) için süre/frekans kuralı adayı var;"
                    f"{_provenance_sut_note(fields)}"
                    f" Adet ve periyot alanları bu kayıtta tam değil — "
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
                    f"adet/periyot alanları yetersiz. Önce evidence ve quality flag’lere bakın."
                ),
                "kind": "sure_empty",
            }
        )
    return rules


def _birlikte_rules(
    proc: dict[str, Any],
    fields: dict[str, Any],
    birlikte: dict[str, Any],
) -> list[dict[str, Any]]:
    kod = proc.get("kod") or "?"
    ad = proc.get("ad") or "işlem"
    liste = proc.get("listeTipi") or "HUV"
    targets = birlikte.get("targetCodes") or []
    evrak = fields.get("evrakBazliMi")
    prov_targets = birlikte.get("provenanceSutTargets") or []
    resolution = birlikte.get("resolution")

    rules: list[dict[str, Any]] = []
    if targets and birlikte.get("canApproveSameList"):
        hedef = ", ".join(targets)
        text = (
            f"{liste} {kod} ({ad}) işlemi yapıldığında, aynı sözleşme/listede yer alan "
            f"{liste} kodları {hedef} ile aynı evrak/başvuru kapsamında birlikte ödenmez."
        )
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
    elif resolution == "sut_targets_cross_list_blocked":
        text = (
            f"{liste} {kod} ({ad}) için HUV birlikte-ödenmez kuralı onaylanamaz: "
            f"hedef işlemler aynı listede (HUV) tanımlı değil. "
            f"Kayıtta yalnızca SUT hedefleri var ({', '.join(prov_targets)}); "
            f"bunlar ayrı bir SUT kural adayı olarak değerlendirilmelidir. "
            f"HUV–SUT karıştırılarak tek birlikte-ödenmez kuralı yazılmaz."
        )
        if evrak is True:
            text += " Evrak bazlı kontrol işareti kaynak kayıtta var."
        rules.append(
            {
                "title": "Birlikte ödenmez (HUV onayı yok — çapraz liste)",
                "text": text,
                "kind": "birlikte_cross_list_blocked",
            }
        )
    else:
        text = (
            f"{liste} {kod} ({ad}) için birlikte-ödenmez tipi aday var; "
            f"hedef {liste} işlemleri kayıtta net değil. "
            f"Aynı listedeki (ör. HUV A yapıldıysa HUV B) hedef işlemler tanımlanmadan "
            f"kural yazılmamalıdır."
        )
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

    if lo is not None or hi is not None:
        lo_s = "?" if lo is None else str(lo)
        hi_s = "?" if hi is None else str(hi)
        text = (
            f"{liste} {kod} ({ad}) işlemi yalnızca {lo_s}–{hi_s} yaş aralığında "
            f"uygulanabilir / ödenebilir yaş kuralı olarak tanımlanabilir."
        )
        text += _provenance_sut_note(fields)
        return [{"title": "Yaş aralığı", "text": text, "kind": "yas"}]

    text = (
        f"{liste} {kod} ({ad}) için yaş kuralı adayı üretilmiş ancak yaş sınırları parse edilmemiş. "
        f"Evidence’daki yaş ifadesi okunarak alt/üst sınır uzman tarafından yazılmalıdır."
    )
    text += _provenance_sut_note(fields)
    return [{"title": "Yaş kuralı (sınır eksik)", "text": text, "kind": "yas_partial"}]


def build_presentation(
    proposal: dict[str, Any],
    *,
    signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """UI-facing blocks: same-list targets, SUT provenance, mapping trust."""
    signals = signals or []
    fields = _merge_fields(proposal, signals)
    proc = proposal.get("primaryProcedure") or {}
    liste = proc.get("listeTipi") or "HUV"
    rule_type = proposal.get("targetRuleType")

    birlikte = None
    if rule_type == "birlikteOdenmez":
        birlikte = resolve_birlikte_targets(proc, fields)

    mapping_statuses = sorted(
        {
            str(s.get("mappingStatus"))
            for s in signals
            if s.get("mappingStatus")
        }
    )
    origins = sorted({str(s.get("origin")) for s in signals if s.get("origin")})
    engine_types = sorted(
        {str(s.get("engineRuleType")) for s in signals if s.get("engineRuleType")}
    )
    flags = proposal.get("qualityFlags") or []
    crosswalk_trusted = "source_crosswalk_not_trusted" not in flags
    if "direct" in mapping_statuses:
        trust_label = "Doğrudan eşleme sinyali var"
    elif "review_only" in mapping_statuses:
        trust_label = "İnceleme gerektiren eşleme"
    elif mapping_statuses:
        trust_label = "Eşleme durumu: " + ", ".join(mapping_statuses)
    else:
        trust_label = "Eşleme durumu kayıtta yok"

    target_block: dict[str, Any] = {
        "listeTipi": liste,
        "codes": [],
        "refs": [],
        "resolution": None,
        "applicable": rule_type == "birlikteOdenmez",
    }
    if birlikte:
        target_block.update(
            {
                "codes": birlikte.get("targetCodes") or [],
                "refs": birlikte.get("targetRefs") or [],
                "resolution": birlikte.get("resolution"),
                "canApproveSameList": bool(birlikte.get("canApproveSameList")),
                "separateSutCandidate": bool(birlikte.get("separateSutCandidate")),
                "provenanceSutTargets": birlikte.get("provenanceSutTargets") or [],
            }
        )

    source_label = (
        "Ayrı SUT izi / aday (HUV birlikte-ödenmez peer’ı değildir)"
        if rule_type == "birlikteOdenmez"
        else "Kaynak SUT (eşleme) — peer listesi değildir"
    )

    return {
        "targetProcedures": target_block,
        "sourceSut": {
            "sourceSutCode": fields.get("sourceSutCode"),
            "targetSutCodes": _as_code_list(fields.get("targetSutCodes")),
            "label": source_label,
            "separateSutCandidate": bool((birlikte or {}).get("separateSutCandidate")),
        },
        "mappingTrust": {
            "mappingStatuses": mapping_statuses,
            "origins": origins,
            "engineRuleTypes": engine_types,
            "crosswalkTrusted": crosswalk_trusted,
            "trustLabel": trust_label,
            "qualityFlags": list(flags),
        },
        "birlikteResolution": birlikte,
    }


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

    birlikte = None
    if rule_type == "birlikteOdenmez":
        birlikte = resolve_birlikte_targets(proc, fields)

    consistency = _consistency(
        proposal,
        evidence_count=evidence_count,
        fields=fields,
        birlikte=birlikte,
    )

    if rule_type == "sure":
        rules = _sure_rules(proc, fields)
    elif rule_type == "birlikteOdenmez":
        rules = _birlikte_rules(proc, fields, birlikte or {})
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

    used = {k: fields[k] for k in sorted(fields.keys()) if k != "procedureRefs"}
    if birlikte:
        used["resolvedTargetCodes"] = birlikte.get("targetCodes") or []
        used["targetResolution"] = birlikte.get("resolution")

    return {
        "proposalId": proposal.get("proposalId"),
        "ruleType": rule_type,
        "disclaimer": disclaimer,
        "consistency": consistency,
        "usedFields": used,
        "examples": rules,
        "presentation": build_presentation(proposal, signals=signals),
        "howGenerated": (
            "Deterministik şablon: proposedFields + engine signal alanları birleştirilir; "
            "birlikte ödenmez yalnız aynı liste tipinde (HUV↔HUV veya SUT↔SUT) çözülür; "
            "SUT→HUV projeksiyonu yapılmaz. Model çağrısı yoktur."
        ),
    }
