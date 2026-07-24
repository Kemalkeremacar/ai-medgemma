from __future__ import annotations

import re
import unicodedata


STOPWORDS = {
    "ve",
    "veya",
    "ile",
    "icin",
    "için",
    "gibi",
    "dahil",
    "genel",
    "tip",
    "tek",
    "iki",
    "bir",
    "adet",
    "olan",
    "olarak",
}


SUT_CODE_RE = re.compile(r"\b(?:P?\d{5,6}|[A-Z]\d{5,6})\b", re.IGNORECASE)
HUV_CODE_RE = re.compile(r"\b\d{2}\.\d{5}\b")
ACRONYM_RE = re.compile(r"\b[A-ZÇĞİÖŞÜ]{2,8}\b")

TOKEN_ALIASES = {
    "anestezisi": {"anestezi"},
    "anestezi": {"anestezisi"},
    "cilt": {"deri"},
    "deri": {"cilt", "yumusak", "doku"},
    "yara": {"laserasyon", "kesi", "onarim"},
    "yumusak": {"doku", "deri", "cilt"},
    "doku": {"yumusak", "deri", "cilt"},
    "angiotensin": {"anjiotensin"},
    "anjiotensin": {"angiotensin"},
    "converting": {"donusturucu", "donusturen"},
    "donusturucu": {"converting"},
    "donusturen": {"converting"},
    "enzyme": {"enzim"},
    "enzim": {"enzyme"},
    "intramuscular": {"intramuskuler"},
    "intramuskuler": {"intramuscular"},
    "intravenous": {"intravenoz"},
    "intravenoz": {"intravenous"},
    "laserasyonu": {"laserasyon"},
    "laserasyon": {"laserasyonu"},
    "lazerasyonu": {"laserasyon", "laserasyonu"},
    "lazerasyon": {"laserasyon", "laserasyonu"},
    "onarimi": {"onarim"},
    "onarim": {"onarimi", "sutur", "dikis"},
    "sutur": {"onarim", "dikis"},
    "dikis": {"onarim", "sutur"},
    "arteryel": {"arteriyel", "intraarteriyel"},
    "arteriyel": {"arteryel", "intraarteriyel"},
    "ponksiyon": {"kanulasyon", "kateterizasyon"},
    "kanulasyon": {"ponksiyon", "kateterizasyon"},
    "kateterizasyon": {"kanulasyon", "ponksiyon"},
}


def fold(text: str | None) -> str:
    value = (text or "").casefold().replace("ı", "i").replace("İ", "i")
    value = "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value).split())


def tokens(text: str | None) -> set[str]:
    return {
        token
        for token in fold(text).split()
        if len(token) >= 3 and token not in STOPWORDS
    }

def expanded_tokens(text: str | None) -> set[str]:
    result = tokens(text)
    for token in list(result):
        result.update(TOKEN_ALIASES.get(token, set()))
    return result


def acronyms(text: str | None) -> set[str]:
    return {match.group(0).upper() for match in ACRONYM_RE.finditer(text or "")}


def split_sut_codes(raw: str | None) -> list[str]:
    if not raw:
        return []
    return sorted({match.group(0).upper() for match in SUT_CODE_RE.finditer(raw)})


def extract_huv_codes(text: str | None) -> list[str]:
    if not text:
        return []
    return sorted({match.group(0) for match in HUV_CODE_RE.finditer(text)})


def extract_sut_codes(text: str | None) -> list[str]:
    return split_sut_codes(text)


def clean_family_name(name: str | None) -> str:
    value = re.split(r"[,;(]", name or "", maxsplit=1)[0].strip()
    value = re.sub(r"\b(tek|iki|çift|cift|sağ|sol|sag|küçük|kucuk|büyük|buyuk)\b", "", value, flags=re.IGNORECASE)
    value = " ".join(value.split())
    return value or (name or "").strip() or "belirsiz"


def extract_modifiers(text: str | None) -> list[str]:
    folded = fold(text)
    checks = [
        ("yenidoğan", ("yenidogan", "neonatal")),
        ("çocuk", ("cocuk", "pediatrik")),
        ("evde", ("evde", "ev hizmeti")),
        ("ameliyathanede", ("ameliyathanede", "ameliyathane icinde")),
        ("ameliyathane dışı", ("ameliyathane disi", "ameliyathane dışı")),
        ("tek taraf", ("tek taraf", "unilateral")),
        ("çift taraf", ("cift taraf", "iki taraf", "bilateral")),
        ("kontrastlı", ("kontrastli",)),
        ("kontrastsız", ("kontrastsiz",)),
        ("açık", ("acik",)),
        ("kapalı/laparoskopik", ("kapali", "laparoskopik", "endoskopik")),
        ("revizyon", ("revizyon", "reoperasyon")),
        ("seans", ("seans",)),
        ("günlük", ("gunluk", "gunde")),
        ("yüzeyel", ("yuzeyel", "deri alti")),
        ("derin", ("derin",)),
    ]
    found: list[str] = []
    for label, variants in checks:
        if any(variant in folded for variant in variants):
            found.append(label)
    return found


def score_ratio(a: str | None, b: str | None) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, fold(a), fold(b)).ratio()

