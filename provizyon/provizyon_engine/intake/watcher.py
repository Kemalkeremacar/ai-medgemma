"""Klasör izleyici: izlenen kök altına düşen provizyon klasörlerini otomatik
olarak işe çevirip Redis kuyruğuna ekler.

Çalışma mantığı (polling):
  - ``INTAKE_WATCH_DIR`` altındaki her alt klasör taranır.
  - İçinde "PopupPage" (Hizmet Döküm Formu) PDF'i olan klasörler aday kabul edilir.
  - Klasör "stabil" mi? (son değişiklikten ``INTAKE_STABLE_SECONDS`` geçmişse;
    kopyalama bitmiş varsayılır.)
  - Daha önce işlenmemişse (Redis ``INTAKE_SEEN_KEY`` setinde yoksa) iş üretilir,
    kuyruğa eklenir ve klasör "seen" olarak işaretlenir.

inotify yerine polling kullanılır: ağ/uzak dosya sistemlerinde daha dayanıklıdır
ve çoklu izleyiciye karşı Redis seti idempotentlik sağlar.

Çalıştırma:
    python -m provizyon_engine.intake.watcher              # sürekli izle
    python -m provizyon_engine.intake.watcher --once       # bir tarama yap, çık
    python -m provizyon_engine.intake.watcher --reset-seen # işlenmiş kaydını temizle
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from .. import settings
from ..logging_setup import configure_logging
from ..queue.redis_queue import RedisQueue
from .folder_intake import build_job_from_folder, find_popup_pdf

configure_logging(
    process_name="provizyon-watcher",
    log_file=settings.GEMMA_ROOT / "logs" / "provizyon-watcher.log",
)
log = logging.getLogger("provizyon.watcher")

_RUNNING = True


def _handle_signal(signum, frame):  # noqa: ARG001
    global _RUNNING
    log.info("Sinyal alındı (%s); izleyici durduruluyor...", signum)
    _RUNNING = False


def _folder_mtime(folder: Path) -> float:
    """Klasördeki en yeni dosyanın değişiklik zamanı (stabilite kontrolü için)."""

    latest = folder.stat().st_mtime
    for child in folder.rglob("*"):
        try:
            latest = max(latest, child.stat().st_mtime)
        except OSError:
            continue
    return latest


def scan_once(queue: RedisQueue, watch_dir: Path) -> int:
    """Bir tarama yapar; yeni ve stabil klasörleri kuyruğa ekler. Eklenen sayısını döner."""

    if not watch_dir.is_dir():
        log.warning("İzlenen klasör yok: %s", watch_dir)
        return 0

    enqueued = 0
    now = time.time()
    for folder in sorted(watch_dir.iterdir()):
        if not folder.is_dir():
            continue
        key = str(folder.resolve())
        try:
            if queue.client.sismember(settings.INTAKE_SEEN_KEY, key):
                continue
        except Exception:
            pass

        if find_popup_pdf(folder) is None:
            continue  # Henüz popup gelmemiş; sonraki taramada bakılır.

        age = now - _folder_mtime(folder)
        if age < settings.INTAKE_STABLE_SECONDS:
            log.info("Klasör henüz stabil değil, atlanıyor: %s (%.0fs)", folder.name, age)
            continue

        try:
            job = build_job_from_folder(folder)
        except Exception as exc:
            log.error("Klasör işlenemedi (%s): %s", folder.name, exc)
            # Bozuk klasörü tekrar tekrar denememek için seen işaretle.
            _mark_seen(queue, key)
            continue

        try:
            added = queue.enqueue(job.provizyon_id, job.model_dump(mode="json"))
        except Exception as exc:
            log.error("Kuyruğa eklenemedi (%s): %s", job.provizyon_id, exc)
            continue

        # Aktif iş varken de seen işaretle (reset script ile çift kuyruk önlenir).
        _mark_seen(queue, key)
        if not added:
            log.debug("Zaten kuyrukta, atlandı: %s", job.provizyon_id)
            continue

        enqueued += 1
        log.info(
            "Kuyruğa eklendi: %s (hasta=%s, HUV=%s, tanı=%s, belge=%s)",
            job.provizyon_id,
            job.patient_name,
            len(job.huv_codes),
            len(job.diagnoses),
            len(job.documents),
        )
    return enqueued


def _mark_seen(queue: RedisQueue, key: str) -> None:
    try:
        queue.client.sadd(settings.INTAKE_SEEN_KEY, key)
    except Exception:
        pass


def run_watcher(once: bool = False) -> None:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    queue = RedisQueue()
    if not queue.ping():
        log.error("Redis'e bağlanılamadı: %s", settings.REDIS_URL)
        sys.exit(1)

    watch_dir = settings.INTAKE_WATCH_DIR
    watch_dir.mkdir(parents=True, exist_ok=True)
    log.info(
        "İzleyici başladı. Klasör: %s, poll=%ss, stabilite=%ss",
        watch_dir,
        settings.INTAKE_POLL_SECONDS,
        settings.INTAKE_STABLE_SECONDS,
    )

    if once:
        n = scan_once(queue, watch_dir)
        log.info("Tek tarama tamamlandı; %s iş kuyruğa eklendi.", n)
        return

    while _RUNNING:
        try:
            scan_once(queue, watch_dir)
        except Exception as exc:
            log.exception("Tarama hatası: %s", exc)
        # Sinyale duyarlı bekleme.
        for _ in range(settings.INTAKE_POLL_SECONDS):
            if not _RUNNING:
                break
            time.sleep(1)

    log.info("İzleyici durdu.")


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Provizyon klasör izleyici.")
    ap.add_argument("--once", action="store_true", help="Bir tarama yap ve çık.")
    ap.add_argument("--reset-seen", action="store_true", help="İşlenmiş klasör kaydını temizle.")
    args = ap.parse_args(argv)

    if args.reset_seen:
        queue = RedisQueue()
        if queue.ping():
            queue.client.delete(settings.INTAKE_SEEN_KEY)
            print("İşlenmiş klasör kaydı temizlendi.")
            return 0
        print("Redis'e erişilemiyor.", file=sys.stderr)
        return 1

    run_watcher(once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
