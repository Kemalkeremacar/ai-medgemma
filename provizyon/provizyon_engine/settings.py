"""Provizyon orkestratör ayarları.

Tüm değerler ortam değişkeniyle override edilebilir. Varsayılanlar bu
makinedeki (DGX) yerleşik servis adreslerine göre seçilmiştir.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROVIZYON_ROOT = PACKAGE_ROOT.parent

try:
    from dotenv import load_dotenv
    load_dotenv(PROVIZYON_ROOT / ".env")
except ImportError:
    pass
SUT_ROOT = PROVIZYON_ROOT / "lib"
GEMMA_ROOT = PROVIZYON_ROOT.parent  # GemmaApp/


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- Redis kuyruk ---------------------------------------------------------
REDIS_URL = _env("PROVIZYON_REDIS_URL", "redis://127.0.0.1:6379/0")
QUEUE_NAME = _env("PROVIZYON_QUEUE_NAME", "provizyon:jobs")
PROCESSING_QUEUE = _env("PROVIZYON_PROCESSING_QUEUE", "provizyon:processing")
DEAD_LETTER_QUEUE = _env("PROVIZYON_DEAD_LETTER_QUEUE", "provizyon:dead")
RESULT_KEY_PREFIX = _env("PROVIZYON_RESULT_PREFIX", "provizyon:result:")
RECENT_KEY = _env("PROVIZYON_RECENT_KEY", "provizyon:recent")
RESULT_TTL_SECONDS = _env_int("PROVIZYON_RESULT_TTL", 7 * 24 * 3600)
MAX_RETRIES = _env_int("PROVIZYON_MAX_RETRIES", 3)

# --- Klasör izleyici (intake watcher) ------------------------------------
# Bu kök altına düşen her provizyon alt klasörü (PopupPage PDF içerenler)
# otomatik olarak işe çevrilip kuyruğa eklenir.
INTAKE_WATCH_DIR = Path(_env("PROVIZYON_INTAKE_WATCH_DIR", str(GEMMA_ROOT / "data" / "intake")))
INTAKE_POLL_SECONDS = _env_int("PROVIZYON_INTAKE_POLL_SECONDS", 10)
# Bir klasörün "hazır" sayılması için son değişiklikten bu kadar saniye geçmeli
# (tüm dosyalar kopyalanana kadar beklemek için).
INTAKE_STABLE_SECONDS = _env_int("PROVIZYON_INTAKE_STABLE_SECONDS", 15)
# İşlenen klasörleri takip eden Redis seti (tekrar enqueue'u önler).
INTAKE_SEEN_KEY = _env("PROVIZYON_INTAKE_SEEN_KEY", "provizyon:intake:seen")

# --- Belge kaynağı --------------------------------------------------------
# Dosya sistemi tabanlı: işle gelen belge yolları bu kök altında çözümlenir.
DOCUMENT_ROOT = Path(_env("PROVIZYON_DOCUMENT_ROOT", str(GEMMA_ROOT / "data" / "documents")))
# Vision için hazırlanan ara görseller buraya yazılır.
WORK_DIR = Path(_env("PROVIZYON_WORK_DIR", str(GEMMA_ROOT / "data" / "provizyon_work")))

# --- OCR (Tesseract) ------------------------------------------------------
TESSERACT_CMD = _env("PROVIZYON_TESSERACT_CMD", "tesseract")
OCR_LANG = _env("PROVIZYON_OCR_LANG", "tur+eng")
# OCR'ı tetiklemek için sayfadaki gömülü metnin altında kalması gereken eşik.
OCR_MIN_TEXT_CHARS = _env_int("PROVIZYON_OCR_MIN_TEXT_CHARS", 40)
# True ise görseli olan her sayfa OCR'dan geçer (gömülü metin olsa bile).
OCR_ALL_PAGES = _env_bool("PROVIZYON_OCR_ALL_PAGES", True)
# OCR için PDF render / görsel ölçekleme DPI (vision'dan yüksek tutulur).
OCR_DPI = _env_int("PROVIZYON_OCR_DPI", 400)
# OCR öncesi görselin minimum uzun kenarı (piksel).
OCR_MIN_EDGE_PX = _env_int("PROVIZYON_OCR_MIN_EDGE", 2400)
# Tesseract sayfa segmentasyon modu (3 = otomatik, 6 = tek blok).
OCR_PSM = _env_int("PROVIZYON_OCR_PSM", 3)
OCR_PREPROCESS = _env_bool("PROVIZYON_OCR_PREPROCESS", True)
# Taramalarda eğrilik düzeltme (deskew); numpy gerektirir, yoksa sessizce atlanır.
OCR_DESKEW = _env_bool("PROVIZYON_OCR_DESKEW", True)
# Deskew için taranacak maksimum açı (± derece) ve adım.
OCR_DESKEW_MAX_ANGLE = float(_env("PROVIZYON_OCR_DESKEW_MAX_ANGLE", "6"))
OCR_DESKEW_STEP = float(_env("PROVIZYON_OCR_DESKEW_STEP", "0.5"))
# Hafif gürültü azaltma (median filtre) — tuz-biber taramalar için.
OCR_DENOISE = _env_bool("PROVIZYON_OCR_DENOISE", True)
# Otsu ikilileştirme; Tesseract zaten dahili ikilileştirir, düzensiz aydınlatmalı
# taramalarda regresyon riski olduğundan varsayılan kapalı (test edip açın).
OCR_BINARIZE = _env_bool("PROVIZYON_OCR_BINARIZE", False)
# OCR sonrası bu skorun altındaki sayfalar düşük kalite sayılır (0.0–1.0).
OCR_MIN_QUALITY = float(_env("PROVIZYON_OCR_MIN_QUALITY", "0.35"))

# --- Vision / görsel hazırlama -------------------------------------------
# MedGemma'ya gönderilecek görsellerin en uzun kenar piksel sınırı.
VISION_MAX_EDGE_PX = _env_int("PROVIZYON_VISION_MAX_EDGE", 2048)
# MedGemma'ya gönderilecek maksimum görsel sayısı (0 = sınırsız).
VISION_MAX_IMAGES = _env_int("PROVIZYON_VISION_MAX_IMAGES", 0)
# PDF sayfasından çıkarılacak gömülü görseller için minimum piksel alanı (gürültü filtresi).
EMBEDDED_IMAGE_MIN_AREA = _env_int("PROVIZYON_EMBEDDED_IMAGE_MIN_AREA", 256)
# MedGemma prompt'una girecek metin kanıtı üst sınırı (karakter).
TEXT_EVIDENCE_MAX_CHARS = _env_int("PROVIZYON_TEXT_EVIDENCE_MAX_CHARS", 32000)
# PDF sayfasını görsele render ederken kullanılan DPI.
PDF_RENDER_DPI = _env_int("PROVIZYON_PDF_RENDER_DPI", 200)

# --- MedGemma -------------------------------------------------------------
MEDGEMMA_BASE_URL = _env("PROVIZYON_MEDGEMMA_BASE_URL", _env("MEDGEMMA_BASE_URL", "http://127.0.0.1:8000/v1"))
MEDGEMMA_API_KEY = _env("PROVIZYON_MEDGEMMA_API_KEY", "sk-no-key")
MEDGEMMA_MODEL = _env("PROVIZYON_MEDGEMMA_MODEL", "/raid/monassist1/medgemma_model_gptq_w4")
MEDGEMMA_TIMEOUT = _env_int("PROVIZYON_MEDGEMMA_TIMEOUT", 900)
MEDGEMMA_MAX_TOKENS = _env_int("PROVIZYON_MEDGEMMA_MAX_TOKENS", 4096)
MEDGEMMA_TEMPERATURE = float(_env("PROVIZYON_MEDGEMMA_TEMPERATURE", "0.1"))
# Vision desteği otomatik tespit edilir; "off" ile zorla kapatılabilir.
MEDGEMMA_VISION_MODE = _env("PROVIZYON_MEDGEMMA_VISION_MODE", "auto")  # auto | on | off

# --- Qdrant / embedding ---------------------------------------------------
QDRANT_URL = _env("PROVIZYON_QDRANT_URL", _env("QDRANT_URL", "http://127.0.0.1:6333"))
TEI_URL = _env("PROVIZYON_TEI_URL", _env("TEI_URL", "http://127.0.0.1:8002"))
EMBEDDING_DIM = _env_int("PROVIZYON_EMBEDDING_DIM", 1024)
PATIENT_FINDINGS_COLLECTION = _env("PROVIZYON_FINDINGS_COLLECTION", "patient_findings")
DIAGNOSIS_RULES_COLLECTION = _env("PROVIZYON_DIAGNOSIS_COLLECTION", "huv_diagnosis_rules")
SUT_DIAGNOSIS_RULES_COLLECTION = _env("PROVIZYON_SUT_DIAGNOSIS_COLLECTION", "sut_diagnosis_rules")
# Tanı-işlem geçmiş ödeme eğilimi sinyalleri (Qdrant pilot collection).
DIAGNOSIS_PROCEDURE_COLLECTION = _env("PROVIZYON_DIAGNOSIS_PROCEDURE_COLLECTION", "diagnosis_procedure_pilot")
ENABLE_DIAGNOSIS_PAYMENT_SIGNAL = _env_bool("PROVIZYON_ENABLE_DIAGNOSIS_PAYMENT_SIGNAL", True)
ENABLE_PATIENT_CONTEXT = _env_bool("PROVIZYON_ENABLE_PATIENT_CONTEXT", True)
ENABLE_SIMILAR_CASES = _env_bool("PROVIZYON_ENABLE_SIMILAR_CASES", True)
PATIENT_CONTEXT_MAX_RECORDS = _env_int("PROVIZYON_PATIENT_CONTEXT_MAX", 5)
PATIENT_CONTEXT_SIMILAR_LIMIT = _env_int("PROVIZYON_SIMILAR_CASE_LIMIT", 3)

# --- SUT motor verileri ---------------------------------------------------
_DATA_GENERATED = PROVIZYON_ROOT / "data" / "generated"
SUT_OUT_DIR = Path(_env("PROVIZYON_SUT_OUT_DIR", str(_DATA_GENERATED / "unified_catalog_final_medgemma")))
SUT_RULES_PATH = Path(_env("PROVIZYON_SUT_RULES", str(_DATA_GENERATED / "sut_rules_merged.json")))
SUT_INDEX_PATH = Path(_env("PROVIZYON_SUT_INDEX", str(_DATA_GENERATED / "sut_index_core.json")))
SUT_UNIFIED_COLLECTION = _env("PROVIZYON_SUT_COLLECTION", "huv_sut_unified_catalog")

# --- MSSQL veritabanı -----------------------------------------------------
MSSQL_HOST = _env("MSSQL_HOST", "178.157.14.208")
MSSQL_PORT = _env_int("MSSQL_PORT", 1433)
MSSQL_DATABASE = _env("MSSQL_DATABASE", "ESYS_SAGLIK_TEST")
MSSQL_USER = _env("MSSQL_USER", "SQLMediUser")
MSSQL_PASSWORD = _env("MSSQL_PASSWORD", "")
MSSQL_DRIVER = _env("MSSQL_DRIVER", "ODBC Driver 18 for SQL Server")

def get_mssql_conn_str(database: str | None = None) -> str:
    db = database or MSSQL_DATABASE
    return (
        f"DRIVER={{{MSSQL_DRIVER}}};"
        f"SERVER={MSSQL_HOST},{MSSQL_PORT};"
        f"DATABASE={db};"
        f"UID={MSSQL_USER};"
        f"PWD={MSSQL_PASSWORD};"
        "TrustServerCertificate=yes;"
    )
