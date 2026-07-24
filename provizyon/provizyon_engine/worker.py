"""Provizyon worker'ı: kuyruğu tüketir, orkestratörü çalıştırır, sonucu saklar.

Çalıştırma:
    python -m provizyon_engine.worker

Crash sonrası, başlangıçta ``processing`` listesinde kalan işler ana kuyruğa
geri alınır (reclaim_stale).
"""

from __future__ import annotations

import gc
import logging
import os
import signal
import sys
import time
from datetime import datetime

from . import settings
from .logging_setup import configure_logging
from .models import JobResult, JobStatus, KararDurumu, ProvizyonJob
from .orchestrator import ProvizyonOrchestrator
from .persistence.results_store import ResultStore
from .queue.redis_queue import RedisQueue

WORKER_ID = os.environ.get("PROVIZYON_WORKER_ID", "1").strip() or "1"

configure_logging(
    process_name=f"provizyon-worker:{WORKER_ID}",
    log_file=settings.GEMMA_ROOT / "logs" / f"provizyon-worker-{WORKER_ID}.log",
)
log = logging.getLogger("provizyon.worker")

_RUNNING = True

_STALE_CHECK_INTERVAL = 300


def _handle_signal(signum, frame):  # noqa: ARG001
    global _RUNNING
    log.info("Sinyal alındı (%s); worker durduruluyor...", signum)
    _RUNNING = False


def _safe_remove_from_processing(queue: RedisQueue, message) -> None:
    """Processing listesinden mesajı güvenli şekilde kaldırır (son çare)."""
    try:
        queue.client.lrem(queue.processing_queue, 1, message.receipt)
        log.warning("Processing'den zorla kaldırıldı: %s", message.job_id)
    except Exception:
        pass


def _elapsed_ms(result: JobResult) -> int | None:
    try:
        t0 = datetime.fromisoformat(result.started_at)
        t1 = datetime.fromisoformat(result.finished_at)
        return max(0, int((t1 - t0).total_seconds() * 1000))
    except Exception:
        return None


def _layer_status(layer) -> str | None:
    if layer is None:
        return None
    st = getattr(layer, "status", None)
    return st.value if st is not None else None


def _job_summary(result: JobResult, *, attempts: int, elapsed_wall_ms: int) -> str:
    """Tek satır operasyon özeti — gürültüsüz, aranabilir."""

    docs_mode = (result.raw or {}).get("documents_mode") or "normal"
    layers = {
        "belge_hasta": _layer_status(result.belge_hasta),
        "zorunlu_evrak": _layer_status(result.zorunlu_evrak),
        "tani": _layer_status(result.tani_kurali),
        "sut_tani": _layer_status(result.sut_tani_kurali),
        "sut": _layer_status(result.sut_kurali),
        "medgemma": "ok" if result.medgemma is not None else "yok",
    }
    layer_s = ",".join(f"{k}={v}" for k, v in layers.items() if v)
    ms = _elapsed_ms(result)
    if ms is None:
        ms = elapsed_wall_ms
    karar = result.nihai_karar.value if result.nihai_karar else "?"
    return (
        f"job={result.provizyon_id} status={result.status.value} karar={karar} "
        f"ms={ms} attempt={attempts} docs_mode={docs_mode} layers[{layer_s}]"
    )


def process_message(
    queue: RedisQueue,
    orchestrator: ProvizyonOrchestrator,
    store: ResultStore,
    message,
) -> None:
    job_id = message.job_id
    try:
        job = ProvizyonJob.model_validate(message.payload)
    except Exception as exc:
        log.error("İş ayrıştırılamadı job=%s err=%s", job_id, exc)
        queue.retry(message, max_retries=0)
        store.save(
            JobResult(
                provizyon_id=job_id,
                status=JobStatus.FAILED,
                nihai_karar=KararDurumu.MANUEL_INCELEME,
                error=f"Geçersiz iş payload: {exc}",
            )
        )
        return

    t0 = time.monotonic()
    log.info("İş başladı job=%s attempt=%s", job.provizyon_id, message.attempts)
    result = orchestrator.run(job)
    store.save(result)
    wall_ms = int((time.monotonic() - t0) * 1000)

    if result.status == JobStatus.FAILED:
        requeued = queue.retry(message, max_retries=settings.MAX_RETRIES)
        log.error(
            "%s requeued=%s err=%s",
            _job_summary(result, attempts=message.attempts, elapsed_wall_ms=wall_ms),
            requeued,
            (result.error or "")[:200],
        )
        return

    queue.ack(message)
    log.info(_job_summary(result, attempts=message.attempts, elapsed_wall_ms=wall_ms))


def run_worker() -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    queue = RedisQueue(worker_id=WORKER_ID)
    if not queue.ping():
        log.error("Redis'e bağlanılamadı: %s", settings.REDIS_URL)
        sys.exit(1)

    reclaimed = queue.reclaim_stale()
    if reclaimed:
        log.info("Önceki çalışmadan %s iş ana kuyruğa geri alındı.", reclaimed)

    orchestrator = ProvizyonOrchestrator()
    store = ResultStore(backend=queue)

    log.info(
        "Worker başladı id=%s queue=%s processing=%s",
        WORKER_ID,
        settings.QUEUE_NAME,
        queue.processing_queue,
    )

    last_stale_check = time.monotonic()
    jobs_since_gc = 0

    while _RUNNING:
        now = time.monotonic()
        if now - last_stale_check > _STALE_CHECK_INTERVAL:
            try:
                reclaimed = queue.reclaim_stale()
                if reclaimed:
                    log.info("Periyodik stale reclaim: %d iş geri alındı.", reclaimed)
            except Exception:
                pass
            last_stale_check = now

        try:
            message = queue.dequeue(timeout=5)
        except Exception as exc:
            log.error("Kuyruk okuma hatası: %s", exc)
            continue
        if message is None:
            continue

        try:
            process_message(queue, orchestrator, store, message)
        except Exception as exc:
            log.exception("Beklenmedik işleme hatası job=%s: %s", message.job_id, exc)
            try:
                queue.retry(message, max_retries=settings.MAX_RETRIES)
            except Exception:
                _safe_remove_from_processing(queue, message)
                store.save(
                    JobResult(
                        provizyon_id=message.job_id,
                        status=JobStatus.FAILED,
                        nihai_karar=KararDurumu.MANUEL_INCELEME,
                        error=f"Kurtarılamaz hata: {exc}",
                    )
                )
        finally:
            jobs_since_gc += 1
            if jobs_since_gc >= 3:
                gc.collect()
                jobs_since_gc = 0

    log.info("Worker durdu.")


if __name__ == "__main__":
    run_worker()
