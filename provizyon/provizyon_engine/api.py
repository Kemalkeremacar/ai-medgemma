"""Provizyon Orkestratör API (port 8020).

Endpoint'ler:
  POST /provizyon/enqueue      -> işi Redis kuyruğuna ekler (asenkron)
  POST /provizyon/process-sync -> işi anında çalıştırır (kuyruksuz; test/debug)
  GET  /provizyon/{id}         -> iş sonucunu döner
  GET  /queue/stats            -> kuyruk derinlikleri
  GET  /health                 -> servis sağlığı
  GET  /system/health          -> tüm servislerin durumu (GPU, Qdrant, vs.)
  GET  /analytics/findings     -> patient_findings vektör DB analitik istatistikleri
  GET  /shadow/review-reduction/summary           -> handoff headline + H40 özeti
  GET  /shadow/review-reduction/decision-register -> aggregate karar defteri
  GET  /shadow/review-reduction/703790            -> düzeltilmiş H40 proposal detayı
  GET  /                       -> kontrol paneli UI
  GET  /dashboard              -> kontrol paneli UI
  GET  /copilot                -> Finansal Risk UI
  GET  /dashboard/yonetici     -> Ne Yaptık? UI
  GET  /dashboard/demo         -> Sistem (maskeli Provizyonlar + Kurum Analiz)
  GET  /dashboard/demo-sunum   -> Diyagram (animasyonlu provizyon akışı)
  GET  /dashboard/kural-onerileri -> DGX kural önerileri demo (read-only handoff)
  GET  /rule-proposal-demo/...    -> demo static + JSON API köprüsü
  POST /rule-proposal-demo/api/oneri-ai/chat -> uzman Öneri AI sohbeti

Worker (kuyruğu tüketen süreç) ayrıdır: ``python -m provizyon_engine.worker``.
"""

from __future__ import annotations

import http.server
import json
import logging
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any

import httpx
import psutil
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import settings
from .intake.folder_intake import build_job_from_folder
from .intake.db_intake import fetch_pending_provizyonlar, fetch_provizyon
from .models import JobResult, ProvizyonJob
from .orchestrator import ProvizyonOrchestrator
from .queue.redis_queue import RedisQueue
from .shadow_handoff import (
    ShadowHandoffError,
    attach_shadow_advice_to_result,
    load_703790_detail,
    load_decision_register,
    load_summary,
)
from .rule_proposal_handoff import (
    RuleProposalHandoffError,
    get_store,
    raw_enabled,
    read_static_file,
    render_index_html,
)
from .rule_proposal_oneri_ai import chat as oneri_ai_chat

app = FastAPI(
    title="Provizyon Orkestratör API",
    version="0.2.0",
    description=(
        "Sağlık sisteminden gelen provizyon işlerini kuyruğa alan, belge/OCR + "
        "hasta-belge uyumu + HUV/ICD tanı + SUT/ICD tanı + SUT kural + MedGemma klinik "
        "değerlendirmesini orkestre eden API. Dashboard ve sistem izleme dahil."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class _QuietAccessFilter(logging.Filter):
    """Panel poll / health gürültüsünü access log'dan çıkarır."""

    _SKIP = (
        "GET /queue/stats",
        "GET /queue/recent",
        "GET /health",
        "GET /system/health",
        "GET /favicon",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(s in msg for s in self._SKIP)


def _configure_api_logging() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _QuietAccessFilter) for f in access.filters):
        access.addFilter(_QuietAccessFilter())
    # httpx/qdrant çağrıları API sürecinde de olabiliyor
    for name in ("httpx", "httpcore", "urllib3", "qdrant_client"):
        logging.getLogger(name).setLevel(logging.WARNING)


@app.on_event("startup")
def _on_startup() -> None:
    _configure_api_logging()


_queue: RedisQueue | None = None


def get_queue() -> RedisQueue:
    global _queue
    if _queue is None:
        _queue = RedisQueue()
    return _queue


@app.post("/provizyon/enqueue")
def enqueue(job: ProvizyonJob) -> dict[str, Any]:
    queue = get_queue()
    if not queue.ping():
        raise HTTPException(503, f"Redis kuyruğuna erişilemiyor: {settings.REDIS_URL}")
    payload = job.model_dump(mode="json")
    queue.enqueue(job.provizyon_id, payload)
    return {
        "status": "queued",
        "provizyon_id": job.provizyon_id,
        "queue": settings.QUEUE_NAME,
    }


@app.post("/provizyon/process-sync", response_model=JobResult)
def process_sync(job: ProvizyonJob) -> JobResult:
    """İşi kuyruğa almadan anında çalıştırır (debug/entegrasyon testi)."""

    orchestrator = ProvizyonOrchestrator()
    result = orchestrator.run(job)
    # Senkron sonucu da sonuç deposuna yaz ki GET ile okunabilsin.
    try:
        q = get_queue()
        q.store_result(job.provizyon_id, result.model_dump(mode="json"))
        q._track_recent(job.provizyon_id)
    except Exception:
        pass
    return result


class IntakeFolderRequest(BaseModel):
    folder: str
    enqueue: bool = False


@app.post("/provizyon/intake-folder")
def intake_folder(req: IntakeFolderRequest) -> dict[str, Any]:
    """Bir provizyon klasöründen (Hizmet Döküm Formu + belgeler) iş üretir.

    ``enqueue=true`` ise üretilen işi Redis kuyruğuna ekler. Aksi halde yalnızca
    önizleme (parse sonucu) döner.
    """

    try:
        job = build_job_from_folder(req.folder)
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"Klasör işlenemedi: {exc}") from exc

    queued = False
    if req.enqueue:
        queue = get_queue()
        if not queue.ping():
            raise HTTPException(503, f"Redis kuyruğuna erişilemiyor: {settings.REDIS_URL}")
        queue.enqueue(job.provizyon_id, job.model_dump(mode="json"))
        queued = True

    return {
        "status": "queued" if queued else "preview",
        "provizyon_id": job.provizyon_id,
        "job": job.model_dump(mode="json"),
    }


class IntakeDbRequest(BaseModel):
    provizyon_id: int | None = None
    pending: bool = False
    enqueue: bool = False
    # Belgesiz tam akış: belge indirme atlanır, belge katmanları SKIPPED, MedGemma
    # yalnızca üstveri/metinle çalışır. Belge yokluğu hata sayılmaz.
    skip_documents: bool = False
    # pending modda en fazla kaç kayıt çekileceği (varsayılan panel: 100, üst sınır 200).
    limit: int | None = None
    # newest = ProvizyonId DESC; random = NEWID() örneklemesi.
    sample: str = "newest"
    # True ise recent/kuyrukta zaten olan ID'leri alma (eski değerlendirmeyi koru).
    exclude_existing: bool = True


@app.post("/provizyon/intake-db")
def intake_db(req: IntakeDbRequest) -> dict[str, Any]:
    """MSSQL veritabanından (dbo.S_VW_PROVIZYON_AI) provizyon çeker ve iş üretir.

    ``provizyon_id`` verilirse tek iş, ``pending=true`` ise vakıf değerlendirmesi
    bekleyen (DurumId=5) kayıtlar çekilir. ``enqueue=true`` ile kuyruğa eklenir.
    ``skip_documents=true`` ile belgesiz tam akış (belge katmanları SKIPPED),
    ``limit`` ile kayıt sayısı sınırlanır (1–200).
    ``sample=random`` rastgele örnekler; ``exclude_existing`` önceki sonuçları korur.
    """

    if not req.provizyon_id and not req.pending:
        raise HTTPException(400, "provizyon_id veya pending=true gerekli.")

    effective_limit = req.limit
    if effective_limit is not None:
        effective_limit = max(1, min(200, int(effective_limit)))

    sample = (req.sample or "newest").strip().lower()
    if sample not in {"newest", "random"}:
        raise HTTPException(400, "sample 'newest' veya 'random' olmalı.")

    exclude_ids: set[str] = set()
    queue = get_queue()
    if req.pending and req.exclude_existing:
        if queue.ping():
            try:
                # Recent listedeki tüm ID'ler (eski 100 vb.) yeniden çekilmesin / ezilmesin.
                for item in queue.recent_results(limit=300):
                    pid = str(item.get("provizyon_id") or "").strip()
                    if pid:
                        exclude_ids.add(pid)
                # Aktif kuyruk/processing da hariç.
                for jid in list(queue.client.lrange(queue.recent_key, 0, 299) or []):
                    exclude_ids.add(str(jid))
            except Exception:
                pass

    try:
        if req.pending:
            jobs = fetch_pending_provizyonlar(
                skip_documents=req.skip_documents,
                limit=effective_limit,
                sample=sample,
                exclude_ids=exclude_ids,
            )
        else:
            jobs = [fetch_provizyon(req.provizyon_id, skip_documents=req.skip_documents)]
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Veritabanı hatası: {exc}") from exc

    queued_ids: list[str] = []
    already_queued: list[str] = []
    skipped_existing: list[str] = []
    if req.enqueue:
        if not queue.ping():
            raise HTTPException(503, f"Redis kuyruğuna erişilemiyor: {settings.REDIS_URL}")
        for job in jobs:
            # Tamamlanmış sonucu ezme: done/failed kayıt varsa atla.
            existing = queue.get_result(job.provizyon_id) or {}
            st = str(existing.get("status") or "")
            if st in {"done", "failed"} and req.exclude_existing:
                skipped_existing.append(job.provizyon_id)
                continue
            added = queue.enqueue(job.provizyon_id, job.model_dump(mode="json"))
            if added:
                queued_ids.append(job.provizyon_id)
            else:
                already_queued.append(job.provizyon_id)

    return {
        "status": "queued" if req.enqueue else "preview",
        "count": len(jobs),
        "queued_count": len(queued_ids),
        "already_queued_count": len(already_queued),
        "skipped_existing_count": len(skipped_existing),
        "documents_mode": "skipped_full_pipeline" if req.skip_documents else "normal",
        "limit": effective_limit,
        "sample": sample,
        "exclude_existing": req.exclude_existing,
        "excluded_seed_count": len(exclude_ids),
        "provizyon_ids": [j.provizyon_id for j in jobs],
        "queued_ids": queued_ids,
        "already_queued_ids": already_queued,
        "skipped_existing_ids": skipped_existing[:50],
        "jobs": [j.model_dump(mode="json") for j in jobs] if not req.enqueue else [],
    }


@app.delete("/provizyon/{provizyon_id}")
def delete_result(provizyon_id: str) -> dict[str, Any]:
    """Test artefaktı veya eski kayıt temizliği."""
    queue = get_queue()
    if not queue.ping():
        raise HTTPException(503, f"Redis kuyruğuna erişilemiyor: {settings.REDIS_URL}")
    removed = queue.remove_result(provizyon_id)
    if not removed:
        raise HTTPException(404, f"Sonuç bulunamadı: {provizyon_id}")
    return {"status": "deleted", "provizyon_id": provizyon_id}


@app.delete("/queue/results")
def clear_queue_results(
    keep_active: bool = Query(True, description="Kuyruktaki işleri koru"),
    clear_all: bool = Query(False, description="Tüm sonuçları sil (aktif hariç)"),
) -> dict[str, Any]:
    """Eski sonuçları / recent listesini temizler. Varsayılan: done+failed+yetim queued."""
    queue = get_queue()
    if not queue.ping():
        raise HTTPException(503, f"Redis kuyruğuna erişilemiyor: {settings.REDIS_URL}")
    stats = queue.clear_results(keep_active=keep_active, clear_all=clear_all)
    return {"status": "cleared", **stats}


_RL_ORDER: dict[str, int] = {
    "red": 6, "orange": 5, "yellow": 4, "gray": 3, "blue": 2, "green": 1,
}
_DT_ORDER: dict[str, int] = {
    "automatic_defensible": 3, "manual_review": 2, "low_risk": 1,
}


@app.get("/provizyon/{provizyon_id}")
def get_result(provizyon_id: str) -> dict[str, Any]:
    queue = get_queue()
    result = queue.get_result(provizyon_id)
    if result is None:
        raise HTTPException(404, f"Sonuç bulunamadı: {provizyon_id}")
    reasons = result.get("risk_reasons") or []
    if reasons:
        reasons.sort(
            key=lambda r: (
                _RL_ORDER.get(r.get("risk_level", ""), 0),
                _DT_ORDER.get(r.get("decision_type", ""), 0),
            ),
            reverse=True,
        )
        result["risk_reasons"] = reasons
    # Eski sonuçlarda da salt okunur gölge tavsiyeyi hesapla (canlı karar değişmez).
    attach_shadow_advice_to_result(result)
    return result


@app.get("/queue/stats")
def queue_stats() -> dict[str, Any]:
    queue = get_queue()
    if not queue.ping():
        return {"redis": "down", "url": settings.REDIS_URL}
    return {"redis": "ok", "url": settings.REDIS_URL, "depth": queue.queue_depth()}


@app.get("/queue/recent")
def recent(limit: int = 25) -> dict[str, Any]:
    """Son gönderilen işlerin özet durumlarını döner (en yeni önce)."""

    queue = get_queue()
    if not queue.ping():
        raise HTTPException(503, f"Redis kuyruğuna erişilemiyor: {settings.REDIS_URL}")
    limit = max(1, min(300, limit))
    return {"items": queue.recent_results(limit=limit)}


def _shadow_http(exc: ShadowHandoffError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@app.get("/shadow/review-reduction/summary")
def shadow_review_reduction_summary() -> dict[str, Any]:
    """Aggregate review-reduction handoff headline (read-only, not apply-ready)."""
    try:
        return load_summary()
    except ShadowHandoffError as exc:
        raise _shadow_http(exc) from exc


@app.get("/shadow/review-reduction/decision-register")
def shadow_review_reduction_decision_register() -> dict[str, Any]:
    """Final decision register from the DGX transfer bundle (aggregate only)."""
    try:
        return load_decision_register()
    except ShadowHandoffError as exc:
        raise _shadow_http(exc) from exc


@app.get("/shadow/review-reduction/703790")
def shadow_review_reduction_703790() -> dict[str, Any]:
    """Corrected 703790 H40-only shadow proposal detail (overlay disabled)."""
    try:
        return load_703790_detail()
    except ShadowHandoffError as exc:
        raise _shadow_http(exc) from exc


@app.get("/health")
def health() -> dict[str, Any]:
    queue = get_queue()
    redis_ok = queue.ping()
    return {
        "status": "ok",
        "service": "provizyon-orchestrator",
        "redis": "ok" if redis_ok else "down",
        "queue_depth": queue.queue_depth() if redis_ok else None,
        "config": {
            "queue_name": settings.QUEUE_NAME,
            "document_root": str(settings.DOCUMENT_ROOT),
            "medgemma_base_url": settings.MEDGEMMA_BASE_URL,
            "medgemma_vision_mode": settings.MEDGEMMA_VISION_MODE,
            "qdrant_url": settings.QDRANT_URL,
            "tei_url": settings.TEI_URL,
            "findings_collection": settings.PATIENT_FINDINGS_COLLECTION,
            "ocr_lang": settings.OCR_LANG,
        },
    }


# ---------------------------------------------------------------------------
# Analytics — patient_findings vektör DB istatistikleri
# ---------------------------------------------------------------------------

import time as _time
_analytics_cache: dict[str, Any] = {}
_analytics_cache_ts: float = 0.0
_ANALYTICS_CACHE_TTL = 120  # saniye


def _scroll_all_findings() -> list[dict[str, Any]]:
    """patient_findings collection'ındaki tüm noktaları scroll ile çeker."""
    try:
        url = settings.QDRANT_URL
        points: list[dict[str, Any]] = []
        offset = None
        while True:
            body: dict[str, Any] = {"limit": 256, "with_payload": True}
            if offset is not None:
                body["offset"] = offset
            r = httpx.post(
                f"{url}/collections/{settings.PATIENT_FINDINGS_COLLECTION}/points/scroll",
                json=body, timeout=30.0,
            )
            data = r.json().get("result", {})
            batch = data.get("points", [])
            if not batch:
                break
            points.extend(batch)
            offset = data.get("next_page_offset")
            if offset is None:
                break
        return points
    except Exception:
        return []


def _aggregate_findings(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Ham Qdrant noktalarından analitik istatistikler üretir."""
    from collections import Counter

    karar_counter: Counter[str] = Counter()
    layer_status: dict[str, Counter[str]] = {}
    kurum_karar: dict[str, Counter[str]] = {}
    yas_karar: dict[str, Counter[str]] = {}
    cinsiyet_karar: dict[str, Counter[str]] = {}
    facility_karar: dict[str, Counter[str]] = {}
    provizyon_ids: set[str] = set()
    huv_count = 0
    sut_count = 0
    tani_fail_details: list[dict[str, str]] = []

    for pt in points:
        payload = pt.get("payload") or {}
        layer = payload.get("layer", "")
        prov_id = payload.get("provizyon_id", "")
        nihai = payload.get("nihai_karar", "")
        status = payload.get("status", "")
        kurum = payload.get("institution_name", "") or "Bilinmiyor"
        yas_g = payload.get("yas_grubu", "") or "bilinmiyor"
        cinsiyet = payload.get("cinsiyet", "") or "bilinmiyor"
        facility = payload.get("facility_level", "") or "bilinmiyor"

        if layer == "nihai_karar":
            provizyon_ids.add(prov_id)
            karar_counter[nihai] += 1
            kurum_karar.setdefault(kurum, Counter())[nihai] += 1
            yas_karar.setdefault(yas_g, Counter())[nihai] += 1
            cinsiyet_karar.setdefault(cinsiyet, Counter())[nihai] += 1
            facility_karar.setdefault(facility, Counter())[nihai] += 1

        if layer in ("tani_kurali", "sut_tani_kurali", "sut_kurali"):
            layer_status.setdefault(layer, Counter())[status or "unknown"] += 1

        if layer == "tani_kurali":
            huv_count += 1
        elif layer == "sut_tani_kurali":
            sut_count += 1

        if layer in ("tani_kurali", "sut_tani_kurali") and status == "fail":
            msg = payload.get("message", "")
            if len(tani_fail_details) < 50:
                tani_fail_details.append({
                    "provizyon_id": prov_id,
                    "layer": layer,
                    "message": msg[:200],
                    "nihai_karar": nihai,
                    "kurum": kurum,
                })

    kurum_summary = []
    for kurum, counts in sorted(kurum_karar.items(), key=lambda x: -sum(x[1].values())):
        total = sum(counts.values())
        kurum_summary.append({
            "kurum": kurum,
            "toplam": total,
            "uygun": counts.get("uygun", 0),
            "tani_uyumsuz": counts.get("tani_uyumsuz", 0),
            "tani_eksik": counts.get("tani_eksik", 0),
            "manuel_inceleme": counts.get("manuel_inceleme", 0),
            "diger_red": total - counts.get("uygun", 0) - counts.get("manuel_inceleme", 0)
                         - counts.get("tani_uyumsuz", 0) - counts.get("tani_eksik", 0),
            "uygun_oran": round(counts.get("uygun", 0) / max(total, 1) * 100, 1),
        })

    return {
        "toplam_provizyon": len(provizyon_ids),
        "toplam_nokta": len(points),
        "karar_dagilimi": dict(karar_counter.most_common()),
        "layer_status": {k: dict(v.most_common()) for k, v in layer_status.items()},
        "huv_kayit": huv_count,
        "sut_kayit": sut_count,
        "kurum_bazli": kurum_summary,
        "yas_grubu": {k: dict(v) for k, v in yas_karar.items()},
        "cinsiyet": {k: dict(v) for k, v in cinsiyet_karar.items()},
        "kurum_tipi": {k: dict(v) for k, v in facility_karar.items()},
        "tani_fail_ornekler": tani_fail_details,
    }


@app.get("/analytics/findings")
def analytics_findings() -> dict[str, Any]:
    """patient_findings collection'ından toplu analitik istatistikler döner (2dk cache)."""
    global _analytics_cache, _analytics_cache_ts

    now = _time.time()
    if _analytics_cache and (now - _analytics_cache_ts) < _ANALYTICS_CACHE_TTL:
        return _analytics_cache

    points = _scroll_all_findings()
    if not points:
        return {"error": "patient_findings collection boş veya erişilemiyor.", "toplam_nokta": 0}

    result = _aggregate_findings(points)
    result["cache_ts"] = now
    result["collection"] = settings.PATIENT_FINDINGS_COLLECTION

    _analytics_cache = result
    _analytics_cache_ts = now
    return result


# ---------------------------------------------------------------------------
# Dashboard UI + System monitoring
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_LOG_DIR = settings.GEMMA_ROOT / "logs"
_DATA_GENERATED = settings.PROVIZYON_ROOT / "data" / "generated"
_CATALOG_DIR = _DATA_GENERATED / "unified_catalog_final_medgemma"

_HTTP = httpx.Client(timeout=3.0)

_SERVICE_PROBES = [
    ("Provizyon API", 8020, "/health"),
    ("MedGemma", 8000, "/v1/models"),
    ("Qdrant", 6333, "/collections"),
    ("TEI", 8002, "/info"),
    ("Open WebUI", 3000, "/health"),
]

_LOG_MAP: dict[str, str | tuple[str, ...]] = {
    "medgemma": "medgemma.log",
    "provizyon-api": "provizyon-api.log",
    "provizyon-worker": "provizyon-worker-1.log",
    "qdrant": ("docker", "qdrant"),
    "tei": ("docker", "tei-bge-m3"),
    "webui": ("docker", "open-webui"),
    "test": "test-results.log",
}


def _probe_service(name: str, port: int, path: str) -> dict[str, Any]:
    import time

    url = f"http://127.0.0.1:{port}{path}"
    t0 = time.perf_counter()
    try:
        r = _HTTP.get(url)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        ok = 200 <= r.status_code < 400
        return {
            "name": name,
            "port": port,
            "path": path,
            "status": "ok" if ok else "down",
            "http": r.status_code,
            "latency_ms": latency_ms,
            "detail": f"HTTP {r.status_code}" if ok else f"HTTP {r.status_code} — yanıt beklenen aralıkta değil",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "name": name,
            "port": port,
            "path": path,
            "status": "down",
            "latency_ms": latency_ms,
            "error": str(exc)[:160],
            "detail": "Bağlantı kurulamadı",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }


def _probe_redis(host: str = "127.0.0.1", port: int = 6379) -> dict[str, Any]:
    import socket
    import time

    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            resp = sock.recv(64)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        ok = b"PONG" in resp
        return {
            "name": "Redis",
            "port": port,
            "path": "PING",
            "status": "ok" if ok else "down",
            "latency_ms": latency_ms,
            "detail": "PONG alındı" if ok else "PING yanıtı beklenmiyor",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    except Exception as exc:
        return {
            "name": "Redis",
            "port": port,
            "path": "PING",
            "status": "down",
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "error": str(exc)[:160],
            "detail": "Bağlantı kurulamadı",
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }


def _probe_prov_workers() -> dict[str, Any]:
    import time

    running: list[dict[str, Any]] = []
    for pf in sorted(_LOG_DIR.glob("provizyon-worker-*.pid")):
        try:
            pid = int(pf.read_text(encoding="utf-8").strip())
            worker_id = pf.stem.removeprefix("provizyon-worker-")
            if psutil.pid_exists(pid):
                try:
                    proc = psutil.Process(pid)
                    running.append({
                        "id": worker_id,
                        "pid": pid,
                        "cpu_pct": proc.cpu_percent(interval=0.0),
                        "status": proc.status(),
                    })
                except (psutil.Error, OSError):
                    running.append({"id": worker_id, "pid": pid})
        except (ValueError, OSError):
            continue
    count = len(running)
    return {
        "name": "Provizyon Worker",
        "port": None,
        "path": "pid",
        "status": "ok" if count else "down",
        "workers": count,
        "detail": f"{count} worker aktif" if count else "çalışan worker yok",
        "worker_pids": running,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _gpu_info() -> dict[str, Any]:
    def _num(raw: str) -> int | None:
        s = (raw or "").strip().replace("[", "").replace("]", "")
        if not s or s.upper() == "N/A":
            return None
        try:
            return int(float(s))
        except ValueError:
            return None

    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,memory.free,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            text=True, timeout=5,
        ).strip()
        # Çoklu GPU varsa ilk satırı al.
        line = out.splitlines()[0] if out else ""
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            return {}
        mem_used = _num(parts[1])
        mem_total = _num(parts[2])
        return {
            "name": parts[0] or None,
            "mem_used_mb": mem_used,
            "mem_total_mb": mem_total,
            "mem_free_mb": _num(parts[3]),
            "utilization_pct": _num(parts[4]),
            "temp_c": _num(parts[5]),
            "mem_unavailable": mem_total is None,
        }
    except Exception:
        return {}


def _system_metrics() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu_pct": psutil.cpu_percent(interval=0.3),
        "ram_used_gb": round(vm.used / 1e9, 1),
        "ram_total_gb": round(vm.total / 1e9, 1),
        "ram_pct": vm.percent,
        "swap_used_gb": round(sw.used / 1e9, 1),
        "swap_total_gb": round(sw.total / 1e9, 1),
        "disk_used_gb": round(disk.used / 1e9, 1),
        "disk_total_gb": round(disk.total / 1e9, 1),
        "disk_pct": disk.percent,
    }


def _qdrant_collections() -> list[dict[str, Any]]:
    try:
        r = _HTTP.get(f"http://127.0.0.1:6333/collections")
        cols = r.json().get("result", {}).get("collections", [])
        result = []
        for c in cols:
            name = c["name"]
            try:
                info = _HTTP.get(f"http://127.0.0.1:6333/collections/{name}").json()
                pts = info["result"]["points_count"]
                st = info["result"]["status"]
            except Exception:
                pts, st = "?", "?"
            result.append({"name": name, "points": pts, "status": st})
        return result
    except Exception:
        return []


def _qdrant_payload_count(collection: str, key: str, value: Any) -> int | None:
    """Canlı Qdrant points/count (exact) — dashboard auto/review için."""
    try:
        r = _HTTP.post(
            f"http://127.0.0.1:6333/collections/{collection}/points/count",
            json={
                "exact": True,
                "filter": {"must": [{"key": key, "match": {"value": value}}]},
            },
        )
        r.raise_for_status()
        count = r.json().get("result", {}).get("count")
        return int(count) if isinstance(count, int) else None
    except Exception:
        return None


def _diagnosis_stats() -> dict[str, Any]:
    """HUV/SUT auto-review sayıları canlı Qdrant payload filter'larından.

    Runtime motor da aynı koleksiyonları okur; eski summary JSON ile
    reconcile etmek dashboard'da yanlış güven (ör. 5466 auto) yaratıyordu.
    """
    huv_collection = settings.DIAGNOSIS_RULES_COLLECTION
    sut_collection = settings.SUT_DIAGNOSIS_RULES_COLLECTION
    cols = {c.get("name"): c for c in _qdrant_collections()}
    huv_col = cols.get(huv_collection, {})
    sut_col = cols.get(sut_collection, {})
    huv_pts = huv_col.get("points") if isinstance(huv_col.get("points"), int) else None
    sut_pts = sut_col.get("points") if isinstance(sut_col.get("points"), int) else None

    huv_auto = _qdrant_payload_count(huv_collection, "runtime_decision_mode", "automatic") if huv_pts else None
    huv_review = _qdrant_payload_count(huv_collection, "runtime_decision_mode", "manual_review") if huv_pts else None
    sut_auto = _qdrant_payload_count(sut_collection, "runtime_decision_mode", "automatic") if sut_pts else None
    sut_review = _qdrant_payload_count(sut_collection, "runtime_decision_mode", "manual_review") if sut_pts else None

    # Filter başarısızsa (Qdrant down / index yok) summary JSON'a düş — ama kaynak bayrağını ayır.
    stats_source = "qdrant_live"
    if huv_pts and (huv_auto is None or huv_review is None):
        stats_source = "summary_fallback"
        huv_summary_path = (
            _DATA_GENERATED / "diagnosis_rules" / "runtime" / "huv_diagnosis_runtime_summary.json"
        )
        try:
            huv_summary = json.loads(huv_summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            huv_summary = {}
        if huv_auto is None:
            huv_auto = huv_summary.get("auto_usable_rules")
        if huv_review is None:
            huv_review = huv_summary.get("review_queue_rules")
            if huv_auto is not None and isinstance(huv_pts, int):
                huv_review = max(0, huv_pts - int(huv_auto))
    if sut_pts and (sut_auto is None or sut_review is None):
        stats_source = "summary_fallback"
        sut_summary_path = (
            _DATA_GENERATED
            / "sut_diagnosis_rules"
            / "ek2b"
            / "runtime"
            / "sut_diagnosis_runtime_summary.json"
        )
        try:
            sut_summary = json.loads(sut_summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            sut_summary = {}
        if sut_auto is None:
            sut_auto = sut_summary.get("auto_usable_rules")
        if sut_review is None:
            sut_review = sut_summary.get("review_queue_rules")
            if sut_auto is not None and isinstance(sut_pts, int):
                sut_review = max(0, sut_pts - int(sut_auto))

    return {
        "huv_total_rules": huv_pts,
        "sut_total_rules": sut_pts,
        "huv_auto_rules": huv_auto,
        "huv_review_rules": huv_review,
        "sut_auto_rules": sut_auto,
        "sut_review_rules": sut_review,
        "huv_collection": huv_collection,
        "sut_collection": sut_collection,
        "qdrant_source": True,
        "stats_source": stats_source,
    }


def _count_lines(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except Exception:
        return None


def _catalog_stats() -> dict[str, Any]:
    crosswalk = _count_lines(_CATALOG_DIR / "huv_sut_crosswalk.jsonl")
    unified = _count_lines(_CATALOG_DIR / "unified_catalog.jsonl")
    notes = _count_lines(_CATALOG_DIR / "huv_note_rules.jsonl")
    generated_at: str | None = None
    summary_path = _CATALOG_DIR / "summary.json"
    if summary_path.exists():
        try:
            generated_at = json.loads(summary_path.read_text(encoding="utf-8")).get("generated_at")
        except Exception:
            pass
    qdrant_pts: int | None = None
    for col in _qdrant_collections():
        if col.get("name") == settings.SUT_UNIFIED_COLLECTION and isinstance(col.get("points"), int):
            qdrant_pts = col["points"]
            break
    return {
        "out_dir": str(_CATALOG_DIR),
        "crosswalk_rows": crosswalk,
        "unified_rows": unified,
        "note_rules": notes,
        "qdrant_collection": settings.SUT_UNIFIED_COLLECTION,
        "qdrant_points": qdrant_pts,
        "qdrant_in_sync": (
            crosswalk is not None and qdrant_pts is not None and crosswalk == qdrant_pts
        ),
        "generated_at": generated_at,
        # Katalog dosyası diskte kalır; runtime HUV→SUT eşleştirme ayrı bayrakla kontrol edilir.
        "huv_sut_crosswalk_runtime_enabled": bool(
            getattr(settings, "ENABLE_HUV_SUT_CROSSWALK", False)
        ),
        "huv_sut_crosswalk_note": (
            "runtime eşleştirme açık"
            if getattr(settings, "ENABLE_HUV_SUT_CROSSWALK", False)
            else "runtime eşleştirme kapalı (PROVIZYON_ENABLE_HUV_SUT_CROSSWALK=0)"
        ),
    }


@app.get("/system/health")
def system_health() -> dict[str, Any]:
    services = [_probe_service(n, p, path) for n, p, path in _SERVICE_PROBES]
    services.append(_probe_redis())
    services.append(_probe_prov_workers())
    return {
        "services": services,
        "gpu": _gpu_info(),
        "system": _system_metrics(),
        "qdrant_collections": _qdrant_collections(),
        "diagnosis_stats": _diagnosis_stats(),
        "catalog_stats": _catalog_stats(),
    }


@app.get("/system/logs")
def system_logs(
    service: str = Query("provizyon-api", description="Servis adı: medgemma, provizyon-api, provizyon-worker, qdrant, tei, webui, test"),
    lines: int = Query(80, ge=1, le=2000),
) -> dict[str, Any]:
    target = _LOG_MAP.get(service)
    if target is None:
        raise HTTPException(404, f"Bilinmeyen servis: {service}. Geçerli: {', '.join(_LOG_MAP)}")

    if isinstance(target, tuple):
        try:
            out = subprocess.check_output(
                ["docker", "logs", "--tail", str(lines), target[1]],
                text=True, timeout=10, stderr=subprocess.STDOUT,
            )
        except Exception as exc:
            out = f"[docker logs hatası: {exc}]"
        return {"service": service, "source": f"docker:{target[1]}", "lines": out.splitlines()[-lines:]}

    log_path = _LOG_DIR / target
    if not log_path.exists():
        return {"service": service, "source": str(log_path), "lines": []}
    with open(log_path) as f:
        tail = list(deque(f, maxlen=lines))
    return {"service": service, "source": str(log_path), "lines": [l.rstrip("\n") for l in tail]}


def _serve_static_html(filename: str) -> HTMLResponse:
    html_path = _STATIC_DIR / filename
    if not html_path.exists():
        raise HTTPException(404, f"{filename} bulunamadı")
    content = html_path.read_text(encoding="utf-8")
    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    """Ana kontrol paneli (sol menü + gömülü sayfalar)."""
    return _serve_static_html("dashboard.html")


@app.get("/dashboard/demo", include_in_schema=False)
def dashboard_demo_page():
    """Sistem — Provizyonlar + Kurum Analiz, kimlik alanları maskeli."""
    return _serve_static_html("dashboard_demo.html")


@app.get("/dashboard/demo-sunum", include_in_schema=False)
def dashboard_demo_sunum_page():
    """Diyagram — animasyonlu belgesiz provizyon akış diyagramı."""
    return _serve_static_html("dashboard_demo_sunum.html")


@app.get("/dashboard/kural-onerileri", include_in_schema=False)
@app.get("/rule-proposal-demo/", include_in_schema=False)
@app.get("/rule-proposal-demo", include_in_schema=False)
def dashboard_kural_onerileri_page():
    """DGX kural önerileri demo — dondurulmuş handoff, read-only."""
    try:
        return HTMLResponse(
            content=render_index_html(),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    except RuleProposalHandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.get("/rule-proposal-demo/api/summary")
def rule_proposal_summary():
    try:
        return get_store().get_summary()
    except RuleProposalHandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.get("/rule-proposal-demo/api/help")
def rule_proposal_help():
    try:
        return {
            "title": "Uzman Yardım Rehberi",
            "format": "markdown",
            "markdown": get_store().get_help_markdown(),
        }
    except RuleProposalHandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.get("/rule-proposal-demo/api/proposals")
def rule_proposal_list(
    q: str = "",
    ruleType: str = "",
    priority: str = "",
    qualityFlag: str = "",
    completeness: str = "",
    listeTipi: str = "",
    hasAi: str = "",
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=200),
):
    try:
        return get_store().list_proposals(
            q=q,
            rule_type=ruleType,
            priority=priority,
            quality_flag=qualityFlag,
            completeness=completeness,
            liste_tipi=listeTipi,
            has_ai=hasAi,
            page=page,
            page_size=pageSize,
        )
    except RuleProposalHandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.get("/rule-proposal-demo/api/proposals/{proposal_id}")
def rule_proposal_detail(proposal_id: str):
    try:
        detail = get_store().get_proposal(proposal_id)
    except RuleProposalHandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    if not detail:
        raise HTTPException(404, "not_found")
    return detail


@app.get("/rule-proposal-demo/api/proposals/{proposal_id}/example-rules")
def rule_proposal_example_rules(proposal_id: str):
    try:
        detail = get_store().get_example_rules(proposal_id)
    except RuleProposalHandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    if not detail:
        raise HTTPException(404, "not_found")
    return detail


@app.get("/rule-proposal-demo/api/ai")
def rule_proposal_ai_list(
    q: str = "",
    status: str = "",
    stage: str = "",
    outcome: str = "",
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=200),
):
    try:
        return get_store().list_ai(
            q=q,
            status=status,
            stage=stage,
            outcome=outcome,
            page=page,
            page_size=pageSize,
        )
    except RuleProposalHandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.get("/rule-proposal-demo/api/ai/{packet_id}")
def rule_proposal_ai_detail(packet_id: str):
    try:
        detail = get_store().get_ai(packet_id)
    except RuleProposalHandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    if not detail:
        raise HTTPException(404, "not_found")
    return detail


@app.get("/rule-proposal-demo/api/raw/{packet_id}")
def rule_proposal_raw(packet_id: str):
    if not raw_enabled():
        raise HTTPException(
            403,
            "Ham cevaplar kapalı. PROVIZYON_RULE_PROPOSAL_ENABLE_RAW=1 ile açın.",
        )
    try:
        detail = get_store().get_raw(packet_id)
    except RuleProposalHandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    if not detail:
        raise HTTPException(404, "not_found")
    return detail


class OneriAiChatRequest(BaseModel):
    message: str
    proposalId: str | None = None
    history: list[dict[str, str]] | None = None


@app.post("/rule-proposal-demo/api/oneri-ai/chat")
def rule_proposal_oneri_ai_chat(body: OneriAiChatRequest):
    """Uzman ↔ Öneri AI sohbeti (kural önerisi / HUV-SUT / provizyon / MedGemma)."""
    try:
        return oneri_ai_chat(
            body.message,
            proposal_id=body.proposalId,
            history=body.history,
        )
    except ValueError as exc:
        code = str(exc)
        if code == "message_required":
            raise HTTPException(400, "message gerekli") from exc
        if code == "message_too_long":
            raise HTTPException(400, "message çok uzun (max 4000)") from exc
        raise HTTPException(400, code) from exc
    except RuleProposalHandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.get("/rule-proposal-demo/{asset}", include_in_schema=False)
def rule_proposal_demo_asset(asset: str):
    if asset.startswith("api"):
        raise HTTPException(404, "not_found")
    if asset in {"app.css", "app.js"} or asset.endswith((".css", ".js", ".svg", ".png", ".ico")):
        try:
            body, ctype = read_static_file(asset)
        except RuleProposalHandoffError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        from fastapi.responses import Response

        return Response(
            content=body,
            media_type=ctype,
            headers={"Cache-Control": "no-cache"},
        )
    raise HTTPException(404, "not_found")


@app.get("/copilot", include_in_schema=False)
@app.get("/dashboard/copilot", include_in_schema=False)
def copilot_page():
    """Finansal Risk paneli — uzman karar destek UI."""
    return _serve_static_html("copilot.html")


@app.get("/yonetici", include_in_schema=False)
@app.get("/dashboard/yonetici", include_in_schema=False)
def yonetici_page():
    """Ne Yaptık? — kural motoru test sonuçları özeti."""
    return _serve_static_html("yonetici.html")


# ---------------------------------------------------------------------------
# Legacy port redirect (8010 -> 8020)
# ---------------------------------------------------------------------------

_redirect_log = logging.getLogger("provizyon.redirect")


def _start_redirect_server(from_port: int = 8010, to_port: int = 8020) -> None:
    """Start a lightweight HTTP server that 307-redirects all requests to *to_port*."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _redirect(self):
            host = (self.headers.get("Host") or "localhost").split(":")[0]
            self.send_response(307)
            self.send_header("Location", f"http://{host}:{to_port}{self.path}")
            self.end_headers()

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _redirect  # type: ignore[assignment]

        def log_message(self, *_a: object) -> None:  # noqa: D401
            pass

    try:
        srv = http.server.HTTPServer(("0.0.0.0", from_port), _Handler)
    except OSError:
        _redirect_log.info("Port %s zaten meşgul, redirect atlanıyor", from_port)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _redirect_log.info("Legacy redirect: :%s -> :%s", from_port, to_port)


@app.on_event("startup")
def _on_startup() -> None:
    _start_redirect_server()


