"""MedGemma Gateway — mevcut vLLM sunucusunun önünde duran bağımsız API katmanı.

Bu servis mevcut projeye dokunmaz. İçeride çalışan vLLM'e (varsayılan
http://127.0.0.1:8000/v1) OpenAI uyumlu istekleri iletir; ağa açık portta
API key ile korunur, böylece başka bir makineden güvenle çağrılabilir.

Özellikler:
- Birden çok isimli API key (api_keys.txt) veya tek anahtar (GATEWAY_API_KEY).
- İstek kaydı (JSONL, varsayılan sadece meta veri — tıbbi içerik yazılmaz).
- Kuyruk (eşzamanlılık sınırı).
- Rate limit (anahtar başına dakikada N istek; varsayılan 0 = sınırsız/kapalı).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


_HERE = Path(__file__).resolve().parent

# --- Ayarlar (gateway.env / ortam değişkenleri) ---
UPSTREAM_BASE_URL = _env("GATEWAY_UPSTREAM_URL", "http://127.0.0.1:8000/v1").rstrip("/")
GATEWAY_API_KEY = _env("GATEWAY_API_KEY")
API_KEYS_FILE = _env("GATEWAY_API_KEYS_FILE", str(_HERE / "api_keys.txt"))
DEFAULT_MODEL = _env("GATEWAY_DEFAULT_MODEL", "/raid/monassist1/medgemma_model_gptq_w4")
TIMEOUT_SECONDS = float(_env("GATEWAY_TIMEOUT_SECONDS", "900"))
# Kuyruk: aynı anda en fazla kaç istek upstream'e gitsin (gerisi sırada bekler).
MAX_CONCURRENCY = int(_env("GATEWAY_MAX_CONCURRENCY", "4"))
# Bir istek kuyrukta en fazla kaç saniye bekleyebilir (SLA_SECONDS ile birlikte
# üst sınır olarak kullanılır; asıl belirleyici SLA_SECONDS bütçesidir).
QUEUE_TIMEOUT_SECONDS = float(_env("GATEWAY_QUEUE_TIMEOUT_SECONDS", "300"))
# SLA: bir isteğin gateway'e ulaşmasından yanıta kadar toplam süre bütçesi (saniye).
# Kuyrukta + upstream'de geçen süre birlikte bu değeri aşarsa istek 504 döner ve
# upstream isteği iptal edilir (GPU slotu hemen serbest kalır).
SLA_SECONDS = float(_env("GATEWAY_SLA_SECONDS", "300"))
# İstek kaydı.
LOG_REQUESTS = _env("GATEWAY_LOG_REQUESTS", "1") not in ("0", "false", "False", "")
LOG_FILE = _env("GATEWAY_LOG_FILE", str(_HERE.parent / "logs" / "gateway_requests.jsonl"))
# Log'a prompt/cevap içeriği de yazılsın mı? Varsayılan: hayır (tıbbi gizlilik).
LOG_CONTENT = _env("GATEWAY_LOG_CONTENT", "0") in ("1", "true", "True")
# Rate limit: anahtar başına dakikada en fazla istek. 0 = sınırsız (kapalı).
RATE_LIMIT_PER_MIN = int(_env("GATEWAY_RATE_LIMIT_PER_MIN", "0"))

app = FastAPI(title="MedGemma Gateway", version="1.2.0")


class SlaTimeout(Exception):
    """SLA (toplam deadline) aşıldı. ``stage``: 'queue' | 'upstream'."""

    def __init__(self, stage: str, waited_ms: int) -> None:
        super().__init__(f"SLA timeout at {stage} after {waited_ms} ms")
        self.stage = stage
        self.waited_ms = waited_ms


@app.exception_handler(SlaTimeout)
async def _sla_timeout_handler(request: Request, exc: SlaTimeout) -> JSONResponse:
    """Deadline aşımında istemcinin loglayabileceği net sözleşme (HTTP 504)."""
    return JSONResponse(
        status_code=504,
        content={
            "error": "timeout",
            "stage": exc.stage,
            "waited_ms": exc.waited_ms,
            "deadline_ms": round(SLA_SECONDS * 1000),
        },
    )


# --- API key yönetimi ---------------------------------------------------------
def _load_api_keys() -> dict[str, str]:
    """key -> isim eşlemesi. Kaynaklar: api_keys.txt + GATEWAY_API_KEY (tek anahtar).

    Dosya formatı (satır başına bir anahtar):
        <key>:<isim>
        <key>            # isim verilmezse "unnamed"
    # ile başlayan ve boş satırlar yok sayılır.
    """
    keys: dict[str, str] = {}
    path = Path(API_KEYS_FILE)
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, name = line.split(":", 1)
                key, name = key.strip(), name.strip() or "unnamed"
            else:
                key, name = line, "unnamed"
            if key:
                keys[key] = name
    if GATEWAY_API_KEY:
        keys.setdefault(GATEWAY_API_KEY, "default")
    return keys


API_KEYS = _load_api_keys()


# --- Rate limit (varsayılan kapalı) ------------------------------------------
_rate_hits: dict[str, deque[float]] = defaultdict(deque)


def _check_rate_limit(client_name: str) -> None:
    if RATE_LIMIT_PER_MIN <= 0:
        return
    now = time.time()
    hits = _rate_hits[client_name]
    cutoff = now - 60.0
    while hits and hits[0] < cutoff:
        hits.popleft()
    if len(hits) >= RATE_LIMIT_PER_MIN:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit aşıldı (dakikada {RATE_LIMIT_PER_MIN}). Biraz sonra tekrar deneyin.",
        )
    hits.append(now)


async def get_client(authorization: str = Header(default="")) -> str:
    """Bearer token doğrular, istemci ismini döner. Anahtar tanımlı değilse 503."""
    if not API_KEYS:
        raise HTTPException(
            status_code=503,
            detail="Hiç API key tanımlı değil; servis güvenlik gereği kapalı.",
        )
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Eksik veya geçersiz Authorization başlığı.")
    token = authorization[len("Bearer "):].strip()
    name = API_KEYS.get(token)
    if name is None:
        raise HTTPException(status_code=401, detail="Geçersiz API key.")
    _check_rate_limit(name)
    return name


# --- İstek kaydı -------------------------------------------------------------
def _log_event(record: dict[str, Any]) -> None:
    if not LOG_REQUESTS:
        return
    try:
        p = Path(LOG_FILE)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        # Loglama asla isteği düşürmesin.
        pass


# --- Kuyruk ------------------------------------------------------------------
class _Gate:
    """Basit istek kuyruğu: eşzamanlılığı sınırlar, bekleyenleri sayar."""

    def __init__(self, limit: int) -> None:
        self.sem = asyncio.Semaphore(limit)
        self.limit = limit
        self.active = 0
        self.waiting = 0


gate = _Gate(MAX_CONCURRENCY)


def _remaining(started: float) -> float:
    """İstek başlangıcından bu yana SLA bütçesinden kalan süre (saniye)."""
    return SLA_SECONDS - (time.monotonic() - started)


class _Slot:
    """Kuyrukta yer bekleyip alan async context manager.

    ``budget`` saniye içinde slot açılmazsa ``SlaTimeout('queue', ...)`` fırlatır.
    Slot alınınca beklenen süre ``queue_wait_ms`` alanında tutulur.
    """

    def __init__(self, budget: float) -> None:
        self.budget = budget
        self.queue_wait_ms = 0

    async def __aenter__(self) -> "_Slot":
        gate.waiting += 1
        t0 = time.monotonic()
        try:
            await asyncio.wait_for(gate.sem.acquire(), timeout=max(0.0, self.budget))
        except asyncio.TimeoutError:
            gate.waiting -= 1
            raise SlaTimeout("queue", round((time.monotonic() - t0) * 1000))
        gate.waiting -= 1
        gate.active += 1
        self.queue_wait_ms = round((time.monotonic() - t0) * 1000)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        gate.active -= 1
        gate.sem.release()


# --- Endpoint'ler ------------------------------------------------------------
@app.get("/health")
async def health() -> dict[str, Any]:
    """Kimlik doğrulama gerektirmeyen sağlık kontrolü (upstream'i de yoklar)."""
    upstream_ok = False
    detail = ""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{UPSTREAM_BASE_URL}/models")
            upstream_ok = resp.status_code == 200
            if not upstream_ok:
                detail = f"upstream status {resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
    return {
        "status": "ok" if upstream_ok else "degraded",
        "upstream": UPSTREAM_BASE_URL,
        "upstream_reachable": upstream_ok,
        "detail": detail,
        "queue": {
            "max_concurrency": gate.limit,
            "active": gate.active,
            "waiting": gate.waiting,
        },
        "sla_seconds": SLA_SECONDS,
        "api_keys": len(API_KEYS),
        "rate_limit_per_min": RATE_LIMIT_PER_MIN or "sınırsız",
        "logging": LOG_REQUESTS,
    }


@app.get("/v1/models")
async def list_models(client_name: str = Depends(get_client)) -> JSONResponse:
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        resp = await client.get(f"{UPSTREAM_BASE_URL}/models")
    return JSONResponse(status_code=resp.status_code, content=resp.json())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timing_headers(queue_wait_ms: int, upstream_ms: int, started: float) -> dict[str, str]:
    """İstemcinin kendi loglaması için zamanlama başlıkları."""
    return {
        "X-Queue-Wait-Ms": str(queue_wait_ms),
        "X-Upstream-Ms": str(upstream_ms),
        "X-Total-Ms": str(round((time.monotonic() - started) * 1000)),
        "X-SLA-Ms": str(round(SLA_SECONDS * 1000)),
    }


async def _proxy_chat(request: Request, path: str, client_name: str) -> Any:
    """OpenAI uyumlu POST isteklerini upstream'e iletir; streaming destekler."""
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Geçersiz JSON gövdesi.")

    # İstemci ne gönderirse göndersin doğru modeli biz koyarız → istemcinin
    # gerçek model adını/yolunu bilmesine gerek yok ("medgemma" gibi bir değer de olur).
    payload["model"] = DEFAULT_MODEL

    url = f"{UPSTREAM_BASE_URL}/{path}"
    is_stream = bool(payload.get("stream", False))
    started = time.monotonic()

    base_record: dict[str, Any] = {
        "ts": _now_iso(),
        "client": client_name,
        "path": f"/v1/{path}",
        "model": payload.get("model"),
        "stream": is_stream,
    }
    if LOG_CONTENT:
        base_record["request"] = payload

    if is_stream:
        async def event_stream():
            status_holder = {"code": 200}
            slot = _Slot(_remaining(started))
            rec = dict(base_record)
            async with slot:
                try:
                    up_budget = _remaining(started)
                    if up_budget <= 0:
                        raise SlaTimeout("upstream", round((time.monotonic() - started) * 1000))
                    async with httpx.AsyncClient(timeout=up_budget) as client:
                        async with client.stream("POST", url, json=payload) as resp:
                            status_holder["code"] = resp.status_code
                            async for chunk in resp.aiter_raw():
                                yield chunk
                except (SlaTimeout, httpx.TimeoutException):
                    status_holder["code"] = 504
                finally:
                    rec["status"] = status_holder["code"]
                    rec["queue_wait_ms"] = slot.queue_wait_ms
                    rec["duration_ms"] = round((time.monotonic() - started) * 1000)
                    _log_event(rec)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    rec = dict(base_record)
    try:
        slot = _Slot(_remaining(started))
        async with slot:
            up_budget = _remaining(started)
            if up_budget <= 0:
                raise SlaTimeout("upstream", round((time.monotonic() - started) * 1000))
            up_started = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=up_budget) as client:
                    resp = await client.post(url, json=payload)
            except httpx.TimeoutException:
                raise SlaTimeout("upstream", round((time.monotonic() - started) * 1000))
        upstream_ms = round((time.monotonic() - up_started) * 1000)
        data = resp.json()
        rec["status"] = resp.status_code
        rec["queue_wait_ms"] = slot.queue_wait_ms
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            rec["prompt_tokens"] = usage.get("prompt_tokens")
            rec["completion_tokens"] = usage.get("completion_tokens")
            rec["total_tokens"] = usage.get("total_tokens")
        if LOG_CONTENT:
            rec["response"] = data
        return JSONResponse(
            status_code=resp.status_code,
            content=data,
            headers=_timing_headers(slot.queue_wait_ms, upstream_ms, started),
        )
    except SlaTimeout as exc:
        rec["status"] = 504
        rec["error"] = f"timeout@{exc.stage}"
        raise
    except HTTPException as exc:
        rec["status"] = exc.status_code
        rec["error"] = exc.detail
        raise
    except Exception as exc:  # noqa: BLE001
        rec["status"] = 502
        rec["error"] = str(exc)
        raise HTTPException(status_code=502, detail=f"Upstream hatası: {exc}")
    finally:
        rec["duration_ms"] = round((time.monotonic() - started) * 1000)
        _log_event(rec)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, client_name: str = Depends(get_client)) -> Any:
    return await _proxy_chat(request, "chat/completions", client_name)


@app.post("/v1/completions")
async def completions(request: Request, client_name: str = Depends(get_client)) -> Any:
    return await _proxy_chat(request, "completions", client_name)


# --- Kolaylık endpoint'i: ham (kendi) JSON yapısını kabul eder --------------
async def _call_upstream(
    payload: dict[str, Any], client_name: str, label: str
) -> tuple[int, Any, dict[str, str]]:
    """Hazır bir chat payload'ını kuyruk + SLA + log ile upstream'e gönderir."""
    url = f"{UPSTREAM_BASE_URL}/chat/completions"
    started = time.monotonic()
    rec: dict[str, Any] = {
        "ts": _now_iso(), "client": client_name, "path": label,
        "model": payload.get("model"), "stream": False,
    }
    if LOG_CONTENT:
        rec["request"] = payload
    try:
        slot = _Slot(_remaining(started))
        async with slot:
            up_budget = _remaining(started)
            if up_budget <= 0:
                raise SlaTimeout("upstream", round((time.monotonic() - started) * 1000))
            up_started = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=up_budget) as client:
                    resp = await client.post(url, json=payload)
            except httpx.TimeoutException:
                raise SlaTimeout("upstream", round((time.monotonic() - started) * 1000))
        upstream_ms = round((time.monotonic() - up_started) * 1000)
        data = resp.json()
        rec["status"] = resp.status_code
        rec["queue_wait_ms"] = slot.queue_wait_ms
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            rec["prompt_tokens"] = usage.get("prompt_tokens")
            rec["completion_tokens"] = usage.get("completion_tokens")
            rec["total_tokens"] = usage.get("total_tokens")
        if LOG_CONTENT:
            rec["response"] = data
        return resp.status_code, data, _timing_headers(slot.queue_wait_ms, upstream_ms, started)
    except SlaTimeout as exc:
        rec["status"] = 504
        rec["error"] = f"timeout@{exc.stage}"
        raise
    except HTTPException as exc:
        rec["status"] = exc.status_code
        rec["error"] = exc.detail
        raise
    except Exception as exc:  # noqa: BLE001
        rec["status"] = 502
        rec["error"] = str(exc)
        raise HTTPException(status_code=502, detail=f"Upstream hatası: {exc}")
    finally:
        rec["duration_ms"] = round((time.monotonic() - started) * 1000)
        _log_event(rec)


@app.post("/degerlendir")
async def degerlendir(request: Request, client_name: str = Depends(get_client)) -> Any:
    """Kendi JSON yapını olduğu gibi kabul eder ve JSON sonuç döner.

    Beklenen gövde:
      - ``Prompt`` (veya ``prompt``): sistem talimatı (zorunlu).
      - (opsiyonel) ``temperature``, ``max_tokens``: model ayarları.
      - Geri kalan tüm alanlar (hastaAd, tanilar, islemler, ...) değerlendirilecek
        veri olarak modele iletilir.

    Cevap: modelin ürettiği JSON nesnesi doğrudan döner (skor, risk, ...).
    """
    try:
        body: Any = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Geçersiz JSON gövdesi.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Gövde bir JSON nesnesi olmalı.")

    data = dict(body)
    prompt = data.pop("Prompt", None) or data.pop("prompt", None)
    if not prompt or not str(prompt).strip():
        raise HTTPException(status_code=400, detail="'Prompt' alanı gerekli.")

    temperature = data.pop("temperature", 0)
    max_tokens = data.pop("max_tokens", 1024)

    payload = {
        "model": DEFAULT_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": str(prompt)},
            {"role": "user", "content": "Aşağıdaki provizyonu değerlendir:\n"
                + json.dumps(data, ensure_ascii=False)},
        ],
    }

    status, resp, timing = await _call_upstream(payload, client_name, "/degerlendir")
    try:
        content = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return JSONResponse(status_code=status, content=resp, headers=timing)
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        parsed = {"_ham_cevap": content, "_uyari": "Model geçerli JSON döndürmedi."}
    return JSONResponse(status_code=status, content=parsed, headers=timing)
