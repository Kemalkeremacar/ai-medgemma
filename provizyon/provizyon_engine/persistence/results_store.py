"""İş sonucu deposu (karar sırası.txt Adım 9 - veri katmanı).

Nihai ``JobResult``'ı:
- Redis'te (API'nin hızlı okuması için, kuyruk backend'i üzerinden) saklar.
- Bir JSONL audit log dosyasına (izlenebilirlik/denetim için) **özet** satır ekler
  (PHI/kimlik alanları ve ham katman dump'ı yazılmaz).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import settings
from ..models import JobResult

_AUDIT_PATH = settings.GEMMA_ROOT / "logs" / "provizyon-results.jsonl"


def _layer_status(layer: Any) -> str | None:
    if not layer:
        return None
    st = layer.get("status") if isinstance(layer, dict) else getattr(layer, "status", None)
    if st is None:
        return None
    return st.value if hasattr(st, "value") else str(st)


def _audit_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Tam JobResult yerine denetim için yeterli özet (kimlik/PHI yok)."""

    raw = payload.get("raw") or {}
    medgemma = payload.get("medgemma")
    started = payload.get("started_at")
    finished = payload.get("finished_at")
    elapsed_ms: int | None = None
    if started and finished:
        try:
            t0 = datetime.fromisoformat(started)
            t1 = datetime.fromisoformat(finished)
            elapsed_ms = max(0, int((t1 - t0).total_seconds() * 1000))
        except Exception:
            pass

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "provizyon_id": payload.get("provizyon_id"),
        "status": payload.get("status"),
        "nihai_karar": payload.get("nihai_karar"),
        "decision_type": payload.get("decision_type"),
        "risk_level": payload.get("risk_level"),
        "documents_mode": raw.get("documents_mode"),
        "elapsed_ms": elapsed_ms,
        "layers": {
            "belge_hasta": _layer_status(payload.get("belge_hasta")),
            "zorunlu_evrak": _layer_status(payload.get("zorunlu_evrak")),
            "tani_kurali": _layer_status(payload.get("tani_kurali")),
            "sut_tani_kurali": _layer_status(payload.get("sut_tani_kurali")),
            "sut_kurali": _layer_status(payload.get("sut_kurali")),
            "medgemma": (
                (medgemma or {}).get("guven")
                if isinstance(medgemma, dict)
                else ("ok" if medgemma else None)
            ),
        },
        "warnings_count": len(payload.get("warnings") or []),
        "error": (payload.get("error") or None),
    }


class ResultStore:
    def __init__(self, backend: Any | None = None, *, audit_path: Path | None = None) -> None:
        # backend: QueueBackend (store_result/get_result). None ise sadece audit.
        self.backend = backend
        self.audit_path = audit_path or _AUDIT_PATH

    def save(self, result: JobResult) -> None:
        payload = result.model_dump(mode="json")
        if self.backend is not None:
            try:
                self.backend.store_result(result.provizyon_id, payload)
            except Exception:
                pass
        self._append_audit(payload)

    def get(self, provizyon_id: str) -> dict[str, Any] | None:
        if self.backend is not None:
            return self.backend.get_result(provizyon_id)
        return None

    def _append_audit(self, payload: dict[str, Any]) -> None:
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            record = _audit_record(payload)
            with open(self.audit_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass
