"""Redis tabanlı dayanıklı iş kuyruğu.

Tasarım:
- ``QUEUE_NAME`` (list): bekleyen işler (LPUSH ile eklenir, BRPOPLPUSH ile alınır).
- ``PROCESSING_QUEUE`` (list): işlenmekte olan işler (crash sonrası reaper bunları geri alabilir).
- ``DEAD_LETTER_QUEUE`` (list): retry limiti aşan işler.
- ``RESULT_KEY_PREFIX + job_id`` (string, TTL'li): iş sonucu.
- ``provizyon:attempts:<job_id>`` (string): deneme sayacı.

Mesaj zarfı JSON: ``{"job_id", "payload", "enqueued_at"}``. ``receipt`` alanı,
processing list'inden silinmek üzere zarfın tam string halidir.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .. import settings
from .backend import QueueMessage


class RedisQueue:
    """``QueueBackend`` protokolünün Redis implementasyonu."""

    def __init__(
        self,
        url: str | None = None,
        *,
        queue_name: str | None = None,
        processing_queue: str | None = None,
        dead_letter_queue: str | None = None,
        result_prefix: str | None = None,
        result_ttl: int | None = None,
        worker_id: str | None = None,
    ) -> None:
        try:
            import redis  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "redis paketi kurulu değil. Kurmak için: python -m pip install redis"
            ) from exc

        self._redis_module = redis
        self.client = redis.Redis.from_url(
            url or settings.REDIS_URL,
            decode_responses=True,
        )
        self.queue_name = queue_name or settings.QUEUE_NAME
        self._processing_base = processing_queue or settings.PROCESSING_QUEUE
        # Çoklu worker güvenliği: her worker kendi processing listesine alır
        # (BRPOPLPUSH hedefi). Böylece başlangıçtaki reclaim yalnızca kendi
        # işlerini geri alır, diğer worker'ların işlerine dokunmaz.
        self.worker_id = worker_id
        if worker_id:
            self.processing_queue = f"{self._processing_base}:{worker_id}"
        else:
            self.processing_queue = self._processing_base
        self.dead_letter_queue = dead_letter_queue or settings.DEAD_LETTER_QUEUE
        self.result_prefix = result_prefix or settings.RESULT_KEY_PREFIX
        self.result_ttl = result_ttl if result_ttl is not None else settings.RESULT_TTL_SECONDS
        self.recent_key = settings.RECENT_KEY

    # -- yardımcılar --------------------------------------------------------
    def _attempts_key(self, job_id: str) -> str:
        return f"provizyon:attempts:{job_id}"

    def _result_key(self, job_id: str) -> str:
        return f"{self.result_prefix}{job_id}"

    def _make_envelope(self, job_id: str, payload: dict[str, Any], attempts: int) -> str:
        return json.dumps(
            {
                "job_id": job_id,
                "payload": payload,
                "attempts": attempts,
                "enqueued_at": time.time(),
                # Aynı içerikli iki işin processing list'inde çakışmaması için.
                "envelope_id": uuid.uuid4().hex,
            },
            ensure_ascii=False,
        )

    def _envelope_job_id(self, envelope: str) -> str | None:
        try:
            return str(json.loads(envelope).get("job_id") or "") or None
        except json.JSONDecodeError:
            return None

    def is_job_active(self, job_id: str) -> bool:
        """İş kuyrukta, işleniyor veya yakın zamana kadar başlatılmış mı?"""
        if self.client.exists(self._attempts_key(job_id)):
            return True
        return self._job_in_queue(self.queue_name, job_id) or any(
            self._job_in_queue(q, job_id) for q in self._all_processing_queues()
        )

    def _job_in_queue(self, queue_name: str, job_id: str) -> bool:
        try:
            envelopes = self.client.lrange(queue_name, 0, -1)
        except Exception:
            return False
        for envelope in envelopes:
            if self._envelope_job_id(envelope) == job_id:
                return True
        return False

    # -- public API ---------------------------------------------------------
    def enqueue(self, job_id: str, payload: dict[str, Any]) -> bool:
        """İşi kuyruğa ekler. Zaten aktifse ``False`` döner (çift enqueue yok)."""
        if self.is_job_active(job_id):
            return False
        # Yarış koşulunda (reset + watcher) çift eklemeyi önlemek için atomik claim.
        if not self.client.set(self._attempts_key(job_id), 1, nx=True):
            return False
        envelope = self._make_envelope(job_id, payload, attempts=1)
        try:
            self.client.lpush(self.queue_name, envelope)
        except Exception:
            self.client.delete(self._attempts_key(job_id))
            raise
        # Sonucu "queued" olarak işaretle ki API hemen durumu görebilsin.
        self.store_result(
            job_id,
            {"provizyon_id": payload.get("provizyon_id", job_id), "status": "queued"},
        )
        self._track_recent(job_id)
        return True

    def _track_recent(self, job_id: str) -> None:
        # Tekrarları önlemek için önce sil, sonra başa ekle; son 200 ile sınırla
        # (API /dashboard recent üst sınırıyla uyumlu).
        try:
            self.client.lrem(self.recent_key, 0, job_id)
            self.client.lpush(self.recent_key, job_id)
            self.client.ltrim(self.recent_key, 0, 199)
        except Exception:
            pass

    def recent_results(self, limit: int = 25) -> list[dict[str, Any]]:
        """Son gönderilen işlerin özet sonuçlarını döner (en yeni önce)."""

        try:
            job_ids = self.client.lrange(self.recent_key, 0, max(0, limit - 1))
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for jid in job_ids:
            if not self.is_live_job_id(jid):
                continue
            result = self.get_result(jid) or {}
            raw = result.get("raw") or {}
            meta = raw.get("job_meta") or {}
            pipeline = raw.get("pipeline") or {}
            evidence = raw.get("medgemma_evidence") or {}
            partial = bool(evidence.get("partial_vision") or evidence.get("excluded_pages"))
            if pipeline.get("version") == "v4" and pipeline.get("full_vision"):
                pipeline_label = "v4 tam"
            elif pipeline.get("version") == "v4" and partial:
                pipeline_label = "v4 kısmi"
            elif partial:
                pipeline_label = "v3 kısmi"
            elif pipeline.get("version") == "v4":
                pipeline_label = "v4"
            else:
                pipeline_label = "legacy"
            started = result.get("started_at")
            finished = result.get("finished_at")
            elapsed_s: str | None = None
            if started and finished:
                try:
                    from datetime import datetime, timezone

                    t0 = datetime.fromisoformat(started)
                    t1 = datetime.fromisoformat(finished)
                    secs = (t1 - t0).total_seconds()
                    elapsed_s = f"{secs:.1f}s" if secs < 60 else f"{secs / 60:.1f}m"
                except Exception:
                    pass
            risk_reasons = result.get("risk_reasons") or []
            documents = raw.get("documents") or {}
            risk_summary: str | None = None
            if risk_reasons:
                risk_summary = (risk_reasons[0].get("message") or "")[:160] or None
            shadow = raw.get("shadow_advice")
            if not isinstance(shadow, dict) or not shadow.get("status"):
                try:
                    from ..shadow_handoff import evaluate_shadow_advice

                    shadow = evaluate_shadow_advice(
                        sut_codes=list(meta.get("sut_codes") or []),
                        huv_codes=list(meta.get("huv_codes") or []),
                        diagnoses=list(meta.get("diagnoses") or []),
                        nihai_karar=result.get("nihai_karar"),
                    )
                except Exception:
                    shadow = {}
            out.append(
                {
                    "provizyon_id": result.get("provizyon_id", jid),
                    "hasta_id": result.get("hasta_id"),
                    "patient_name": meta.get("patient_name"),
                    "status": result.get("status", "queued"),
                    "nihai_karar": result.get("nihai_karar"),
                    "gerekce": (result.get("gerekce") or "")[:160],
                    "finished_at": finished,
                    "elapsed": elapsed_s,
                    "pipeline": pipeline_label,
                    "vision_images": evidence.get("image_count"),
                    "decision_type": result.get("decision_type"),
                    "risk_level": result.get("risk_level"),
                    "risk_reasons_count": len(risk_reasons),
                    "risk_summary": risk_summary,
                    "code_family": meta.get("code_family"),
                    "diagnoses": meta.get("diagnoses", []),
                    "huv_codes_count": len(meta.get("huv_codes") or []),
                    "sut_codes_count": len(meta.get("sut_codes") or []),
                    "warnings_count": len(result.get("warnings") or []),
                    "medgemma_guven": (result.get("medgemma") or {}).get("guven"),
                    "document_count": documents.get("provided", 0),
                    "partial_vision": partial,
                    "shadow_advice_status": shadow.get("status"),
                    "shadow_advice_label": shadow.get("label"),
                }
            )
        return out

    def dequeue(self, timeout: int = 5) -> QueueMessage | None:
        envelope = self.client.brpoplpush(self.queue_name, self.processing_queue, timeout=timeout)
        if envelope is None:
            return None
        try:
            data = json.loads(envelope)
        except json.JSONDecodeError:
            # Bozuk zarf: processing'den temizle ve atla.
            self.client.lrem(self.processing_queue, 1, envelope)
            return None
        job_id = str(data.get("job_id"))
        attempts = int(self.client.get(self._attempts_key(job_id)) or data.get("attempts") or 1)
        return QueueMessage(
            job_id=job_id,
            payload=data.get("payload") or {},
            receipt=envelope,
            attempts=attempts,
        )

    def ack(self, message: QueueMessage) -> None:
        self.client.lrem(self.processing_queue, 1, message.receipt)
        self.client.delete(self._attempts_key(message.job_id))

    def retry(self, message: QueueMessage, *, max_retries: int | None = None) -> bool:
        max_retries = max_retries if max_retries is not None else settings.MAX_RETRIES
        # Önce processing'den çıkar.
        self.client.lrem(self.processing_queue, 1, message.receipt)
        attempts = self.client.incr(self._attempts_key(message.job_id))
        if attempts > max_retries:
            self.client.lpush(self.dead_letter_queue, message.receipt)
            self.client.delete(self._attempts_key(message.job_id))
            return False
        if self._job_in_queue(self.queue_name, message.job_id):
            return True
        new_envelope = self._make_envelope(message.job_id, message.payload, attempts=attempts)
        self.client.lpush(self.queue_name, new_envelope)
        return True

    def store_result(self, job_id: str, result: dict[str, Any]) -> None:
        key = self._result_key(job_id)
        self.client.set(key, json.dumps(result, ensure_ascii=False))
        if self.result_ttl and self.result_ttl > 0:
            self.client.expire(key, self.result_ttl)
        # Tamamlanan / işlenen işleri recent başa taşı; aksi halde toplu enqueue
        # (100+) ltrim ile done kayıtlarını listeden düşürüyordu.
        status = str((result or {}).get("status") or "")
        if status in {"done", "failed", "processing"}:
            self._track_recent(job_id)

    def get_result(self, job_id: str) -> dict[str, Any] | None:
        raw = self.client.get(self._result_key(job_id))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def remove_result(self, job_id: str) -> bool:
        """Sonuç kaydını ve recent listesinden çıkarır (test artefaktları için)."""
        removed = False
        try:
            if self.client.delete(self._result_key(job_id)):
                removed = True
            self.client.lrem(self.recent_key, 0, job_id)
        except Exception:
            pass
        return removed

    def _active_job_ids(self) -> set[str]:
        """Pending + processing kuyruklarındaki iş kimlikleri."""
        active: set[str] = set()
        try:
            for envelope in self.client.lrange(self.queue_name, 0, -1):
                jid = self._envelope_job_id(envelope)
                if jid:
                    active.add(jid)
            for q in self._all_processing_queues():
                for envelope in self.client.lrange(q, 0, -1):
                    jid = self._envelope_job_id(envelope)
                    if jid:
                        active.add(jid)
        except Exception:
            pass
        return active

    def clear_results(
        self,
        *,
        statuses: set[str] | None = None,
        keep_active: bool = True,
        clear_all: bool = False,
    ) -> dict[str, int]:
        """Sonuç kayıtlarını ve recent listesini temizler.

        Varsayılan: ``done`` / ``failed`` / yetim ``queued`` silinir.
        ``keep_active=True`` iken kuyruktaki işlere dokunulmaz.
        """
        want = statuses or {"done", "failed", "queued"}
        active = self._active_job_ids() if keep_active else set()
        deleted = 0
        skipped_active = 0
        try:
            keys = list(self.client.scan_iter(match=f"{self.result_prefix}*", count=500))
        except Exception:
            keys = []
        for key in keys:
            raw = self.client.get(key)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            key_s = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
            job_id = str(data.get("provizyon_id") or key_s.removeprefix(self.result_prefix))
            status = str(data.get("status") or "")
            if not clear_all and status not in want:
                continue
            if keep_active and job_id in active:
                skipped_active += 1
                continue
            if self.remove_result(job_id):
                deleted += 1
        # Recent'te kalmış ama result'ı olmayan kimlikleri de temizle
        try:
            for jid in list(self.client.lrange(self.recent_key, 0, -1)):
                sid = jid.decode() if isinstance(jid, (bytes, bytearray)) else str(jid)
                if keep_active and sid in active:
                    continue
                if self.get_result(sid) is None:
                    self.client.lrem(self.recent_key, 0, jid)
        except Exception:
            pass
        return {
            "deleted": deleted,
            "skipped_active": skipped_active,
            "recent_len": int(self.client.llen(self.recent_key) or 0),
        }

    @staticmethod
    def is_live_job_id(job_id: str) -> bool:
        """Gerçek intake veya demo provizyonları: sayısal ID veya demo-/DEMO- prefix."""
        if not job_id:
            return False
        return job_id.isdigit() or job_id.lower().startswith("demo-")

    def _all_processing_queues(self) -> list[str]:
        """Temel processing listesi + tüm per-worker processing listeleri."""

        queues = {self._processing_base}
        try:
            for key in self.client.keys(f"{self._processing_base}:*"):
                queues.add(key)
        except Exception:
            pass
        return sorted(queues)

    def queue_depth(self) -> dict[str, int]:
        processing = 0
        for q in self._all_processing_queues():
            processing += int(self.client.llen(q))
        return {
            "pending": int(self.client.llen(self.queue_name)),
            "processing": processing,
            "dead": int(self.client.llen(self.dead_letter_queue)),
        }

    def dedupe_pending(self) -> int:
        """Aynı ``job_id`` için yinelenen zarfları kuyruktan çıkarır."""
        pending = self.client.lrange(self.queue_name, 0, -1)
        seen: set[str] = set()
        removed = 0
        for envelope in pending:
            job_id = self._envelope_job_id(envelope)
            if not job_id:
                continue
            if job_id in seen:
                if self.client.lrem(self.queue_name, 1, envelope):
                    removed += 1
            else:
                seen.add(job_id)
        return removed

    def _drain_processing(self, processing_queue: str) -> int:
        count = 0
        while True:
            envelope = self.client.rpop(processing_queue)
            if envelope is None:
                break
            job_id = self._envelope_job_id(envelope)
            if job_id and self._job_in_queue(self.queue_name, job_id):
                continue
            self.client.lpush(self.queue_name, envelope)
            count += 1
        return count

    def reclaim_stale(self) -> int:
        """Bu worker'ın kendi processing listesindeki işleri ana kuyruğa alır.

        Worker başlangıcında çağrılır. Çoklu worker'da yalnızca KENDİ
        listesini boşalttığı için diğer worker'ların işleyen işlerine
        dokunmaz. İdempotent; deneme sayacı korunur.
        """

        return self._drain_processing(self.processing_queue)

    def reclaim_all_stale(self) -> int:
        """Tüm processing listelerini geri alır (yönetimsel/elle kullanım).

        Hiçbir worker çalışmıyorken çağrılmalıdır; aksi halde işlenmekte olan
        işleri geri alıp çift işlemeye yol açabilir.
        """

        total = 0
        for q in self._all_processing_queues():
            total += self._drain_processing(q)
        return total

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False
