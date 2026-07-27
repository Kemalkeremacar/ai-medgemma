"""Provizyon orkestratörü (karar sırası.txt'deki 10 adımlı akış).

Akış ve öncelik:
  1. İş geldi (ProvizyonJob)
  2. Belgeleri çözümle (dosya sistemi)
  3. Belge içeriğini çıkar + OCR
  4. Belge-hasta uyumu  -> uyumsuz ise YANLIS_HASTA_BELGESI (erken çıkış, RAG'e yazma)
  5. Zorunlu evrak       -> belge gerekli/yok ise EVRAK_EKSIK (erken çıkış, MedGemma yok)
  6. HUV+ICD tanı kuralı — yalnızca HUV provizyonlarında
  6b. SUT+ICD tanı kuralı — yalnızca SUT provizyonlarında
  7. SUT işlem kuralı   (mevcut SUTEvaluator/advise)
  8. MedGemma klinik     (structured JSON, hibrit metin/görsel)
  9. Nihai karar birleştirme (öncelik sırası)
 10. Persist: Qdrant patient_findings + sonuç deposu

Bağımlılıklar (belge kaynağı, MedGemma client, persistence) enjekte edilebilir;
böylece test ortamında dış servisler olmadan çalışır.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Any

from . import settings
from .decision import merge_decisions
from .risk_normalizer import item_requires_strict_manual_review
from .documents import (
    FilesystemDocumentSource,
    build_evidence_package,
    extract_document,
    match_documents,
    ocr_document,
)
from .documents.classify import (
    classify_evidence_role,
    enrich_document_titles,
    infer_gender_from_documents,
    refine_doc_types,
)
from .documents.extract import ExtractedDocument
from .models import (
    Cinsiyet,
    JobResult,
    JobStatus,
    KararDurumu,
    LayerResult,
    LayerStatus,
    ProvizyonJob,
)


@dataclass
class OrchestratorConfig:
    enable_diagnosis: bool = True
    enable_sut_diagnosis: bool = True
    enable_sut_rules: bool = True
    enable_medgemma: bool = True
    enable_diagnosis_payment: bool = True
    enable_persistence: bool = True
    enable_patient_context: bool = True
    use_qdrant_rag: bool = True
    include_vision: bool = True
    # HUV→SUT catalog eşleştirmesi; varsayılan settings'ten (genelde kapalı).
    enable_huv_sut_crosswalk: bool = False


class ProvizyonOrchestrator:
    def __init__(
        self,
        *,
        config: OrchestratorConfig | None = None,
        document_source: Any | None = None,
        medgemma_client: Any | None = None,
        findings_writer: Any | None = None,
        findings_reader: Any | None = None,
    ) -> None:
        if config is None:
            from . import settings as _settings

            config = OrchestratorConfig(
                enable_huv_sut_crosswalk=_settings.ENABLE_HUV_SUT_CROSSWALK,
            )
        self.config = config
        self.document_source = document_source or FilesystemDocumentSource()
        self._medgemma_client = medgemma_client
        self._findings_writer = findings_writer
        self._findings_reader = findings_reader

    # ----------------------------------------------------------------------
    def run(self, job: ProvizyonJob) -> JobResult:
        result = JobResult(
            provizyon_id=job.provizyon_id,
            hasta_id=job.hasta_id,
            status=JobStatus.PROCESSING,
            nihai_karar=KararDurumu.MANUEL_INCELEME,
        )
        try:
            return self._run_pipeline(job, result)
        except Exception as exc:  # hiçbir iş worker'ı çökertmemeli
            result.status = JobStatus.FAILED
            result.nihai_karar = KararDurumu.MANUEL_INCELEME
            result.error = f"{exc}"
            result.gerekce = f"Orkestratör hatası: {exc}"
            result.raw["traceback"] = traceback.format_exc()
            return result

    # ----------------------------------------------------------------------
    def _run_pipeline(self, job: ProvizyonJob, result: JobResult) -> JobResult:
        # Belgesiz tam akış modu: belge indirilmemiştir; belge-hasta ve zorunlu
        # evrak katmanları SKIPPED işaretlenir (hata değil), MedGemma yalnızca
        # provizyon üstverisiyle (yaş/cinsiyet/tanı/işlem + katman özeti) çalışır.
        docless = (job.documents_mode or "").strip() == "skipped_full_pipeline"
        result.raw["documents_mode"] = "skipped_full_pipeline" if docless else "normal"
        if docless:
            result.warnings.append(
                "Belgesiz değerlendirme; evrak kapısı atlandı, klinik AI metin girdileriyle çalıştı."
            )

        # Adım 2-3: belgeleri çözümle, çıkar, OCR
        # Belgesiz DB intake: belge yok — folder/OCR yolunu tamamen atla.
        extracted: list[ExtractedDocument] = []
        if docless:
            existing = []
            missing = []
            documents_present = False
            document_analysis_failed = False
        else:
            refs = self.document_source.resolve_all(job.documents)
            existing = [r for r in refs if r.exists]
            missing = [r for r in refs if not r.exists]
            if missing:
                result.warnings.extend(
                    f"Belge bulunamadı: {r.path} ({r.error})" for r in missing
                )

            for ref in existing:
                doc = extract_document(ref, render_images=True)
                doc = ocr_document(doc)
                extracted.append(doc)

            result.warnings.extend(
                refine_doc_types(
                    extracted,
                    procedure_names=[p.name for p in job.procedures if p.name],
                )
            )
            enrich_document_titles(extracted)
            if job.cinsiyet == Cinsiyet.BILINMIYOR:
                inferred = infer_gender_from_documents(extracted)
                if inferred is not None:
                    job.cinsiyet = inferred

            documents_present = len(existing) > 0
            document_analysis_failed = documents_present and all(
                (not doc.combined_text) and all(p.image_path is None for p in doc.pages)
                for doc in extracted
            )
        result.raw["documents"] = {
            "provided": len(job.documents),
            "found": len(existing),
            "missing": len(missing),
            "missing_files": [r.path.name for r in missing] if missing else [],
            "analysis_failed": document_analysis_failed,
            "items": [
                {
                    "file": doc.ref.path.name,
                    "title": doc.ref.title,
                    "doc_type": doc.ref.doc_type,
                    "doc_type_confidence": doc.ref.meta.get("doc_type_confidence"),
                    "doc_type_source": doc.ref.meta.get("doc_type_source"),
                    "evidence_role": classify_evidence_role(doc.ref.doc_type),
                    "text_chars": len(doc.combined_text or ""),
                    "page_count": len(doc.pages),
                    "ocr_pages": sum(1 for p in doc.pages if p.text_source == "ocr"),
                    "embedded_images": int(doc.meta.get("embedded_images") or 0),
                    "ocr_quality_avg": round(
                        sum(p.ocr_quality for p in doc.pages if p.ocr_quality is not None)
                        / max(1, sum(1 for p in doc.pages if p.ocr_quality is not None)),
                        3,
                    )
                    if any(p.ocr_quality is not None for p in doc.pages)
                    else None,
                    "ocr_low_quality_pages": sum(
                        1
                        for p in doc.pages
                        if p.ocr_quality is not None and p.ocr_quality < settings.OCR_MIN_QUALITY
                    ),
                    "needs_ocr": doc.needs_ocr,
                    "error": doc.error,
                    "ocr_error_count": len(doc.meta.get("ocr_errors") or []),
                }
                for doc in extracted
                if doc.ref.exists
            ],
        }
        result.raw["pipeline"] = {
            "version": "v4",
            "ocr_all_pages": settings.OCR_ALL_PAGES,
            "vision_max_images": settings.VISION_MAX_IMAGES,
            "full_vision": settings.VISION_MAX_IMAGES <= 0,
            "embedded_image_min_area": settings.EMBEDDED_IMAGE_MIN_AREA,
        }
        result.raw["job_meta"] = {
            "tc_kimlik": job.tc_kimlik,
            "patient_name": job.patient_name,
            "hasta_id": job.hasta_id,
            "yas": job.yas,
            "cinsiyet": job.cinsiyet.value,
            "facility_level": job.facility_level,
            "institution_name": job.institution_label(),
            "notes": list(job.notes or []),
            "model_sorulari": job.model_sorulari,
            "diagnosis_code_source": job.diagnosis_code_source(),
            "code_family": job.code_family,
            "huv_codes": job.all_huv_codes(),
            "sut_codes": job.all_sut_codes(),
            "diagnoses": list(job.diagnoses),
            "documents_mode": job.documents_mode,
            "procedures": [
                {"code": p.code, "name": p.name, "code_type": getattr(p, "code_type", None)}
                for p in job.procedures
                if p.code or p.name
            ],
        }

        # Adım 4: Belge-hasta uyumu (önce hasta/belge doğruluğu)
        if docless:
            result.belge_hasta = LayerResult(
                layer="belge_hasta",
                status=LayerStatus.SKIPPED,
                message="Belgesiz değerlendirme; belge-hasta uyumu kontrolü atlandı.",
            )
        elif documents_present:
            belge_hasta = match_documents(job, extracted)
            result.belge_hasta = belge_hasta
            if belge_hasta.status == LayerStatus.FAIL:
                return self._finalize(
                    job, result, KararDurumu.YANLIS_HASTA_BELGESI,
                    belge_hasta.message, allow_document_rag=False,
                )
            if "Doğrulanamayan" in (belge_hasta.message or ""):
                result.warnings.append(belge_hasta.message)

        # Adım 5: Zorunlu evrak kontrolü
        if docless:
            result.zorunlu_evrak = LayerResult(
                layer="zorunlu_evrak",
                status=LayerStatus.SKIPPED,
                message="Belgesiz değerlendirme; zorunlu evrak kapısı atlandı (belge yokluğu hata sayılmaz).",
            )
        else:
            from .documents.requirement import check_requirement

            zorunlu = check_requirement(
                job,
                documents_present=documents_present,
                enable_huv_sut_crosswalk=self.config.enable_huv_sut_crosswalk,
            )
            result.zorunlu_evrak = zorunlu
            if zorunlu.status == LayerStatus.FAIL:
                return self._finalize(
                    job, result, KararDurumu.EVRAK_EKSIK, zorunlu.message,
                )

        code_source = job.diagnosis_code_source()
        if code_source == "none":
            result.warnings.append(
                "Tanılabilecek HUV/SUT işlem kodu yok (branş/TZH/other); "
                "tanı katmanları atlandı, karar ağırlıklı MedGemma + merge."
            )

        # Adım 6: HUV+ICD tanı kuralı (HUV provizyonları)
        if self.config.enable_diagnosis and code_source in ("huv", "both"):
            from .engines.diagnosis import check_diagnoses

            result.tani_kurali = check_diagnoses(job.all_huv_codes(), job.diagnoses)
        elif self.config.enable_diagnosis:
            skip_msg = (
                "SUT provizyonu; HUV tanı kuralı değerlendirilmedi."
                if code_source == "sut"
                else "Değerlendirilebilir HUV kodu yok; HUV tanı kuralı atlandı."
            )
            result.tani_kurali = LayerResult(
                layer="tani_kurali",
                status=LayerStatus.SKIPPED,
                message=skip_msg,
                detail={"diagnosis_code_source": code_source},
            )

        # Adım 6b: SUT+ICD tanı kuralı (SUT provizyonları)
        if self.config.enable_sut_diagnosis and code_source in ("sut", "both"):
            from .engines.sut_diagnosis import check_sut_diagnoses

            result.sut_tani_kurali = check_sut_diagnoses(job)
        elif self.config.enable_sut_diagnosis:
            skip_msg = (
                "HUV provizyonu; SUT tanı kuralı değerlendirilmedi."
                if code_source == "huv"
                else "Değerlendirilebilir SUT kodu yok; SUT tanı kuralı atlandı."
            )
            result.sut_tani_kurali = LayerResult(
                layer="sut_tani_kurali",
                status=LayerStatus.SKIPPED,
                message=skip_msg,
                detail={"diagnosis_code_source": code_source},
            )

        # Adım 7: SUT işlem kuralı
        if self.config.enable_sut_rules:
            from .engines.sut_rules import check_sut_rules

            result.sut_kurali = check_sut_rules(
                job,
                use_qdrant=self.config.use_qdrant_rag,
                enable_huv_sut_crosswalk=self.config.enable_huv_sut_crosswalk,
            )

        # Adım 8: MedGemma klinik değerlendirme (belge varsa ve analiz başarılıysa)
        # Tanı FAIL -> sonuç zaten belli (TANI_EKSIK/TANI_UYUMSUZ); MedGemma ~25sn boşa gider.
        tani_fail = (
            (result.tani_kurali and result.tani_kurali.status == LayerStatus.FAIL)
            or (result.sut_tani_kurali and result.sut_tani_kurali.status == LayerStatus.FAIL)
        )
        medgemma_layer: LayerResult | None = None
        run_medgemma = self.config.enable_medgemma and not tani_fail and (
            docless or (documents_present and not document_analysis_failed)
        )
        if run_medgemma:
            patient_context = self._load_patient_context(job, result)
            medgemma_layer = self._run_medgemma(
                job, extracted, result, patient_context=patient_context, docless=docless
            )
        elif tani_fail:
            result.warnings.append("Tanı kuralı FAIL; MedGemma klinik değerlendirmesi atlandı (maliyet/hız).")

        from .documents.rejection_signals import scan_extracted_documents

        prior_rejection_signals = scan_extracted_documents(extracted)
        if prior_rejection_signals:
            result.raw["prior_rejection_signals"] = prior_rejection_signals
            result.warnings.append(
                f"Belgelerde iade/red sinyali ({len(prior_rejection_signals)}); otomatik onay engellendi."
            )

        # Adım 9: Nihai karar birleştirme
        outcome = merge_decisions(
            belge_hasta=result.belge_hasta,
            zorunlu_evrak=result.zorunlu_evrak,
            tani_kurali=result.tani_kurali,
            sut_tani_kurali=result.sut_tani_kurali,
            sut_kurali=result.sut_kurali,
            medgemma=result.medgemma,
            medgemma_layer=medgemma_layer,
            document_analysis_failed=document_analysis_failed,
            prior_rejection_signals=prior_rejection_signals or None,
        )

        # Adım 9b: Tanı-işlem geçmiş ödeme eğilimi sinyali (mevcut kararı bozmaz).
        if self.config.enable_diagnosis_payment and settings.ENABLE_DIAGNOSIS_PAYMENT_SIGNAL:
            self._apply_diagnosis_payment(job, result, outcome)

        return self._finalize(
            job,
            result,
            outcome.karar,
            outcome.gerekce,
            extra_warnings=outcome.warnings,
            allow_document_rag=outcome.karar != KararDurumu.YANLIS_HASTA_BELGESI,
            decision_type=outcome.decision_type,
            risk_level=outcome.risk_level,
            risk_reasons=outcome.risk_reasons,
        )

    # ----------------------------------------------------------------------
    def _apply_diagnosis_payment(self, job: ProvizyonJob, result: JobResult, outcome) -> None:
        """Geçmiş ödeme eğilimi sinyalini karara işler (Adım 9b).

        Sinyal katmanı hiçbir koşulda mevcut kararı veya pipeline'ı çökertmez.
        """

        try:
            from .engines.diagnosis_payment import (
                apply_diagnosis_payment_signals,
                collect_diagnosis_payment_signals,
            )

            reasons, signals = collect_diagnosis_payment_signals(job)
            result.raw["diagnosis_payment_signals"] = {
                "collection": settings.DIAGNOSIS_PROCEDURE_COLLECTION,
                "signals": signals,
            }
            if not reasons:
                return

            updated = apply_diagnosis_payment_signals(
                karar=outcome.karar,
                decision_type=outcome.decision_type,
                risk_level=outcome.risk_level,
                risk_reasons=outcome.risk_reasons,
                signal_reasons=reasons,
            )
            escalated = updated.karar != outcome.karar
            outcome.karar = updated.karar
            outcome.decision_type = updated.decision_type
            outcome.risk_level = updated.risk_level
            outcome.risk_reasons = updated.risk_reasons
            if escalated:
                signal_msg = reasons[0].message
                outcome.gerekce = (
                    signal_msg
                    if not outcome.gerekce
                    else f"{outcome.gerekce} | {signal_msg}"
                )
        except Exception as exc:
            result.warnings.append(f"Tanı-işlem ödeme sinyali okunamadı: {exc}")
            result.raw["diagnosis_payment_signals"] = {
                "collection": settings.DIAGNOSIS_PROCEDURE_COLLECTION,
                "error": str(exc),
            }

    # ----------------------------------------------------------------------
    def _load_patient_context(self, job: ProvizyonJob, result: JobResult):
        if not self.config.enable_patient_context:
            return None
        try:
            from .persistence.patient_context import load_patient_context

            ctx = load_patient_context(job, reader=self._findings_reader)
            if ctx is not None:
                result.raw["patient_context"] = ctx.to_dict()
                if ctx.notes:
                    result.warnings.extend(ctx.notes)
            return ctx
        except Exception as exc:
            result.warnings.append(f"Hasta bağlamı yüklenemedi: {exc}")
            return None

    def _run_medgemma(
        self,
        job: ProvizyonJob,
        extracted: list[ExtractedDocument],
        result: JobResult,
        *,
        patient_context=None,
        docless: bool = False,
    ) -> LayerResult | None:
        from .medgemma.clinical_eval import evaluate_clinical

        evidence = build_evidence_package(
            extracted,
            include_images=self.config.include_vision and not docless,
            huv_codes=job.all_huv_codes(),
            sut_codes=job.all_sut_codes(),
            icd_codes=job.diagnoses,
            patient_name=job.patient_name,
            procedure_names=[p.name for p in job.procedures if p.name],
            extra_keywords=self._evidence_extra_keywords(result),
        )
        deterministic_summary = self._deterministic_summary(result, docless=docless)
        result.raw["deterministic_summary"] = deterministic_summary
        out, layer = evaluate_clinical(
            job,
            evidence,
            deterministic_summary=deterministic_summary,
            patient_context=patient_context,
            client=self._medgemma_client,
            docless=docless,
        )
        result.medgemma = out
        result.raw["medgemma_layer"] = layer.model_dump(mode="json")
        layer_detail = (layer.detail or {}) if layer else {}
        # Üst düzey iz: MedGemma’ya giden / gelen (dashboard’da öne çıkarılır).
        if isinstance(layer_detail.get("exchange"), dict):
            result.raw["medgemma_exchange"] = layer_detail["exchange"]
        result.raw["medgemma_evidence"] = {
            "selected_pages": evidence.selected_page_numbers,
            "excluded_pages": evidence.excluded_page_numbers,
            "partial_vision": evidence.partial_vision,
            "image_count": len(evidence.image_paths),
            "vision_requested": layer_detail.get("vision_requested", len(evidence.image_paths)),
            "vision_sent": layer_detail.get("vision_sent", len(evidence.image_paths)),
            "vision_dropped": bool(layer_detail.get("vision_dropped")),
            "embedded_images": sum(
                int(doc.meta.get("embedded_images") or 0)
                for doc in extracted
                if doc.ref.exists and not doc.error
            ),
            "text_chars": len(evidence.text_evidence),
            "document_titles": list(evidence.document_titles),
            "notes": list(evidence.notes),
        }
        if layer_detail.get("vision_dropped"):
            result.warnings.append(
                f"MedGemma vision düşürüldü ({layer_detail.get('vision_sent', 0)}/"
                f"{layer_detail.get('vision_requested', '?')} görsel gönderildi)."
            )
        elif layer_detail.get("vision_sent", 0) < layer_detail.get("vision_requested", 0):
            result.warnings.append(
                f"MedGemma kısmi vision: {layer_detail.get('vision_sent')}/"
                f"{layer_detail.get('vision_requested')} görsel."
            )
        if evidence.notes:
            result.warnings.extend(evidence.notes)
        return layer

    def _evidence_extra_keywords(self, result: JobResult) -> list[str]:
        return []

    def _deterministic_summary(
        self, result: JobResult, *, docless: bool = False
    ) -> dict[str, Any]:
        """MedGemma'ya giden ince kural özeti (+ kompakt item bağlamı).

        Tam ``LayerResult.detail`` gönderilmez (token/gürültü). Bunun yerine
        status/message + en fazla N kritik item (kod, policy, soft/strict, kısa gerekçe).
        """
        summary: dict[str, Any] = {}
        if result.belge_hasta:
            summary["belge_hasta"] = {
                "status": result.belge_hasta.status.value,
                "message": result.belge_hasta.message,
            }
        if result.zorunlu_evrak:
            summary["zorunlu_evrak"] = {
                "status": result.zorunlu_evrak.status.value,
                "message": result.zorunlu_evrak.message,
            }
        if result.tani_kurali:
            summary["tani_kurali"] = {
                "status": result.tani_kurali.status.value,
                "message": result.tani_kurali.message,
                "blocks_automatic_approval": result.tani_kurali.status
                == LayerStatus.FAIL,
                **_diagnosis_layer_item_summary(result.tani_kurali, code_key="huv_code"),
            }
        if result.sut_tani_kurali:
            summary["sut_tani_kurali"] = {
                "status": result.sut_tani_kurali.status.value,
                "message": result.sut_tani_kurali.message,
                "blocks_automatic_approval": result.sut_tani_kurali.status
                == LayerStatus.FAIL,
                **_diagnosis_layer_item_summary(
                    result.sut_tani_kurali, code_key="sut_code"
                ),
            }
        if result.sut_kurali:
            summary["sut_kurali"] = {
                "status": result.sut_kurali.status.value,
                "message": result.sut_kurali.message,
                **_sut_rule_finding_summary(result.sut_kurali),
            }
        if docless:
            summary["documents_mode"] = "skipped_full_pipeline"
            summary["review_required_note"] = (
                "review_required otomatik red değildir. Belgesiz değerlendirme: "
                "belge yokluğu hata/eksik_evrak değildir. "
                "items[] yalnızca referans: soft_review=true olan kalemler tek başına "
                "manuel_inceleme_gerekli=true gerektirmez. Tanı-işlem klinik uyumu netse "
                "manuel_inceleme_gerekli=false ve guven=medium|high olabilir; "
                "yalnızca kural review demek manuel_inceleme_gerekli=true gerektirmez — "
                "gerekçede belirt."
            )
        else:
            summary["review_required_note"] = (
                "review_required kural durumu otomatik red değildir; "
                "items[] referansdır — soft_review=true tek başına manuel inceleme zorunlu kılmaz; "
                "belgeler destekliyorsa manuel_inceleme_gerekli=false döndür."
            )
        return summary

    # ----------------------------------------------------------------------
    def _finalize(
        self,
        job: ProvizyonJob,
        result: JobResult,
        karar: KararDurumu,
        gerekce: str,
        *,
        extra_warnings: list[str] | None = None,
        allow_document_rag: bool = True,
        decision_type=None,
        risk_level=None,
        risk_reasons=None,
    ) -> JobResult:
        from .models import _utcnow

        result.nihai_karar = karar
        result.gerekce = gerekce
        # Nihai skor = MedGemma'nın klinik_skoru (kural özeti + kanıta dayanarak üretir).
        if result.medgemma is not None:
            result.klinik_skor = result.medgemma.klinik_skor
            result.skor_dayanak = (result.medgemma.skor_dayanak or "").strip()
            if result.klinik_skor is None:
                result.warnings.append(
                    "MedGemma klinik_skor üretmedi; nihai skor boş bırakıldı."
                )
        if decision_type is not None:
            result.decision_type = decision_type
        if risk_level is not None:
            result.risk_level = risk_level
        if risk_reasons is not None:
            result.risk_reasons = risk_reasons
        result.status = JobStatus.DONE
        if extra_warnings:
            result.warnings.extend(extra_warnings)
        result.finished_at = _utcnow()

        # Salt okunur gölge tavsiye (canlı kararı değiştirmez).
        try:
            from .shadow_handoff import evaluate_shadow_advice_for_job

            advice = evaluate_shadow_advice_for_job(job)
            advice["nihai_karar"] = (
                karar.value if hasattr(karar, "value") else str(karar)
            )
            result.raw["shadow_advice"] = advice
            if advice.get("status") == "matched_candidate":
                result.warnings.append(
                    "Gölge tavsiye: 703790+H40* aday eşleşmesi — canlı karar değişmedi."
                )
        except Exception as exc:
            result.raw["shadow_advice"] = {
                "status": "error",
                "label": "Gölge değerlendirme hatası",
                "message": str(exc),
                "live_decision_unchanged": True,
                "apply_ready": False,
                "shadow_only": True,
            }

        if self.config.enable_persistence:
            self._persist_findings(job, result, allow_document_rag=allow_document_rag)

        return result

    def _persist_findings(
        self, job: ProvizyonJob, result: JobResult, *, allow_document_rag: bool
    ) -> None:
        try:
            writer = self._findings_writer
            if writer is None:
                from .persistence.qdrant_findings import PatientFindingsWriter

                writer = PatientFindingsWriter()
            info = writer.write(
                result,
                tc_kimlik=job.tc_kimlik,
                allow_document_rag=allow_document_rag,
                institution_name=job.institution_label(),
                facility_level=job.facility_level,
                yas=job.yas,
                cinsiyet=job.cinsiyet.value if job.cinsiyet else None,
            )
            result.raw["persistence"] = info
        except Exception as exc:
            result.warnings.append(f"Qdrant patient_findings yazımı başarısız: {exc}")
            result.raw["persistence"] = {"error": str(exc)}


# MedGemma deterministic summary — kompakt item bağlamı (token tavanı)
_SUMMARY_MAX_ITEMS = 12
_SUMMARY_REASON_MAX = 160


def _clip_text(value: Any, limit: int = _SUMMARY_REASON_MAX) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _layer_detail_items(layer: LayerResult) -> list[dict[str, Any]]:
    detail = layer.detail or {}
    result = detail.get("result") or {}
    items = result.get("items") or detail.get("blocking_items") or []
    return [item for item in items if isinstance(item, dict)]


def _diagnosis_layer_item_summary(
    layer: LayerResult, *, code_key: str
) -> dict[str, Any]:
    """Tanı katmanı item'larından MedGemma için kompakt dilim."""
    detail = layer.detail or {}
    raw_items = _layer_detail_items(layer)
    # Öncelik: blocking / review / unknown; PASS item'ları sonda
    def _priority(item: dict[str, Any]) -> tuple[int, str]:
        status = str(item.get("status") or "")
        if status in {"review_required", "diagnosis_mismatch", "missing_diagnosis"}:
            pri = 0
        elif status.startswith("unknown") or status in {"not_payable_by_diagnosis", "not_payable_by_sut_diagnosis"}:
            pri = 1
        elif item_requires_strict_manual_review(item):
            pri = 2
        else:
            pri = 3
        code = str(item.get(code_key) or item.get("huv_code") or item.get("sut_code") or "")
        return (pri, code)

    ranked = sorted(raw_items, key=_priority)
    compact: list[dict[str, Any]] = []
    soft_n = 0
    strict_n = 0
    for item in ranked[:_SUMMARY_MAX_ITEMS]:
        strict = item_requires_strict_manual_review(item)
        soft = (item.get("diagnosis_required") is False) and not strict
        if soft:
            soft_n += 1
        if strict:
            strict_n += 1
        code = (
            item.get(code_key)
            or item.get("huv_code")
            or item.get("sut_code")
            or item.get("procedure_key")
        )
        entry: dict[str, Any] = {
            "code": code,
            "status": item.get("status"),
            "diagnosis_policy": item.get("diagnosis_policy"),
            "diagnosis_required": item.get("diagnosis_required"),
            "tentative_status": item.get("tentative_status"),
            "soft_review": soft,
            "strict_manual": strict,
        }
        reason = _clip_text(item.get("reason") or item.get("message"))
        if reason:
            entry["reason"] = reason
        name = item.get("procedure_name")
        if name:
            entry["procedure_name"] = _clip_text(name, 80)
        compact.append(entry)

    out: dict[str, Any] = {}
    if detail.get("overall_status") is not None:
        out["overall_status"] = detail.get("overall_status")
    if compact:
        out["items"] = compact
        out["items_total"] = len(raw_items)
        out["items_truncated"] = len(raw_items) > len(compact)
        out["soft_review_count"] = soft_n
        out["strict_manual_count"] = strict_n
    return out


def _sut_rule_finding_summary(layer: LayerResult) -> dict[str, Any]:
    detail = layer.detail or {}
    findings = detail.get("blocking_findings") or []
    if not isinstance(findings, list) or not findings:
        return {}
    compact: list[dict[str, Any]] = []
    for finding in findings[:_SUMMARY_MAX_ITEMS]:
        if not isinstance(finding, dict):
            continue
        entry: dict[str, Any] = {
            "service_code": finding.get("service_code") or finding.get("code"),
            "rule_id": finding.get("rule_id") or finding.get("id"),
            "status": finding.get("status"),
        }
        reason = _clip_text(
            finding.get("message")
            or finding.get("reason")
            or finding.get("title")
            or finding.get("rule_name")
        )
        if reason:
            entry["reason"] = reason
        compact.append(entry)
    if not compact:
        return {}
    return {
        "blocking_findings": compact,
        "blocking_findings_total": len(findings),
        "blocking_findings_truncated": len(findings) > len(compact),
    }
