"""Nihai karar birleştirme (karar sırası.txt Adım 6).

Alt karar katmanlarını öncelik sırasına göre birleştirir:

  1. Belge-hasta doğruluğu   (yanlış hasta belgesi -> her şeyi geçersiz kılar)
  2. Zorunlu evrak           (eksik evrak)
  3. İşlem-tanı kuralı        (tanı eksik / tanı uyumsuz)
  4. Klinik / AI yorumu       (klinik uyumsuzluk / belge kanıtı yetersiz)
  5. SUT işlem kuralı çakışması
  6. Belirsizlik              (manuel inceleme)
  7. Hepsi uygunsa            (uygun)

Erken çıkış mantığı orkestratörde uygulanır; bu fonksiyon yine de tüm
katmanları alıp tutarlı bir nihai sonuç üretebilir (idempotent).
"""

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
from .risk_normalizer import (
    layer_has_strict_manual_review,
    layer_has_unknown_codes,
    medgemma_confidence_allows_diagnosis_override,
    medgemma_parse_failed,
    medgemma_supports,
    normalize_provision_risk,
)


@dataclass
class DecisionOutcome:
    karar: KararDurumu
    gerekce: str
    warnings: list[str]
    decision_type: DecisionType | None = None
    risk_level: RiskLevel | None = None
    risk_reasons: list[RiskReason] = field(default_factory=list)


_DETERMINISTIC_LAYERS = frozenset(
    {"belge_hasta", "zorunlu_evrak", "tani_kurali", "sut_tani_kurali", "sut_kurali"}
)

_DIAGNOSIS_REVIEW_LAYERS = frozenset({"tani_kurali", "sut_tani_kurali"})


def _tani_fail_outcome(layer: LayerResult, warnings: list[str]) -> DecisionOutcome:
    detail = layer.detail or {}
    if detail.get("missing_diagnosis") and not detail.get("diagnosis_mismatch"):
        return DecisionOutcome(
            KararDurumu.TANI_EKSIK,
            layer.message or "İşlem için gerekli tanı eksik.",
            warnings,
        )
    return DecisionOutcome(
        KararDurumu.TANI_UYUMSUZ,
        layer.message or "Tanı işlemle uyumsuz.",
        warnings,
    )


def _medgemma_confident(medgemma: MedGemmaClinicalOutput | None) -> bool:
    if medgemma is None or medgemma.guven != "high":
        return False
    if medgemma.klinik_celiski is True or medgemma.yas_cinsiyet_uygun is False:
        return False
    # Belgesiz: belge alanları null; klinik yerindelik yüksek güvenle yeterli.
    if (
        medgemma.islem_belge_destekli is None
        and medgemma.tani_belge_destekli is None
        and medgemma.eksik_evrak is None
    ):
        return True
    return (
        medgemma.islem_belge_destekli is True
        and medgemma.tani_belge_destekli is True
    )


def _diagnosis_review_only(review_layers: list[LayerResult]) -> bool:
    """Yalnızca tanı katmanları REVIEW durumunda (unknown kod yok)."""

    if not review_layers:
        return False
    for layer in review_layers:
        if layer.layer not in _DIAGNOSIS_REVIEW_LAYERS:
            return False
        if layer_has_unknown_codes(layer):
            return False
    return True


def _finalize_outcome(
    outcome: DecisionOutcome,
    *,
    belge_hasta: LayerResult | None,
    zorunlu_evrak: LayerResult | None,
    tani_kurali: LayerResult | None,
    sut_tani_kurali: LayerResult | None,
    sut_kurali: LayerResult | None,
    medgemma: MedGemmaClinicalOutput | None,
    medgemma_layer: LayerResult | None,
) -> DecisionOutcome:
    normalized = normalize_provision_risk(
        karar=outcome.karar,
        gerekce=outcome.gerekce,
        belge_hasta=belge_hasta,
        zorunlu_evrak=zorunlu_evrak,
        tani_kurali=tani_kurali,
        sut_tani_kurali=sut_tani_kurali,
        sut_kurali=sut_kurali,
        medgemma=medgemma,
        medgemma_layer=medgemma_layer,
    )
    return DecisionOutcome(
        karar=normalized.karar,
        gerekce=normalized.gerekce,
        warnings=outcome.warnings,
        decision_type=normalized.decision_type,
        risk_level=normalized.risk_level,
        risk_reasons=normalized.risk_reasons,
    )


def merge_decisions(
    *,
    belge_hasta: LayerResult | None,
    zorunlu_evrak: LayerResult | None,
    tani_kurali: LayerResult | None,
    sut_tani_kurali: LayerResult | None,
    sut_kurali: LayerResult | None,
    medgemma: MedGemmaClinicalOutput | None,
    medgemma_layer: LayerResult | None,
    document_analysis_failed: bool = False,
    prior_rejection_signals: list[str] | None = None,
) -> DecisionOutcome:
    warnings: list[str] = []

    # 1) Belge-hasta uyumu — yanlış hasta belgesi her şeyi geçersiz kılar.
    if belge_hasta and belge_hasta.status == LayerStatus.FAIL:
        return _finalize_outcome(
            DecisionOutcome(
                KararDurumu.YANLIS_HASTA_BELGESI,
                belge_hasta.message or "Belge başka bir hastaya ait.",
                warnings,
            ),
            belge_hasta=belge_hasta,
            zorunlu_evrak=zorunlu_evrak,
            tani_kurali=tani_kurali,
            sut_tani_kurali=sut_tani_kurali,
            sut_kurali=sut_kurali,
            medgemma=medgemma,
            medgemma_layer=medgemma_layer,
        )

    # 2) Zorunlu evrak — belge gerekli ama yok.
    if zorunlu_evrak and zorunlu_evrak.status == LayerStatus.FAIL:
        return _finalize_outcome(
            DecisionOutcome(
                KararDurumu.EVRAK_EKSIK,
                zorunlu_evrak.message or "Gerekli evrak eksik.",
                warnings,
            ),
            belge_hasta=belge_hasta,
            zorunlu_evrak=zorunlu_evrak,
            tani_kurali=tani_kurali,
            sut_tani_kurali=sut_tani_kurali,
            sut_kurali=sut_kurali,
            medgemma=medgemma,
            medgemma_layer=medgemma_layer,
        )

    # 3) HUV işlem-tanı kuralı.
    if tani_kurali and tani_kurali.status == LayerStatus.FAIL:
        return _finalize_outcome(
            _tani_fail_outcome(tani_kurali, warnings),
            belge_hasta=belge_hasta,
            zorunlu_evrak=zorunlu_evrak,
            tani_kurali=tani_kurali,
            sut_tani_kurali=sut_tani_kurali,
            sut_kurali=sut_kurali,
            medgemma=medgemma,
            medgemma_layer=medgemma_layer,
        )

    # 3b) SUT işlem-tanı kuralı.
    if sut_tani_kurali and sut_tani_kurali.status == LayerStatus.FAIL:
        return _finalize_outcome(
            _tani_fail_outcome(sut_tani_kurali, warnings),
            belge_hasta=belge_hasta,
            zorunlu_evrak=zorunlu_evrak,
            tani_kurali=tani_kurali,
            sut_tani_kurali=sut_tani_kurali,
            sut_kurali=sut_kurali,
            medgemma=medgemma,
            medgemma_layer=medgemma_layer,
        )

    # 4) Klinik / AI yorumu — hard negatifler.
    if medgemma is not None:
        if medgemma.klinik_celiski is True or medgemma.yas_cinsiyet_uygun is False:
            return _finalize_outcome(
                DecisionOutcome(
                    KararDurumu.KLINIK_UYUMSUZLUK,
                    medgemma.gerekce or "Klinik uyumsuzluk/çelişki tespit edildi.",
                    warnings,
                ),
                belge_hasta=belge_hasta,
                zorunlu_evrak=zorunlu_evrak,
                tani_kurali=tani_kurali,
                sut_tani_kurali=sut_tani_kurali,
                sut_kurali=sut_kurali,
                medgemma=medgemma,
                medgemma_layer=medgemma_layer,
            )
        if medgemma.eksik_evrak is True:
            return _finalize_outcome(
                DecisionOutcome(
                    KararDurumu.EVRAK_EKSIK,
                    medgemma.gerekce or "MedGemma eksik evrak tespit etti.",
                    warnings,
                ),
                belge_hasta=belge_hasta,
                zorunlu_evrak=zorunlu_evrak,
                tani_kurali=tani_kurali,
                sut_tani_kurali=sut_tani_kurali,
                sut_kurali=sut_kurali,
                medgemma=medgemma,
                medgemma_layer=medgemma_layer,
            )
        if medgemma.islem_belge_destekli is False or medgemma.tani_belge_destekli is False:
            return _finalize_outcome(
                DecisionOutcome(
                    KararDurumu.BELGE_KANITI_YETERSIZ,
                    medgemma.gerekce or "Belge işlemi/tanıyı yeterince desteklemiyor.",
                    warnings,
                ),
                belge_hasta=belge_hasta,
                zorunlu_evrak=zorunlu_evrak,
                tani_kurali=tani_kurali,
                sut_tani_kurali=sut_tani_kurali,
                sut_kurali=sut_kurali,
                medgemma=medgemma,
                medgemma_layer=medgemma_layer,
            )

    # 4b) Belgelerde önceki iade/red yazışması — otomatik onay verilmez.
    if prior_rejection_signals:
        snippet = prior_rejection_signals[0]
        msg = (
            "Belgelerde önceki iade/red ifadesi tespit edildi; otomatik onay uygun değil. "
            f"Örnek: {snippet}"
        )
        warnings.append("Önceki iade/red yazışması tespit edildi.")
        return _finalize_outcome(
            DecisionOutcome(KararDurumu.MANUEL_INCELEME, msg, warnings),
            belge_hasta=belge_hasta,
            zorunlu_evrak=zorunlu_evrak,
            tani_kurali=tani_kurali,
            sut_tani_kurali=sut_tani_kurali,
            sut_kurali=sut_kurali,
            medgemma=medgemma,
            medgemma_layer=medgemma_layer,
        )

    # 5) SUT işlem kuralı çakışması (birlikte ödenmez, frekans vb.).
    if sut_kurali and sut_kurali.status == LayerStatus.FAIL:
        return _finalize_outcome(
            DecisionOutcome(
                KararDurumu.MANUEL_INCELEME,
                sut_kurali.message or "SUT kural çakışması; manuel inceleme gerekli.",
                warnings,
            ),
            belge_hasta=belge_hasta,
            zorunlu_evrak=zorunlu_evrak,
            tani_kurali=tani_kurali,
            sut_tani_kurali=sut_tani_kurali,
            sut_kurali=sut_kurali,
            medgemma=medgemma,
            medgemma_layer=medgemma_layer,
        )

    # 6) MedGemma çalışamadı / belge analizi tamamlanamadı.
    if document_analysis_failed:
        warnings.append("Belge analizi (extract/OCR) tamamlanamadı.")
        return _finalize_outcome(
            DecisionOutcome(
                KararDurumu.BELGE_ANALIZI_TAMAMLANAMADI,
                "Belgeler işlenemedi; içerik çıkarılamadı.",
                warnings,
            ),
            belge_hasta=belge_hasta,
            zorunlu_evrak=zorunlu_evrak,
            tani_kurali=tani_kurali,
            sut_tani_kurali=sut_tani_kurali,
            sut_kurali=sut_kurali,
            medgemma=medgemma,
            medgemma_layer=medgemma_layer,
        )
    if medgemma_layer and medgemma_layer.status == LayerStatus.INSUFFICIENT:
        return _finalize_outcome(
            DecisionOutcome(
                KararDurumu.AI_YORUMU_BEKLENIYOR,
                medgemma_layer.message or "MedGemma klinik yorumu alınamadı.",
                warnings,
            ),
            belge_hasta=belge_hasta,
            zorunlu_evrak=zorunlu_evrak,
            tani_kurali=tani_kurali,
            sut_tani_kurali=sut_tani_kurali,
            sut_kurali=sut_kurali,
            medgemma=medgemma,
            medgemma_layer=medgemma_layer,
        )

    # 7) Belirsizlik — review_required tek başına otomatik manuel inceleme değil.
    review_layers = [
        layer
        for layer in (
            belge_hasta,
            zorunlu_evrak,
            tani_kurali,
            sut_tani_kurali,
            sut_kurali,
            medgemma_layer,
        )
        if layer is not None and layer.status == LayerStatus.REVIEW
    ]
    medgemma_review = bool(medgemma and medgemma.manuel_inceleme_gerekli)

    if review_layers or medgemma_review:
        confident = _medgemma_confident(medgemma)
        supported = medgemma_supports(medgemma)

        only_belge_hasta_review = (
            len(review_layers) == 1
            and review_layers[0].layer == "belge_hasta"
            and not medgemma_review
        )
        if only_belge_hasta_review and confident:
            warnings.append(
                "Belge-hasta uyumu otomatik doğrulanamadı ancak AI (MedGemma) tarafından yüksek güvenle doğrulandı."
            )
            return _finalize_outcome(
                DecisionOutcome(
                    KararDurumu.UYGUN,
                    "Belge-hasta uyumu AI tarafından doğrulandı; provizyon onaylanabilir.",
                    warnings,
                ),
                belge_hasta=belge_hasta,
                zorunlu_evrak=zorunlu_evrak,
                tani_kurali=tani_kurali,
                sut_tani_kurali=sut_tani_kurali,
                sut_kurali=sut_kurali,
                medgemma=medgemma,
                medgemma_layer=medgemma_layer,
            )

        diagnosis_review_only = _diagnosis_review_only(review_layers)
        strict_manual = any(
            layer_has_strict_manual_review(layer)
            for layer in review_layers
            if layer.layer in _DIAGNOSIS_REVIEW_LAYERS
        )
        confident_enough = medgemma_confidence_allows_diagnosis_override(medgemma)
        if (
            diagnosis_review_only
            and supported
            and confident_enough
            and not medgemma_parse_failed(medgemma)
            and not strict_manual
        ):
            # Soft tanı-REVIEW override: belgeli=high; belgesiz=medium|high.
            # AI manuel bayrağı veya strict unsupported_policy -> manuel inceleme.
            if not medgemma_review:
                docless_mg = (
                    medgemma is not None
                    and medgemma.islem_belge_destekli is None
                    and medgemma.tani_belge_destekli is None
                )
                guven = (medgemma.guven if medgemma else "") or ""
                warnings.append(
                    "Tanı kuralı manuel sınıfta; "
                    + (
                        f"belgesiz klinik yerindelik {guven} güvenle uyumlu — düşük risk."
                        if docless_mg
                        else "belgeler işlem ve tanıyı destekliyor — düşük risk."
                    )
                )
                gerekce = (
                    medgemma.gerekce
                    if medgemma and medgemma.gerekce
                    else "Tanı kuralı manuel sınıfta; savunulabilir provizyon riski saptanmadı."
                )
                return _finalize_outcome(
                    DecisionOutcome(KararDurumu.UYGUN, gerekce, warnings),
                    belge_hasta=belge_hasta,
                    zorunlu_evrak=zorunlu_evrak,
                    tani_kurali=tani_kurali,
                    sut_tani_kurali=sut_tani_kurali,
                    sut_kurali=sut_kurali,
                    medgemma=medgemma,
                    medgemma_layer=medgemma_layer,
                )

        deterministic_reviews = [
            layer for layer in review_layers if layer.layer in _DETERMINISTIC_LAYERS
        ]
        # Deterministik REVIEW yok (TZH-only / SUT skip / tanı SKIPPED): yalnız
        # MedGemma katmanı REVIEW kalmış olabilir (belgesiz PASS üretemez).
        # high → mevcut davranış (manuel bayrağını da ezer).
        # belgesiz medium → yalnızca AI manuel istemiyorsa uygun.
        if (
            not deterministic_reviews
            and supported
            and not medgemma_parse_failed(medgemma)
        ):
            if confident:
                warnings.append(
                    "Deterministik kontroller uygun/atlandı; AI (MedGemma) yüksek güvenle onayladı."
                )
                return _finalize_outcome(
                    DecisionOutcome(
                        KararDurumu.UYGUN,
                        medgemma.gerekce
                        or "MedGemma klinik değerlendirmesi provizyonu destekliyor.",
                        warnings,
                    ),
                    belge_hasta=belge_hasta,
                    zorunlu_evrak=zorunlu_evrak,
                    tani_kurali=tani_kurali,
                    sut_tani_kurali=sut_tani_kurali,
                    sut_kurali=sut_kurali,
                    medgemma=medgemma,
                    medgemma_layer=medgemma_layer,
                )
            if (
                not medgemma_review
                and medgemma_confidence_allows_diagnosis_override(medgemma)
            ):
                docless_mg = (
                    medgemma is not None
                    and medgemma.islem_belge_destekli is None
                    and medgemma.tani_belge_destekli is None
                )
                guven = (medgemma.guven if medgemma else "") or ""
                warnings.append(
                    "Deterministik kontroller atlandı/uygun; "
                    + (
                        f"belgesiz klinik yerindelik {guven} güvenle uyumlu — düşük risk."
                        if docless_mg
                        else "AI klinik değerlendirmesi destekliyor — düşük risk."
                    )
                )
                return _finalize_outcome(
                    DecisionOutcome(
                        KararDurumu.UYGUN,
                        medgemma.gerekce
                        if medgemma and medgemma.gerekce
                        else "Deterministik kural atlandı; klinik yerindelik uygun.",
                        warnings,
                    ),
                    belge_hasta=belge_hasta,
                    zorunlu_evrak=zorunlu_evrak,
                    tani_kurali=tani_kurali,
                    sut_tani_kurali=sut_tani_kurali,
                    sut_kurali=sut_kurali,
                    medgemma=medgemma,
                    medgemma_layer=medgemma_layer,
                )

        reasons = [layer.message for layer in review_layers if layer.message]
        if medgemma_review and medgemma and medgemma.gerekce:
            reasons.append(medgemma.gerekce)
        return _finalize_outcome(
            DecisionOutcome(
                KararDurumu.MANUEL_INCELEME,
                " | ".join(_dedupe_messages(reasons))
                or "Sonuç kesinleştirilemedi; manuel inceleme gerekli.",
                warnings,
            ),
            belge_hasta=belge_hasta,
            zorunlu_evrak=zorunlu_evrak,
            tani_kurali=tani_kurali,
            sut_tani_kurali=sut_tani_kurali,
            sut_kurali=sut_kurali,
            medgemma=medgemma,
            medgemma_layer=medgemma_layer,
        )

    # 8) Her şey uygun.
    return _finalize_outcome(
        DecisionOutcome(
            KararDurumu.UYGUN,
            "Tüm kontroller uygun; provizyon onaylanabilir.",
            warnings,
        ),
        belge_hasta=belge_hasta,
        zorunlu_evrak=zorunlu_evrak,
        tani_kurali=tani_kurali,
        sut_tani_kurali=sut_tani_kurali,
        sut_kurali=sut_kurali,
        medgemma=medgemma,
        medgemma_layer=medgemma_layer,
    )


def _dedupe_messages(messages: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for msg in messages:
        text = msg.strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
