"""HUV-ICD tanı kurallarını Qdrant ``huv_diagnosis_rules`` collection'ından okur."""

from __future__ import annotations

from typing import Any

from .. import settings


def _payload_to_rule(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "procedure_key": payload.get("procedure_key"),
        "huv_code": payload.get("huv_code"),
        "procedure_name": payload.get("procedure_name"),
        "diagnosis_policy": payload.get("diagnosis_policy"),
        "required_icd10_patterns": list(payload.get("required_icd10_patterns") or []),
        "excluded_icd10_patterns": list(payload.get("excluded_icd10_patterns") or []),
        "required_diagnosis_groups": list(payload.get("required_diagnosis_groups") or []),
        "decision_if_missing": payload.get("decision_if_missing"),
        "review_required": payload.get("review_required"),
        "confidence": payload.get("confidence"),
        "reason": payload.get("reason"),
        "source_evidence": payload.get("source_evidence"),
        "quality_flags": list(payload.get("quality_flags") or []),
        "runtime_decision_mode": payload.get("runtime_decision_mode"),
    }


class DiagnosisRulesQdrantReader:
    """Exact ``huv_code`` filtresiyle tanı kuralı payload'larını yükler."""

    def __init__(
        self,
        *,
        qdrant_url: str | None = None,
        collection: str | None = None,
    ) -> None:
        self.qdrant_url = qdrant_url or settings.QDRANT_URL
        self.collection = collection or settings.DIAGNOSIS_RULES_COLLECTION
        self._client = None
        self._cache: dict[str, dict[str, Any]] = {}
        self._missing: set[str] = set()

    def _qdrant(self):
        if self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(url=self.qdrant_url, timeout=60, check_compatibility=False)
        return self._client

    def ping(self) -> bool:
        try:
            self._qdrant().get_collection(self.collection)
            return True
        except Exception:
            return False

    def fetch_rule(self, huv_code: str) -> dict[str, Any] | None:
        from diagnosis_rules.provision_diagnosis_checker import normalize_huv_code

        code = normalize_huv_code(huv_code)
        if not code:
            return None
        if code in self._cache:
            return self._cache[code]
        if code in self._missing:
            return None

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        client = self._qdrant()
        points, _ = client.scroll(
            collection_name=self.collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="huv_code", match=MatchValue(value=code))]
            ),
            limit=1,
            with_payload=True,
        )
        if not points:
            self._missing.add(code)
            return None

        rule = _payload_to_rule(points[0].payload or {})
        self._cache[code] = rule
        return rule

    def build_lookup(self, huv_codes: list[str]) -> dict[str, Any]:
        from diagnosis_rules.provision_diagnosis_checker import normalize_huv_code

        unique_codes: list[str] = []
        seen: set[str] = set()
        for raw in huv_codes:
            code = normalize_huv_code(raw)
            if code and code not in seen:
                seen.add(code)
                unique_codes.append(code)

        missing = [code for code in unique_codes if code not in self._cache and code not in self._missing]
        if missing:
            self._fetch_batch(missing)

        rules_by_huv_code: dict[str, dict[str, Any]] = {}
        aliases: dict[str, str] = {}
        for code in unique_codes:
            rule = self._cache.get(code)
            if not rule:
                continue
            rules_by_huv_code[code] = rule
            procedure_key = str(rule.get("procedure_key") or f"HUV::{code}")
            aliases[procedure_key] = code
            aliases[code] = code

        return {
            "source": "qdrant",
            "collection": self.collection,
            "rules_by_huv_code": rules_by_huv_code,
            "aliases": aliases,
        }

    def _fetch_batch(self, huv_codes: list[str]) -> None:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        if not huv_codes:
            return

        client = self._qdrant()
        points, _ = client.scroll(
            collection_name=self.collection,
            scroll_filter=Filter(
                should=[
                    FieldCondition(key="huv_code", match=MatchValue(value=code))
                    for code in huv_codes
                ]
            ),
            limit=max(len(huv_codes) * 2, 16),
            with_payload=True,
        )
        found: set[str] = set()
        for point in points:
            payload = point.payload or {}
            code = str(payload.get("huv_code") or "").strip()
            if not code:
                continue
            found.add(code)
            self._cache[code] = _payload_to_rule(payload)

        for code in huv_codes:
            if code not in found and code not in self._cache:
                self._missing.add(code)
