"""Read-only indexes over the frozen handoff snapshot."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from example_rules import build_example_rules  # noqa: E402

HANDOFF_ROOT = _APP_DIR.parent
LABELS = {
    "accepted": "Teknik doğrulamayı geçti",
    "blocked": "Güvenlik kontrolünde engellendi",
    "insufficient_evidence": "Kanıt yetersiz",
    "no_change": "Değişiklik önermiyor",
    "pending": "Uzman kararı bekliyor",
    "proposal": "AI kural hipotezi",
    "not_processed": "Henüz işlenmedi",
}

RULE_TYPE_LABELS = {
    "sure": "Süre / frekans",
    "birlikteOdenmez": "Birlikte ödenmez",
    "yas": "Yaş",
}


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _owner_key(liste: str | None, kod: str | None) -> str | None:
    if not liste or not kod:
        return None
    return f"{liste}::{kod}"


class DataStore:
    def __init__(self, root: Path | None = None, enable_raw: bool = False) -> None:
        self.root = root or HANDOFF_ROOT
        self.enable_raw = enable_raw
        self._load()

    def _load(self) -> None:
        snap = self.root / "data" / "snapshot"
        self.summary = _load_json(snap / "demo-summary.json")
        self.progress = _load_json(snap / "engine-proposals.ai-progress.snapshot.json")
        enriched = _load_json(snap / "engine-proposals.partial-enriched.json")
        ai_doc = _load_json(snap / "engine-proposals.ai-partial-results.json")

        self.proposals: list[dict[str, Any]] = list(enriched.get("proposals") or [])
        self.coverage: list[dict[str, Any]] = list(enriched.get("coverage") or [])
        self.engine_signals: list[dict[str, Any]] = list(enriched.get("engineSignals") or [])
        self.official_evidence: list[dict[str, Any]] = list(enriched.get("officialEvidence") or [])
        self.existing_rules: list[dict[str, Any]] = list(enriched.get("existingRuleContexts") or [])
        self.safety = enriched.get("safety") or {}
        self.generated_at = enriched.get("generatedAt")
        self.schema_version = enriched.get("schemaVersion")

        self.proposal_by_id = {p["proposalId"]: p for p in self.proposals}
        self.evidence_by_id = {e["evidenceId"]: e for e in self.official_evidence}
        self.signal_by_id = {s["signalId"]: s for s in self.engine_signals}
        self.rule_by_id = {r["contextId"]: r for r in self.existing_rules}

        self.coverage_by_owner: dict[str, dict[str, Any]] = {}
        self.crosswalk_by_id: dict[str, dict[str, Any]] = {}
        self.crosswalk_rows: list[dict[str, Any]] = []
        for cov in self.coverage:
            proc = cov.get("procedure") or {}
            owner = _owner_key(proc.get("listeTipi"), proc.get("kod"))
            if owner:
                self.coverage_by_owner[owner] = cov
            for cw in cov.get("crosswalks") or []:
                row = {
                    "crosswalkId": cw.get("crosswalkId"),
                    "ownerId": owner,
                    "huvCode": cw.get("huvCode") or proc.get("kod"),
                    "huvName": proc.get("ad"),
                    "sutCode": cw.get("sutCode"),
                    "sutName": cw.get("sutName"),
                    "relationType": cw.get("relationType"),
                    "confidence": cw.get("confidence"),
                    "confidenceScore": cw.get("confidenceScore"),
                    "reviewRecommended": cw.get("reviewRecommended"),
                    "reason": cw.get("reason"),
                    "hypothesisOnly": cw.get("hypothesisOnly"),
                    "decisionSource": cw.get("decisionSource"),
                    "alternatives": cw.get("alternatives") or [],
                    "aiSyntheses": cov.get("aiSyntheses") or [],
                    "coverageStatus": cov.get("coverageStatus"),
                    "proposalIds": cov.get("proposalIds") or [],
                }
                if cw.get("crosswalkId"):
                    self.crosswalk_by_id[cw["crosswalkId"]] = row
                self.crosswalk_rows.append(row)

        # Crosswalk AI results are kept only for optional raw/debug; not shown in UI.
        self.ai_results: list[dict[str, Any]] = list(ai_doc.get("results") or [])
        self.ai_by_packet = {r["packetId"]: r for r in self.ai_results}
        self.ai_by_owner: dict[str, list[dict[str, Any]]] = {}
        for r in self.ai_results:
            self.ai_by_owner.setdefault(r.get("ownerId") or "", []).append(r)

        self.proposal_list: list[dict[str, Any]] = []
        for p in self.proposals:
            proc = p.get("primaryProcedure") or {}
            owner = _owner_key(proc.get("listeTipi"), proc.get("kod"))
            self.proposal_list.append(
                {
                    "proposalId": p.get("proposalId"),
                    "procedureKod": proc.get("kod"),
                    "procedureAd": proc.get("ad"),
                    "listeTipi": proc.get("listeTipi"),
                    "targetRuleType": p.get("targetRuleType"),
                    "targetRuleTypeLabel": RULE_TYPE_LABELS.get(
                        p.get("targetRuleType") or "", p.get("targetRuleType")
                    ),
                    "priority": p.get("priority"),
                    "completeness": p.get("completeness"),
                    "evidenceCount": len(p.get("officialEvidenceIds") or []),
                    "qualityFlags": p.get("qualityFlags") or [],
                    "expertDecision": p.get("expertDecision"),
                    "humanReviewRequired": p.get("humanReviewRequired"),
                    "ownerId": owner,
                }
            )

        self.quality_flags = sorted(
            {f for p in self.proposals for f in (p.get("qualityFlags") or [])}
        )
        self.rule_types = sorted({p.get("targetRuleType") for p in self.proposals if p.get("targetRuleType")})
        self.priorities = sorted({p.get("priority") for p in self.proposals if p.get("priority")})

        self.raw_by_packet: dict[str, dict[str, Any]] = {}
        raw_path = self.root / "restricted" / "engine-proposals.ai-raw-responses.json"
        if self.enable_raw and raw_path.exists():
            raw_doc = _load_json(raw_path)
            for item in raw_doc.get("responses") or []:
                packet_id = item.get("packetId")
                if packet_id:
                    self.raw_by_packet[packet_id] = item

        # Optional coverage CSV count sanity (not required for API).
        cov_csv = self.root / "data" / "base" / "engine-proposals.coverage.csv"
        self.coverage_csv_rows = 0
        if cov_csv.exists():
            with cov_csv.open("r", encoding="utf-8", newline="") as fh:
                self.coverage_csv_rows = max(sum(1 for _ in csv.reader(fh)) - 1, 0)

    def get_summary(self) -> dict[str, Any]:
        counts = dict(self.summary.get("counts") or {})
        return {
            "counts": counts,
            "limitations": self.summary.get("limitations") or [],
            "labels": dict(self.summary.get("recommendedUiLabels") or LABELS),
            "snapshotCreatedAt": self.summary.get("snapshotCreatedAt"),
            "sourceState": self.summary.get("sourceState"),
            "schemaVersion": self.summary.get("schemaVersion"),
            "generatedAt": self.generated_at,
            "progress": {
                "completedPackets": (self.progress.get("counts") or {}).get("completedPackets"),
                "stage": (self.progress.get("counts") or {}).get("stage"),
                "status": (self.progress.get("counts") or {}).get("status"),
                "sourceState": self.progress.get("sourceState"),
            },
            "filterOptions": {
                "ruleTypes": [
                    {"value": rt, "label": RULE_TYPE_LABELS.get(rt, rt)} for rt in self.rule_types
                ],
                "priorities": self.priorities,
                "qualityFlags": self.quality_flags[:80],
                "completeness": sorted(
                    {p.get("completeness") for p in self.proposals if p.get("completeness")}
                ),
                "listeTipi": sorted(
                    {
                        (p.get("primaryProcedure") or {}).get("listeTipi")
                        for p in self.proposals
                        if (p.get("primaryProcedure") or {}).get("listeTipi")
                    }
                ),
            },
            "safety": {
                "writesToDatabase": False,
                "callsModel": False,
                "rawEnabled": self.enable_raw,
                "partialSnapshot": True,
                "crosswalkUiDisabled": True,
            },
        }

    @staticmethod
    def _paginate(items: list[Any], page: int, page_size: int) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 200)
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": (total + page_size - 1) // page_size if total else 0,
            "items": items[start:end],
        }

    def list_proposals(
        self,
        *,
        q: str = "",
        rule_type: str = "",
        priority: str = "",
        quality_flag: str = "",
        completeness: str = "",
        liste_tipi: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        q_norm = (q or "").strip().lower()
        items = self.proposal_list
        if q_norm:
            items = [
                p
                for p in items
                if q_norm in (p.get("proposalId") or "").lower()
                or q_norm in (p.get("procedureKod") or "").lower()
                or q_norm in (p.get("procedureAd") or "").lower()
            ]
        if rule_type:
            items = [p for p in items if p.get("targetRuleType") == rule_type]
        if priority:
            items = [p for p in items if p.get("priority") == priority]
        if completeness:
            items = [p for p in items if p.get("completeness") == completeness]
        if quality_flag:
            items = [p for p in items if quality_flag in (p.get("qualityFlags") or [])]
        if liste_tipi:
            items = [p for p in items if p.get("listeTipi") == liste_tipi]
        return self._paginate(items, page, page_size)

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        p = self.proposal_by_id.get(proposal_id)
        if not p:
            return None
        proc = p.get("primaryProcedure") or {}
        owner = _owner_key(proc.get("listeTipi"), proc.get("kod"))
        evidence = [
            self.evidence_by_id[eid]
            for eid in (p.get("officialEvidenceIds") or [])
            if eid in self.evidence_by_id
        ]
        signals = [
            self.signal_by_id[sid]
            for sid in (p.get("engineSignalIds") or [])
            if sid in self.signal_by_id
        ]
        comparison = p.get("existingRuleComparison") or {}
        existing = [
            self.rule_by_id[cid]
            for cid in (comparison.get("contextIds") or [])
            if cid in self.rule_by_id
        ]
        return {
            "proposal": p,
            "ownerId": owner,
            "officialEvidence": evidence,
            "engineSignals": signals,
            "existingRules": existing,
            "labels": LABELS,
            "ruleTypeLabel": RULE_TYPE_LABELS.get(p.get("targetRuleType") or "", p.get("targetRuleType")),
            "listeTipi": proc.get("listeTipi"),
        }

    def get_example_rules(self, proposal_id: str) -> dict[str, Any] | None:
        p = self.proposal_by_id.get(proposal_id)
        if not p:
            return None
        signals = [
            self.signal_by_id[sid]
            for sid in (p.get("engineSignalIds") or [])
            if sid in self.signal_by_id
        ]
        return build_example_rules(
            p,
            signals=signals,
            evidence_count=len(p.get("officialEvidenceIds") or []),
        )

    def list_ai(
        self,
        *,
        q: str = "",
        status: str = "",
        stage: str = "",
        outcome: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        q_norm = (q or "").strip().lower()
        items = []
        for r in self.ai_results:
            syn = r.get("synthesis") or {}
            row = {
                "packetId": r.get("packetId"),
                "ownerId": r.get("ownerId"),
                "ownerType": r.get("ownerType"),
                "stage": r.get("stage"),
                "status": r.get("status"),
                "statusLabel": LABELS.get(r.get("status") or "", r.get("status")),
                "outcome": syn.get("outcome"),
                "outcomeLabel": LABELS.get(syn.get("outcome") or "", syn.get("outcome")),
                "errors": r.get("errors") or [],
                "selectedCrosswalkId": syn.get("selectedCrosswalkId"),
                "reviewRecommended": (syn.get("proposedFields") or {}).get("reviewRecommended"),
                "hasRaw": r.get("packetId") in self.raw_by_packet if self.enable_raw else False,
            }
            items.append(row)
        if q_norm:
            items = [
                r
                for r in items
                if q_norm in (r.get("packetId") or "").lower()
                or q_norm in (r.get("ownerId") or "").lower()
            ]
        if status:
            items = [r for r in items if r.get("status") == status]
        if stage:
            items = [r for r in items if r.get("stage") == stage]
        if outcome:
            items = [r for r in items if r.get("outcome") == outcome]
        return self._paginate(items, page, page_size)

    def get_ai(self, packet_id: str) -> dict[str, Any] | None:
        r = self.ai_by_packet.get(packet_id)
        if not r:
            return None
        syn = r.get("synthesis") or {}
        selected = None
        selected_id = syn.get("selectedCrosswalkId")
        if selected_id:
            selected = self.crosswalk_by_id.get(selected_id)
        cov = self.coverage_by_owner.get(r.get("ownerId") or "")
        outcome = syn.get("outcome")
        return {
            "result": r,
            "statusLabel": LABELS.get(r.get("status") or "", r.get("status")),
            "outcomeLabel": LABELS.get(outcome, outcome) if outcome else None,
            "selectedCrosswalk": selected,
            "candidateCrosswalks": (cov or {}).get("crosswalks") or [],
            "labels": LABELS,
            "hasRaw": packet_id in self.raw_by_packet if self.enable_raw else False,
        }

    def list_crosswalks(
        self,
        *,
        q: str = "",
        review_recommended: str = "",
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        q_norm = (q or "").strip().lower()
        items = self.crosswalk_rows
        if q_norm:
            items = [
                c
                for c in items
                if q_norm in (c.get("huvCode") or "").lower()
                or q_norm in (c.get("huvName") or "").lower()
                or q_norm in (c.get("sutCode") or "").lower()
                or q_norm in (c.get("sutName") or "").lower()
                or q_norm in (c.get("crosswalkId") or "").lower()
            ]
        if review_recommended == "true":
            items = [c for c in items if c.get("reviewRecommended")]
        elif review_recommended == "false":
            items = [c for c in items if c.get("reviewRecommended") is False]
        return self._paginate(items, page, page_size)

    def get_crosswalk(self, crosswalk_id: str) -> dict[str, Any] | None:
        row = self.crosswalk_by_id.get(crosswalk_id)
        if not row:
            return None
        owner = row.get("ownerId") or ""
        ai_items = self.ai_by_owner.get(owner, [])
        proposals = [
            self.proposal_list_item(pid)
            for pid in (row.get("proposalIds") or [])
            if pid in self.proposal_by_id
        ]
        return {
            "crosswalk": row,
            "aiResults": ai_items,
            "proposals": [p for p in proposals if p],
            "labels": LABELS,
        }

    def proposal_list_item(self, proposal_id: str) -> dict[str, Any] | None:
        for item in self.proposal_list:
            if item.get("proposalId") == proposal_id:
                return item
        return None

    def get_help_markdown(self) -> str:
        """Customer-facing expert help (single source: YARDIM_UZMAN.md)."""
        path = self.root / "YARDIM_UZMAN.md"
        if not path.is_file():
            return (
                "# Yardım bulunamadı\n\n"
                "`YARDIM_UZMAN.md` handoff kökünde yok.\n"
            )
        return path.read_text(encoding="utf-8")

    def get_raw(self, packet_id: str) -> dict[str, Any] | None:
        if not self.enable_raw:
            return None
        item = self.raw_by_packet.get(packet_id)
        if not item:
            return None
        structured = self.get_ai(packet_id)
        return {
            "warning": "DOĞRULANMAMIŞ MODEL ÇIKTISI — KURAL DEĞİLDİR",
            "raw": {
                "packetId": item.get("packetId"),
                "ownerId": item.get("ownerId"),
                "ownerType": item.get("ownerType"),
                "stage": item.get("stage"),
                "status": item.get("status"),
                "validationErrors": item.get("validationErrors") or [],
                "rawResponse": _redact_secrets(item.get("rawResponse") or ""),
            },
            "structured": structured,
            "labels": LABELS,
        }


_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|secret|token)\b\s*[:=]\s*['\"]?[^\s'\"]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._\-+=/]+")


def _redact_secrets(text: str) -> str:
    if not isinstance(text, str):
        return text
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
