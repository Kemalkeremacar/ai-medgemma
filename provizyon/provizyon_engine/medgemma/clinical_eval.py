"""MedGemma klinik değerlendirme (karar sırası.txt Adım 5).

Kanıt paketini (metin + görsel) + provizyon bağlamını + deterministic kural
sonuçlarını MedGemma'ya gönderir ve **structured JSON** klinik değerlendirme
alır. Serbest metin döndürülmez; çıktı ``MedGemmaClinicalOutput``'a parse edilir.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .. import settings
from ..documents.prepare import EvidencePackage
from ..models import (
    LayerResult,
    MedGemmaClinicalOutput,
    OzelSoruCevap,
    ProvizyonJob,
)
from .client import MedGemmaConfig, MedGemmaVisionClient


def _text_limit_for_vision(image_count: int) -> int:
    """Görsel sayısına göre metin kanıtı üst sınırı — 64K context taşmasını önler."""

    base = settings.TEXT_EVIDENCE_MAX_CHARS
    if image_count >= 20:
        return min(base, 12000)
    if image_count >= 12:
        return min(base, 18000)
    if image_count >= 8:
        return min(base, 24000)
    return base

SYSTEM_PROMPT = (
    "Sen bir tıbbi provizyon değerlendirme asistanısın. Sağlık sigortası için "
    "yüklenen belgeleri (epikriz, rapor, fatura, yazışma) klinik açıdan değerlendirirsin. "
    "Görevin: işlem-belge uyumu, tanı-belge uyumu, yaş/cinsiyet klinik uygunluğu, "
    "şikayet/epikriz ile tetkiklerin klinik uyumu, ödeme/endikasyon uygunluğu ve "
    "klinik çelişkileri tespit etmek; sistemin sorduğu özel sorulara yanıt vermek. "
    "SADECE geçerli bir JSON nesnesi döndür; açıklama/markdown ekleme. "
    "Belge tutarlılığı tek başına otomatik ödeme onayı değildir. "
    "Belgelerde önceki iade/red/ödenmez ifadesi, şikayet-tetkik uyumsuzluğu veya "
    "klinik endikasyon eksikliği varsa klinik_celiski=true ve manuel_inceleme_gerekli=true yap. "
    "Deterministik review_required durumunda belgeler desteklese bile klinik endikasyon "
    "belirsizse manuel_inceleme_gerekli=true döndür. "
    "Yalnızca belge-tutarlılığı net, klinik endikasyon uygun ve çelişki yoksa "
    "manuel_inceleme_gerekli=false yap."
)

DOCLESS_SYSTEM_PROMPT = (
    "Sen bir tıbbi provizyon değerlendirme asistanısın. Bu değerlendirmede YÜKLENMİŞ BELGE YOKTUR; "
    "yalnızca provizyon üstverisi (yaş, cinsiyet, tanı ICD kodları, işlem kodları/adları) ve "
    "deterministik kural (HUV/SUT) sonuçları verilmiştir. "
    "Görevin: SALT KLİNİK YERİNDELİK — tanı ile işlem klinik uyumu, yaş/cinsiyet uygunluğu, tanı-işlem çelişkisi. "
    "Belge alanları (islem_belge_destekli, tani_belge_destekli, eksik_evrak) MUTLAKA null; "
    "belge yokluğunu eksik_evrak=true veya belge_destekli=false yapma; gerekçede 'belge yok diye yetersiz' deme. "
    "Tanı-işlem klinik olarak uyumluysa guven=high veya medium olabilir; belge yokluğu tek başına low güven gerekçesi değildir. "
    "Deterministik kural review/manuel/review_required ise bunu gerekçede belirt; klinik çelişki yoksa "
    "klinik_celiski=false bırak. ÖNEMLİ: Yalnızca kuralın review_required demesi "
    "manuel_inceleme_gerekli=true yapmanı gerektirmez — kural zaten ayrı katmanda işlenir. "
    "DETERMINISTIC KURAL SONUCU içindeki items[] referanstır: soft_review=true kalemler "
    "(ör. tanı zorunlu olmayan lab) tek başına manuel_inceleme_gerekli=true gerektirmez. "
    "Tanı-işlem klinik uyumlu ve çelişki yoksa manuel_inceleme_gerekli=false ver. "
    "manuel_inceleme_gerekli=true yalnız şu durumlarda: açık klinik çelişki, yaş/cinsiyet uygunsuzluğu, "
    "veya tanı-işlem endikasyonunun klinik olarak gerçekten belirsiz olması. "
    "SADECE geçerli bir JSON nesnesi döndür; açıklama/markdown ekleme."
)

DOCLESS_DEFAULT_MODEL_SORULARI = [
    "Hasta yaşı nedir?",
    "Hasta cinsiyeti nedir?",
    "Hangi işlemler talep edilmiştir?",
    "Hangi tanı kodları girilmiştir?",
    "Tanı ile işlem klinik olarak uyumlu mu?",
    "Yaş/cinsiyet bu işlem için klinik olarak uygun mu?",
    "Klinik çelişki veya endikasyon belirsizliği var mı?",
]

REPAIR_SYSTEM_PROMPT = (
    "Sen yalnızca bozuk JSON yanıtını geçerli JSON'a dönüştüren bir asistansın. "
    "SADECE geçerli JSON nesnesi döndür; açıklama ekleme."
)

JSON_SCHEMA_HINT = """Şu şemada JSON döndür:
{
  "islem_belge_destekli": true|false|null,
  "tani_belge_destekli": true|false|null,
  "yas_cinsiyet_uygun": true|false|null,
  "klinik_celiski": true|false|null,
  "eksik_evrak": true|false|null,
  "manuel_inceleme_gerekli": true|false,
  "ozel_soru_cevaplari": [{"soru": "...", "cevap": "..."}],
  "gerekce": "kısa Türkçe gerekçe",
  "guven": "high|medium|low"
}"""

DEFAULT_MODEL_SORULARI = [
    "Hasta yaşı nedir?",
    "Hasta cinsiyeti nedir?",
    "Hangi işlemler yapılmıştır?",
    "Hangi tanı konulmuştur?",
    "Belgeler işlem ve tanıyı destekliyor mu?",
    "Şikayet/epikriz ile yapılan tetkikler klinik olarak uyumlu mu?",
    "Belgelerde önceki iade, red veya ödenmez ifadesi var mı?",
    "Klinik çelişki, endikasyon eksikliği veya eksik evrak var mı?",
]


def _questions_for_job(job: ProvizyonJob, *, docless: bool = False) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    defaults = DOCLESS_DEFAULT_MODEL_SORULARI if docless else DEFAULT_MODEL_SORULARI
    for question in [*job.model_sorulari, *defaults]:
        text = question.strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _tribool_label(value: bool | None) -> str:
    if value is True:
        return "Evet"
    if value is False:
        return "Hayır"
    return "Belirsiz"


def _ensure_ozel_soru_cevaplari(
    parsed: MedGemmaClinicalOutput,
    questions: list[str],
) -> MedGemmaClinicalOutput:
    if parsed.ozel_soru_cevaplari:
        return parsed

    fallback: list[OzelSoruCevap] = []
    structured = [
        ("İşlem belge tarafından destekleniyor mu?", _tribool_label(parsed.islem_belge_destekli)),
        ("Tanı belge tarafından destekleniyor mu?", _tribool_label(parsed.tani_belge_destekli)),
        ("Yaş/cinsiyet klinik olarak uygun mu?", _tribool_label(parsed.yas_cinsiyet_uygun)),
        ("Klinik çelişki var mı?", _tribool_label(parsed.klinik_celiski)),
        ("Eksik evrak var mı?", _tribool_label(parsed.eksik_evrak)),
    ]
    for soru, cevap in structured:
        fallback.append(OzelSoruCevap(soru=soru, cevap=cevap))

    for soru in questions:
        if any(item.soru == soru for item in fallback):
            continue
        fallback.append(OzelSoruCevap(soru=soru, cevap="Yanıt üretilemedi."))

    parsed.ozel_soru_cevaplari = fallback
    return parsed


def _apply_evidence_flags(
    parsed: MedGemmaClinicalOutput,
    evidence: EvidencePackage,
) -> MedGemmaClinicalOutput:
    if not evidence.partial_vision:
        return parsed
    # Kısmi görsel: model zaten güçlü pozitif sinyaller veriyorsa
    # gereksiz yere manuel'e itmeyelim; uyarı yeterli.
    all_positive = (
        parsed.islem_belge_destekli is True
        and parsed.tani_belge_destekli is not False
        and parsed.klinik_celiski is not True
        and parsed.eksik_evrak is not True
        and parsed.yas_cinsiyet_uygun is not False
    )
    if all_positive and parsed.guven == "high":
        pass  # uyarı eklenir ama guven/manuel değişmez
    else:
        parsed.manuel_inceleme_gerekli = True
        if parsed.guven == "high":
            parsed.guven = "medium"
    return parsed


def evaluate_clinical(
    job: ProvizyonJob,
    evidence: EvidencePackage,
    *,
    deterministic_summary: dict[str, Any] | None = None,
    patient_context: Any | None = None,
    client: MedGemmaVisionClient | None = None,
    config: MedGemmaConfig | None = None,
    docless: bool = False,
) -> tuple[MedGemmaClinicalOutput, LayerResult]:
    """MedGemma klinik değerlendirme yapar; (çıktı, LayerResult) döner.

    ``docless=True`` ise belge yoktur; değerlendirme yalnızca provizyon üstverisi
    ve deterministik kural sonuçlarıyla yapılır (belge yokluğu hata sayılmaz).
    """

    from ..models import LayerStatus

    # Belgesiz modda kanıt paketi boş olabilir; bu bir hata değildir, MedGemma
    # provizyon üstverisi (yaş/cinsiyet/tanı/işlem) + kural özetiyle çalışır.
    if not evidence.has_text and not evidence.has_images and not docless:
        out = MedGemmaClinicalOutput(
            manuel_inceleme_gerekli=True,
            gerekce="Belge metni/görseli olmadığı için klinik değerlendirme yapılamadı.",
            guven="low",
        )
        return out, LayerResult(
            layer="medgemma",
            status=LayerStatus.SKIPPED,
            message="MedGemma'ya gönderilecek belge kanıtı yok.",
        )

    client = client or MedGemmaVisionClient(config)
    questions = _questions_for_job(job, docless=docless)
    text_limit = _text_limit_for_vision(len(evidence.image_paths))
    user_text = _build_prompt(
        job, evidence, deterministic_summary, patient_context, questions,
        max_text_chars=text_limit, docless=docless,
    )
    system_prompt = DOCLESS_SYSTEM_PROMPT if docless else SYSTEM_PROMPT

    try:
        raw = client.chat(
            system_prompt,
            user_text,
            image_paths=evidence.image_paths,
            json_mode=True,
        )
    except Exception as exc:
        out = MedGemmaClinicalOutput(
            manuel_inceleme_gerekli=True,
            gerekce=f"MedGemma çağrısı başarısız: {exc}",
            guven="low",
        )
        return out, LayerResult(
            layer="medgemma",
            status=LayerStatus.INSUFFICIENT,
            message=f"MedGemma kullanılamadı: {exc}",
            detail={"error": str(exc)},
        )

    parsed = _parse_output(raw, client=client)
    parsed = _ensure_ozel_soru_cevaplari(parsed, questions)
    parsed = _apply_evidence_flags(parsed, evidence)
    docless_manual_softened = False
    if docless:
        # Belge yok: belge tabanlı alanlar değerlendirilemez. Model yanlışlıkla
        # false/true döndürse bile null'a çekilir; böylece karar birleştirmede
        # belge yokluğu EVRAK_EKSIK / BELGE_KANITI_YETERSIZ üretmez.
        parsed.islem_belge_destekli = None
        parsed.tani_belge_destekli = None
        parsed.eksik_evrak = None
        if parsed.gerekce:
            g = parsed.gerekce
            for bad in (
                "(eksik_evrak=true)",
                "eksik_evrak=true",
                "eksik_evrak = true",
            ):
                g = g.replace(bad, "(belgesiz değerlendirme)")
            parsed.gerekce = g
        parsed, docless_manual_softened = _soften_docless_manual_flag(parsed)
    call_meta = getattr(client, "last_call_meta", None)
    layer = _to_layer(parsed, raw, questions=questions, evidence=evidence, call_meta=call_meta)
    if docless_manual_softened:
        layer.detail["docless_manual_softened"] = True
    return parsed, layer


def _build_prompt(
    job: ProvizyonJob,
    evidence: EvidencePackage,
    deterministic_summary: dict[str, Any] | None,
    patient_context: Any | None = None,
    questions: list[str] | None = None,
    max_text_chars: int | None = None,
    docless: bool = False,
) -> str:
    lines: list[str] = []
    if docless:
        lines.append(
            "NOT: Bu değerlendirmede yüklenmiş belge YOKTUR. Yalnızca aşağıdaki provizyon "
            "üstverisi ve deterministik kural sonuçları ile SALT KLİNİK YERİNDELİK değerlendir. "
            "Belge tabanlı alanları (islem_belge_destekli, tani_belge_destekli, eksik_evrak) null bırak."
        )
        lines.append("")
    lines.append("PROVIZYON BİLGİSİ:")
    lines.append(f"- Hasta yaşı: {job.yas if job.yas is not None else 'bilinmiyor'}")
    lines.append(f"- Cinsiyet: {job.cinsiyet.value}")
    lines.append(f"- HUV işlem kodları: {', '.join(job.all_huv_codes()) or '-'}")
    if job.procedures:
        proc_labels = [
            f"{p.code} ({p.name})" if p.name else str(p.code)
            for p in job.procedures
            if p.code or p.name
        ]
        lines.append(f"- İşlemler: {', '.join(proc_labels)}")
    lines.append(f"- Girilen tanılar (ICD-10): {', '.join(job.diagnoses) or '-'}")
    lines.append(f"- Belgeler: {'YOK (belgesiz değerlendirme)' if docless else (', '.join(evidence.document_titles) or '-')}")

    if patient_context is not None:
        from ..persistence.patient_context import format_patient_context_for_prompt

        history_text = format_patient_context_for_prompt(patient_context)
        if history_text:
            lines.append("")
            lines.append(history_text)

    if deterministic_summary:
        lines.append("")
        lines.append("DETERMINISTIC KURAL SONUCU (referans, klinik yorumunu buna göre yap):")
        lines.append(json.dumps(deterministic_summary, ensure_ascii=False))

    questions = questions or _questions_for_job(job, docless=docless)
    if questions:
        lines.append("")
        lines.append(
            "KLİNİK SORULAR (her birine ozel_soru_cevaplari içinde ayrı soru-cevap olarak yanıt ver; "
            "bu alan ZORUNLUDUR ve boş bırakılmamalıdır):"
        )
        for idx, soru in enumerate(questions, 1):
            lines.append(f"{idx}. {soru}")

    if evidence.has_text:
        lines.append("")
        lines.append("BELGE METNİ (OCR/çıkarılmış, ilgililik sırasına göre):")
        text = evidence.text_evidence
        max_chars = max_text_chars if max_text_chars is not None else settings.TEXT_EVIDENCE_MAX_CHARS
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[metin kısaltıldı]"
        lines.append(text)

    if evidence.has_images:
        lines.append("")
        if evidence.selected_page_numbers:
            page_list = ", ".join(str(n) for n in evidence.selected_page_numbers)
            lines.append(
                f"Ayrıca {len(evidence.image_paths)} belge görseli ekte verildi "
                f"(global sayfa no: {page_list}); klinik değerlendirmede kullan."
            )
        else:
            lines.append(
                f"Ayrıca {len(evidence.image_paths)} belge görseli ekte verildi; "
                "klinik değerlendirmede kullan."
            )

    if evidence.notes:
        lines.append("")
        lines.append("NOT: " + "; ".join(evidence.notes))

    lines.append("")
    lines.append(JSON_SCHEMA_HINT)
    return "\n".join(lines)


def _parse_output(
    raw: str,
    *,
    client: MedGemmaVisionClient | None = None,
) -> MedGemmaClinicalOutput:
    data = _extract_json(raw)
    if data is None and client is not None:
        try:
            repaired = client.chat(
                REPAIR_SYSTEM_PROMPT,
                _repair_prompt(raw),
                json_mode=True,
            )
            data = _extract_json(repaired)
        except Exception:
            data = None

    if data is None:
        return MedGemmaClinicalOutput(
            manuel_inceleme_gerekli=True,
            gerekce="MedGemma çıktısı JSON olarak ayrıştırılamadı.",
            guven="low",
            raw_text=raw[:2000],  # extra alan (model_config extra=allow)
        )

    # ozel_soru_cevaplari normalizasyonu
    cevaplar: list[OzelSoruCevap] = []
    for item in data.get("ozel_soru_cevaplari") or []:
        if isinstance(item, dict):
            cevaplar.append(
                OzelSoruCevap(
                    soru=str(item.get("soru", "")),
                    cevap=str(item.get("cevap", "")),
                )
            )
    try:
        return MedGemmaClinicalOutput(
            islem_belge_destekli=_as_tribool(data.get("islem_belge_destekli")),
            tani_belge_destekli=_as_tribool(data.get("tani_belge_destekli")),
            yas_cinsiyet_uygun=_as_tribool(data.get("yas_cinsiyet_uygun")),
            klinik_celiski=_as_tribool(data.get("klinik_celiski")),
            eksik_evrak=_as_tribool(data.get("eksik_evrak")),
            manuel_inceleme_gerekli=bool(data.get("manuel_inceleme_gerekli", False)),
            ozel_soru_cevaplari=cevaplar,
            gerekce=str(data.get("gerekce", "")),
            guven=str(data.get("guven", "medium")).lower(),
        )
    except Exception:
        return MedGemmaClinicalOutput(
            manuel_inceleme_gerekli=True,
            gerekce="MedGemma çıktısı şema doğrulamasından geçmedi.",
            guven="low",
        )


def _as_tribool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "evet", "var", "yes", "1"}:
        return True
    if text in {"false", "hayir", "hayır", "yok", "no", "0"}:
        return False
    return None


def _extract_json(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    cleaned = raw.strip()
    # ```json ... ``` bloklarını temizle
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # İlk {...} bloğunu yakalamayı dene
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _repair_prompt(response: str) -> str:
    return (
        "Aşağıdaki bozuk veya markdown içeren yanıtı geçerli JSON nesnesine dönüştür:\n\n"
        f"{response[:8000]}"
    )


def _soften_docless_manual_flag(
    parsed: MedGemmaClinicalOutput,
) -> tuple[MedGemmaClinicalOutput, bool]:
    """Kural-review yüzünden basılan manuel bayrağını belgesiz akışta yumuşat.

    Model sıkça deterministik review_required görünce manuel_inceleme_gerekli=true
    basar. Belgesiz + orta/yüksek güven + klinik çelişki yokken bu bayrak
    tanı-REVIEW override'ını gereksiz kapatır; yumuşatma yalnız bu koşullarda.
    """

    if parsed.guven not in ("medium", "high"):
        return parsed, False
    if parsed.klinik_celiski is True or parsed.yas_cinsiyet_uygun is False:
        return parsed, False
    if not parsed.manuel_inceleme_gerekli:
        return parsed, False
    parsed.manuel_inceleme_gerekli = False
    return parsed, True


def _positive_clinical_signals(parsed: MedGemmaClinicalOutput) -> bool:
    return (
        parsed.islem_belge_destekli is True
        and parsed.tani_belge_destekli is not False
        and parsed.yas_cinsiyet_uygun is not False
        and parsed.klinik_celiski is not True
        and parsed.eksik_evrak is not True
    )


def _to_layer(
    parsed: MedGemmaClinicalOutput,
    raw: str,
    *,
    questions: list[str] | None = None,
    evidence: EvidencePackage | None = None,
    call_meta: Any | None = None,
) -> LayerResult:
    from ..models import LayerStatus

    # Negatif klinik sinyaller FAIL'e, belirsizlik REVIEW'e işaret eder.
    hard_negative = (
        parsed.islem_belge_destekli is False
        or parsed.tani_belge_destekli is False
        or parsed.yas_cinsiyet_uygun is False
        or parsed.klinik_celiski is True
        or parsed.eksik_evrak is True
    )
    if hard_negative:
        status = LayerStatus.FAIL
    elif parsed.guven == "low":
        status = LayerStatus.REVIEW
    elif parsed.manuel_inceleme_gerekli and not _positive_clinical_signals(parsed):
        status = LayerStatus.REVIEW
    elif _positive_clinical_signals(parsed):
        status = LayerStatus.PASS
    else:
        status = LayerStatus.REVIEW

    detail: dict[str, Any] = {
            "raw_excerpt": raw[:1000],
            "raw_response": raw,
            "ozel_soru_cevaplari": [
                {"soru": item.soru, "cevap": item.cevap}
                for item in parsed.ozel_soru_cevaplari
            ],
            "questions": questions or [],
            "selected_pages": evidence.selected_page_numbers if evidence else [],
            "excluded_pages": evidence.excluded_page_numbers if evidence else [],
            "partial_vision": bool(evidence and evidence.partial_vision),
        }
    if call_meta is not None:
        detail["vision_requested"] = call_meta.vision_requested
        detail["vision_sent"] = call_meta.vision_sent
        detail["vision_dropped"] = call_meta.vision_dropped
        detail["fallback_reason"] = call_meta.fallback_reason
        detail["attempts"] = call_meta.attempts
        detail["json_mode_used"] = call_meta.json_mode_used

    return LayerResult(
        layer="medgemma",
        status=status,
        message=parsed.gerekce or "MedGemma klinik değerlendirmesi tamamlandı.",
        detail=detail,
    )
