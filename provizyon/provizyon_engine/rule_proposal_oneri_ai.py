"""Öneri AI — uzman kural onay / tartışma sohbeti.

Uygulamanın içerik üretirken kullandığı kaynaklardan bağlam toplar:
kural önerisi handoff (deterministik), resmî evidence, motor sinyalleri,
HUV/SUT listeleri, Qdrant tanı kuralları, SUT JSON kuralları, provizyon
değerlendirmeleri ve (mümkünse) MedGemma yorumu.

Yanıt iki katmanlıdır:
1) Deterministik öneriler — motor/handoff/JSON/Qdrant temelli
2) Model yorumu — MedGemma tartışma / sonuç çıkarma (opsiyonel)

Canlı kural yayınlamaz; karar destek amaçlıdır.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import settings
from .rule_proposal_handoff import RuleProposalHandoffError, get_store

# Model yorumu için üst süre (sn). Aşılırsa yalnızca deterministik bölüm döner.
_MODEL_HARD_TIMEOUT_S = 120

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sen Provizyon.AI içindeki «Öneri AI» model yorumcususun.
Uzman kişi kural koymak / onaylamak için seninle tartışır.

Sana verilen BAĞLAM ve DETERMINİSTİK ÖNERİLER zaten iç sistemden derlenmiştir.
Görevin yalnızca **Model yorumu** yazmak:

- Deterministik önerileri tekrar listeleme; üzerine yorum yap, riskleri, boşlukları,
  onay öncesi kontrol listesini ve alternatif okumaları tartış.
- HUV ve SUT listelerini AYRI tut; HUV↔SUT eşleştirmesini varsayma
  (bağlamda «hipotez/crosswalk» yazıyorsa bunu hipotez olarak işle).
- Deterministik öneri ≠ resmi kural; örnek taslak ≠ yayın onayı.
- Yalnızca bağlamdaki bilgilere dayan; uydurma.
- Türkçe yaz; kısa paragraflar veya madde işaretleri kullan.
- Canlı kural yazma yetkin yok; uzman kararını destekle.

Çıktında «Deterministik» başlığı açma — sadece yorumunu yaz.
"""

# HUV/SUT tarzı işlem kodları: 02.02041, P1234, 704.210 vb.
_CODE_RE = re.compile(
    r"\b(\d{2}\.\d{2,5}[A-Z0-9]*|[A-Z]{1,3}\d{2,6}[A-Z0-9./-]{0,8}|\d{4,7}[A-Z]?)\b",
    re.I,
)
_PROPOSAL_ID_RE = re.compile(r"\b(engine_proposal_[a-f0-9]+)\b", re.I)
_PROVIZYON_ID_RE = re.compile(r"\b(\d{6,10})\b")


def _trim(obj: Any, *, max_chars: int = 6000) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "…[kısaltıldı]"


def _add_source(sources: list[str], label: str) -> None:
    if label not in sources:
        sources.append(label)


def _extract_tokens(message: str) -> dict[str, list[str]]:
    codes = list(dict.fromkeys(_CODE_RE.findall(message or "")))
    proposal_ids = list(dict.fromkeys(_PROPOSAL_ID_RE.findall(message or "")))
    provizyon_ids = list(dict.fromkeys(_PROVIZYON_ID_RE.findall(message or "")))
    return {
        "codes": codes[:12],
        "proposal_ids": proposal_ids[:6],
        "provizyon_ids": provizyon_ids[:4],
    }


def _compact_proposal(detail: dict[str, Any]) -> dict[str, Any]:
    p = detail.get("proposal") or {}
    proc = p.get("primaryProcedure") or {}
    evidence = detail.get("officialEvidence") or []
    signals = detail.get("engineSignals") or []
    existing = detail.get("existingRules") or []
    return {
        "proposalId": p.get("proposalId"),
        "listeTipi": proc.get("listeTipi") or detail.get("listeTipi"),
        "procedureKod": proc.get("kod"),
        "procedureAd": proc.get("ad"),
        "targetRuleType": p.get("targetRuleType"),
        "ruleTypeLabel": detail.get("ruleTypeLabel"),
        "priority": p.get("priority"),
        "completeness": p.get("completeness"),
        "qualityFlags": p.get("qualityFlags") or [],
        "proposedFields": p.get("proposedFields") or {},
        "evidenceCount": len(evidence),
        "evidenceQuotes": [
            {
                "id": e.get("evidenceId") or e.get("id"),
                "quote": (e.get("quote") or e.get("text") or "")[:400],
                "source": e.get("source") or e.get("sourceTitle"),
                "fileName": e.get("fileName"),
            }
            for e in evidence[:4]
        ],
        "engineSignals": [
            {
                "id": s.get("signalId") or s.get("id"),
                "engineRuleType": s.get("engineRuleType") or s.get("signalType") or s.get("type"),
                "targetRuleType": s.get("targetRuleType"),
                "confidence": s.get("confidence"),
                "fields": s.get("fields") or {},
                "summary": (s.get("summary") or s.get("message") or "")[:280],
            }
            for s in signals[:6]
        ],
        "existingRules": [
            {
                "id": r.get("contextId") or r.get("ruleId") or r.get("id"),
                "businessFields": r.get("businessFields") or {},
                "summary": (r.get("summary") or r.get("ruleText") or "")[:280],
            }
            for r in existing[:4]
        ],
    }


def _compact_provizyon(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("raw") or {}
    return {
        "provizyon_id": result.get("provizyon_id"),
        "karar": result.get("karar") or result.get("final_decision"),
        "risk_level": result.get("risk_level"),
        "huv_codes": (result.get("meta") or {}).get("huv_codes")
        or raw.get("huv_codes")
        or [],
        "sut_codes": (result.get("meta") or {}).get("sut_codes")
        or raw.get("sut_codes")
        or [],
        "tani_kurali": result.get("tani_kurali"),
        "sut_tani_kurali": result.get("sut_tani_kurali"),
        "sut_kurali": result.get("sut_kurali"),
        "medgemma": result.get("medgemma"),
        "warnings": (result.get("warnings") or [])[:8],
        "risk_reasons": (result.get("risk_reasons") or [])[:6],
    }


def _compact_qdrant_rule(rule: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "code": rule.get("huv_code") or rule.get("sut_code"),
        "procedure_name": rule.get("procedure_name"),
        "diagnosis_policy": rule.get("diagnosis_policy"),
        "required_icd10_patterns": (rule.get("required_icd10_patterns") or [])[:12],
        "excluded_icd10_patterns": (rule.get("excluded_icd10_patterns") or [])[:8],
        "decision_if_missing": rule.get("decision_if_missing"),
        "runtime_decision_mode": rule.get("runtime_decision_mode"),
        "review_required": rule.get("review_required"),
        "confidence": rule.get("confidence"),
        "reason": (rule.get("reason") or "")[:400],
        "special_constraints": rule.get("special_constraints") or {},
        "quality_flags": (rule.get("quality_flags") or [])[:8],
    }


@lru_cache(maxsize=1)
def _sut_rules_index() -> dict[str, list[dict[str, Any]]]:
    """source_code → kompakt SUT kural satırları (JSON)."""
    path = Path(settings.SUT_RULES_PATH)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("SUT rules JSON okunamadı: %s", exc)
        return {}
    index: dict[str, list[dict[str, Any]]] = {}
    for item in data.get("rules") or []:
        code = str(item.get("source_code") or "").strip()
        if not code:
            continue
        row = {
            "rule_id": item.get("rule_id"),
            "source_code": code,
            "source_name": item.get("source_name"),
            "rule_type": item.get("rule_type"),
            "severity": item.get("severity"),
            "period": item.get("period"),
            "limit": item.get("limit"),
            "target_codes": (item.get("target_codes") or [])[:8],
            "condition": (item.get("condition") or "")[:220],
            "source_quote": (item.get("source_quote") or "")[:280],
            "source_list": item.get("source_list"),
            "confidence": item.get("confidence"),
        }
        index.setdefault(code, []).append(row)
    return index


def _lookup_sut_json_rules(codes: list[str], *, limit_per_code: int = 4) -> list[dict[str, Any]]:
    index = _sut_rules_index()
    if not index:
        return []
    out: list[dict[str, Any]] = []
    for code in codes:
        variants = {code, code.lstrip("0") or code}
        # Noktasız / noktalı yakınlık
        if "." in code:
            variants.add(code.replace(".", ""))
        matched: list[dict[str, Any]] = []
        for v in variants:
            matched.extend(index.get(v) or [])
        if not matched:
            continue
        out.append(
            {
                "source_code": code,
                "matchCount": len(matched),
                "rules": matched[:limit_per_code],
            }
        )
    return out


def _fetch_qdrant_rules(codes: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    blocks: list[dict[str, Any]] = []
    sources: list[str] = []
    if not codes:
        return blocks, sources

    # diagnosis_rules paket yolu (lib/) — orchestrator ile aynı bootstrap
    try:
        from . import _sut_bootstrap  # noqa: F401
    except Exception as exc:
        logger.debug("sut bootstrap skipped: %s", exc)

    try:
        from .persistence.diagnosis_rules_qdrant import DiagnosisRulesQdrantReader

        reader = DiagnosisRulesQdrantReader()
        for code in codes[:6]:
            try:
                rule = reader.fetch_rule(code)
            except Exception as exc:
                logger.debug("HUV qdrant fetch %s: %s", code, exc)
                rule = None
            if rule:
                blocks.append(
                    {
                        "type": "qdrant_huv_tani_kurali",
                        "collection": settings.DIAGNOSIS_RULES_COLLECTION,
                        "data": _compact_qdrant_rule(rule, kind="HUV"),
                    }
                )
                _add_source(sources, "Qdrant HUV tanı")
    except Exception as exc:
        logger.debug("HUV Qdrant reader unavailable: %s", exc)

    try:
        from .persistence.sut_diagnosis_rules_qdrant import SutDiagnosisRulesQdrantReader

        reader = SutDiagnosisRulesQdrantReader()
        for code in codes[:6]:
            try:
                rule = reader.fetch_rule(code)
            except Exception as exc:
                logger.debug("SUT qdrant fetch %s: %s", code, exc)
                rule = None
            if rule:
                blocks.append(
                    {
                        "type": "qdrant_sut_tani_kurali",
                        "collection": settings.SUT_DIAGNOSIS_RULES_COLLECTION,
                        "data": _compact_qdrant_rule(rule, kind="SUT"),
                    }
                )
                _add_source(sources, "Qdrant SUT tanı")
    except Exception as exc:
        logger.debug("SUT Qdrant reader unavailable: %s", exc)

    return blocks, sources


def _codes_from_blocks(blocks: list[dict[str, Any]], tokens: dict[str, list[str]]) -> list[str]:
    codes = list(tokens.get("codes") or [])
    for block in blocks:
        if block.get("type") == "kural_onerisi":
            d = block.get("data") or {}
            if d.get("procedureKod"):
                codes.append(str(d["procedureKod"]))
            fields = d.get("proposedFields") or {}
            for key in ("sourceSutCode", "source_sut_code"):
                if fields.get(key):
                    codes.append(str(fields[key]))
            for key in ("targetSutCodes", "target_sut_codes"):
                for c in fields.get(key) or []:
                    codes.append(str(c))
            for sig in d.get("engineSignals") or []:
                sf = sig.get("fields") or {}
                if sf.get("sourceSutCode"):
                    codes.append(str(sf["sourceSutCode"]))
                for c in sf.get("targetSutCodes") or []:
                    codes.append(str(c))
        elif block.get("type") == "kural_onerisi_arama":
            for it in block.get("items") or []:
                if it.get("procedureKod"):
                    codes.append(str(it["procedureKod"]))
        elif block.get("type") == "provizyon_degerlendirme":
            d = block.get("data") or {}
            codes.extend(str(c) for c in (d.get("huv_codes") or [])[:4])
            codes.extend(str(c) for c in (d.get("sut_codes") or [])[:4])
    out: list[str] = []
    seen: set[str] = set()
    for c in codes:
        c = (c or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out[:12]


def _append_proposal_detail(
    store: Any,
    pid: str,
    *,
    blocks: list[dict[str, Any]],
    sources: list[str],
    seen_prop: set[str],
) -> None:
    if not pid or pid in seen_prop:
        return
    try:
        detail = store.get_proposal(pid)
    except Exception:
        detail = None
    if not detail:
        return
    seen_prop.add(pid)
    blocks.append({"type": "kural_onerisi", "data": _compact_proposal(detail)})
    _add_source(sources, "Kural önerileri")
    if detail.get("officialEvidence"):
        _add_source(sources, "Resmî evidence")
    if detail.get("engineSignals"):
        _add_source(sources, "Kural motoru")
    if detail.get("existingRules"):
        _add_source(sources, "Mevcut kural JSON")
    try:
        examples = store.get_example_rules(pid)
    except Exception:
        examples = None
    if examples and examples.get("examples"):
        blocks.append(
            {
                "type": "ornek_kural_taslagi",
                "data": {
                    "proposalId": pid,
                    "disclaimer": examples.get("disclaimer"),
                    "examples": (examples.get("examples") or [])[:3],
                },
            }
        )
        _add_source(sources, "Deterministik taslak")


def gather_context(
    message: str,
    *,
    proposal_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """İç sistem bağlamını topla. (blocks, source_labels)"""

    blocks: list[dict[str, Any]] = []
    sources: list[str] = []
    tokens = _extract_tokens(message)
    store = get_store()
    seen_prop: set[str] = set()

    ids: list[str] = []
    if proposal_id:
        ids.append(proposal_id.strip())
    ids.extend(tokens["proposal_ids"])
    for pid in ids:
        _append_proposal_detail(
            store, pid, blocks=blocks, sources=sources, seen_prop=seen_prop
        )

    # Kod / serbest metin ile öneri arama
    search_terms = tokens["codes"][:4]
    if not search_terms and message.strip() and not seen_prop:
        words = [w for w in re.split(r"\s+", message.strip()) if len(w) >= 3][:3]
        search_terms = words

    for term in search_terms[:4]:
        try:
            listing = store.list_proposals(q=term, page=1, page_size=5)
        except Exception:
            continue
        items = listing.get("items") or []
        if not items:
            continue
        _add_source(sources, "HUV/SUT listeleri")
        _add_source(sources, "Kural önerileri")
        compact_items = []
        for item in items[:5]:
            compact_items.append(
                {
                    "proposalId": item.get("proposalId"),
                    "listeTipi": item.get("listeTipi"),
                    "procedureKod": item.get("procedureKod"),
                    "procedureAd": item.get("procedureAd"),
                    "targetRuleType": item.get("targetRuleType"),
                    "priority": item.get("priority"),
                    "completeness": item.get("completeness"),
                    "evidenceCount": item.get("evidenceCount"),
                    "qualityFlags": item.get("qualityFlags") or [],
                }
            )
        blocks.append(
            {
                "type": "kural_onerisi_arama",
                "query": term,
                "total": listing.get("total"),
                "items": compact_items,
            }
        )
        # İlk eşleşmenin tam detay + deterministik taslağını da yükle
        top_id = (items[0] or {}).get("proposalId")
        if top_id:
            _append_proposal_detail(
                store, top_id, blocks=blocks, sources=sources, seen_prop=seen_prop
            )

    # Handoff AI paketleri / eşleştirme hipotezleri (crosswalk UI kapalı olsa da veri varsa)
    for term in (tokens["codes"][:3] or search_terms[:2]):
        try:
            ai_listing = store.list_ai(q=term, page=1, page_size=3)
        except Exception:
            ai_listing = None
        if ai_listing and ai_listing.get("items"):
            blocks.append(
                {
                    "type": "ai_paket_ozet",
                    "query": term,
                    "note": "Handoff AI paket özeti — hipotez; canlı crosswalk kapalı olabilir.",
                    "items": ai_listing["items"][:3],
                }
            )
            _add_source(sources, "AI paket JSON")
        try:
            cw = store.list_crosswalks(q=term, page=1, page_size=3)
        except Exception:
            cw = None
        if cw and cw.get("items"):
            blocks.append(
                {
                    "type": "eslesme_hipotezi",
                    "query": term,
                    "note": "HUV↔SUT eşleştirme hipotezi — varsayılan runtime'da kullanılmaz; ayrı değerlendirin.",
                    "items": [
                        {
                            "crosswalkId": c.get("crosswalkId"),
                            "huvCode": c.get("huvCode"),
                            "huvName": c.get("huvName"),
                            "sutCode": c.get("sutCode"),
                            "sutName": c.get("sutName"),
                            "reviewRecommended": c.get("reviewRecommended"),
                        }
                        for c in (cw.get("items") or [])[:3]
                    ],
                }
            )
            _add_source(sources, "Eşleştirme listesi (hipotez)")

    # Provizyon değerlendirmeleri (Redis sonuç)
    for pvid in tokens["provizyon_ids"]:
        try:
            from .queue.redis_queue import RedisQueue

            result = RedisQueue().get_result(pvid)
        except Exception as exc:
            logger.debug("provizyon lookup failed %s: %s", pvid, exc)
            result = None
        if result:
            blocks.append({"type": "provizyon_degerlendirme", "data": _compact_provizyon(result)})
            _add_source(sources, "Provizyon değerlendirme")
            if result.get("medgemma"):
                _add_source(sources, "MedGemma (provizyon)")
            if result.get("tani_kurali") or result.get("sut_tani_kurali") or result.get("sut_kurali"):
                _add_source(sources, "Kural motoru")

    # Kodlara göre Qdrant + SUT JSON
    codes = _codes_from_blocks(blocks, tokens)
    q_blocks, q_sources = _fetch_qdrant_rules(codes)
    blocks.extend(q_blocks)
    for s in q_sources:
        _add_source(sources, s)

    sut_json = _lookup_sut_json_rules(codes)
    if sut_json:
        blocks.append({"type": "sut_json_kurallar", "data": sut_json})
        _add_source(sources, "SUT kural JSON")

    if not blocks:
        try:
            summary = store.get_summary()
            blocks.append(
                {
                    "type": "snapshot_ozet",
                    "data": {
                        "counts": summary.get("counts"),
                        "limitations": summary.get("limitations"),
                        "snapshotCreatedAt": summary.get("snapshotCreatedAt"),
                        "note": (
                            "Belirli bir proposalId / işlem kodu / provizyon no "
                            "verilmedi; genel snapshot özeti."
                        ),
                    },
                }
            )
            _add_source(sources, "Snapshot özeti")
        except RuleProposalHandoffError:
            pass

    return blocks, sources


def build_deterministic_section(blocks: list[dict[str, Any]]) -> str:
    """Motor / JSON / Qdrant temelli deterministik öneri metni (model yok)."""

    if not blocks:
        return (
            "Bağlamda eşleşen kural önerisi, Qdrant kuralı veya provizyon bulunamadı. "
            "Proposal ID, HUV/SUT işlem kodu veya provizyon numarası yazarak tekrar deneyin."
        )

    lines: list[str] = [
        "Aşağıdakiler iç sistemin deterministik çıktılarıdır "
        "(motor + handoff + JSON/Qdrant). Yayın onayı değildir.",
        "",
    ]

    for block in blocks[:12]:
        btype = block.get("type")
        if btype == "kural_onerisi":
            d = block.get("data") or {}
            lines.append(
                f"**Aday** `{d.get('proposalId')}` — "
                f"{d.get('listeTipi') or '?'} `{d.get('procedureKod') or ''}` "
                f"{d.get('procedureAd') or ''}"
            )
            lines.append(
                f"- Tip: {d.get('ruleTypeLabel') or d.get('targetRuleType')} · "
                f"Öncelik: {d.get('priority')} · Completeness: {d.get('completeness')} · "
                f"Evidence: {d.get('evidenceCount')}"
            )
            fields = d.get("proposedFields") or {}
            if fields:
                compact = {
                    k: v
                    for k, v in list(fields.items())[:8]
                    if v not in (None, "", [], {})
                }
                if compact:
                    lines.append(f"- Önerilen alanlar: `{json.dumps(compact, ensure_ascii=False)}`")
            flags = d.get("qualityFlags") or []
            if flags:
                lines.append(f"- Kalite bayrakları: {', '.join(map(str, flags[:6]))}")
            for sig in (d.get("engineSignals") or [])[:3]:
                lines.append(
                    f"- Motor sinyali: {sig.get('engineRuleType') or '—'} "
                    f"(confidence {sig.get('confidence', '—')})"
                )
            for q in (d.get("evidenceQuotes") or [])[:2]:
                if q.get("quote"):
                    lines.append(f"- Evidence: «{(q.get('quote') or '')[:220]}»")
            lines.append("")
        elif btype == "ornek_kural_taslagi":
            d = block.get("data") or {}
            lines.append(f"**Deterministik taslak** (`{d.get('proposalId')}`):")
            for ex in d.get("examples") or []:
                if isinstance(ex, dict):
                    title = ex.get("title") or ex.get("ruleType") or "taslak"
                    body = ex.get("text") or ex.get("draft") or ex.get("body") or ""
                    lines.append(f"- {title}: {body}")
                else:
                    lines.append(f"- {ex}")
            if d.get("disclaimer"):
                lines.append(f"- Not: {d.get('disclaimer')}")
            lines.append("")
        elif btype == "kural_onerisi_arama":
            lines.append(
                f"**Arama** «{block.get('query')}»: {block.get('total', 0)} aday "
                f"(ilk {len(block.get('items') or [])}):"
            )
            for it in (block.get("items") or [])[:4]:
                lines.append(
                    f"- `{it.get('proposalId')}` · {it.get('listeTipi')} "
                    f"{it.get('procedureKod')} — {it.get('procedureAd')} "
                    f"(öncelik {it.get('priority')}, evidence {it.get('evidenceCount')})"
                )
            lines.append("")
        elif btype == "qdrant_huv_tani_kurali":
            d = block.get("data") or {}
            lines.append(
                f"**Qdrant HUV tanı** `{d.get('code')}` — {d.get('procedure_name') or ''}"
            )
            lines.append(
                f"- Policy: {d.get('diagnosis_policy')} · mode: {d.get('runtime_decision_mode')} · "
                f"review: {d.get('review_required')}"
            )
            req = d.get("required_icd10_patterns") or []
            if req:
                lines.append(f"- Gerekli ICD: {', '.join(map(str, req[:8]))}")
            if d.get("reason"):
                lines.append(f"- Gerekçe: {d.get('reason')}")
            lines.append("")
        elif btype == "qdrant_sut_tani_kurali":
            d = block.get("data") or {}
            lines.append(
                f"**Qdrant SUT tanı** `{d.get('code')}` — {d.get('procedure_name') or ''}"
            )
            lines.append(
                f"- Policy: {d.get('diagnosis_policy')} · mode: {d.get('runtime_decision_mode')}"
            )
            req = d.get("required_icd10_patterns") or []
            if req:
                lines.append(f"- Gerekli ICD: {', '.join(map(str, req[:8]))}")
            lines.append("")
        elif btype == "sut_json_kurallar":
            for group in (block.get("data") or [])[:4]:
                lines.append(
                    f"**SUT JSON kurallar** `{group.get('source_code')}` "
                    f"({group.get('matchCount')} kayıt, ilk {len(group.get('rules') or [])}):"
                )
                for r in group.get("rules") or []:
                    lines.append(
                        f"- {r.get('rule_type')} / {r.get('severity')}: "
                        f"limit={r.get('limit')} period={r.get('period')} — "
                        f"{(r.get('source_quote') or r.get('condition') or '')[:180]}"
                    )
                lines.append("")
        elif btype == "provizyon_degerlendirme":
            d = block.get("data") or {}
            lines.append(
                f"**Provizyon** `{d.get('provizyon_id')}` → karar: {d.get('karar') or '—'} "
                f"(risk: {d.get('risk_level') or '—'})"
            )
            for key, label in (
                ("tani_kurali", "HUV tanı katmanı"),
                ("sut_tani_kurali", "SUT tanı katmanı"),
                ("sut_kurali", "SUT işlem katmanı"),
            ):
                layer = d.get(key)
                if isinstance(layer, dict):
                    lines.append(
                        f"- {label}: status={layer.get('status')} "
                        f"detail={(str(layer.get('summary') or layer.get('message') or '')[:160])}"
                    )
            lines.append("")
        elif btype == "eslesme_hipotezi":
            lines.append(
                f"**Eşleştirme hipotezi** («{block.get('query')}») — runtime varsayılanı kapalı:"
            )
            for it in block.get("items") or []:
                lines.append(
                    f"- HUV {it.get('huvCode')} ↔ SUT {it.get('sutCode')} "
                    f"(review={it.get('reviewRecommended')})"
                )
            lines.append("")
        elif btype == "ai_paket_ozet":
            lines.append(f"**AI paket JSON** («{block.get('query')}»):")
            for it in block.get("items") or []:
                lines.append(
                    f"- {it.get('packetId')}: status={it.get('status')} "
                    f"outcome={it.get('outcome')} stage={it.get('stage')}"
                )
            lines.append("")
        elif btype == "snapshot_ozet":
            c = (block.get("data") or {}).get("counts") or {}
            lines.append(
                f"**Snapshot:** {c.get('deterministicProposals', '?')} deterministik öneri, "
                f"{c.get('officialEvidence', '?')} evidence, "
                f"{c.get('engineSignals', '?')} motor sinyali."
            )
            lines.append("")

    return "\n".join(lines).strip()


def _medgemma_reachable(timeout_s: float = 2.5) -> bool:
    try:
        import httpx

        base = settings.MEDGEMMA_BASE_URL.rstrip("/")
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        with httpx.Client(timeout=timeout_s) as client:
            r = client.get(url, headers={"Authorization": f"Bearer {settings.MEDGEMMA_API_KEY}"})
            return r.status_code < 500
    except Exception:
        return False


def _call_medgemma_commentary(
    message: str,
    history: list[dict[str, str]],
    *,
    context_text: str,
    deterministic_text: str,
) -> str | None:
    if not _medgemma_reachable():
        logger.info("Öneri AI: MedGemma erişilemiyor, yalnızca deterministik bölüm")
        return None

    try:
        from .medgemma.client import MedGemmaConfig, MedGemmaVisionClient
    except Exception as exc:
        logger.info("MedGemma import failed: %s", exc)
        return None

    cfg = MedGemmaConfig(
        base_url=settings.MEDGEMMA_BASE_URL,
        api_key=settings.MEDGEMMA_API_KEY,
        model=settings.MEDGEMMA_MODEL,
        timeout=min(settings.MEDGEMMA_TIMEOUT, _MODEL_HARD_TIMEOUT_S),
        temperature=0.2,
        max_tokens=min(settings.MEDGEMMA_MAX_TOKENS, 1200),
        vision_mode="off",
    )

    hist_lines = []
    for turn in (history or [])[-8:]:
        role = turn.get("role") or "user"
        content = (turn.get("content") or "").strip()
        if content:
            hist_lines.append(f"{role.upper()}: {content[:1200]}")

    user_payload = (
        "--- BAĞLAM (iç sistem JSON özeti; uydurma) ---\n"
        f"{context_text}\n\n"
        "--- DETERMINİSTİK ÖNERİLER (uzmana zaten gösterilecek; tekrarlama) ---\n"
        f"{deterministic_text[:6000]}\n\n"
        "--- SOHBET GEÇMİŞİ ---\n"
        f"{chr(10).join(hist_lines) or '(yok)'}\n\n"
        "--- UZMAN SORUSU ---\n"
        f"{message.strip()}\n\n"
        "Yalnızca model yorumunu yaz: tartış, riskleri ve onay öncesi kontrol noktalarını belirt."
    )

    def _run() -> str:
        client = MedGemmaVisionClient(cfg)
        return client.chat(SYSTEM_PROMPT, user_payload, json_mode=False)

    # wait=False: zaman aşımında asılı model thread'i executor __exit__'te
    # tüm isteği tekrar bloke etmesin.
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(_run)
        return fut.result(timeout=_MODEL_HARD_TIMEOUT_S)
    except FuturesTimeout:
        logger.warning("Öneri AI MedGemma zaman aşımı (%ss)", _MODEL_HARD_TIMEOUT_S)
        return None
    except Exception as exc:
        logger.warning("Öneri AI MedGemma çağrısı başarısız: %s", exc)
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _compose_reply(deterministic: str, commentary: str | None) -> str:
    parts = ["## Deterministik öneriler", "", deterministic.strip()]
    if commentary and commentary.strip():
        parts.extend(["", "## Model yorumu", "", commentary.strip()])
    else:
        parts.extend(
            [
                "",
                "## Model yorumu",
                "",
                "MedGemma şu an yorum üretemedi. Yukarıdaki deterministik özetle devam edebilirsiniz; "
                "daha derin tartışma için proposal ID veya işlem kodunu netleştirin.",
            ]
        )
    return "\n".join(parts)


def chat(
    message: str,
    *,
    proposal_id: str | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Uzman sohbet turu — deterministik katman + opsiyonel MedGemma yorumu."""

    msg = (message or "").strip()
    if not msg:
        raise ValueError("message_required")
    if len(msg) > 4000:
        raise ValueError("message_too_long")

    blocks, sources = gather_context(msg, proposal_id=proposal_id)
    deterministic = build_deterministic_section(blocks)
    context_text = _trim(blocks, max_chars=12000)

    commentary = _call_medgemma_commentary(
        msg,
        history or [],
        context_text=context_text,
        deterministic_text=deterministic,
    )
    used_model = bool(commentary and commentary.strip())
    if used_model:
        _add_source(sources, "MedGemma")

    reply = _compose_reply(deterministic, commentary if used_model else None)

    return {
        "reply": reply,
        "sections": {
            "deterministic": deterministic,
            "model": commentary.strip() if used_model else None,
        },
        "sources": sources,
        "usedModel": used_model,
        "contextBlocks": len(blocks),
        "proposalId": proposal_id or None,
        "disclaimer": (
            "Öneri AI karar destek amaçlıdır; canlı kural yayınlamaz. "
            "Deterministik öneri ≠ resmi kural. HUV ve SUT ayrı değerlendirilir."
        ),
    }
