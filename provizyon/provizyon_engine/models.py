"""Provizyon orkestratör veri sözleşmeleri (pydantic).

Burada tanımlanan tipler kuyruğa giren işi (``ProvizyonJob``), MedGemma'nın
structured klinik çıktısını (``MedGemmaClinicalOutput``), her karar katmanının
ara sonucunu ve nihai sonucu (``JobResult``) kapsar.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_SUT_CODE_RE = re.compile(r"^\d{6}$")
_HUV_CODE_RE = re.compile(r"^\d{2}\.\d")
_TZH_CODE_RE = re.compile(r"^TZH\.", re.IGNORECASE)

DiagnosisCodeSource = Literal["huv", "sut", "both", "none"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class DecisionType(str, Enum):
    """Ürün seviyesi karar tipi (Gelir Koruma / risk kuyruğu)."""

    AUTOMATIC_DEFENSIBLE = "automatic_defensible"
    MANUAL_REVIEW = "manual_review"
    LOW_RISK = "low_risk"


class RiskLevel(str, Enum):
    """Risk matrisi seviyesi."""

    RED = "red"
    ORANGE = "orange"
    YELLOW = "yellow"
    GREEN = "green"
    BLUE = "blue"
    GRAY = "gray"


class KararDurumu(str, Enum):
    """Nihai provizyon kararları (karar sırası.txt Adım 6)."""

    UYGUN = "uygun"
    TANI_EKSIK = "tani_eksik"
    TANI_UYUMSUZ = "tani_uyumsuz"
    EVRAK_EKSIK = "evrak_eksik"
    YANLIS_HASTA_BELGESI = "yanlis_hasta_belgesi"
    KLINIK_UYUMSUZLUK = "klinik_uyumsuzluk"
    BELGE_KANITI_YETERSIZ = "belge_kaniti_yetersiz"
    MANUEL_INCELEME = "manuel_inceleme"
    AI_YORUMU_BEKLENIYOR = "ai_yorumu_bekleniyor"
    BELGE_ANALIZI_TAMAMLANAMADI = "belge_analizi_tamamlanamadi"


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Cinsiyet(str, Enum):
    ERKEK = "erkek"
    KADIN = "kadin"
    BILINMIYOR = "bilinmiyor"


class DocumentInput(BaseModel):
    """İşle birlikte gelen bir belge referansı (dosya sistemi yolu)."""

    model_config = ConfigDict(extra="allow")

    path: str = Field(description="DOCUMENT_ROOT'a göreli veya mutlak dosya yolu.")
    doc_type: str | None = Field(default=None, description="Belge türü (epikriz, rapor, görüntü vb.).")
    doc_type_confidence: str | None = Field(default=None, description="Belge türü güveni: high | medium | low.")
    doc_type_source: str | None = Field(default=None, description="Belge türü kaynağı: filename | peek | ocr_peek | full_text | …")
    title: str | None = None
    # Belge meta verisinde hasta bilgisi varsa hasta-belge uyumu için kullanılır.
    declared_hasta_id: str | None = None
    declared_patient_name: str | None = None


class ProcedureInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str = Field(description="HUV veya SUT kodu.")
    code_type: str = Field(default="auto", description="auto | HUV | SUT")
    name: str | None = None
    quantity: int | float | None = 1
    date: str | None = None
    note: str | None = None


class ProvizyonJob(BaseModel):
    """Kuyruğa giren provizyon işi (karar sırası.txt Adım 1)."""

    model_config = ConfigDict(extra="allow")

    provizyon_id: str
    hasta_id: str | None = None
    tc_kimlik: str | None = Field(default=None, description="TC kimlik numarası (11 hane). Sicil no'dan ayrı tutulur.")
    patient_name: str | None = None
    yas: int | None = None
    cinsiyet: Cinsiyet = Cinsiyet.BILINMIYOR
    facility_level: str | None = None
    # Opsiyonel; asıl kaynak genelde notes içindeki "Kurum: …".
    # Alanın tamamen kaldırılması AttributeError üretiyordu — None default ile duruyor.
    institution_name: str | None = None

    huv_codes: list[str] = Field(default_factory=list)
    sut_codes: list[str] = Field(default_factory=list)
    code_family: str | None = Field(
        default=None,
        description="Kaynak sistemden gelen kod ailesi: HUV | SUT. Varsa tanı yönlendirmesi buna göre yapılır.",
    )
    procedures: list[ProcedureInput] = Field(default_factory=list)
    diagnoses: list[str] = Field(default_factory=list)
    documents: list[DocumentInput] = Field(default_factory=list)
    # Sağlık sisteminin MedGemma'ya sordurmak istediği özel sorular.
    model_sorulari: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    # Belge modu: None/"normal" = belgeli akış; "skipped_full_pipeline" = belgesiz
    # policy+klinik değerlendirme (belge-hasta ve zorunlu evrak katmanları SKIPPED,
    # MedGemma yalnızca üstveri/metinle çalışır, belge yokluğu hata sayılmaz).
    documents_mode: str | None = None

    enqueued_at: str = Field(default_factory=_utcnow)

    def institution_label(self) -> str | None:
        """Kurum adı — belgesiz/belgeli ortak sözleşme.

        Öncelik: ``institution_name`` alanı, yoksa ``notes`` içindeki ``Kurum: …``.
        """

        if isinstance(self.institution_name, str) and self.institution_name.strip():
            return self.institution_name.strip()
        for note in self.notes or []:
            text = str(note).strip()
            if text.lower().startswith("kurum:"):
                value = text.split(":", 1)[1].strip()
                if value:
                    return value
        return None

    def all_huv_codes(self) -> list[str]:
        """huv_codes + procedures içindeki gerçek HUV kodlarını birleştirir.

        Branş/muayene gibi belirsiz ``auto``/``other`` kodlar (örn. ``1700``)
        HUV tanı motoruna sokulmaz.
        """

        codes: list[str] = list(self.huv_codes)
        for proc in self.procedures:
            if not proc.code:
                continue
            code = proc.code.strip()
            ctype = (proc.code_type or "auto").strip().upper()
            if ctype == "SUT" or self._is_sut_code(code):
                continue
            if ctype == "HUV" or (ctype in {"AUTO", ""} and self._is_huv_code(code)):
                codes.append(code)
        seen: set[str] = set()
        out: list[str] = []
        for code in codes:
            key = code.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    @staticmethod
    def _is_sut_code(code: str) -> bool:
        code = code.strip()
        return bool(_SUT_CODE_RE.fullmatch(code)) and not code.startswith("0")

    @staticmethod
    def _is_huv_code(code: str) -> bool:
        """Sayısal HUV biçimi (örn. 24.73601); TZH meta veya branş kodu değil."""

        return bool(_HUV_CODE_RE.match(code.strip()))

    def all_sut_codes(self) -> list[str]:
        """sut_codes + procedures içindeki SUT kodlarını birleştirir."""

        codes: list[str] = list(self.sut_codes)
        for proc in self.procedures:
            if not proc.code:
                continue
            if proc.code_type == "SUT" or self._is_sut_code(proc.code):
                codes.append(proc.code)
        seen: set[str] = set()
        out: list[str] = []
        for code in codes:
            key = code.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    def diagnosis_code_source(self) -> DiagnosisCodeSource:
        """Provizyon HUV mu SUT mu kodlarla geldi — tanı motoru yönlendirmesi."""

        # Önce gerçek kod içeriklerine bak; code_family yalnızca destekleyici.
        # (Boş liste_tip / branş kodları family'yi yanıltmasın.)
        huv = self.all_huv_codes()
        sut = self.all_sut_codes()
        if huv and sut:
            return "both"
        if huv and not sut:
            return "huv"
        if sut and not huv:
            return "sut"

        explicit = (self.code_family or "").strip().upper()
        if explicit == "SUT":
            return "sut"
        if explicit == "HUV":
            return "huv"

        for proc in self.procedures:
            if not proc.code:
                continue
            code = proc.code.strip()
            ctype = (proc.code_type or "").strip().upper()
            if ctype == "SUT" or self._is_sut_code(code):
                return "sut"
            if ctype == "HUV" or self._is_huv_code(code):
                return "huv"
            if _TZH_CODE_RE.match(code) or ctype in {"OTHER", "TZH", "META"}:
                continue

        return "none"


# --------------------------------------------------------------------------
# MedGemma structured çıktı sözleşmesi (serbest metin DEĞİL)
# --------------------------------------------------------------------------


class OzelSoruCevap(BaseModel):
    model_config = ConfigDict(extra="allow")

    soru: str
    cevap: str


class MedGemmaClinicalOutput(BaseModel):
    """MedGemma'nın klinik değerlendirme çıktısı (karar sırası.txt Adım 5)."""

    model_config = ConfigDict(extra="allow")

    islem_belge_destekli: bool | None = None
    tani_belge_destekli: bool | None = None
    yas_cinsiyet_uygun: bool | None = None
    klinik_celiski: bool | None = None
    eksik_evrak: bool | None = None
    manuel_inceleme_gerekli: bool = False
    ozel_soru_cevaplari: list[OzelSoruCevap] = Field(default_factory=list)
    gerekce: str = ""
    guven: str = "medium"  # high | medium | low
    # 0–100 klinik yerindelik skoru (MedGemma üretir; kural özeti + kanıta dayanır).
    klinik_skor: int | None = None
    skor_dayanak: str = Field(
        default="",
        description="Skorun kısa dayanağı (kural + klinik sinyaller).",
    )


# --------------------------------------------------------------------------
# Ara karar katmanları
# --------------------------------------------------------------------------


class LayerStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    REVIEW = "review"
    INSUFFICIENT = "insufficient"
    SKIPPED = "skipped"


class LayerResult(BaseModel):
    """Her karar katmanının (belge-hasta, evrak, tanı, SUT, AI) ara sonucu."""

    model_config = ConfigDict(extra="allow")

    layer: str
    status: LayerStatus
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class RiskReason(BaseModel):
    """Provizyoncu dostu risk gerekçesi (işlem/tanı/kural düzeyinde)."""

    model_config = ConfigDict(extra="allow")

    code: str = ""
    layer: str = ""
    rule_trigger: str = ""
    message: str = ""
    action: str = ""
    decision_type: DecisionType = DecisionType.MANUAL_REVIEW
    risk_level: RiskLevel = RiskLevel.GRAY


class JobResult(BaseModel):
    """Orkestratörün ürettiği nihai sonuç."""

    model_config = ConfigDict(extra="allow")

    provizyon_id: str
    hasta_id: str | None = None
    status: JobStatus = JobStatus.DONE
    nihai_karar: KararDurumu
    gerekce: str = ""
    # MedGemma'nın 0–100 klinik skoru (nihai karar yanında gösterilir).
    klinik_skor: int | None = None
    skor_dayanak: str = ""
    decision_type: DecisionType | None = None
    risk_level: RiskLevel | None = None
    risk_reasons: list[RiskReason] = Field(default_factory=list)

    # Adım bazlı ara sonuçlar
    belge_hasta: LayerResult | None = None
    zorunlu_evrak: LayerResult | None = None
    tani_kurali: LayerResult | None = None
    sut_tani_kurali: LayerResult | None = None
    sut_kurali: LayerResult | None = None
    medgemma: MedGemmaClinicalOutput | None = None

    warnings: list[str] = Field(default_factory=list)
    # Ham motor çıktıları (denetim/izlenebilirlik için).
    raw: dict[str, Any] = Field(default_factory=dict)

    started_at: str = Field(default_factory=_utcnow)
    finished_at: str = Field(default_factory=_utcnow)
    error: str | None = None
