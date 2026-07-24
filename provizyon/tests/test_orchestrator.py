"""Provizyon orkestratör uçtan uca senaryo testleri.

Bu testler dış servislere (Redis/MedGemma/Qdrant/Tesseract) ihtiyaç duymaz:
- MedGemma sahte bir client ile enjekte edilir veya devre dışı bırakılır.
- Persistence devre dışı bırakılır.
- Belgeler geçici dosya sistemi kökünden okunur.

Çalıştırma:
    cd provizyon && .venv/bin/python -m pytest tests -q
    veya
    cd provizyon && .venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from provizyon_engine.decision import merge_decisions
from provizyon_engine.documents import requirement as requirement_mod
from provizyon_engine.documents.source import FilesystemDocumentSource
from provizyon_engine.models import (
    Cinsiyet,
    DocumentInput,
    KararDurumu,
    LayerResult,
    LayerStatus,
    MedGemmaClinicalOutput,
    ProcedureInput,
    ProvizyonJob,
)
from provizyon_engine.orchestrator import OrchestratorConfig, ProvizyonOrchestrator


class FakeMedGemmaClient:
    """Sabit JSON döndüren sahte vision client."""

    def __init__(self, payload: str):
        self.payload = payload
        self.last_images = None
        self.last_user_text = None

    def chat(self, system_prompt, user_text, *, image_paths=None, json_mode=True):
        self.last_images = list(image_paths or [])
        self.last_user_text = user_text
        return self.payload


def _orchestrator(tmp: Path, *, medgemma_client=None, enable_medgemma=False, enable_diag=False, enable_sut_diag=False, enable_sut=False):
    config = OrchestratorConfig(
        enable_diagnosis=enable_diag,
        enable_sut_diagnosis=enable_sut_diag,
        enable_sut_rules=enable_sut,
        enable_medgemma=enable_medgemma,
        enable_persistence=False,
        enable_patient_context=False,
        use_qdrant_rag=False,
        include_vision=False,
    )
    return ProvizyonOrchestrator(
        config=config,
        document_source=FilesystemDocumentSource(root=tmp),
        medgemma_client=medgemma_client,
    )


class DecisionMergeTests(unittest.TestCase):
    def test_tani_eksik(self):
        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.FAIL,
            message="tanı yok",
            detail={"missing_diagnosis": True, "diagnosis_mismatch": False},
        )
        out = merge_decisions(
            belge_hasta=None, zorunlu_evrak=None, tani_kurali=tani,
            sut_tani_kurali=None, sut_kurali=None, medgemma=None, medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.TANI_EKSIK)

    def test_tani_uyumsuz(self):
        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.FAIL,
            message="uyumsuz",
            detail={"missing_diagnosis": False, "diagnosis_mismatch": True},
        )
        out = merge_decisions(
            belge_hasta=None, zorunlu_evrak=None, tani_kurali=tani,
            sut_tani_kurali=None, sut_kurali=None, medgemma=None, medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.TANI_UYUMSUZ)

    def test_klinik_uyumsuzluk(self):
        mg = MedGemmaClinicalOutput(klinik_celiski=True, gerekce="çelişki")
        out = merge_decisions(
            belge_hasta=None, zorunlu_evrak=None, tani_kurali=None,
            sut_tani_kurali=None, sut_kurali=None, medgemma=mg, medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.KLINIK_UYUMSUZLUK)

    def test_belge_kaniti_yetersiz(self):
        mg = MedGemmaClinicalOutput(islem_belge_destekli=False, gerekce="kanıt yok")
        out = merge_decisions(
            belge_hasta=None, zorunlu_evrak=None, tani_kurali=None,
            sut_tani_kurali=None, sut_kurali=None, medgemma=mg, medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.BELGE_KANITI_YETERSIZ)

    def test_manuel_inceleme_review(self):
        review = LayerResult(layer="sut_kurali", status=LayerStatus.REVIEW, message="uyarı")
        out = merge_decisions(
            belge_hasta=None, zorunlu_evrak=None, tani_kurali=None,
            sut_tani_kurali=None, sut_kurali=review, medgemma=None, medgemma_layer=review,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)

    def test_uygun(self):
        out = merge_decisions(
            belge_hasta=None, zorunlu_evrak=None, tani_kurali=None,
            sut_tani_kurali=None, sut_kurali=None, medgemma=None, medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.UYGUN)


class OrchestratorScenarioTests(unittest.TestCase):
    def setUp(self):
        self._orig_overrides = requirement_mod._OVERRIDES
        self._orig_prefix_rules = requirement_mod._PREFIX_RULES
        requirement_mod._OVERRIDES = {}
        requirement_mod._PREFIX_RULES = []
        requirement_mod._REQUIRED_DOC_CODES = set()

    def tearDown(self):
        requirement_mod._OVERRIDES = self._orig_overrides
        requirement_mod._PREFIX_RULES = self._orig_prefix_rules
        requirement_mod._REQUIRED_DOC_CODES = None

    def test_evrak_eksik(self):
        # TEST123 belge gerektiriyor (override) ve hiç belge yok.
        requirement_mod._OVERRIDES = {"TEST123": True}
        with tempfile.TemporaryDirectory() as tmp:
            orch = _orchestrator(Path(tmp))
            job = ProvizyonJob(
                provizyon_id="P-EVRAK", hasta_id="H1",
                huv_codes=["TEST123"], documents=[],
            )
            result = orch.run(job)
        self.assertEqual(result.nihai_karar, KararDurumu.EVRAK_EKSIK)

    def test_yanlis_hasta_belgesi(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / "epikriz.txt"
            doc_path.write_text("Hasta: Baska Kisi. Epikriz raporu.", encoding="utf-8")
            orch = _orchestrator(Path(tmp))
            job = ProvizyonJob(
                provizyon_id="P-YANLIS", hasta_id="H1", patient_name="Ahmet Yilmaz",
                huv_codes=["02.16321"],
                documents=[DocumentInput(path="epikriz.txt", declared_hasta_id="H999")],
            )
            result = orch.run(job)
        self.assertEqual(result.nihai_karar, KararDurumu.YANLIS_HASTA_BELGESI)
        self.assertEqual(result.belge_hasta.status, LayerStatus.FAIL)

    def test_uygun_belgesiz(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = _orchestrator(Path(tmp))
            job = ProvizyonJob(provizyon_id="P-UYGUN", hasta_id="H1", huv_codes=["X1"])
            result = orch.run(job)
        self.assertEqual(result.nihai_karar, KararDurumu.UYGUN)

    def test_medgemma_belge_destekli_uygun(self):
        fake = FakeMedGemmaClient(
            '{"islem_belge_destekli": true, "tani_belge_destekli": true, '
            '"yas_cinsiyet_uygun": true, "klinik_celiski": false, "eksik_evrak": false, '
            '"manuel_inceleme_gerekli": false, "ozel_soru_cevaplari": [], '
            '"gerekce": "Belge işlemi destekliyor.", "guven": "high"}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / "rapor.txt"
            doc_path.write_text("Hasta Ahmet Yilmaz icin ameliyat raporu.", encoding="utf-8")
            orch = _orchestrator(Path(tmp), medgemma_client=fake, enable_medgemma=True)
            job = ProvizyonJob(
                provizyon_id="P-MG", hasta_id="H1", patient_name="Ahmet Yilmaz",
                yas=45, cinsiyet=Cinsiyet.ERKEK, huv_codes=["02.16321"],
                documents=[DocumentInput(path="rapor.txt")],
            )
            result = orch.run(job)
        self.assertEqual(result.nihai_karar, KararDurumu.UYGUN)
        self.assertIsNotNone(result.medgemma)
        self.assertTrue(result.medgemma.islem_belge_destekli)

    def test_medgemma_belge_kaniti_yetersiz(self):
        fake = FakeMedGemmaClient(
            '{"islem_belge_destekli": false, "tani_belge_destekli": null, '
            '"manuel_inceleme_gerekli": false, "gerekce": "Belge işlemi desteklemiyor.", "guven": "high"}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / "rapor.txt"
            doc_path.write_text("Hasta Ahmet Yilmaz. Alakasiz icerik.", encoding="utf-8")
            orch = _orchestrator(Path(tmp), medgemma_client=fake, enable_medgemma=True)
            job = ProvizyonJob(
                provizyon_id="P-MG2", hasta_id="H1", patient_name="Ahmet Yilmaz",
                huv_codes=["02.16321"], documents=[DocumentInput(path="rapor.txt")],
            )
            result = orch.run(job)
        self.assertEqual(result.nihai_karar, KararDurumu.BELGE_KANITI_YETERSIZ)


class PatientMatchTests(unittest.TestCase):
    """Belge-hasta eşleşmesi iyileştirme testleri."""

    def test_tc_kimlik_match(self):
        """TC kimlik numarası belge metninde bulunursa match olmalı."""
        from provizyon_engine.documents.patient_match import match_documents
        from provizyon_engine.documents.extract import ExtractedDocument, PageContent
        from provizyon_engine.documents.source import DocumentRef

        job = ProvizyonJob(
            provizyon_id="P-TC",
            hasta_id="0030024",
            tc_kimlik="53491683054",
            patient_name="Ahmet Yilmaz",
        )
        ref = DocumentRef(
            path=Path("/tmp/rapor.txt"), doc_type="rapor", exists=True,
        )
        doc = ExtractedDocument(
            ref=ref,
            pages=[PageContent(page_index=0, text="Lab sonucu TC: 53491683054 değerleri normal.")],
        )
        result = match_documents(job, [doc])
        self.assertEqual(result.status, LayerStatus.PASS)

    def test_short_document_exempt(self):
        """Kısa belgeler (< 200 karakter) exempt olmalı, uncertain değil."""
        from provizyon_engine.documents.patient_match import match_documents
        from provizyon_engine.documents.extract import ExtractedDocument, PageContent
        from provizyon_engine.documents.source import DocumentRef

        job = ProvizyonJob(
            provizyon_id="P-SHORT",
            hasta_id="H1",
            patient_name="Ahmet Yilmaz",
        )
        ref = DocumentRef(
            path=Path("/tmp/fatura.txt"), doc_type="fatura", exists=True,
        )
        doc = ExtractedDocument(
            ref=ref,
            pages=[PageContent(page_index=0, text="Fatura: 500 TL")],
        )
        result = match_documents(job, [doc])
        docs = result.detail.get("documents", [])
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["verdict"], "exempt")
        self.assertEqual(result.status, LayerStatus.PASS)

    def test_main_doc_match_with_uncertain_extras(self):
        """Ana belge match + ek belge uncertain -> PASS (eskiden REVIEW idi)."""
        from provizyon_engine.documents.patient_match import match_documents
        from provizyon_engine.documents.extract import ExtractedDocument, PageContent
        from provizyon_engine.documents.source import DocumentRef

        job = ProvizyonJob(
            provizyon_id="P-MIXED",
            hasta_id="H1",
            patient_name="Ahmet Yilmaz",
        )
        ref1 = DocumentRef(path=Path("/tmp/epikriz.txt"), doc_type="epikriz", exists=True)
        doc1 = ExtractedDocument(
            ref=ref1,
            pages=[PageContent(page_index=0, text="Hasta: Ahmet Yilmaz epikriz raporu " * 10)],
        )
        ref2 = DocumentRef(path=Path("/tmp/lab.txt"), doc_type="rapor", exists=True)
        doc2 = ExtractedDocument(
            ref=ref2,
            pages=[PageContent(page_index=0, text="Laboratuvar sonuçları: Hemoglobin 14.2 g/dL " * 10)],
        )
        result = match_documents(job, [doc1, doc2])
        self.assertEqual(result.status, LayerStatus.PASS)
        self.assertIn("Doğrulanamayan", result.message)


class DecisionMergeOverrideTests(unittest.TestCase):
    """MedGemma override testleri."""

    def test_belge_hasta_review_medgemma_high_override(self):
        """belge_hasta REVIEW + MedGemma high -> UYGUN (override)."""
        belge = LayerResult(
            layer="belge_hasta", status=LayerStatus.REVIEW,
            message="Belge-hasta uyumu doğrulanamadı.",
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            guven="high",
            manuel_inceleme_gerekli=False,
        )
        out = merge_decisions(
            belge_hasta=belge, zorunlu_evrak=None, tani_kurali=None,
            sut_tani_kurali=None, sut_kurali=None, medgemma=mg, medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.UYGUN)
        self.assertTrue(any("AI" in w for w in out.warnings))

    def test_belge_hasta_review_medgemma_medium_no_override(self):
        """belge_hasta REVIEW + MedGemma medium -> MANUEL_INCELEME (no override)."""
        belge = LayerResult(
            layer="belge_hasta", status=LayerStatus.REVIEW,
            message="Belge-hasta uyumu doğrulanamadı.",
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            guven="medium",
            manuel_inceleme_gerekli=False,
        )
        out = merge_decisions(
            belge_hasta=belge, zorunlu_evrak=None, tani_kurali=None,
            sut_tani_kurali=None, sut_kurali=None, medgemma=mg, medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)

    def test_multiple_review_layers_no_override(self):
        """belge_hasta + sut_kurali REVIEW -> override olmamalı."""
        belge = LayerResult(
            layer="belge_hasta", status=LayerStatus.REVIEW,
            message="Belge-hasta uyumu doğrulanamadı.",
        )
        sut = LayerResult(
            layer="sut_kurali", status=LayerStatus.REVIEW,
            message="SUT uyarı.",
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            guven="high",
        )
        out = merge_decisions(
            belge_hasta=belge, zorunlu_evrak=None, tani_kurali=None,
            sut_tani_kurali=None, sut_kurali=sut, medgemma=mg, medgemma_layer=sut,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)

    def test_sut_skipped_medgemma_high_uygun(self):
        """SUT SKIPPED + MedGemma high -> UYGUN (3219368 senaryosu)."""
        sut = LayerResult(
            layer="sut_kurali",
            status=LayerStatus.SKIPPED,
            message="SUT kural kontrolü atlandı.",
        )
        tani = LayerResult(layer="tani_kurali", status=LayerStatus.PASS, message="ok")
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            guven="high",
            manuel_inceleme_gerekli=False,
            gerekce="Belge ve işlemler uyumlu.",
        )
        out = merge_decisions(
            belge_hasta=None, zorunlu_evrak=None, tani_kurali=tani,
            sut_tani_kurali=None, sut_kurali=sut, medgemma=mg, medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.UYGUN)

    def test_medgemma_high_overrides_manuel_flag_without_deterministic_review(self):
        """Deterministik REVIEW yok + MedGemma high -> manuel_inceleme bayrağını ezer."""
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            guven="high",
            manuel_inceleme_gerekli=True,
            gerekce="Klinik olarak uygun.",
        )
        mg_layer = LayerResult(layer="medgemma", status=LayerStatus.REVIEW, message="review")
        out = merge_decisions(
            belge_hasta=None, zorunlu_evrak=None, tani_kurali=None,
            sut_tani_kurali=None, sut_kurali=None, medgemma=mg, medgemma_layer=mg_layer,
        )
        self.assertEqual(out.karar, KararDurumu.UYGUN)

    def test_docless_medium_tzh_skipped_becomes_uygun(self):
        """TZH-only (tani SKIPPED) + belgesiz medium + manuel=false → UYGUN."""
        from provizyon_engine.models import DecisionType, RiskLevel

        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.SKIPPED,
            message="Yalnızca TZH meta kodları var; tanı kuralı kapsamı dışında atlandı: TZH.01.00001",
            detail={"skipped_tzh_codes": ["TZH.01.00001"]},
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=None,
            tani_belge_destekli=None,
            eksik_evrak=None,
            yas_cinsiyet_uygun=True,
            klinik_celiski=False,
            guven="medium",
            manuel_inceleme_gerekli=False,
            gerekce="TZH muayene klinik uyumlu.",
        )
        mg_layer = LayerResult(
            layer="medgemma",
            status=LayerStatus.REVIEW,
            message="Belgesiz review",
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=mg_layer,
        )
        self.assertEqual(out.karar, KararDurumu.UYGUN)
        self.assertEqual(out.decision_type, DecisionType.LOW_RISK)
        self.assertEqual(out.risk_level, RiskLevel.GREEN)
        self.assertTrue(any("belgesiz" in w.lower() for w in out.warnings))

    def test_docless_medium_tzh_skipped_manuel_flag_stays_manuel(self):
        """TZH skip + medium ama AI manuel=true → MANUEL."""
        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.SKIPPED,
            message="Yalnızca TZH meta kodları var",
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=None,
            tani_belge_destekli=None,
            eksik_evrak=None,
            guven="medium",
            manuel_inceleme_gerekli=True,
            klinik_celiski=False,
        )
        mg_layer = LayerResult(layer="medgemma", status=LayerStatus.REVIEW, message="review")
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=mg_layer,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)

    def test_docless_low_tzh_skipped_stays_manuel(self):
        tani = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.SKIPPED,
            message="Yalnızca TZH meta kodları var",
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=None,
            tani_belge_destekli=None,
            eksik_evrak=None,
            guven="low",
            manuel_inceleme_gerekli=False,
        )
        mg_layer = LayerResult(layer="medgemma", status=LayerStatus.REVIEW, message="review")
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=mg_layer,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)

    def test_belgeli_medium_no_deterministic_review_stays_manuel(self):
        """Belgeli medium hâlâ yetersiz; high gerekir."""
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            eksik_evrak=False,
            guven="medium",
            manuel_inceleme_gerekli=False,
        )
        mg_layer = LayerResult(layer="medgemma", status=LayerStatus.REVIEW, message="review")
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=None,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=mg_layer,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)


class CodeExtractTests(unittest.TestCase):
    def test_extract_codes_from_text(self):
        from provizyon_engine.documents.procedure_match import extract_codes_from_text

        codes = extract_codes_from_text("HUV 24.12345 ve SUT 803520 tanı H90.3")
        self.assertIn("24.12345", codes.huv)
        self.assertIn("803520", codes.sut)
        self.assertIn("H90.3", codes.icd)

    def test_date_tokens_not_counted_as_huv(self):
        from provizyon_engine.documents.procedure_match import extract_codes_from_text

        codes = extract_codes_from_text("Tarih 01.2026 ve 03.2026")
        self.assertEqual(codes.huv, [])


class SutTaniDecisionTests(unittest.TestCase):
    def test_sut_tani_review_no_medgemma_is_manuel(self):
        """sut_tani_kurali REVIEW + MedGemma yok → MANUEL_INCELEME."""
        review = LayerResult(layer="sut_tani_kurali", status=LayerStatus.REVIEW, message="kısmi")
        out = merge_decisions(
            belge_hasta=None, zorunlu_evrak=None, tani_kurali=None,
            sut_tani_kurali=review, sut_kurali=None, medgemma=None, medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)

    def test_sut_tani_review_with_high_medgemma_becomes_low_risk(self):
        """sut_tani_kurali REVIEW + destekleyici MedGemma → UYGUN / düşük risk."""
        from provizyon_engine.models import DecisionType, MedGemmaClinicalOutput

        review = LayerResult(
            layer="sut_tani_kurali",
            status=LayerStatus.REVIEW,
            message="manuel inceleme",
            detail={
                "review_required": True,
                "result": {
                    "items": [
                        {
                            "sut_code": "530090",
                            "procedure_name": "Diyabet eğitimi",
                            "status": "review_required",
                            "requires_manual_review": True,
                            "input_diagnoses": ["E11"],
                        }
                    ]
                },
            },
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            guven="high",
            manuel_inceleme_gerekli=False,
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=None,
            sut_tani_kurali=review,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.UYGUN)
        self.assertEqual(out.decision_type, DecisionType.LOW_RISK)

    def test_sut_tani_review_without_medgemma_stays_manuel(self):
        """sut_tani_kurali REVIEW + belge kanıtı yok → MANUEL."""
        from provizyon_engine.models import MedGemmaClinicalOutput
        review = LayerResult(
            layer="sut_tani_kurali", status=LayerStatus.REVIEW, message="eksik kodlar",
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=False,
            tani_belge_destekli=True,
            guven="medium",
            manuel_inceleme_gerekli=True,
        )
        out = merge_decisions(
            belge_hasta=None, zorunlu_evrak=None, tani_kurali=None,
            sut_tani_kurali=review, sut_kurali=None, medgemma=mg, medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.BELGE_KANITI_YETERSIZ)


class RiskNormalizer530090Tests(unittest.TestCase):
    """530090 SUT senaryoları — savunulabilir risk vs düşük risk."""

    def test_530090_missing_diagnosis_defensible(self):
        layer = LayerResult(
            layer="sut_tani_kurali",
            status=LayerStatus.FAIL,
            message="Tanı eksik",
            detail={
                "missing_diagnosis": True,
                "diagnosis_mismatch": False,
                "result": {
                    "items": [
                        {
                            "sut_code": "530090",
                            "procedure_name": "Diyabetli hasta eğitimi",
                            "status": "missing_diagnosis",
                            "input_diagnoses": [],
                        }
                    ]
                },
            },
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=None,
            sut_tani_kurali=layer,
            sut_kurali=None,
            medgemma=None,
            medgemma_layer=None,
        )
        from provizyon_engine.models import DecisionType, RiskLevel

        self.assertEqual(out.karar, KararDurumu.TANI_EKSIK)
        self.assertEqual(out.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(out.risk_level, RiskLevel.RED)

    def test_530090_e11_review_supported_low_risk(self):
        from provizyon_engine.models import DecisionType, MedGemmaClinicalOutput, RiskLevel

        layer = LayerResult(
            layer="sut_tani_kurali",
            status=LayerStatus.REVIEW,
            message="review",
            detail={
                "review_required": True,
                "result": {
                    "items": [
                        {
                            "sut_code": "530090",
                            "procedure_name": "Diyabetli hasta eğitimi",
                            "status": "review_required",
                            "requires_manual_review": True,
                            "input_diagnoses": ["E11"],
                        }
                    ]
                },
            },
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            guven="high",
            manuel_inceleme_gerekli=False,
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=None,
            sut_tani_kurali=layer,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.UYGUN)
        self.assertEqual(out.decision_type, DecisionType.LOW_RISK)
        self.assertEqual(out.risk_level, RiskLevel.GREEN)

    def test_tani_unsupported_policy_review_not_auto_uygun(self):
        """blocking_items + unsupported_policy → MedGemma desteklese bile MANUEL."""
        from provizyon_engine.models import MedGemmaClinicalOutput

        review = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.REVIEW,
            message="Tanı kuralları manuel inceleme gerektiriyor.",
            detail={
                "review_required": True,
                "blocking_items": [
                    {
                        "huv_code": "34.01511",
                        "procedure_name": "ALT",
                        "status": "review_required",
                        "requires_manual_review": True,
                        "tentative_status": "unsupported_policy",
                        "message": "otomatik provizyon kararı verilmemeli.",
                        "input_diagnoses": ["J39"],
                    }
                ],
            },
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            guven="high",
            manuel_inceleme_gerekli=False,
            gerekce="Belgeler uyumlu.",
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=review,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)

    def test_docless_soft_tani_review_medium_becomes_uygun(self):
        """Belgesiz + diagnosis_required=False lab REVIEW + medium → UYGUN."""
        from provizyon_engine.models import DecisionType, MedGemmaClinicalOutput, RiskLevel

        review = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.REVIEW,
            message="Tanı kuralları manuel inceleme gerektiriyor.",
            detail={
                "review_required": True,
                "blocking_items": [
                    {
                        "huv_code": "34.53153",
                        "procedure_name": "Kan sayımı",
                        "status": "review_required",
                        "requires_manual_review": True,
                        "tentative_status": "unsupported_policy",
                        "diagnosis_required": False,
                        "diagnosis_policy": "review_required",
                        "message": "otomatik provizyon kararı verilmemeli.",
                        "input_diagnoses": ["J06.9"],
                    }
                ],
                "result": {
                    "items": [
                        {
                            "huv_code": "34.53153",
                            "procedure_name": "Kan sayımı",
                            "status": "review_required",
                            "requires_manual_review": True,
                            "tentative_status": "unsupported_policy",
                            "diagnosis_required": False,
                            "diagnosis_policy": "review_required",
                            "input_diagnoses": ["J06.9"],
                        }
                    ]
                },
            },
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=None,
            tani_belge_destekli=None,
            eksik_evrak=None,
            yas_cinsiyet_uygun=True,
            klinik_celiski=False,
            guven="medium",
            manuel_inceleme_gerekli=False,
            gerekce="Belgesiz; CBC klinik uyumlu.",
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=review,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.UYGUN)
        self.assertEqual(out.decision_type, DecisionType.LOW_RISK)
        self.assertEqual(out.risk_level, RiskLevel.GREEN)
        self.assertTrue(any("belgesiz" in w.lower() for w in out.warnings))

    def test_docless_soft_tani_review_medium_manuel_flag_stays_manuel(self):
        """Belgesiz medium ama AI manuel bayrağı true → MANUEL (soften öncesi yol)."""
        from provizyon_engine.models import MedGemmaClinicalOutput

        review = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.REVIEW,
            message="Tanı kuralları manuel inceleme gerektiriyor.",
            detail={
                "review_required": True,
                "result": {
                    "items": [
                        {
                            "huv_code": "34.53153",
                            "status": "review_required",
                            "requires_manual_review": True,
                            "tentative_status": "unsupported_policy",
                            "diagnosis_required": False,
                            "input_diagnoses": ["J06.9"],
                        }
                    ]
                },
            },
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=None,
            tani_belge_destekli=None,
            eksik_evrak=None,
            guven="medium",
            manuel_inceleme_gerekli=True,
            klinik_celiski=False,
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=review,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)

    def test_belgeli_soft_tani_review_medium_stays_manuel(self):
        """Belgeli akışta medium hâlâ yetersiz; high gerekir."""
        from provizyon_engine.models import MedGemmaClinicalOutput

        review = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.REVIEW,
            message="Tanı kuralları manuel inceleme gerektiriyor.",
            detail={
                "review_required": True,
                "result": {
                    "items": [
                        {
                            "huv_code": "34.53153",
                            "status": "review_required",
                            "requires_manual_review": True,
                            "diagnosis_required": False,
                            "input_diagnoses": ["J06.9"],
                        }
                    ]
                },
            },
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            eksik_evrak=False,
            guven="medium",
            manuel_inceleme_gerekli=False,
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=review,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)

    def test_docless_soft_tani_review_low_stays_manuel(self):
        from provizyon_engine.models import MedGemmaClinicalOutput

        review = LayerResult(
            layer="tani_kurali",
            status=LayerStatus.REVIEW,
            message="review",
            detail={
                "review_required": True,
                "result": {
                    "items": [
                        {
                            "huv_code": "34.53153",
                            "status": "review_required",
                            "requires_manual_review": True,
                            "diagnosis_required": False,
                            "input_diagnoses": ["J06.9"],
                        }
                    ]
                },
            },
        )
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=None,
            tani_belge_destekli=None,
            eksik_evrak=None,
            guven="low",
            manuel_inceleme_gerekli=False,
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=review,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)

    def test_prior_rejection_signal_blocks_auto_uygun(self):
        from provizyon_engine.models import MedGemmaClinicalOutput

        tani = LayerResult(layer="tani_kurali", status=LayerStatus.PASS, message="ok")
        mg = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            guven="high",
            manuel_inceleme_gerekli=False,
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=tani,
            sut_tani_kurali=None,
            sut_kurali=None,
            medgemma=mg,
            medgemma_layer=None,
            prior_rejection_signals=["vitaminler ve doppler odenmez"],
        )
        self.assertEqual(out.karar, KararDurumu.MANUEL_INCELEME)
        self.assertIn("iade/red", out.gerekce.lower())

    def test_530090_k350_mismatch_defensible(self):
        from provizyon_engine.models import DecisionType, RiskLevel

        layer = LayerResult(
            layer="sut_tani_kurali",
            status=LayerStatus.FAIL,
            message="Tanı uyumsuz",
            detail={
                "missing_diagnosis": False,
                "diagnosis_mismatch": True,
                "result": {
                    "items": [
                        {
                            "sut_code": "530090",
                            "procedure_name": "Diyabetli hasta eğitimi",
                            "status": "diagnosis_mismatch",
                            "input_diagnoses": ["K35.0"],
                        }
                    ]
                },
            },
        )
        out = merge_decisions(
            belge_hasta=None,
            zorunlu_evrak=None,
            tani_kurali=None,
            sut_tani_kurali=layer,
            sut_kurali=None,
            medgemma=None,
            medgemma_layer=None,
        )
        self.assertEqual(out.karar, KararDurumu.TANI_UYUMSUZ)
        self.assertEqual(out.decision_type, DecisionType.AUTOMATIC_DEFENSIBLE)
        self.assertEqual(out.risk_level, RiskLevel.RED)


class FlowIntegrityTests(unittest.TestCase):
    """Orkestratör erken çıkış ve tam akış bütünlüğünü doğrular."""

    def setUp(self):
        self._orig_overrides = requirement_mod._OVERRIDES
        self._orig_prefix_rules = requirement_mod._PREFIX_RULES
        requirement_mod._OVERRIDES = {}
        requirement_mod._PREFIX_RULES = []
        requirement_mod._REQUIRED_DOC_CODES = set()

    def tearDown(self):
        requirement_mod._OVERRIDES = self._orig_overrides
        requirement_mod._PREFIX_RULES = self._orig_prefix_rules
        requirement_mod._REQUIRED_DOC_CODES = None

    def test_yanlis_hasta_skips_diagnosis_and_medgemma(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / "epikriz.txt"
            doc_path.write_text("Hasta: Baska Kisi. Epikriz raporu.", encoding="utf-8")
            fake = FakeMedGemmaClient('{"guven": "high"}')
            orch = _orchestrator(Path(tmp), medgemma_client=fake, enable_medgemma=True)
            job = ProvizyonJob(
                provizyon_id="P-FLOW-YANLIS",
                hasta_id="H1",
                patient_name="Ahmet Yilmaz",
                huv_codes=["02.16321"],
                documents=[DocumentInput(path="epikriz.txt", declared_hasta_id="H999")],
            )
            result = orch.run(job)
        self.assertEqual(result.nihai_karar, KararDurumu.YANLIS_HASTA_BELGESI)
        self.assertIsNone(result.tani_kurali)
        self.assertIsNone(result.medgemma)

    def test_evrak_eksik_skips_diagnosis_and_medgemma(self):
        requirement_mod._OVERRIDES = {"TEST123": True}
        fake = FakeMedGemmaClient('{"guven": "high"}')
        with tempfile.TemporaryDirectory() as tmp:
            orch = _orchestrator(Path(tmp), medgemma_client=fake, enable_medgemma=True)
            job = ProvizyonJob(
                provizyon_id="P-FLOW-EVRAK",
                hasta_id="H1",
                huv_codes=["TEST123"],
                documents=[],
            )
            result = orch.run(job)
        self.assertEqual(result.nihai_karar, KararDurumu.EVRAK_EKSIK)
        self.assertIsNone(result.medgemma)

    def test_happy_path_without_diagnosis_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / "rapor.txt"
            doc_path.write_text(
                "Hasta Ahmet Yilmaz. Epikriz: HUV kodu 02.16321 uygulandı, kontrol önerildi.",
                encoding="utf-8",
            )
            orch = _orchestrator(Path(tmp), enable_diag=False, enable_sut_diag=False)
            job = ProvizyonJob(
                provizyon_id="P-FLOW-OK",
                hasta_id="H1",
                patient_name="Ahmet Yilmaz",
                huv_codes=["02.16321"],
                documents=[DocumentInput(path="rapor.txt")],
            )
            result = orch.run(job)
        self.assertEqual(result.nihai_karar, KararDurumu.UYGUN)

    def test_sut_tani_review_with_medgemma_high_is_uygun(self):
        fake = FakeMedGemmaClient(
            '{"islem_belge_destekli": true, "tani_belge_destekli": true, '
            '"yas_cinsiyet_uygun": true, "klinik_celiski": false, "eksik_evrak": false, '
            '"manuel_inceleme_gerekli": false, "gerekce": "OK", "guven": "high"}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / "rapor.txt"
            doc_path.write_text(
                "Hasta Ahmet Yilmaz icin detayli klinik rapor metni.",
                encoding="utf-8",
            )
            orch = _orchestrator(
                Path(tmp), medgemma_client=fake, enable_medgemma=True,
                enable_diag=False, enable_sut_diag=True,
            )
            job = ProvizyonJob(
                provizyon_id="P-FLOW-SUT-TANI",
                hasta_id="H1",
                patient_name="Ahmet Yilmaz",
                sut_codes=["530090"],
                diagnoses=["E11"],
                documents=[DocumentInput(path="rapor.txt")],
            )
            result = orch.run(job)
        self.assertIsNotNone(result.sut_tani_kurali)
        self.assertIn(
            result.sut_tani_kurali.status,
            (LayerStatus.REVIEW, LayerStatus.PASS),
        )
        self.assertEqual(result.nihai_karar, KararDurumu.UYGUN)
        from provizyon_engine.models import DecisionType

        self.assertEqual(result.decision_type, DecisionType.LOW_RISK)
        self.assertIsNotNone(result.medgemma)


class SingleProvizyonE2ETest(unittest.TestCase):
    """Tek provizyonu, içindeki tüm belgelerle uçtan uca koşturur.

    Varsayılan provizyon: 3208035
    Değiştirmek için: PROVIZYON_TEST_ID=3216442
    """

    def test_provizyon_all_documents(self):
        import os

        from provizyon_engine import settings
        from provizyon_engine.intake.folder_intake import build_job_from_folder

        provizyon_id = os.environ.get("PROVIZYON_TEST_ID", "3208035").strip()
        folder = settings.INTAKE_WATCH_DIR / provizyon_id
        if not folder.is_dir():
            self.skipTest(f"Provizyon klasörü yok: {folder}")

        job = build_job_from_folder(folder)
        self.assertGreater(len(job.documents), 0, "En az bir ek belge bekleniyor")

        fake = FakeMedGemmaClient(
            '{"islem_belge_destekli": true, "tani_belge_destekli": true, '
            '"yas_cinsiyet_uygun": true, "klinik_celiski": false, "eksik_evrak": false, '
            '"manuel_inceleme_gerekli": false, "gerekce": "Mock onay.", "guven": "high"}'
        )
        config = OrchestratorConfig(
            enable_diagnosis=True,
            enable_sut_diagnosis=True,
            enable_sut_rules=True,
            enable_medgemma=True,
            enable_persistence=False,
            enable_patient_context=False,
            use_qdrant_rag=True,
            include_vision=True,
        )
        orch = ProvizyonOrchestrator(
            config=config,
            document_source=FilesystemDocumentSource(root=folder),
            medgemma_client=fake,
        )
        result = orch.run(job)

        docs_meta = result.raw.get("documents") or {}
        self.assertEqual(result.status.value, "done")
        self.assertEqual(docs_meta.get("provided"), len(job.documents))
        self.assertEqual(docs_meta.get("found"), len(job.documents))
        self.assertIn("missing_files", docs_meta)
        meta = result.raw.get("job_meta") or {}
        self.assertIn("diagnoses", meta)
        self.assertIn("procedures", meta)
        self.assertIsNotNone(result.medgemma)
        self.assertGreater(len(fake.last_images or []), 0, "MedGemma'ya görsel gönderilmeli")
        self.assertEqual(result.nihai_karar, KararDurumu.UYGUN)


# Geriye dönük isim (tek provizyon koşar).
IntakeRegressionTests = SingleProvizyonE2ETest


class DiagnosisRouteTests(unittest.TestCase):
    def setUp(self):
        self._orig_overrides = requirement_mod._OVERRIDES
        self._orig_prefix_rules = requirement_mod._PREFIX_RULES
        requirement_mod._OVERRIDES = {}
        requirement_mod._PREFIX_RULES = []
        requirement_mod._REQUIRED_DOC_CODES = set()

    def tearDown(self):
        requirement_mod._OVERRIDES = self._orig_overrides
        requirement_mod._PREFIX_RULES = self._orig_prefix_rules
        requirement_mod._REQUIRED_DOC_CODES = None

    def test_huv_popup_route(self):
        job = ProvizyonJob(provizyon_id="P1", huv_codes=["24.73601"], code_family="HUV")
        self.assertEqual(job.diagnosis_code_source(), "huv")

    def test_sut_popup_route(self):
        job = ProvizyonJob(provizyon_id="P2", sut_codes=["530140"], code_family="SUT")
        self.assertEqual(job.diagnosis_code_source(), "sut")

    def test_first_procedure_wins(self):
        job = ProvizyonJob(
            provizyon_id="P3",
            procedures=[
                ProcedureInput(code="530090", code_type="SUT"),
                ProcedureInput(code="24.73601", code_type="HUV"),
            ],
        )
        self.assertEqual(job.diagnosis_code_source(), "sut")

    def test_huv_job_skips_sut_diagnosis_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = _orchestrator(Path(tmp), enable_diag=True, enable_sut_diag=True)
            job = ProvizyonJob(
                provizyon_id="P-HUV",
                huv_codes=["24.73601"],
                code_family="HUV",
                diagnoses=["K22"],
            )
            result = orch.run(job)
        self.assertIsNotNone(result.tani_kurali)
        self.assertEqual(result.sut_tani_kurali.status, LayerStatus.SKIPPED)
        self.assertIn("HUV provizyonu", result.sut_tani_kurali.message)

    def test_sut_job_skips_huv_diagnosis_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            orch = _orchestrator(Path(tmp), enable_diag=True, enable_sut_diag=True)
            job = ProvizyonJob(
                provizyon_id="P-SUT",
                sut_codes=["530140"],
                code_family="SUT",
                diagnoses=[],
            )
            result = orch.run(job)
        self.assertEqual(result.tani_kurali.status, LayerStatus.SKIPPED)
        self.assertIn("SUT provizyonu", result.tani_kurali.message)
        self.assertIsNotNone(result.sut_tani_kurali)
        self.assertEqual(result.sut_tani_kurali.status, LayerStatus.PASS)


class SutDiagnosisTests(unittest.TestCase):
    def test_sut_allowed_no_diagnosis_required(self):
        from provizyon_engine.engines.sut_diagnosis import check_sut_diagnoses

        job = ProvizyonJob(
            provizyon_id="P-SUT-1",
            sut_codes=["530140"],
            diagnoses=[],
        )
        result = check_sut_diagnoses(job)
        self.assertEqual(result.status, LayerStatus.PASS)
        self.assertEqual(result.detail.get("overall_status"), "allowed")

    def test_sut_review_with_diagnosis_match(self):
        from provizyon_engine.engines.sut_diagnosis import check_sut_diagnoses

        job = ProvizyonJob(
            provizyon_id="P-SUT-2",
            sut_codes=["803000"],
            diagnoses=["E11"],
        )
        result = check_sut_diagnoses(job)
        if result.status == LayerStatus.INSUFFICIENT and "Qdrant" in (result.message or ""):
            self.skipTest("Qdrant sut_diagnosis_rules erişilemiyor")
        self.assertEqual(result.status, LayerStatus.REVIEW)
        self.assertEqual(result.detail.get("overall_status"), "review_required")

    def test_no_sut_codes_skipped(self):
        from provizyon_engine.engines.sut_diagnosis import check_sut_diagnoses

        job = ProvizyonJob(provizyon_id="P-SUT-3", huv_codes=["24.73601"], diagnoses=["K22"])
        result = check_sut_diagnoses(job)
        self.assertEqual(result.status, LayerStatus.SKIPPED)


class SutDiagnosisQdrantTests(unittest.TestCase):
    def test_qdrant_not_required_pass(self):
        from provizyon_engine.engines.sut_diagnosis import check_sut_diagnoses

        result = check_sut_diagnoses(
            ProvizyonJob(provizyon_id="P-SUT-Q1", sut_codes=["530140"], diagnoses=[])
        )
        if result.status == LayerStatus.INSUFFICIENT and "Qdrant" in (result.message or ""):
            self.skipTest("Qdrant sut_diagnosis_rules erişilemiyor")
        self.assertEqual(result.status, LayerStatus.PASS)
        self.assertEqual(result.detail.get("lookup_source"), "qdrant")

    def test_qdrant_review_with_diagnosis_match(self):
        from provizyon_engine.engines.sut_diagnosis import check_sut_diagnoses

        result = check_sut_diagnoses(
            ProvizyonJob(provizyon_id="P-SUT-Q2", sut_codes=["803000"], diagnoses=["E11"])
        )
        if result.status == LayerStatus.INSUFFICIENT and "Qdrant" in (result.message or ""):
            self.skipTest("Qdrant sut_diagnosis_rules erişilemiyor")
        self.assertEqual(result.status, LayerStatus.REVIEW)
        self.assertEqual(result.detail.get("overall_status"), "review_required")
        self.assertEqual(result.detail.get("lookup_source"), "qdrant")

    def test_qdrant_reader_fetch_rule(self):
        from provizyon_engine.persistence.sut_diagnosis_rules_qdrant import SutDiagnosisRulesQdrantReader

        reader = SutDiagnosisRulesQdrantReader()
        if not reader.ping():
            self.skipTest("Qdrant sut_diagnosis_rules erişilemiyor")
        rule = reader.fetch_rule("530140")
        self.assertIsNotNone(rule)
        self.assertEqual(rule["diagnosis_policy"], "not_required")


class TzhDiagnosisTests(unittest.TestCase):
    def test_tzh_only_codes_skipped(self):
        from provizyon_engine.engines.diagnosis import check_diagnoses

        result = check_diagnoses(["TZH.Ilac", "TZH.01.00001"], ["K22"])
        self.assertEqual(result.status, LayerStatus.SKIPPED)
        self.assertIn("TZH.Ilac", result.message)


class SutSkippedTests(unittest.TestCase):
    def test_no_evaluable_mapping(self):
        from provizyon_engine.engines.sut_rules import _has_evaluable_sut_mapping

        self.assertFalse(_has_evaluable_sut_mapping([]))
        self.assertFalse(
            _has_evaluable_sut_mapping([{"sut_code": "", "rule_eval_allowed": False}])
        )
        self.assertTrue(
            _has_evaluable_sut_mapping([{"sut_code": "803520", "rule_eval_allowed": False}])
        )
        self.assertTrue(
            _has_evaluable_sut_mapping([{"sut_code": "", "rule_eval_allowed": True}])
        )


class TaniFailSkipMedGemmaTest(unittest.TestCase):
    """Tanı FAIL -> MedGemma atlanmalı."""

    def setUp(self):
        self._orig_overrides = requirement_mod._OVERRIDES
        self._orig_prefix_rules = requirement_mod._PREFIX_RULES
        requirement_mod._OVERRIDES = {}
        requirement_mod._PREFIX_RULES = []
        requirement_mod._REQUIRED_DOC_CODES = set()

    def tearDown(self):
        requirement_mod._OVERRIDES = self._orig_overrides
        requirement_mod._PREFIX_RULES = self._orig_prefix_rules
        requirement_mod._REQUIRED_DOC_CODES = None

    def test_tani_fail_skips_medgemma(self):
        """Tanı FAIL varken MedGemma çağrılmamalı."""
        fake = FakeMedGemmaClient(
            '{"islem_belge_destekli": true, "tani_belge_destekli": true, '
            '"guven": "high", "manuel_inceleme_gerekli": false, "gerekce": "ok"}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / "rapor.txt"
            doc_path.write_text("Hasta Ahmet Yilmaz icin rapor.", encoding="utf-8")
            orch = _orchestrator(
                Path(tmp), medgemma_client=fake, enable_medgemma=True, enable_diag=True,
            )
            job = ProvizyonJob(
                provizyon_id="P-TANI-FAIL",
                hasta_id="H1",
                patient_name="Ahmet Yilmaz",
                huv_codes=["02.16321"],
                diagnoses=[],
                documents=[DocumentInput(path="rapor.txt")],
            )
            result = orch.run(job)
        self.assertIsNone(result.medgemma)
        self.assertTrue(any("MedGemma" in w and "atlandı" in w for w in result.warnings))


class PatientContextTests(unittest.TestCase):
    def test_group_points_into_records(self):
        from provizyon_engine.persistence.qdrant_findings import group_points_into_records

        points = [
            {"payload": {
                "provizyon_id": "P-OLD",
                "hasta_id": "H1",
                "tc_kimlik": "11111111111",
                "layer": "tani_kurali",
                "status": "pass",
                "message": "ok",
                "nihai_karar": "uygun",
                "finished_at": "2026-01-01T00:00:00+00:00",
            }},
            {"payload": {
                "provizyon_id": "P-OLD",
                "hasta_id": "H1",
                "tc_kimlik": "11111111111",
                "layer": "medgemma",
                "message": "klinik uygun",
                "nihai_karar": "uygun",
                "finished_at": "2026-01-01T00:00:00+00:00",
            }},
        ]
        records = group_points_into_records(points, exclude_provizyon_id="P-NEW", limit=5)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].provizyon_id, "P-OLD")
        self.assertEqual(records[0].tc_kimlik, "11111111111")
        self.assertEqual(len(records[0].layers), 2)

    def test_writer_includes_tc_kimlik(self):
        from provizyon_engine.models import JobResult, KararDurumu
        from provizyon_engine.persistence.qdrant_findings import PatientFindingsWriter

        writer = PatientFindingsWriter()
        result = JobResult(
            provizyon_id="P-TC",
            hasta_id="H1",
            nihai_karar=KararDurumu.UYGUN,
            gerekce="test",
        )
        layers = writer._collect_layers(result, tc_kimlik="53491683054", allow_document_rag=False)
        self.assertEqual(layers[0][2]["tc_kimlik"], "53491683054")

    def test_medgemma_prompt_includes_patient_history(self):
        from provizyon_engine.persistence.qdrant_findings import (
            PatientFindingLayer,
            PatientProvizyonRecord,
        )

        class FakeReader:
            def fetch_by_patient(self, **kwargs):
                return [
                    PatientProvizyonRecord(
                        provizyon_id="3208035",
                        hasta_id="0030024",
                        tc_kimlik="53491683054",
                        nihai_karar="uygun",
                        finished_at="2026-01-23T08:23:00+00:00",
                        layers=[
                            PatientFindingLayer(layer="tani_kurali", status="pass", message="ok"),
                        ],
                    )
                ]

            def fetch_similar(self, *args, **kwargs):
                return []

        fake = FakeMedGemmaClient(
            '{"islem_belge_destekli": true, "tani_belge_destekli": true, '
            '"yas_cinsiyet_uygun": true, "klinik_celiski": false, "eksik_evrak": false, '
            '"manuel_inceleme_gerekli": false, "gerekce": "OK", "guven": "high"}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / "rapor.txt"
            doc_path.write_text("Hasta Ahmet Yilmaz icin detayli klinik rapor metni.", encoding="utf-8")
            config = OrchestratorConfig(
                enable_diagnosis=False,
                enable_sut_rules=False,
                enable_medgemma=True,
                enable_persistence=False,
                enable_patient_context=True,
                use_qdrant_rag=False,
                include_vision=False,
            )
            orch = ProvizyonOrchestrator(
                config=config,
                document_source=FilesystemDocumentSource(root=Path(tmp)),
                medgemma_client=fake,
                findings_reader=FakeReader(),
            )
            job = ProvizyonJob(
                provizyon_id="P-NEW",
                hasta_id="0030024",
                tc_kimlik="53491683054",
                patient_name="Ahmet Yilmaz",
                huv_codes=["02.16321"],
                diagnoses=["H90.3"],
                documents=[DocumentInput(path="rapor.txt")],
            )
            result = orch.run(job)

        self.assertEqual(result.nihai_karar, KararDurumu.UYGUN)
        self.assertIn("patient_context", result.raw)
        self.assertEqual(result.raw["patient_context"]["history_count"], 1)
        self.assertIsNotNone(fake.last_user_text)
        self.assertIn("GEÇMİŞ PROVİZYON KAYITLARI", fake.last_user_text)
        self.assertIn("3208035", fake.last_user_text)

    def test_patient_context_failure_does_not_break_pipeline(self):
        class BrokenReader:
            def fetch_by_patient(self, **kwargs):
                raise RuntimeError("qdrant down")

            def fetch_similar(self, *args, **kwargs):
                raise RuntimeError("qdrant down")

        fake = FakeMedGemmaClient(
            '{"islem_belge_destekli": true, "tani_belge_destekli": true, '
            '"manuel_inceleme_gerekli": false, "gerekce": "OK", "guven": "high"}'
        )
        with tempfile.TemporaryDirectory() as tmp:
            doc_path = Path(tmp) / "rapor.txt"
            doc_path.write_text("Hasta Ahmet Yilmaz icin rapor.", encoding="utf-8")
            config = OrchestratorConfig(
                enable_diagnosis=False,
                enable_sut_rules=False,
                enable_medgemma=True,
                enable_persistence=False,
                enable_patient_context=True,
                use_qdrant_rag=False,
                include_vision=False,
            )
            orch = ProvizyonOrchestrator(
                config=config,
                document_source=FilesystemDocumentSource(root=Path(tmp)),
                medgemma_client=fake,
                findings_reader=BrokenReader(),
            )
            job = ProvizyonJob(
                provizyon_id="P-ERR",
                hasta_id="H1",
                tc_kimlik="11111111111",
                patient_name="Ahmet Yilmaz",
                huv_codes=["02.16321"],
                diagnoses=["H90.3"],
                documents=[DocumentInput(path="rapor.txt")],
            )
            result = orch.run(job)

        self.assertEqual(result.nihai_karar, KararDurumu.UYGUN)
        self.assertTrue(any("Geçmiş hasta kaydı" in w for w in result.warnings))


class DiagnosisQdrantTests(unittest.TestCase):
    def test_qdrant_not_required_pass(self):
        from provizyon_engine.engines.diagnosis import check_diagnoses

        result = check_diagnoses(["17.74498"], ["K22", "J01"])
        if result.status == LayerStatus.INSUFFICIENT and "Qdrant" in (result.message or ""):
            self.skipTest("Qdrant huv_diagnosis_rules erişilemiyor")
        self.assertEqual(result.status, LayerStatus.PASS)
        self.assertEqual(result.detail.get("lookup_source"), "qdrant")

    def test_qdrant_reader_fetch_rule(self):
        from provizyon_engine.persistence.diagnosis_rules_qdrant import DiagnosisRulesQdrantReader

        reader = DiagnosisRulesQdrantReader()
        if not reader.ping():
            self.skipTest("Qdrant huv_diagnosis_rules erişilemiyor")
        rule = reader.fetch_rule("17.74498")
        self.assertIsNotNone(rule)
        self.assertEqual(rule["diagnosis_policy"], "not_required")


class HuvSutCrosswalkDisableTests(unittest.TestCase):
    """HUV→SUT runtime crosswalk kapalıyken beklenen davranış."""

    def test_huv_only_skips_sut_rules_with_reason(self):
        from provizyon_engine.engines.sut_rules import check_sut_rules

        job = ProvizyonJob(
            provizyon_id="P-XW-HUV",
            huv_codes=["24.73601"],
            code_family="HUV",
        )
        result = check_sut_rules(job, use_qdrant=False, enable_huv_sut_crosswalk=False)
        self.assertEqual(result.status, LayerStatus.SKIPPED)
        self.assertEqual(result.detail.get("skipped_reason"), "huv_sut_crosswalk_disabled")

    def test_direct_sut_still_runs_advise(self):
        from unittest.mock import patch

        from provizyon_engine.engines import sut_rules as sut_mod

        job = ProvizyonJob(
            provizyon_id="P-XW-SUT",
            sut_codes=["530140"],
            code_family="SUT",
            procedures=[ProcedureInput(code="530140", code_type="SUT")],
        )
        fake_advice = {
            "resolved_services": [
                {
                    "input_kind": "SUT",
                    "input_code": "530140",
                    "sut_code": "530140",
                    "relation_type": "direct_sut",
                    "rule_eval_allowed": True,
                }
            ],
            "warnings": [],
            "sut_rule_evaluation": {
                "overall_status": "PASS",
                "summary": {},
                "service_results": [],
            },
        }
        with patch.object(sut_mod, "settings") as fake_settings:
            fake_settings.SUT_RULES_PATH.exists.return_value = True
            fake_settings.SUT_OUT_DIR = Path("/tmp")
            fake_settings.SUT_INDEX_PATH = Path("/tmp")
            fake_settings.SUT_UNIFIED_COLLECTION = "test"
            fake_settings.QDRANT_URL = "http://localhost"
            fake_settings.TEI_URL = "http://localhost"
            with patch(
                "unified_catalog.unified_advisor.advise",
                return_value=fake_advice,
            ) as advise_mock:
                result = sut_mod.check_sut_rules(
                    job, use_qdrant=False, enable_huv_sut_crosswalk=False
                )

        self.assertNotEqual(result.status, LayerStatus.SKIPPED)
        self.assertEqual(result.status, LayerStatus.PASS)
        advise_mock.assert_called_once()
        kwargs = advise_mock.call_args.kwargs
        self.assertFalse(kwargs.get("allow_huv_crosswalk"))
        services = kwargs.get("input_services") or []
        self.assertTrue(any(str(s.get("code")) == "530140" for s in services))

    def test_requirement_skips_huv_to_sut_resolve(self):
        from unittest.mock import patch

        job = ProvizyonJob(
            provizyon_id="P-XW-REQ",
            huv_codes=["24.73601"],
            code_family="HUV",
        )

        def _undetermined(code, *args, **kwargs):
            # HUV için belirsiz → crosswalk yolu; aksi halde belge zorunlu değil.
            return None if not requirement_mod._is_direct_sut_code(code) else False

        with patch.object(
            requirement_mod, "_code_requires_document", side_effect=_undetermined
        ):
            with patch.object(requirement_mod, "_resolve_huv_to_sut") as resolve_off:
                resolve_off.return_value = ["530140"]
                requirement_mod.check_requirement(
                    job,
                    documents_present=False,
                    enable_huv_sut_crosswalk=False,
                )
            resolve_off.assert_not_called()

            with patch.object(requirement_mod, "_resolve_huv_to_sut") as resolve_on:
                resolve_on.return_value = []
                requirement_mod.check_requirement(
                    job,
                    documents_present=False,
                    enable_huv_sut_crosswalk=True,
                )
            resolve_on.assert_called_once_with(["24.73601"])

    def test_orchestrator_default_config_disables_crosswalk(self):
        self.assertFalse(OrchestratorConfig().enable_huv_sut_crosswalk)


if __name__ == "__main__":
    unittest.main()
