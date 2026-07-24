"""Provizyon risk normalizer — savunulabilir risk / düşük risk / manuel review ayrıştırması."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    DecisionType,
    KararDurumu,
    LayerResult,
    LayerStatus,
    MedGemmaClinicalOutput,
    RiskLevel,
    RiskReason,
)

DEFENSIBLE_STATUSES = frozenset(
    {
        "missing_diagnosis",
        "diagnosis_missing",
        "diagnosis_mismatch",
        "diagnosis_excluded",
        "sut_constraint_failed",
    }
)

UNKNOWN_STATUSES = frozenset({"unknown_huv", "unknown_sut"})

RISK_LEVEL_PRIORITY: dict[RiskLevel, int] = {
    RiskLevel.RED: 6,
    RiskLevel.ORANGE: 5,
    RiskLevel.YELLOW: 4,
    RiskLevel.GRAY: 3,
    RiskLevel.BLUE: 2,
    RiskLevel.GREEN: 1,
}

_DT_PRIORITY: dict[DecisionType, int] = {
    DecisionType.AUTOMATIC_DEFENSIBLE: 3,
    DecisionType.MANUAL_REVIEW: 2,
    DecisionType.LOW_RISK: 1,
}

KARAR_TO_DECISION: dict[KararDurumu, tuple[DecisionType, RiskLevel]] = {
    KararDurumu.TANI_EKSIK: (DecisionType.AUTOMATIC_DEFENSIBLE, RiskLevel.RED),
    KararDurumu.TANI_UYUMSUZ: (DecisionType.AUTOMATIC_DEFENSIBLE, RiskLevel.RED),
    KararDurumu.EVRAK_EKSIK: (DecisionType.AUTOMATIC_DEFENSIBLE, RiskLevel.YELLOW),
    KararDurumu.YANLIS_HASTA_BELGESI: (DecisionType.AUTOMATIC_DEFENSIBLE, RiskLevel.RED),
    KararDurumu.KLINIK_UYUMSUZLUK: (DecisionType.AUTOMATIC_DEFENSIBLE, RiskLevel.RED),
    KararDurumu.BELGE_KANITI_YETERSIZ: (DecisionType.AUTOMATIC_DEFENSIBLE, RiskLevel.YELLOW),
    KararDurumu.MANUEL_INCELEME: (DecisionType.MANUAL_REVIEW, RiskLevel.ORANGE),
    KararDurumu.AI_YORUMU_BEKLENIYOR: (DecisionType.MANUAL_REVIEW, RiskLevel.GRAY),
    KararDurumu.BELGE_ANALIZI_TAMAMLANAMADI: (DecisionType.MANUAL_REVIEW, RiskLevel.GRAY),
    KararDurumu.UYGUN: (DecisionType.LOW_RISK, RiskLevel.GREEN),
}


@dataclass
class NormalizedRisk:
    karar: KararDurumu
    gerekce: str
    decision_type: DecisionType
    risk_level: RiskLevel
    risk_reasons: list[RiskReason] = field(default_factory=list)


def _is_docless_medgemma(medgemma: MedGemmaClinicalOutput) -> bool:
    """Belgesiz değerlendirmede belge alanları bilinçli olarak null bırakılır."""
    return (
        medgemma.islem_belge_destekli is None
        and medgemma.tani_belge_destekli is None
        and medgemma.eksik_evrak is None
    )


def medgemma_confidence_allows_diagnosis_override(
    medgemma: MedGemmaClinicalOutput | None,
) -> bool:
    """Belgesiz/belgeli klinik override için yeterli MedGemma güveni.

    Kullanım: soft tanı-REVIEW ve deterministik REVIEW yokken (TZH/SUT skip vb.).

    Belgeli akış: yalnızca ``guven=high``.
    Belgesiz akış: ``guven=medium|high`` (çağıran destek/manuel/strict kontrollerini yapar).
    """

    if medgemma is None:
        return False
    if medgemma.klinik_celiski is True or medgemma.yas_cinsiyet_uygun is False:
        return False
    if medgemma.guven == "high":
        return True
    return medgemma.guven == "medium" and _is_docless_medgemma(medgemma)


def medgemma_supports(medgemma: MedGemmaClinicalOutput | None) -> bool:
    if medgemma is None:
        return False
    if (
        medgemma.yas_cinsiyet_uygun is False
        or medgemma.klinik_celiski is True
        or medgemma.eksik_evrak is True
    ):
        return False
    # Belgesiz: belge desteği beklenmez; klinik çelişki yoksa destek kabul edilir.
    if _is_docless_medgemma(medgemma):
        return True
    return (
        medgemma.islem_belge_destekli is True
        and medgemma.tani_belge_destekli is not False
    )


def medgemma_hard_negative(medgemma: MedGemmaClinicalOutput | None) -> bool:
    if medgemma is None:
        return False
    return (
        medgemma.islem_belge_destekli is False
        or medgemma.tani_belge_destekli is False
        or medgemma.yas_cinsiyet_uygun is False
        or medgemma.klinik_celiski is True
        or medgemma.eksik_evrak is True
    )


def medgemma_parse_failed(medgemma: MedGemmaClinicalOutput | None) -> bool:
    if medgemma is None:
        return False
    gerekce = (medgemma.gerekce or "").lower()
    return medgemma.guven == "low" and (
        "json" in gerekce and "ayrıştır" in gerekce
        or "şema doğrulamasından geçmedi" in gerekce
    )


def _layer_items(layer: LayerResult | None) -> list[dict]:
    if layer is None:
        return []
    detail = layer.detail or {}
    result = detail.get("result") or {}
    items = result.get("items") or detail.get("blocking_items") or []
    return [item for item in items if isinstance(item, dict)]


def layer_has_unknown_codes(layer: LayerResult | None) -> bool:
    for item in _layer_items(layer):
        if str(item.get("status") or "") in UNKNOWN_STATUSES:
            return True
    return False


def item_requires_strict_manual_review(item: dict) -> bool:
    """Kural kapsamı dışı / otomatik karar verilmemesi gereken işlemler.

    İşlem için tanı zorunlu DEĞİLSE (``diagnosis_required is False``), kuralın
    "unsupported_policy" sınıfı tek başına katı manuel inceleme gerektirmez:
    tanı aranmayan bir işlemin (ör. rutin lab testi) temiz otomatik-onay kuralı
    bulunmaması, belgeler işlemi destekliyorsa manuel'e itilmesini gerektirmez.
    Bu durumlar aşağı akışta MedGemma desteğine göre düşük risk sayılabilir.
    """

    if item.get("diagnosis_required") is False:
        return False
    if str(item.get("tentative_status") or "") == "unsupported_policy":
        return True
    msg = str(item.get("message") or "").lower()
    return "otomatik provizyon kararı verilmemeli" in msg


def layer_has_strict_manual_review(layer: LayerResult | None) -> bool:
    return any(item_requires_strict_manual_review(item) for item in _layer_items(layer))


def _item_code(item: dict, layer_key: str) -> str:
    if layer_key == "sut_tani_kurali":
        return str(item.get("sut_code") or item.get("procedure_key") or "")
    return str(item.get("huv_code") or item.get("sut_code") or item.get("procedure_key") or "")


def _item_name(item: dict) -> str:
    return str(item.get("procedure_name") or item.get("sut_name") or "")


def _diagnoses_label(item: dict) -> str:
    dx = item.get("input_diagnoses") or []
    if not dx:
        return "yok"
    return ", ".join(str(d) for d in dx)


def classify_diagnosis_item(
    item: dict,
    *,
    layer_key: str,
    medgemma: MedGemmaClinicalOutput | None,
) -> RiskReason:
    code = _item_code(item, layer_key)
    name = _item_name(item)
    label = f"{code} ({name})" if name else code
    status = str(item.get("status") or "")
    policy = str(item.get("diagnosis_policy") or "")
    review_req = bool(
        item.get("requires_manual_review")
        or item.get("review_required")
        or policy in {"review_required", "conditional"}
    )
    diagnoses = _diagnoses_label(item)

    if status in UNKNOWN_STATUSES:
        return RiskReason(
            code=code,
            layer=layer_key,
            rule_trigger=status,
            message=f"{label}: kural kapsamı dışında/bilinmeyen kod; uzman inceleme gerekir.",
            action="Kural kapsamını doğrulayın veya manuel değerlendirin.",
            decision_type=DecisionType.MANUAL_REVIEW,
            risk_level=RiskLevel.GRAY,
        )

    if status in DEFENSIBLE_STATUSES:
        if status in {"missing_diagnosis", "diagnosis_missing"}:
            msg = (
                f"{label}: işlem için tanı zorunlu ancak ICD-10 girilmemiş "
                f"(girilen: {diagnoses}). Provizyoncu açısından savunulabilir risk."
            )
            action = "Eksik ICD-10 tanısını ekleyin veya işlem satırını kontrol edin."
        elif status == "sut_constraint_failed":
            msg = f"{label}: SUT özel şartı sağlanmıyor. Savunulabilir provizyon riski."
            action = "Yaş/cinsiyet/kurum şartını veya ilgili belgeyi doğrulayın."
        else:
            msg = (
                f"{label}: tanı ({diagnoses}) işlemle uyumsuz. "
                "Savunulabilir provizyon riski."
            )
            action = "Tanı kodunu işlemle uyumlu hale getirin veya işlemi sorgulayın."
        return RiskReason(
            code=code,
            layer=layer_key,
            rule_trigger=status,
            message=msg,
            action=action,
            decision_type=DecisionType.AUTOMATIC_DEFENSIBLE,
            risk_level=RiskLevel.RED,
        )

    if status == "review_required" or review_req:
        if item_requires_strict_manual_review(item):
            mg_note = ""
            if medgemma and medgemma.gerekce:
                mg_note = f" AI yorumu: {medgemma.gerekce[:120]}"
            return RiskReason(
                code=code,
                layer=layer_key,
                rule_trigger="unsupported_policy",
                message=(
                    f"{label}: işlem-tanı kuralı otomatik onay kapsamında değil "
                    f"(tanı: {diagnoses}); uzman inceleme gerekir.{mg_note}"
                ),
                action="Provizyon uzmanı tarafından klinik endikasyon ve ödeme uygunluğu doğrulanmalı.",
                decision_type=DecisionType.MANUAL_REVIEW,
                risk_level=RiskLevel.ORANGE,
            )
        # Soft override: MedGemma destekliyor, manuel istemiyor, strict değil.
        # Belgeli = high; belgesiz = medium|high.
        medgemma_flags_manual = bool(medgemma and medgemma.manuel_inceleme_gerekli)
        if (
            medgemma_confidence_allows_diagnosis_override(medgemma)
            and medgemma_supports(medgemma)
            and not medgemma_hard_negative(medgemma)
            and not medgemma_flags_manual
        ):
            docless = bool(medgemma and _is_docless_medgemma(medgemma))
            guven = (medgemma.guven if medgemma else "") or ""
            evidence = (
                f"belgesiz klinik yerindelik {guven} güvenle uyumlu"
                if docless
                else "belgelerde işlem ve tanı destekleniyor"
            )
            return RiskReason(
                code=code,
                layer=layer_key,
                rule_trigger="review_required",
                message=(
                    f"{label}: kural manuel sınıfta; {evidence} "
                    f"(tanı: {diagnoses}). Savunulabilir provizyon riski saptanmadı."
                ),
                action="Düşük risk kuyruğuna alınabilir; rutin kontrol yeterli.",
                decision_type=DecisionType.LOW_RISK,
                risk_level=RiskLevel.GREEN,
            )
        if medgemma is not None and _is_docless_medgemma(medgemma):
            evidence_note = (
                "Belgesiz değerlendirme; belge yokluğu hata değil. "
                "Klinik/kural belirsizliği nedeniyle manuel inceleme."
            )
            action = "Üstveri + kural sonucunu uzman doğrulasın; gerekirse belge ekleyin."
        else:
            evidence_note = "Belge kanıtı yetersiz veya belirsiz."
            action = "İlgili belge ve tanıyı doğrulayın."
        return RiskReason(
            code=code,
            layer=layer_key,
            rule_trigger="review_required",
            message=(
                f"{label}: kural manuel inceleme gerektiriyor "
                f"(tanı: {diagnoses}). {evidence_note}"
            ),
            action=action,
            decision_type=DecisionType.MANUAL_REVIEW,
            risk_level=RiskLevel.ORANGE,
        )

    if policy == "not_required" or status.startswith("allowed"):
        level = RiskLevel.BLUE if policy == "not_required" else RiskLevel.GREEN
        return RiskReason(
            code=code,
            layer=layer_key,
            rule_trigger=status or policy,
            message=f"{label}: tanı zorunluluğu yok veya tanı uyumlu ({diagnoses}).",
            action="Rutin gönderim yapılabilir.",
            decision_type=DecisionType.LOW_RISK,
            risk_level=level,
        )

    return RiskReason(
        code=code,
        layer=layer_key,
        rule_trigger=status or "unknown",
        message=f"{label}: değerlendirme belirsiz (tanı: {diagnoses}).",
        action="Manuel inceleme önerilir.",
        decision_type=DecisionType.MANUAL_REVIEW,
        risk_level=RiskLevel.GRAY,
    )


def _collect_diagnosis_reasons(
    layer: LayerResult | None,
    layer_key: str,
    medgemma: MedGemmaClinicalOutput | None,
) -> list[RiskReason]:
    reasons: list[RiskReason] = []
    for item in _layer_items(layer):
        reasons.append(
            classify_diagnosis_item(item, layer_key=layer_key, medgemma=medgemma)
        )
    return reasons


def _rollup_reasons(reasons: list[RiskReason]) -> tuple[DecisionType, RiskLevel]:
    if not reasons:
        return DecisionType.LOW_RISK, RiskLevel.GREEN

    best_dt = DecisionType.LOW_RISK
    best_rl = RiskLevel.GREEN
    best_pri = 0

    for reason in reasons:
        pri = _DT_PRIORITY.get(reason.decision_type, 0) * 10 + RISK_LEVEL_PRIORITY.get(
            reason.risk_level, 0
        )
        if pri > best_pri:
            best_pri = pri
            best_dt = reason.decision_type
            best_rl = reason.risk_level

    return best_dt, best_rl


def _karar_from_rollup(
    karar: KararDurumu,
    decision_type: DecisionType,
    *,
    medgemma: MedGemmaClinicalOutput | None,
) -> KararDurumu:
    if karar in {
        KararDurumu.TANI_EKSIK,
        KararDurumu.TANI_UYUMSUZ,
        KararDurumu.EVRAK_EKSIK,
        KararDurumu.YANLIS_HASTA_BELGESI,
        KararDurumu.KLINIK_UYUMSUZLUK,
        KararDurumu.BELGE_KANITI_YETERSIZ,
        KararDurumu.AI_YORUMU_BEKLENIYOR,
        KararDurumu.BELGE_ANALIZI_TAMAMLANAMADI,
    }:
        return karar

    if decision_type == DecisionType.LOW_RISK and medgemma_supports(medgemma):
        return KararDurumu.UYGUN
    if decision_type == DecisionType.MANUAL_REVIEW:
        return KararDurumu.MANUEL_INCELEME
    if decision_type == DecisionType.AUTOMATIC_DEFENSIBLE:
        if karar == KararDurumu.MANUEL_INCELEME:
            return KararDurumu.TANI_UYUMSUZ
        return karar
    return karar


def build_provisioner_gerekce(
    reasons: list[RiskReason],
    *,
    karar: KararDurumu,
    medgemma: MedGemmaClinicalOutput | None = None,
    fallback: str = "",
) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason.message and reason.message not in seen:
            seen.add(reason.message)
            parts.append(reason.message)

    if medgemma and medgemma.gerekce:
        mg = medgemma.gerekce.strip()
        if mg and mg not in seen and karar == KararDurumu.MANUEL_INCELEME:
            parts.append(mg)

    if parts:
        return " | ".join(parts)
    return fallback or "Değerlendirme tamamlandı."


def normalize_provision_risk(
    *,
    karar: KararDurumu,
    gerekce: str,
    belge_hasta: LayerResult | None = None,
    zorunlu_evrak: LayerResult | None = None,
    tani_kurali: LayerResult | None = None,
    sut_tani_kurali: LayerResult | None = None,
    sut_kurali: LayerResult | None = None,
    medgemma: MedGemmaClinicalOutput | None = None,
    medgemma_layer: LayerResult | None = None,
) -> NormalizedRisk:
    reasons: list[RiskReason] = []

    if belge_hasta and belge_hasta.status == LayerStatus.FAIL:
        reasons.append(
            RiskReason(
                layer="belge_hasta",
                rule_trigger="belge_hasta_fail",
                message=belge_hasta.message or "Belge başka bir hastaya ait.",
                action="Doğru hasta belgelerini yükleyin.",
                decision_type=DecisionType.AUTOMATIC_DEFENSIBLE,
                risk_level=RiskLevel.RED,
            )
        )

    if zorunlu_evrak and zorunlu_evrak.status == LayerStatus.FAIL:
        reasons.append(
            RiskReason(
                layer="zorunlu_evrak",
                rule_trigger="evrak_eksik",
                message=zorunlu_evrak.message or "Gerekli evrak eksik.",
                action="Eksik belgeyi tamamlayın.",
                decision_type=DecisionType.AUTOMATIC_DEFENSIBLE,
                risk_level=RiskLevel.YELLOW,
            )
        )

    reasons.extend(_collect_diagnosis_reasons(tani_kurali, "tani_kurali", medgemma))
    reasons.extend(
        _collect_diagnosis_reasons(sut_tani_kurali, "sut_tani_kurali", medgemma)
    )

    if medgemma_hard_negative(medgemma):
        reasons.append(
            RiskReason(
                layer="medgemma",
                rule_trigger="klinik_hard_negative",
                message=medgemma.gerekce or "Klinik uyumsuzluk veya belge kanıtı yetersiz.",
                action="Belge/tanı/işlem uyumunu düzeltin.",
                decision_type=DecisionType.AUTOMATIC_DEFENSIBLE,
                risk_level=RiskLevel.RED,
            )
        )

    if medgemma_parse_failed(medgemma):
        reasons.append(
            RiskReason(
                layer="medgemma",
                rule_trigger="json_parse_fail",
                message="MedGemma çıktısı JSON olarak ayrıştırılamadı.",
                action="AI katmanını yeniden çalıştırın veya manuel inceleyin.",
                decision_type=DecisionType.MANUAL_REVIEW,
                risk_level=RiskLevel.ORANGE,
            )
        )

    if sut_kurali and sut_kurali.status == LayerStatus.FAIL:
        reasons.append(
            RiskReason(
                layer="sut_kurali",
                rule_trigger="sut_kural_fail",
                message=sut_kurali.message or "SUT kural çakışması.",
                action="Birlikte ödenemez veya frekans kuralını kontrol edin.",
                decision_type=DecisionType.MANUAL_REVIEW,
                risk_level=RiskLevel.ORANGE,
            )
        )

    if reasons:
        decision_type, risk_level = _rollup_reasons(reasons)
    else:
        decision_type, risk_level = KARAR_TO_DECISION.get(
            karar, (DecisionType.LOW_RISK, RiskLevel.GREEN)
        )

    final_karar = _karar_from_rollup(karar, decision_type, medgemma=medgemma)
    if final_karar == KararDurumu.UYGUN:
        decision_type = DecisionType.LOW_RISK
        if risk_level not in {RiskLevel.RED, RiskLevel.YELLOW, RiskLevel.ORANGE}:
            risk_level = RiskLevel.GREEN

    if final_karar in KARAR_TO_DECISION:
        dt_k, rl_k = KARAR_TO_DECISION[final_karar]
        if RISK_LEVEL_PRIORITY[rl_k] >= RISK_LEVEL_PRIORITY[risk_level]:
            decision_type = dt_k
            risk_level = rl_k

    final_gerekce = build_provisioner_gerekce(
        reasons,
        karar=final_karar,
        medgemma=medgemma,
        fallback=gerekce,
    )

    reasons.sort(
        key=lambda r: (
            RISK_LEVEL_PRIORITY.get(r.risk_level, 0),
            _DT_PRIORITY.get(r.decision_type, 0),
        ),
        reverse=True,
    )

    return NormalizedRisk(
        karar=final_karar,
        gerekce=final_gerekce,
        decision_type=decision_type,
        risk_level=risk_level,
        risk_reasons=reasons,
    )
