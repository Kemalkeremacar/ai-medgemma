"""SUT EK-2B ICD-10 tanı kurallarını Qdrant ``sut_diagnosis_rules`` collection'ından okur."""

from __future__ import annotations

from typing import Any

from .. import settings


def _payload_to_rule(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "procedure_key": payload.get("procedure_key"),
        "sut_code": payload.get("sut_code"),
        "procedure_name": payload.get("procedure_name"),
        "source_list": payload.get("source_list"),
        "source_file": payload.get("source_file"),
        "source_row": payload.get("source_row"),
        "description": payload.get("description"),
        "diagnosis_policy": payload.get("diagnosis_policy"),
        "required_icd10_patterns": list(payload.get("required_icd10_patterns") or []),
        "excluded_icd10_patterns": list(payload.get("excluded_icd10_patterns") or []),
        "required_diagnosis_groups": list(payload.get("required_diagnosis_groups") or []),
        "special_constraints": dict(payload.get("special_constraints") or {}),
        "decision_if_missing": payload.get("decision_if_missing"),
        "review_required": payload.get("review_required"),
        "confidence": payload.get("confidence"),
        "reason": payload.get("reason"),
        "source_evidence": payload.get("source_evidence"),
        "quality_flags": list(payload.get("quality_flags") or []),
        "quality_actions": list(payload.get("quality_actions") or []),
        "runtime_decision_mode": payload.get("runtime_decision_mode"),
        "generated_at": payload.get("generated_at"),
    }


class SutDiagnosisRulesQdrantReader:
    """Exact ``sut_code`` filtresiyle SUT tanı kuralı payload'larını yükler."""

    def __init__(
        self,
        *,
        qdrant_url: str | None = None,
        collection: str | None = None,
    ) -> None:
        self.qdrant_url = qdrant_url or settings.QDRANT_URL
        self.collection = collection or settings.SUT_DIAGNOSIS_RULES_COLLECTION
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

    def fetch_rule(self, sut_code: str) -> dict[str, Any] | None:
        from diagnosis_rules.sut_provision_diagnosis_checker import normalize_sut_code

        code = normalize_sut_code(sut_code)
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
                must=[FieldCondition(key="sut_code", match=MatchValue(value=code))]
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

    def build_lookup(self, sut_codes: list[str]) -> dict[str, Any]:
        from diagnosis_rules.sut_provision_diagnosis_checker import normalize_sut_code

        unique_codes: list[str] = []
        seen: set[str] = set()
        for raw in sut_codes:
            code = normalize_sut_code(raw)
            if code and code not in seen:
                seen.add(code)
                unique_codes.append(code)

        missing = [code for code in unique_codes if code not in self._cache and code not in self._missing]
        if missing:
            self._fetch_batch(missing)

        rules_by_sut_code: dict[str, dict[str, Any]] = {}
        aliases: dict[str, str] = {}
        for code in unique_codes:
            rule = self._cache.get(code)
            if not rule:
                continue
            rules_by_sut_code[code] = rule
            procedure_key = str(rule.get("procedure_key") or f"SUT::{code}")
            aliases[procedure_key] = code
            aliases[code] = code
            aliases[f"SUT::{code}"] = code

        return {
            "source": "qdrant",
            "collection": self.collection,
            "rules_by_sut_code": rules_by_sut_code,
            "aliases": aliases,
        }

    def _fetch_batch(self, sut_codes: list[str]) -> None:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        if not sut_codes:
            return

        client = self._qdrant()
        points, _ = client.scroll(
            collection_name=self.collection,
            scroll_filter=Filter(
                should=[
                    FieldCondition(key="sut_code", match=MatchValue(value=code))
                    for code in sut_codes
                ]
            ),
            limit=max(len(sut_codes) * 2, 16),
            with_payload=True,
        )
        found: set[str] = set()
        for point in points:
            payload = point.payload or {}
            code = str(payload.get("sut_code") or "").strip()
            if not code:
                continue
            found.add(code)
            self._cache[code] = _payload_to_rule(payload)

        for code in sut_codes:
            if code not in found and code not in self._cache:
                self._missing.add(code)
