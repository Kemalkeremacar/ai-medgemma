#!/usr/bin/env python3
"""patient_findings collection'ına binlerce gerçekçi provizyon kaydı yazar.

Kullanım:
    cd /home/monassist1/GemmaApp/provizyon
    python seed_patient_findings.py [--count 2000] [--batch-size 50] [--dry-run]

Üretilen kayıtlar:
  - HUV işlem-tanı uyumu (tani_kurali layer)
  - SUT işlem-tanı uyumu (sut_tani_kurali layer)
  - SUT işlem kuralı (sut_kurali layer)
  - MedGemma klinik yorumu (medgemma layer)
  - Nihai karar (nihai_karar layer)

Her kayıt gerçekçi tıbbi veriler içerir: HUV/SUT kodları, ICD-10 tanıları,
Türkçe gerekçeler, ve farklı karar sonuçları.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from provizyon_engine.models import (
    JobResult,
    JobStatus,
    KararDurumu,
    LayerResult,
    LayerStatus,
    MedGemmaClinicalOutput,
)

# ---------------------------------------------------------------------------
# Gerçekçi tıbbi veri havuzları
# ---------------------------------------------------------------------------

TURKISH_FIRST_NAMES_M = [
    "Ahmet", "Mehmet", "Mustafa", "Ali", "Hüseyin", "Hasan", "İbrahim",
    "Ömer", "Yusuf", "Murat", "Osman", "Süleyman", "Halil", "Emre",
    "Burak", "Fatih", "Kemal", "Serkan", "Cemal", "Kadir", "Selim",
    "Cem", "Bülent", "Ercan", "Ramazan", "Orhan", "Volkan", "Taner",
    "Cengiz", "Sedat", "Erdal", "Bayram", "Ferhat", "Tolga", "Onur",
]
TURKISH_FIRST_NAMES_F = [
    "Fatma", "Ayşe", "Emine", "Hatice", "Zeynep", "Elif", "Meryem",
    "Şerife", "Zehra", "Sultan", "Hanife", "Merve", "Havva", "Gülsüm",
    "Hacer", "Hülya", "Serpil", "Filiz", "Derya", "Nesrin", "Dilek",
    "Esra", "Gül", "Sibel", "Melek", "Aynur", "Sevim", "Nurten",
    "Songül", "Sema", "Pınar", "Özlem", "Nazan", "Tülay", "Aysun",
]
TURKISH_LAST_NAMES = [
    "Yılmaz", "Kaya", "Demir", "Çelik", "Şahin", "Öztürk", "Arslan",
    "Doğan", "Kılıç", "Aslan", "Aydın", "Özdemir", "Yıldırım", "Erdoğan",
    "Polat", "Koç", "Aksoy", "Yıldız", "Kurt", "Güneş", "Kaplan",
    "Korkmaz", "Keskin", "Çetin", "Bulut", "Aktaş", "Ünal", "Acar",
    "Şimşek", "Karaca", "Özer", "Tekin", "Duman", "Ateş", "Karataş",
    "Avcı", "Bayrak", "Güler", "Kara", "Özkan", "Taş", "Tunç",
]

INSTITUTIONS = [
    ("Acıbadem Hastanesi", "özel"),
    ("Memorial Hastanesi", "özel"),
    ("Medicana International", "özel"),
    ("Medipol Üniversitesi Hastanesi", "üniversite"),
    ("Florence Nightingale Hastanesi", "özel"),
    ("Liv Hospital", "özel"),
    ("Medical Park", "özel"),
    ("Başkent Üniversitesi Hastanesi", "üniversite"),
    ("Koç Üniversitesi Hastanesi", "üniversite"),
    ("İstanbul Tıp Fakültesi", "üniversite"),
    ("Cerrahpaşa Tıp Fakültesi", "üniversite"),
    ("Ankara Üniversitesi Tıp Fakültesi", "üniversite"),
    ("Hacettepe Üniversitesi Hastanesi", "üniversite"),
    ("Ege Üniversitesi Hastanesi", "üniversite"),
    ("Gazi Üniversitesi Hastanesi", "üniversite"),
    ("Dokuz Eylül Üniversitesi Hastanesi", "üniversite"),
    ("Yeditepe Üniversitesi Hastanesi", "özel"),
    ("Anadolu Sağlık Merkezi", "özel"),
    ("VM Medical Park", "özel"),
    ("Özel Bayındır Hastanesi", "özel"),
]

# ---------------------------------------------------------------------------
# Klinik senaryolar: (alan, HUV_kodları, SUT_kodları, uygun_tanılar,
#   uyumsuz_tanılar, işlem_adı, klinik_alan)
# ---------------------------------------------------------------------------

CLINICAL_SCENARIOS = [
    # --- ORTOPEDİ ---
    {
        "alan": "ortopedi",
        "huv_codes": ["21.101.004", "21.101.005", "21.101.010"],
        "sut_codes": ["520070", "520080", "520090"],
        "procedures": [
            ("21.101.004", "Diz MR", "HUV"),
            ("520070", "Diz MR görüntüleme", "SUT"),
        ],
        "uygun_tanilar": ["M17.1", "M17.0", "M23.2", "M23.3", "S83.0", "S83.1", "M76.5"],
        "uyumsuz_tanilar": ["J18.9", "K35.8", "I21.0", "E11.9", "N39.0"],
        "gerekce_uygun": "Diz MR görüntüleme gonartroz / menisküs yırtığı tanısıyla uyumludur.",
        "gerekce_uyumsuz": "Diz MR görüntüleme için konulan tanı ({tani}) ortopedik bir endikasyon içermemektedir.",
    },
    {
        "alan": "ortopedi",
        "huv_codes": ["21.305.001", "21.305.002"],
        "sut_codes": ["520370", "520380"],
        "procedures": [
            ("21.305.001", "Total diz protezi", "HUV"),
            ("520370", "Total diz protezi", "SUT"),
        ],
        "uygun_tanilar": ["M17.0", "M17.1", "M17.9", "M17.2", "M17.3"],
        "uyumsuz_tanilar": ["M54.5", "M79.3", "G56.0", "J45.9", "E11.9"],
        "gerekce_uygun": "Total diz protezi primer gonartroz tanısıyla endikedir.",
        "gerekce_uyumsuz": "Total diz protezi için {tani} tanısı endikasyon oluşturmamaktadır.",
    },
    {
        "alan": "ortopedi",
        "huv_codes": ["21.320.001", "21.320.005"],
        "sut_codes": ["520400", "520410"],
        "procedures": [
            ("21.320.001", "Total kalça protezi", "HUV"),
            ("520400", "Total kalça protezi", "SUT"),
        ],
        "uygun_tanilar": ["M16.0", "M16.1", "M16.9", "S72.0", "M87.0"],
        "uyumsuz_tanilar": ["M54.5", "E11.9", "I10", "J44.1", "F32.1"],
        "gerekce_uygun": "Total kalça protezi koksartroz / femur boyun kırığı tanısıyla endikedir.",
        "gerekce_uyumsuz": "Total kalça protezi için {tani} tanısı uygun değildir.",
    },
    {
        "alan": "ortopedi",
        "huv_codes": ["21.101.015", "21.101.020"],
        "sut_codes": ["520050", "520060"],
        "procedures": [
            ("21.101.015", "Omuz MR", "HUV"),
            ("520050", "Omuz MR görüntüleme", "SUT"),
        ],
        "uygun_tanilar": ["M75.1", "M75.0", "S43.0", "M75.4", "S42.2"],
        "uyumsuz_tanilar": ["K80.2", "N40.0", "E78.0", "J06.9", "L30.9"],
        "gerekce_uygun": "Omuz MR rotator kaf yırtığı / omuz sıkışma sendromu tanısıyla uyumludur.",
        "gerekce_uyumsuz": "Omuz MR için {tani} tanısı ortopedik endikasyon içermemektedir.",
    },
    {
        "alan": "ortopedi",
        "huv_codes": ["21.260.001", "21.260.005"],
        "sut_codes": ["520300", "520310"],
        "procedures": [
            ("21.260.001", "Artroskopik menisküs tamiri", "HUV"),
            ("520300", "Artroskopik diz cerrahisi", "SUT"),
        ],
        "uygun_tanilar": ["M23.2", "M23.3", "S83.2", "M23.0", "M23.1"],
        "uyumsuz_tanilar": ["I10", "E11.9", "J18.9", "G43.9", "N18.3"],
        "gerekce_uygun": "Artroskopik menisküs tamiri menisküs yırtığı tanısıyla endikedir.",
        "gerekce_uyumsuz": "Artroskopik menisküs tamiri için {tani} tanısı uygun değildir.",
    },
    # --- KARDİYOLOJİ ---
    {
        "alan": "kardiyoloji",
        "huv_codes": ["09.001.004", "09.001.005"],
        "sut_codes": ["700740", "700750"],
        "procedures": [
            ("09.001.004", "Koroner anjiyografi", "HUV"),
            ("700740", "Koroner anjiyografi", "SUT"),
        ],
        "uygun_tanilar": ["I25.1", "I20.0", "I21.0", "I21.1", "I25.0", "I20.8"],
        "uyumsuz_tanilar": ["M54.5", "K80.2", "J45.9", "E11.9", "H25.0"],
        "gerekce_uygun": "Koroner anjiyografi kronik iskemik kalp hastalığı / akut koroner sendrom tanısıyla endikedir.",
        "gerekce_uyumsuz": "Koroner anjiyografi için {tani} tanısı kardiyolojik endikasyon içermemektedir.",
    },
    {
        "alan": "kardiyoloji",
        "huv_codes": ["09.007.001", "09.007.010"],
        "sut_codes": ["700760", "700770"],
        "procedures": [
            ("09.007.001", "Koroner stent yerleştirme", "HUV"),
            ("700760", "PTCA + Stent", "SUT"),
        ],
        "uygun_tanilar": ["I25.1", "I21.0", "I21.1", "I21.2", "I20.0", "I25.5"],
        "uyumsuz_tanilar": ["M17.1", "G43.9", "K21.0", "J44.1", "N39.0"],
        "gerekce_uygun": "Koroner stent yerleştirme akut miyokard infarktüsü / stabil anjina tanısıyla endikedir.",
        "gerekce_uyumsuz": "Koroner stent için {tani} tanısı kardiyolojik endikasyon değildir.",
    },
    {
        "alan": "kardiyoloji",
        "huv_codes": ["09.010.001", "09.010.005"],
        "sut_codes": ["700800", "700810"],
        "procedures": [
            ("09.010.001", "CABG (koroner bypass)", "HUV"),
            ("700800", "Koroner arter bypass greft", "SUT"),
        ],
        "uygun_tanilar": ["I25.1", "I25.0", "I21.0", "I25.2", "I20.0"],
        "uyumsuz_tanilar": ["H25.0", "L40.0", "M54.5", "E05.0", "N40.0"],
        "gerekce_uygun": "CABG ameliyatı çok damar koroner arter hastalığı tanısıyla endikedir.",
        "gerekce_uyumsuz": "CABG için {tani} tanısı uygun değildir; kardiyovasküler endikasyon bulunmamaktadır.",
    },
    {
        "alan": "kardiyoloji",
        "huv_codes": ["09.002.001", "09.002.010"],
        "sut_codes": ["700600", "700610"],
        "procedures": [
            ("09.002.001", "Ekokardiyografi", "HUV"),
            ("700600", "Transtorasik ekokardiyografi", "SUT"),
        ],
        "uygun_tanilar": ["I50.0", "I50.9", "I34.1", "I35.0", "I42.0", "I38"],
        "uyumsuz_tanilar": ["M54.5", "K80.2", "J06.9", "E11.9", "L30.9"],
        "gerekce_uygun": "Ekokardiyografi kalp yetmezliği / kapak hastalığı tanısıyla uyumludur.",
        "gerekce_uyumsuz": "Ekokardiyografi için {tani} tanısı kardiyolojik endikasyon oluşturmamaktadır.",
    },
    # --- GÖZ HASTALIKLARI ---
    {
        "alan": "goz",
        "huv_codes": ["12.001.010", "12.001.015"],
        "sut_codes": ["600350", "600360"],
        "procedures": [
            ("12.001.010", "Katarakt ameliyatı (FAKO)", "HUV"),
            ("600350", "Fakoemülsifikasyon + GİL", "SUT"),
        ],
        "uygun_tanilar": ["H25.0", "H25.1", "H25.9", "H26.0", "H26.9", "H28.0"],
        "uyumsuz_tanilar": ["M17.1", "I25.1", "E11.9", "J44.1", "K80.2"],
        "gerekce_uygun": "Katarakt ameliyatı senil katarakt / lens opasitesi tanısıyla endikedir.",
        "gerekce_uyumsuz": "Katarakt ameliyatı için {tani} tanısı oftalmolojik endikasyon değildir.",
    },
    {
        "alan": "goz",
        "huv_codes": ["12.005.001", "12.005.005"],
        "sut_codes": ["600400", "600410"],
        "procedures": [
            ("12.005.001", "Vitrektomi", "HUV"),
            ("600400", "Pars plana vitrektomi", "SUT"),
        ],
        "uygun_tanilar": ["H33.0", "H33.2", "H43.1", "H35.3", "H44.0"],
        "uyumsuz_tanilar": ["M54.5", "I10", "E78.0", "J45.9", "K21.0"],
        "gerekce_uygun": "Vitrektomi retina dekolmanı / vitreus hemorajisi tanısıyla endikedir.",
        "gerekce_uyumsuz": "Vitrektomi için {tani} tanısı oftalmolojik endikasyon içermemektedir.",
    },
    {
        "alan": "goz",
        "huv_codes": ["12.003.001", "12.003.005"],
        "sut_codes": ["600300", "600310"],
        "procedures": [
            ("12.003.001", "İntravitreal enjeksiyon", "HUV"),
            ("600300", "İntravitreal enjeksiyon", "SUT"),
        ],
        "uygun_tanilar": ["H35.3", "H36.0", "E11.3", "H34.1", "H35.0"],
        "uyumsuz_tanilar": ["M17.1", "K35.8", "I21.0", "J44.1", "N39.0"],
        "gerekce_uygun": "İntravitreal enjeksiyon diyabetik retinopati / maküla dejenerasyonu tanısıyla endikedir.",
        "gerekce_uyumsuz": "İntravitreal enjeksiyon için {tani} tanısı uygun değildir.",
    },
    # --- GENEL CERRAHİ ---
    {
        "alan": "genel_cerrahi",
        "huv_codes": ["16.001.001", "16.001.005"],
        "sut_codes": ["620010", "620020"],
        "procedures": [
            ("16.001.001", "Apendektomi", "HUV"),
            ("620010", "Apendektomi (laparoskopik)", "SUT"),
        ],
        "uygun_tanilar": ["K35.8", "K35.3", "K35.2", "K36", "K37"],
        "uyumsuz_tanilar": ["M17.1", "I25.1", "H25.0", "G43.9", "E05.0"],
        "gerekce_uygun": "Apendektomi akut apandisit tanısıyla endikedir.",
        "gerekce_uyumsuz": "Apendektomi için {tani} tanısı cerrahi endikasyon oluşturmamaktadır.",
    },
    {
        "alan": "genel_cerrahi",
        "huv_codes": ["16.010.001", "16.010.010"],
        "sut_codes": ["620100", "620110"],
        "procedures": [
            ("16.010.001", "Kolesistektomi", "HUV"),
            ("620100", "Laparoskopik kolesistektomi", "SUT"),
        ],
        "uygun_tanilar": ["K80.0", "K80.1", "K80.2", "K81.0", "K81.1", "K82.1"],
        "uyumsuz_tanilar": ["M54.5", "I10", "E11.9", "J06.9", "H25.0"],
        "gerekce_uygun": "Kolesistektomi safra taşı / kolesistit tanısıyla endikedir.",
        "gerekce_uyumsuz": "Kolesistektomi için {tani} tanısı cerrahi endikasyon değildir.",
    },
    {
        "alan": "genel_cerrahi",
        "huv_codes": ["16.020.001", "16.020.005"],
        "sut_codes": ["620200", "620210"],
        "procedures": [
            ("16.020.001", "İnguinal herni onarımı", "HUV"),
            ("620200", "İnguinal herni tamiri", "SUT"),
        ],
        "uygun_tanilar": ["K40.9", "K40.3", "K40.4", "K40.0", "K40.1"],
        "uyumsuz_tanilar": ["I25.1", "M17.1", "E11.9", "J44.1", "N39.0"],
        "gerekce_uygun": "İnguinal herni onarımı kasık fıtığı tanısıyla endikedir.",
        "gerekce_uyumsuz": "İnguinal herni onarımı için {tani} tanısı uygun değildir.",
    },
    {
        "alan": "genel_cerrahi",
        "huv_codes": ["16.030.001", "16.030.010"],
        "sut_codes": ["620300", "620310"],
        "procedures": [
            ("16.030.001", "Tiroidektomi", "HUV"),
            ("620300", "Total tiroidektomi", "SUT"),
        ],
        "uygun_tanilar": ["E04.0", "E04.1", "E04.2", "C73", "E05.0", "E06.1"],
        "uyumsuz_tanilar": ["M17.1", "K80.2", "I21.0", "J45.9", "N40.0"],
        "gerekce_uygun": "Tiroidektomi nodüler guatr / tiroid karsinomu tanısıyla endikedir.",
        "gerekce_uyumsuz": "Tiroidektomi için {tani} tanısı uygun endikasyon değildir.",
    },
    # --- NÖROLOJİ ---
    {
        "alan": "noroloji",
        "huv_codes": ["13.001.001", "13.001.005"],
        "sut_codes": ["530010", "530020"],
        "procedures": [
            ("13.001.001", "EEG", "HUV"),
            ("530010", "Elektroensefalografi", "SUT"),
        ],
        "uygun_tanilar": ["G40.0", "G40.1", "G40.9", "R56.0", "G41.0", "R55"],
        "uyumsuz_tanilar": ["M17.1", "K80.2", "H25.0", "E11.9", "I25.1"],
        "gerekce_uygun": "EEG epilepsi / nöbet tanısıyla endikedir.",
        "gerekce_uyumsuz": "EEG için {tani} tanısı nörolojik endikasyon içermemektedir.",
    },
    {
        "alan": "noroloji",
        "huv_codes": ["13.005.001", "13.005.005"],
        "sut_codes": ["530050", "530060"],
        "procedures": [
            ("13.005.001", "EMG", "HUV"),
            ("530050", "Elektromiyografi", "SUT"),
        ],
        "uygun_tanilar": ["G56.0", "G57.0", "G62.9", "M54.1", "G60.0", "G61.0"],
        "uyumsuz_tanilar": ["K35.8", "H33.0", "I21.0", "E04.0", "J45.9"],
        "gerekce_uygun": "EMG karpal tünel sendromu / periferik nöropati tanısıyla uyumludur.",
        "gerekce_uyumsuz": "EMG için {tani} tanısı nörolojik endikasyon oluşturmamaktadır.",
    },
    {
        "alan": "noroloji",
        "huv_codes": ["13.010.001", "13.010.005"],
        "sut_codes": ["530100", "530110"],
        "procedures": [
            ("13.010.001", "Beyin MR", "HUV"),
            ("530100", "Kranial MR görüntüleme", "SUT"),
        ],
        "uygun_tanilar": ["G43.9", "G40.9", "I63.9", "C71.9", "G35", "R51"],
        "uyumsuz_tanilar": ["M17.1", "K80.2", "S72.0", "E11.9", "N40.0"],
        "gerekce_uygun": "Beyin MR migren / epilepsi / serebrovasküler olay tanısıyla endikedir.",
        "gerekce_uyumsuz": "Beyin MR için {tani} tanısı nörolojik endikasyon değildir.",
    },
    # --- RADYOLOJİ ---
    {
        "alan": "radyoloji",
        "huv_codes": ["21.100.001", "21.100.005"],
        "sut_codes": ["510010", "510020"],
        "procedures": [
            ("21.100.001", "Toraks BT", "HUV"),
            ("510010", "Toraks bilgisayarlı tomografi", "SUT"),
        ],
        "uygun_tanilar": ["J18.9", "J44.1", "C34.9", "J84.1", "J93.1", "R91"],
        "uyumsuz_tanilar": ["M17.1", "K80.2", "H25.0", "M54.5", "E11.9"],
        "gerekce_uygun": "Toraks BT pnömoni / KOAH / akciğer kitlesi tanısıyla endikedir.",
        "gerekce_uyumsuz": "Toraks BT için {tani} tanısı pulmoner endikasyon içermemektedir.",
    },
    {
        "alan": "radyoloji",
        "huv_codes": ["21.102.001", "21.102.005"],
        "sut_codes": ["510100", "510110"],
        "procedures": [
            ("21.102.001", "Abdomen BT", "HUV"),
            ("510100", "Batın bilgisayarlı tomografi", "SUT"),
        ],
        "uygun_tanilar": ["K80.2", "K35.8", "C78.7", "K57.3", "R10.4", "K85.0"],
        "uyumsuz_tanilar": ["M75.1", "G43.9", "I10", "J06.9", "H25.0"],
        "gerekce_uygun": "Abdomen BT akut karın / safra taşı / batın kitlesi tanısıyla uyumludur.",
        "gerekce_uyumsuz": "Abdomen BT için {tani} tanısı abdominal endikasyon değildir.",
    },
    {
        "alan": "radyoloji",
        "huv_codes": ["21.103.001", "21.103.005"],
        "sut_codes": ["510200", "510210"],
        "procedures": [
            ("21.103.001", "Lomber MR", "HUV"),
            ("510200", "Lomber vertebra MR", "SUT"),
        ],
        "uygun_tanilar": ["M54.5", "M54.1", "M51.1", "M48.0", "G55.1", "M47.8"],
        "uyumsuz_tanilar": ["I25.1", "H25.0", "E04.0", "K35.8", "J45.9"],
        "gerekce_uygun": "Lomber MR lombalji / disk hernisi / spinal stenoz tanısıyla endikedir.",
        "gerekce_uyumsuz": "Lomber MR için {tani} tanısı uygun endikasyon değildir.",
    },
    # --- ÜROLOJİ ---
    {
        "alan": "uroloji",
        "huv_codes": ["17.001.001", "17.001.005"],
        "sut_codes": ["660010", "660020"],
        "procedures": [
            ("17.001.001", "Sistoskopi", "HUV"),
            ("660010", "Sistoskopi", "SUT"),
        ],
        "uygun_tanilar": ["N30.0", "N30.1", "C67.9", "R31.0", "N32.0", "N35.0"],
        "uyumsuz_tanilar": ["M17.1", "I25.1", "H25.0", "J44.1", "E04.0"],
        "gerekce_uygun": "Sistoskopi hematüri / mesane tümörü / sistit tanısıyla endikedir.",
        "gerekce_uyumsuz": "Sistoskopi için {tani} tanısı ürolojik endikasyon değildir.",
    },
    {
        "alan": "uroloji",
        "huv_codes": ["17.010.001", "17.010.005"],
        "sut_codes": ["660100", "660110"],
        "procedures": [
            ("17.010.001", "TUR-P", "HUV"),
            ("660100", "Transüretral prostat rezeksiyonu", "SUT"),
        ],
        "uygun_tanilar": ["N40.0", "N40.1", "N40.3", "C61"],
        "uyumsuz_tanilar": ["M17.1", "K80.2", "I21.0", "H25.0", "E11.9"],
        "gerekce_uygun": "TUR-P benign prostat hiperplazisi tanısıyla endikedir.",
        "gerekce_uyumsuz": "TUR-P için {tani} tanısı ürolojik endikasyon oluşturmamaktadır.",
    },
    {
        "alan": "uroloji",
        "huv_codes": ["17.020.001", "17.020.005"],
        "sut_codes": ["660200", "660210"],
        "procedures": [
            ("17.020.001", "ESWL", "HUV"),
            ("660200", "Ekstrakorporeal şok dalga litotripsi", "SUT"),
        ],
        "uygun_tanilar": ["N20.0", "N20.1", "N20.2", "N21.0", "N13.2"],
        "uyumsuz_tanilar": ["M54.5", "I10", "E11.9", "J06.9", "G43.9"],
        "gerekce_uygun": "ESWL böbrek / üreter taşı tanısıyla endikedir.",
        "gerekce_uyumsuz": "ESWL için {tani} tanısı uygun değildir.",
    },
    # --- GASTROENTEROLOJİ ---
    {
        "alan": "gastro",
        "huv_codes": ["15.001.001", "15.001.005"],
        "sut_codes": ["610010", "610020"],
        "procedures": [
            ("15.001.001", "Üst GİS endoskopi", "HUV"),
            ("610010", "Özofagogastroduodenoskopi", "SUT"),
        ],
        "uygun_tanilar": ["K21.0", "K25.9", "K26.9", "K29.0", "R10.1", "K22.2"],
        "uyumsuz_tanilar": ["M17.1", "I25.1", "G40.9", "H25.0", "E04.0"],
        "gerekce_uygun": "Üst GİS endoskopi gastrit / peptik ülser / reflü tanısıyla uyumludur.",
        "gerekce_uyumsuz": "Üst GİS endoskopi için {tani} tanısı gastrointestinal endikasyon değildir.",
    },
    {
        "alan": "gastro",
        "huv_codes": ["15.005.001", "15.005.005"],
        "sut_codes": ["610050", "610060"],
        "procedures": [
            ("15.005.001", "Kolonoskopi", "HUV"),
            ("610050", "Kolonoskopi", "SUT"),
        ],
        "uygun_tanilar": ["K51.0", "K50.0", "C18.9", "K57.3", "D12.6", "R19.5"],
        "uyumsuz_tanilar": ["M54.5", "I10", "H33.0", "E05.0", "J45.9"],
        "gerekce_uygun": "Kolonoskopi ülseratif kolit / kolon kitlesi / divertikülit tanısıyla endikedir.",
        "gerekce_uyumsuz": "Kolonoskopi için {tani} tanısı gastrointestinal endikasyon içermemektedir.",
    },
    {
        "alan": "gastro",
        "huv_codes": ["15.010.001", "15.010.005"],
        "sut_codes": ["610100", "610110"],
        "procedures": [
            ("15.010.001", "ERCP", "HUV"),
            ("610100", "Endoskopik retrograd kolanjiopankreatografi", "SUT"),
        ],
        "uygun_tanilar": ["K80.5", "K83.1", "K85.1", "K80.3", "K83.0"],
        "uyumsuz_tanilar": ["M17.1", "G43.9", "N40.0", "I50.0", "E11.9"],
        "gerekce_uygun": "ERCP koledok taşı / biliyer obstrüksiyon tanısıyla endikedir.",
        "gerekce_uyumsuz": "ERCP için {tani} tanısı biliyer/pankreatik endikasyon değildir.",
    },
    # --- KULAK BURUN BOĞAZ ---
    {
        "alan": "kbb",
        "huv_codes": ["11.001.001", "11.001.005"],
        "sut_codes": ["550010", "550020"],
        "procedures": [
            ("11.001.001", "Tonsillektomi", "HUV"),
            ("550010", "Tonsillektomi ve adenoidektomi", "SUT"),
        ],
        "uygun_tanilar": ["J35.0", "J35.1", "J35.3", "J36", "J03.9"],
        "uyumsuz_tanilar": ["M17.1", "I25.1", "E11.9", "K80.2", "H25.0"],
        "gerekce_uygun": "Tonsillektomi kronik tonsillit / adenoid hipertrofisi tanısıyla endikedir.",
        "gerekce_uyumsuz": "Tonsillektomi için {tani} tanısı KBB endikasyonu değildir.",
    },
    {
        "alan": "kbb",
        "huv_codes": ["11.005.001", "11.005.005"],
        "sut_codes": ["550050", "550060"],
        "procedures": [
            ("11.005.001", "Septoplasti", "HUV"),
            ("550050", "Septum deviasyonu ameliyatı", "SUT"),
        ],
        "uygun_tanilar": ["J34.2", "J34.3", "J32.0", "J32.4", "J01.0"],
        "uyumsuz_tanilar": ["I25.1", "M16.0", "E04.0", "K80.2", "N40.0"],
        "gerekce_uygun": "Septoplasti nazal septum deviasyonu / kronik sinüzit tanısıyla endikedir.",
        "gerekce_uyumsuz": "Septoplasti için {tani} tanısı KBB endikasyonu oluşturmamaktadır.",
    },
    {
        "alan": "kbb",
        "huv_codes": ["11.010.001", "11.010.005"],
        "sut_codes": ["550100", "550110"],
        "procedures": [
            ("11.010.001", "Timpanoplasti", "HUV"),
            ("550100", "Timpanoplasti", "SUT"),
        ],
        "uygun_tanilar": ["H72.0", "H72.9", "H65.2", "H66.1", "H66.3"],
        "uyumsuz_tanilar": ["M54.5", "I10", "E11.9", "J45.9", "K35.8"],
        "gerekce_uygun": "Timpanoplasti timpanik membran perforasyonu / kronik otit tanısıyla endikedir.",
        "gerekce_uyumsuz": "Timpanoplasti için {tani} tanısı otolojik endikasyon değildir.",
    },
    # --- GÖĞÜS CERRAHİSİ ---
    {
        "alan": "gogus_cerrahisi",
        "huv_codes": ["10.001.001", "10.001.005"],
        "sut_codes": ["570010", "570020"],
        "procedures": [
            ("10.001.001", "Lobektomi", "HUV"),
            ("570010", "Akciğer lobektomi", "SUT"),
        ],
        "uygun_tanilar": ["C34.1", "C34.9", "J47.9", "J85.2", "C34.3"],
        "uyumsuz_tanilar": ["M17.1", "K80.2", "H25.0", "E11.9", "I10"],
        "gerekce_uygun": "Lobektomi akciğer karsinomu / bronşektazi tanısıyla endikedir.",
        "gerekce_uyumsuz": "Lobektomi için {tani} tanısı pulmoner cerrahi endikasyon değildir.",
    },
    # --- FİZİK TEDAVİ ---
    {
        "alan": "ftr",
        "huv_codes": ["22.001.001", "22.001.005"],
        "sut_codes": ["730010", "730020"],
        "procedures": [
            ("22.001.001", "Fizik tedavi seansı", "HUV"),
            ("730010", "Fizik tedavi ve rehabilitasyon", "SUT"),
        ],
        "uygun_tanilar": ["M54.5", "M54.1", "M75.1", "M17.1", "M47.8", "M79.1"],
        "uyumsuz_tanilar": ["I21.0", "K35.8", "C34.9", "E04.0", "G40.9"],
        "gerekce_uygun": "Fizik tedavi lombalji / servikal disk hernisi / gonartroz tanısıyla uyumludur.",
        "gerekce_uyumsuz": "Fizik tedavi için {tani} tanısı rehabilitasyon endikasyonu değildir.",
    },
    # --- PLASTİK CERRAHİ ---
    {
        "alan": "plastik",
        "huv_codes": ["19.001.001", "19.001.005"],
        "sut_codes": ["640010", "640020"],
        "procedures": [
            ("19.001.001", "Meme rekonstrüksiyonu", "HUV"),
            ("640010", "Meme rekonstrüksiyonu", "SUT"),
        ],
        "uygun_tanilar": ["C50.9", "C50.4", "Z42.1", "N62", "Q83.0"],
        "uyumsuz_tanilar": ["M17.1", "I25.1", "E11.9", "K80.2", "J44.1"],
        "gerekce_uygun": "Meme rekonstrüksiyonu mastektomi sonrası / meme kanseri tanısıyla endikedir.",
        "gerekce_uyumsuz": "Meme rekonstrüksiyonu için {tani} tanısı plastik cerrahi endikasyonu değildir.",
    },
    # --- BEYİN CERRAHİSİ ---
    {
        "alan": "beyin_cerrahisi",
        "huv_codes": ["14.001.001", "14.001.005"],
        "sut_codes": ["540010", "540020"],
        "procedures": [
            ("14.001.001", "Lomber diskektomi", "HUV"),
            ("540010", "Lomber disk cerrahisi", "SUT"),
        ],
        "uygun_tanilar": ["M51.1", "M51.0", "G55.1", "M54.1", "M48.0"],
        "uyumsuz_tanilar": ["I25.1", "H25.0", "K80.2", "E04.0", "J45.9"],
        "gerekce_uygun": "Lomber diskektomi lomber disk hernisi / spinal stenoz tanısıyla endikedir.",
        "gerekce_uyumsuz": "Lomber diskektomi için {tani} tanısı nöroşirürji endikasyonu değildir.",
    },
    {
        "alan": "beyin_cerrahisi",
        "huv_codes": ["14.010.001", "14.010.005"],
        "sut_codes": ["540100", "540110"],
        "procedures": [
            ("14.010.001", "Kraniotomi", "HUV"),
            ("540100", "Kraniotomi ile tümör eksizyonu", "SUT"),
        ],
        "uygun_tanilar": ["C71.9", "C71.1", "D33.0", "I61.0", "S06.3"],
        "uyumsuz_tanilar": ["M17.1", "K80.2", "E11.9", "J06.9", "N39.0"],
        "gerekce_uygun": "Kraniotomi beyin tümörü / intrakranial hemoraji tanısıyla endikedir.",
        "gerekce_uyumsuz": "Kraniotomi için {tani} tanısı nöroşirürji endikasyonu değildir.",
    },
    # --- KADIN HASTALIKLARI ---
    {
        "alan": "kadin_dogum",
        "huv_codes": ["18.001.001", "18.001.005"],
        "sut_codes": ["650010", "650020"],
        "procedures": [
            ("18.001.001", "Sezaryen", "HUV"),
            ("650010", "Sezaryen doğum", "SUT"),
        ],
        "uygun_tanilar": ["O82.0", "O82.1", "O60.1", "O64.1", "O68.0", "O34.2"],
        "uyumsuz_tanilar": ["M17.1", "I25.1", "E04.0", "K80.2", "G40.9"],
        "gerekce_uygun": "Sezaryen doğum komplikasyonu / fetal distres / malprezantasyon tanısıyla endikedir.",
        "gerekce_uyumsuz": "Sezaryen için {tani} tanısı obstetrik endikasyon değildir.",
    },
    {
        "alan": "kadin_dogum",
        "huv_codes": ["18.010.001", "18.010.005"],
        "sut_codes": ["650100", "650110"],
        "procedures": [
            ("18.010.001", "Histerektomi", "HUV"),
            ("650100", "Total abdominal histerektomi", "SUT"),
        ],
        "uygun_tanilar": ["D25.9", "D25.0", "N80.0", "C54.1", "N85.0", "N92.0"],
        "uyumsuz_tanilar": ["M54.5", "I10", "E11.9", "J44.1", "N40.0"],
        "gerekce_uygun": "Histerektomi uterin myom / endometriyozis / endometrium kanseri tanısıyla endikedir.",
        "gerekce_uyumsuz": "Histerektomi için {tani} tanısı jinekolojik endikasyon değildir.",
    },
    # --- ONKOLOJİ ---
    {
        "alan": "onkoloji",
        "huv_codes": ["20.001.001", "20.001.005"],
        "sut_codes": ["750010", "750020"],
        "procedures": [
            ("20.001.001", "Kemoterapi", "HUV"),
            ("750010", "Antineoplastik kemoterapi", "SUT"),
        ],
        "uygun_tanilar": ["C50.9", "C34.9", "C18.9", "C61", "C56", "C20"],
        "uyumsuz_tanilar": ["M17.1", "I10", "E11.9", "J06.9", "K21.0"],
        "gerekce_uygun": "Kemoterapi malign neoplazm tanısıyla endikedir.",
        "gerekce_uyumsuz": "Kemoterapi için {tani} tanısı onkolojik endikasyon değildir.",
    },
    {
        "alan": "onkoloji",
        "huv_codes": ["20.010.001", "20.010.005"],
        "sut_codes": ["750100", "750110"],
        "procedures": [
            ("20.010.001", "Radyoterapi", "HUV"),
            ("750100", "Radyoterapi seansı", "SUT"),
        ],
        "uygun_tanilar": ["C50.9", "C34.9", "C71.9", "C61", "C53.9", "C20"],
        "uyumsuz_tanilar": ["M54.5", "K80.2", "E04.0", "J45.9", "N39.0"],
        "gerekce_uygun": "Radyoterapi kanser tanısıyla endikedir.",
        "gerekce_uyumsuz": "Radyoterapi için {tani} tanısı onkolojik endikasyon oluşturmamaktadır.",
    },
    # --- DERMATOLOJİ ---
    {
        "alan": "dermatoloji",
        "huv_codes": ["08.001.001", "08.001.005"],
        "sut_codes": ["590010", "590020"],
        "procedures": [
            ("08.001.001", "Deri biyopsisi", "HUV"),
            ("590010", "Deri biyopsisi", "SUT"),
        ],
        "uygun_tanilar": ["L40.0", "L82.1", "D22.9", "C44.9", "L30.9", "L93.0"],
        "uyumsuz_tanilar": ["M17.1", "I25.1", "K80.2", "E04.0", "J44.1"],
        "gerekce_uygun": "Deri biyopsisi psoriasis / nevüs / cilt lezyonu tanısıyla uyumludur.",
        "gerekce_uyumsuz": "Deri biyopsisi için {tani} tanısı dermatolojik endikasyon değildir.",
    },
]

GUVEN_LEVELS = ["high", "medium", "low"]

# Karar dağılımı ağırlıkları (gerçekçi dağılım)
KARAR_WEIGHTS = {
    KararDurumu.UYGUN: 45,
    KararDurumu.TANI_UYUMSUZ: 12,
    KararDurumu.TANI_EKSIK: 8,
    KararDurumu.MANUEL_INCELEME: 20,
    KararDurumu.EVRAK_EKSIK: 5,
    KararDurumu.KLINIK_UYUMSUZLUK: 5,
    KararDurumu.BELGE_KANITI_YETERSIZ: 3,
    KararDurumu.BELGE_ANALIZI_TAMAMLANAMADI: 2,
}


def _generate_tc() -> str:
    """Geçerli formatta rastgele TC kimlik numarası üretir (doğrulama algoritmasız)."""
    digits = [random.randint(1, 9)] + [random.randint(0, 9) for _ in range(9)]
    digits.append(random.randint(0, 9))
    return "".join(str(d) for d in digits)


def _generate_patient() -> dict:
    cinsiyet = random.choice(["erkek", "kadin"])
    if cinsiyet == "erkek":
        ad = random.choice(TURKISH_FIRST_NAMES_M)
    else:
        ad = random.choice(TURKISH_FIRST_NAMES_F)
    soyad = random.choice(TURKISH_LAST_NAMES)

    yas = random.choices(
        [random.randint(0, 17), random.randint(18, 64), random.randint(65, 95)],
        weights=[10, 60, 30],
    )[0]

    return {
        "tc_kimlik": _generate_tc(),
        "hasta_id": f"H{random.randint(100000, 999999)}",
        "patient_name": f"{ad} {soyad}",
        "yas": yas,
        "cinsiyet": cinsiyet,
    }


def _weighted_karar() -> KararDurumu:
    kararlar = list(KARAR_WEIGHTS.keys())
    weights = list(KARAR_WEIGHTS.values())
    return random.choices(kararlar, weights=weights, k=1)[0]


def _random_timestamp(days_back: int = 365) -> str:
    """Son N gün içinde rastgele bir tarih üretir."""
    base = datetime.now(timezone.utc)
    delta = timedelta(
        days=random.randint(0, days_back),
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
    )
    return (base - delta).isoformat()


def generate_provizyon_record(
    provizyon_id: str,
    patient: dict,
    scenario: dict,
    karar: KararDurumu,
    *,
    use_huv: bool = True,
) -> JobResult:
    """Tek bir provizyon kaydı üretir."""

    is_uygun = karar == KararDurumu.UYGUN
    is_tani_fail = karar in (KararDurumu.TANI_UYUMSUZ, KararDurumu.TANI_EKSIK)
    is_evrak = karar == KararDurumu.EVRAK_EKSIK
    is_klinik = karar == KararDurumu.KLINIK_UYUMSUZLUK

    if is_uygun or karar == KararDurumu.MANUEL_INCELEME:
        tani = random.choice(scenario["uygun_tanilar"])
    elif is_tani_fail:
        tani = random.choice(scenario["uyumsuz_tanilar"])
    else:
        tani = random.choice(
            scenario["uygun_tanilar"] + scenario["uyumsuz_tanilar"]
        )

    procedure = random.choice(scenario["procedures"])
    proc_code, proc_name, proc_type = procedure

    if use_huv:
        tani_layer_name = "tani_kurali"
        tani_prefix = "İşlem-tanı uyumu"
    else:
        tani_layer_name = "sut_tani_kurali"
        tani_prefix = "SUT işlem-tanı uyumu"

    # -- tani_kurali / sut_tani_kurali layer --
    if is_tani_fail:
        if karar == KararDurumu.TANI_EKSIK:
            tani_status = LayerStatus.FAIL
            tani_msg = f"{tani_prefix}: {proc_code} ({proc_name}) için tanı kodu eksik. Gerekli tanı grubu sağlanmamış."
        else:
            tani_status = LayerStatus.FAIL
            tani_msg = scenario["gerekce_uyumsuz"].format(tani=tani)
    elif is_uygun:
        tani_status = LayerStatus.PASS
        tani_msg = scenario["gerekce_uygun"]
    elif karar == KararDurumu.MANUEL_INCELEME:
        tani_status = random.choice([LayerStatus.REVIEW, LayerStatus.PASS])
        if tani_status == LayerStatus.REVIEW:
            tani_msg = f"{tani_prefix}: {proc_code} ({proc_name}) + {tani} tanı kombinasyonu manuel inceleme gerektiriyor."
        else:
            tani_msg = scenario["gerekce_uygun"]
    else:
        tani_status = random.choice([LayerStatus.PASS, LayerStatus.REVIEW, LayerStatus.INSUFFICIENT])
        tani_msg = f"{tani_prefix}: {proc_code} ({proc_name}) değerlendirmesi: {tani_status.value}."

    tani_layer = LayerResult(
        layer=tani_layer_name,
        status=tani_status,
        message=tani_msg,
        detail={
            "overall_status": "allowed" if tani_status == LayerStatus.PASS else (
                "not_payable_by_diagnosis" if tani_status == LayerStatus.FAIL else "review_required"
            ),
            "procedure_code": proc_code,
            "procedure_name": proc_name,
            "diagnosis_code": tani,
        },
    )

    # -- sut_kurali layer --
    if is_uygun:
        sut_status = LayerStatus.PASS
        sut_msg = f"SUT kuralı: {proc_code} ({proc_name}) işlemi SUT kurallarına uygun."
    elif is_klinik:
        sut_status = LayerStatus.FAIL
        sut_msg = f"SUT kuralı: {proc_code} işlemi klinik koşulları karşılamıyor."
    elif karar == KararDurumu.MANUEL_INCELEME:
        sut_status = LayerStatus.REVIEW
        sut_msg = f"SUT kuralı: {proc_code} ({proc_name}) işlemi inceleme gerektiriyor."
    else:
        sut_status = random.choice([LayerStatus.PASS, LayerStatus.REVIEW])
        sut_msg = f"SUT kuralı: {proc_code} ({proc_name}) durumu: {sut_status.value}."

    sut_layer = LayerResult(
        layer="sut_kurali",
        status=sut_status,
        message=sut_msg,
        detail={"procedure_code": proc_code, "procedure_name": proc_name},
    )

    # -- MedGemma layer --
    guven = random.choice(GUVEN_LEVELS)
    if is_uygun:
        medgemma = MedGemmaClinicalOutput(
            islem_belge_destekli=True,
            tani_belge_destekli=True,
            yas_cinsiyet_uygun=True,
            klinik_celiski=False,
            eksik_evrak=False,
            manuel_inceleme_gerekli=False,
            gerekce=(
                f"{proc_name} ({proc_code}) işlemi {tani} tanısı için klinik olarak uygundur. "
                f"Hasta {patient['yas']} yaşında, {patient['cinsiyet']}, endikasyon mevcuttur."
            ),
            guven=guven,
        )
    elif is_klinik:
        medgemma = MedGemmaClinicalOutput(
            islem_belge_destekli=False,
            tani_belge_destekli=random.choice([True, False]),
            yas_cinsiyet_uygun=random.choice([True, False]),
            klinik_celiski=True,
            eksik_evrak=False,
            manuel_inceleme_gerekli=True,
            gerekce=(
                f"{proc_name} ({proc_code}) işlemi için belgeler klinik uyumsuzluk göstermektedir. "
                f"Tanı {tani} ile işlem arasında klinik tutarsızlık mevcuttur."
            ),
            guven="low",
        )
    elif is_evrak:
        medgemma = MedGemmaClinicalOutput(
            islem_belge_destekli=False,
            tani_belge_destekli=False,
            yas_cinsiyet_uygun=True,
            klinik_celiski=False,
            eksik_evrak=True,
            manuel_inceleme_gerekli=True,
            gerekce=f"{proc_name} işlemi için gerekli destekleyici evrak eksiktir.",
            guven="low",
        )
    else:
        medgemma = MedGemmaClinicalOutput(
            islem_belge_destekli=random.choice([True, False]),
            tani_belge_destekli=random.choice([True, False]),
            yas_cinsiyet_uygun=True,
            klinik_celiski=random.choice([True, False]),
            eksik_evrak=random.choice([True, False]),
            manuel_inceleme_gerekli=karar != KararDurumu.UYGUN,
            gerekce=(
                f"{proc_name} ({proc_code}) ve {tani} tanısı değerlendirildi. "
                f"Karar: {karar.value}."
            ),
            guven=guven,
        )

    # -- gerekçe --
    gerekce_parts = [tani_msg]
    if sut_status == LayerStatus.FAIL:
        gerekce_parts.append(sut_msg)
    gerekce = " | ".join(gerekce_parts)

    finished = _random_timestamp()

    result = JobResult(
        provizyon_id=provizyon_id,
        hasta_id=patient["hasta_id"],
        status=JobStatus.DONE,
        nihai_karar=karar,
        gerekce=gerekce,
        tani_kurali=tani_layer if use_huv else None,
        sut_tani_kurali=tani_layer if not use_huv else None,
        sut_kurali=sut_layer,
        medgemma=medgemma,
        started_at=finished,
        finished_at=finished,
    )
    return result


def run_seed(
    count: int = 2000,
    batch_size: int = 50,
    dry_run: bool = False,
) -> dict:
    """Ana seed fonksiyonu."""

    print(f"\n{'='*60}")
    print(f"  patient_findings Seed — {count} kayıt üretiliyor")
    print(f"{'='*60}\n")

    patients_pool_size = max(count // 5, 50)
    patients = [_generate_patient() for _ in range(patients_pool_size)]
    print(f"  {patients_pool_size} benzersiz hasta profili üretildi.")

    if not dry_run:
        from provizyon_engine.persistence.qdrant_findings import PatientFindingsWriter
        writer = PatientFindingsWriter()
        if not writer.ping():
            print("HATA: Qdrant'a erişilemiyor! Bağlantıyı kontrol edin.")
            return {"error": "qdrant_unreachable"}
        print("  Qdrant bağlantısı başarılı.")

    stats = {
        "total": 0,
        "written": 0,
        "errors": 0,
        "by_karar": {},
        "by_alan": {},
        "huv_count": 0,
        "sut_count": 0,
    }

    t0 = time.time()
    batch_results = []

    for i in range(count):
        patient = random.choice(patients)
        scenario = random.choice(CLINICAL_SCENARIOS)
        karar = _weighted_karar()
        use_huv = random.choice([True, False])

        provizyon_id = f"SEED-{i+1:06d}"
        institution_name, facility_level = random.choice(INSTITUTIONS)

        result = generate_provizyon_record(
            provizyon_id=provizyon_id,
            patient=patient,
            scenario=scenario,
            karar=karar,
            use_huv=use_huv,
        )

        stats["total"] += 1
        stats["by_karar"][karar.value] = stats["by_karar"].get(karar.value, 0) + 1
        stats["by_alan"][scenario["alan"]] = stats["by_alan"].get(scenario["alan"], 0) + 1
        if use_huv:
            stats["huv_count"] += 1
        else:
            stats["sut_count"] += 1

        if dry_run:
            stats["written"] += 1
            continue

        batch_results.append((result, patient, institution_name, facility_level))

        if len(batch_results) >= batch_size or i == count - 1:
            for res, pat, inst, fac in batch_results:
                try:
                    info = writer.write(
                        res,
                        tc_kimlik=pat["tc_kimlik"],
                        institution_name=inst,
                        facility_level=fac,
                        yas=pat["yas"],
                        cinsiyet=pat["cinsiyet"],
                    )
                    stats["written"] += info.get("written", 0)
                    if info.get("errors"):
                        stats["errors"] += len(info["errors"])
                except Exception as exc:
                    stats["errors"] += 1
                    print(f"  HATA [{res.provizyon_id}]: {exc}")

            elapsed = time.time() - t0
            rate = stats["total"] / max(elapsed, 0.001)
            print(
                f"  [{stats['total']:>6d}/{count}] "
                f"yazılan={stats['written']}, hata={stats['errors']} "
                f"({rate:.1f} kayıt/sn)"
            )
            batch_results.clear()

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"  Tamamlandı: {elapsed:.1f}sn")
    print(f"  Toplam kayıt:    {stats['total']}")
    print(f"  Yazılan nokta:   {stats['written']}")
    print(f"  Hatalar:         {stats['errors']}")
    print(f"  HUV kayıtları:   {stats['huv_count']}")
    print(f"  SUT kayıtları:   {stats['sut_count']}")
    print(f"\n  Karar dağılımı:")
    for k, v in sorted(stats["by_karar"].items(), key=lambda x: -x[1]):
        pct = v / stats["total"] * 100
        print(f"    {k:30s} {v:5d} ({pct:.1f}%)")
    print(f"\n  Alan dağılımı:")
    for k, v in sorted(stats["by_alan"].items(), key=lambda x: -x[1]):
        pct = v / stats["total"] * 100
        print(f"    {k:25s} {v:5d} ({pct:.1f}%)")
    print(f"{'='*60}\n")
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="patient_findings collection'ına test verisi yazar."
    )
    parser.add_argument(
        "--count", type=int, default=2000,
        help="Üretilecek provizyon sayısı (varsayılan: 2000)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="Qdrant yazım batch boyutu (varsayılan: 50)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Qdrant'a yazmadan sadece veri üret ve istatistikleri göster.",
    )
    args = parser.parse_args()
    run_seed(count=args.count, batch_size=args.batch_size, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
