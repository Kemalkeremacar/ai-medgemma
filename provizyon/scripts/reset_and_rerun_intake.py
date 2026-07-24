#!/usr/bin/env python3
"""Redis sonuçları + Qdrant patient_findings temizle; intake klasörlerini yeniden kuyruğa al."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from provizyon_engine import settings
from provizyon_engine.intake.folder_intake import build_job_from_folder
from provizyon_engine.queue.redis_queue import RedisQueue


def discover_intake_ids(intake_dir: Path) -> list[str]:
    if not intake_dir.is_dir():
        return []
    return sorted(p.name for p in intake_dir.iterdir() if p.is_dir())


def reset_work_cache() -> list[str]:
    removed: list[str] = []
    for sub in ("ocr_cache", "ocr_render", "peek_ocr"):
        path = settings.WORK_DIR / sub
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path))
    return removed


def reset_redis(queue: RedisQueue) -> dict[str, int]:
    c = queue.client
    stats: dict[str, int] = {}
    for key in (
        settings.QUEUE_NAME,
        settings.PROCESSING_QUEUE,
        settings.DEAD_LETTER_QUEUE,
        settings.RECENT_KEY,
        settings.INTAKE_SEEN_KEY,
    ):
        if c.delete(key):
            stats[key] = 1
    for prefix, label in (
        (settings.RESULT_KEY_PREFIX, "results"),
        ("provizyon:attempts:", "attempts"),
    ):
        keys = list(c.scan_iter(f"{prefix}*"))
        if keys:
            stats[label] = c.delete(*keys)
    for key in c.scan_iter(f"{settings.PROCESSING_QUEUE}:*"):
        c.delete(key)
        stats["processing_workers"] = stats.get("processing_workers", 0) + 1
    return stats


def reset_qdrant(provizyon_ids: list[str]) -> int:
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import FieldCondition, Filter, MatchValue
    except ImportError:
        print("qdrant-client yok; patient_findings atlandı", file=sys.stderr)
        return 0

    client = QdrantClient(url=settings.QDRANT_URL, timeout=30)
    deleted = 0
    for pid in provizyon_ids:
        try:
            client.delete(
                collection_name=settings.PATIENT_FINDINGS_COLLECTION,
                points_selector=Filter(
                    must=[FieldCondition(key="provizyon_id", match=MatchValue(value=pid))]
                ),
                wait=True,
            )
            deleted += 1
        except Exception as exc:
            print(f"Qdrant silme uyarısı ({pid}): {exc}", file=sys.stderr)
    return deleted


def _mark_intake_seen(queue: RedisQueue, folder: Path) -> None:
    try:
        queue.client.sadd(settings.INTAKE_SEEN_KEY, str(folder.resolve()))
    except Exception:
        pass


def enqueue_intake(queue: RedisQueue, intake_dir: Path) -> list[str]:
    ids: list[str] = []
    for folder in sorted(p for p in intake_dir.iterdir() if p.is_dir()):
        job = build_job_from_folder(folder)
        added = queue.enqueue(job.provizyon_id, job.model_dump(mode="json"))
        _mark_intake_seen(queue, folder)
        if added:
            ids.append(job.provizyon_id)
            print(f"  Kuyruğa eklendi: {job.provizyon_id} ({job.patient_name or '—'})", flush=True)
        else:
            print(f"  Zaten kuyrukta, atlandı: {job.provizyon_id}", flush=True)
    return ids


def wait_done(queue: RedisQueue, job_ids: list[str], *, timeout_sec: int = 7200) -> bool:
    pending = set(job_ids)
    t0 = time.time()
    while pending and (time.time() - t0) < timeout_sec:
        time.sleep(15)
        done_now: list[str] = []
        for jid in list(pending):
            r = queue.get_result(jid) or {}
            st = r.get("status")
            if st == "done":
                karar = (r.get("nihai_karar") or "?").replace("_", " ")
                print(f"  ✓ {jid} → {karar}", flush=True)
                done_now.append(jid)
            elif st == "failed":
                print(f"  ✗ {jid} failed: {r.get('error', '?')}", flush=True)
                done_now.append(jid)
        for jid in done_now:
            pending.discard(jid)
        if pending:
            depth = queue.queue_depth()
            print(
                f"  Bekleyen: {len(pending)} | kuyruk pending={depth.get('pending', 0)} "
                f"processing={depth.get('processing', 0)}",
                flush=True,
            )
    return not pending


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Provizyon kayıtlarını sıfırla ve intake'ı yeniden koştur.")
    ap.add_argument("--no-enqueue", action="store_true", help="Yalnızca temizlik yap")
    ap.add_argument("--no-wait", action="store_true", help="Kuyruğa ekledikten sonra bekleme")
    ap.add_argument(
        "--demo-only",
        action="store_true",
        help="Intake yerine demo/fixtures/ sentetik job setini kuyruğa al",
    )
    ap.add_argument("--intake-dir", type=Path, default=settings.INTAKE_WATCH_DIR)
    args = ap.parse_args(argv)
    intake_ids = discover_intake_ids(args.intake_dir)

    queue = RedisQueue()
    if not queue.ping():
        print(f"Redis erişilemiyor: {settings.REDIS_URL}", file=sys.stderr)
        return 1

    print("Redis temizleniyor…", flush=True)
    rstats = reset_redis(queue)
    dupes = queue.dedupe_pending()
    if dupes:
        rstats["deduped_pending"] = dupes
    print(f"  Redis: {json.dumps(rstats)}", flush=True)

    print("Qdrant patient_findings temizleniyor…", flush=True)
    qn = reset_qdrant(intake_ids)
    print(f"  {qn} provizyon için silme isteği gönderildi ({len(intake_ids)} klasör)", flush=True)

    cleared = reset_work_cache()
    if cleared:
        print(f"  OCR/work cache temizlendi: {', '.join(cleared)}", flush=True)

    audit = settings.GEMMA_ROOT / "logs" / "provizyon-results.jsonl"
    if audit.exists():
        backup = audit.with_suffix(".jsonl.bak")
        if backup.exists():
            backup = audit.with_name(audit.stem + f".{int(time.time())}.jsonl.bak")
        audit.rename(backup)
        print(f"  Audit log yedeklendi: {backup.name}", flush=True)

    if args.no_enqueue:
        print("Temizlik tamam (enqueue atlandı).", flush=True)
        return 0

    if args.demo_only:
        from demo.fixture_loader import enqueue_fixtures, list_fixture_ids

        print(f"\nDemo fixture'lar kuyruğa alınıyor ({len(list_fixture_ids())} adet)…", flush=True)
        job_ids = enqueue_fixtures(queue)
        for jid in job_ids:
            print(f"  Kuyruğa eklendi: {jid}", flush=True)
    else:
        print(f"\nIntake kuyruğa alınıyor ({args.intake_dir})…", flush=True)
        job_ids = enqueue_intake(queue, args.intake_dir)

    if not job_ids:
        print("Kuyruğa eklenecek iş bulunamadı.", file=sys.stderr)
        return 1

    if args.no_wait:
        print(f"\n{len(job_ids)} iş kuyruğa eklendi. Worker işlemesini bekleyin.", flush=True)
        return 0

    print(f"\n{len(job_ids)} iş tamamlanması bekleniyor (worker çalışıyor olmalı)…", flush=True)
    ok = wait_done(queue, job_ids)
    if not ok:
        print("Zaman aşımı veya eksik iş.", file=sys.stderr)
        return 2

    print("\nTamamlandı. Dashboard: http://127.0.0.1:8020/dashboard", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
