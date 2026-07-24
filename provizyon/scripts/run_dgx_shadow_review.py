"""DGX MedGemma Shadow Review Batch İşlemcisi.

Handoff paketindeki JSONL review isteklerini sırayla MedGemma'ya gönderir,
yanıtları response schema'ya göre doğrular ve çıktı JSONL dosyası üretir.

Kullanım:
    python provizyon/scripts/run_dgx_shadow_review.py <handoff_klasörü>

Örnek:
    python provizyon/scripts/run_dgx_shadow_review.py \
        data/handoffs/medgemma_dgx_handoff_final_24_big_medgemma_prefill/
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent.parent

def _apply_env_file() -> None:
    p = _PROJECT_ROOT / "services" / "vllm_medgemma" / "medgemma.env"
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _get_settings() -> dict[str, Any]:
    _apply_env_file()
    return {
        "base_url": os.getenv("VLLM_BASE_URL", "http://127.0.0.1:8000"),
        "model": os.getenv("VLLM_MODEL", "/raid/monassist1/medgemma_model_gptq_w4"),
        "timeout": int(os.getenv("VLLM_TIMEOUT_SECONDS", "120")),
    }


# ── Hafif vLLM HTTP istemcisi (vllm_client.py'nin async chat mantığı) ────────

class _Client:
    def __init__(self, base_url: str, model: str, timeout: int, max_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._timeout = httpx.Timeout(timeout)
        self._retries = max_retries

    async def chat(self, system: str, user: str, *, temperature: float = 0.1,
                   max_tokens: int = 2048) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as c:
                    r = await c.post(f"{self.base_url}/v1/chat/completions", json=payload)
                    if r.status_code >= 400:
                        raise RuntimeError(f"vLLM HTTP {r.status_code}: {r.text[:500]}")
                    return r.json()
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                if attempt < self._retries:
                    await asyncio.sleep(min(1 + attempt, 3))
        raise last_exc  # type: ignore[misc]

SCHEMA_VERSION = "shadow_medgemma_dgx_review_response.v1"

REQUIRED_FIELDS = [
    "schema_version",
    "request_id",
    "approval_packet_id",
    "preview_operation_id",
    "operation_type",
    "medgemma_status",
    "medgemma_validation_status",
    "clinical_alignment",
    "medgemma_confidence",
    "confidence_label",
    "safe_for_ai_shadow_approval",
    "recommended_action",
    "supports_observed_procedure",
    "supports_current_rule",
    "supports_proposed_target",
    "evidence_gaps",
    "reasoning_summary",
    "no_live_write_ack",
    "no_human_approval_claim_ack",
]

ALLOWED_VALUES: dict[str, list[str]] = {
    "medgemma_status": ["completed", "blocked", "error"],
    "medgemma_validation_status": ["valid", "invalid", "blocked"],
    "clinical_alignment": ["aligned", "partially_aligned", "not_aligned", "insufficient_evidence"],
    "confidence_label": ["very_low", "low", "medium", "high"],
    "recommended_action": [
        "ai_shadow_approve_candidate",
        "keep_pending",
        "reject_preview_operation",
        "request_official_source_validation",
        "needs_domain_expert_review",
    ],
    "supports_observed_procedure": ["yes", "no", "uncertain", "not_applicable"],
    "supports_current_rule": ["yes", "no", "uncertain", "not_applicable"],
    "supports_proposed_target": ["yes", "no", "uncertain", "not_applicable"],
}

SYSTEM_PROMPT = (
    "Sen bir MedGemma klinik shadow reviewer ve confidence scorer'sın. "
    "Görevin, verilen klinik review bağlamını değerlendirmek ve strict JSON formatında yanıt üretmek.\n\n"
    "KURALLAR:\n"
    "- Sadece strict JSON object döndür; markdown/code fence kullanma.\n"
    "- İnsan/admin/uzman onayı iddia etme; bu yalnızca AI shadow scoring çıktısıdır.\n"
    "- Qdrant, runtime, production veya canlı sisteme yazma/çağırma yapma.\n"
    "- safe_for_ai_shadow_approval yalnızca confidence >= 0.90, clinical_alignment 'aligned', "
    "kanıt boşluğu yoksa ve deterministik kısıtlamalar sağlanıyorsa true olabilir.\n"
    "- safe_for_ai_shadow_approval true ise recommended_action 'ai_shadow_approve_candidate' olmalı.\n"
    "- no_live_write_ack ve no_human_approval_claim_ack her zaman true olmalı.\n"
    "- reasoning_summary Türkçe ve kısa olmalı.\n"
    "- medgemma_confidence 0.0-1.0 arası bir sayı olmalı."
)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def build_blocked_response(req: dict[str, Any]) -> dict[str, Any]:
    """Inference başarısız olduğunda sözleşmeye uygun blocked yanıt üret."""
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": req["request_id"],
        "approval_packet_id": req["approval_packet_id"],
        "preview_operation_id": req["preview_operation_id"],
        "operation_type": req["operation_type"],
        "medgemma_status": "blocked",
        "medgemma_validation_status": "blocked",
        "clinical_alignment": "insufficient_evidence",
        "medgemma_confidence": 0.0,
        "confidence_label": "very_low",
        "safe_for_ai_shadow_approval": False,
        "recommended_action": "keep_pending",
        "supports_observed_procedure": "uncertain",
        "supports_current_rule": "not_applicable",
        "supports_proposed_target": "uncertain",
        "evidence_gaps": ["MedGemma inference başarısız veya bloke oldu"],
        "reasoning_summary": "MedGemma inference çalıştırılamadı; blocked yanıt döndürülüyor.",
        "no_live_write_ack": True,
        "no_human_approval_claim_ack": True,
    }


def validate_and_fix(resp: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
    """Yanıtı doğrula, kimlik alanlarını enjekte et ve hard constraint'leri uygula."""

    resp["schema_version"] = SCHEMA_VERSION
    resp["request_id"] = req["request_id"]
    resp["approval_packet_id"] = req["approval_packet_id"]
    resp["preview_operation_id"] = req["preview_operation_id"]
    resp["operation_type"] = req["operation_type"]
    resp["no_live_write_ack"] = True
    resp["no_human_approval_claim_ack"] = True

    for field, allowed in ALLOWED_VALUES.items():
        if field in resp and resp[field] not in allowed:
            log(f"  UYARI: {field}='{resp[field]}' geçersiz, düzeltiliyor")
            if field == "clinical_alignment":
                resp[field] = "insufficient_evidence"
            elif field == "confidence_label":
                resp[field] = "very_low"
            elif field == "medgemma_status":
                resp[field] = "completed"
            elif field == "medgemma_validation_status":
                resp[field] = "valid"
            elif field == "recommended_action":
                resp[field] = "keep_pending"
            else:
                resp[field] = allowed[-1]

    conf = resp.get("medgemma_confidence")
    if not isinstance(conf, (int, float)) or conf < 0.0 or conf > 1.0:
        log(f"  UYARI: medgemma_confidence={conf} geçersiz, 0.0 yapılıyor")
        resp["medgemma_confidence"] = 0.0
        resp["safe_for_ai_shadow_approval"] = False

    if resp.get("safe_for_ai_shadow_approval") is True:
        conf_val = resp.get("medgemma_confidence", 0.0)
        alignment = resp.get("clinical_alignment")
        action = resp.get("recommended_action")

        if conf_val < 0.90 or alignment != "aligned":
            log(f"  UYARI: safe_for_ai_shadow_approval=true ama confidence={conf_val}, "
                f"alignment={alignment} → false'a çevriliyor")
            resp["safe_for_ai_shadow_approval"] = False
            if action == "ai_shadow_approve_candidate":
                resp["recommended_action"] = "keep_pending"

        if resp.get("safe_for_ai_shadow_approval") is True:
            resp["recommended_action"] = "ai_shadow_approve_candidate"

    if not isinstance(resp.get("evidence_gaps"), list):
        resp["evidence_gaps"] = []

    if not isinstance(resp.get("reasoning_summary"), str) or not resp["reasoning_summary"]:
        resp["reasoning_summary"] = "Model çıktısından reasoning bilgisi alınamadı."

    for field in REQUIRED_FIELDS:
        if field not in resp:
            log(f"  UYARI: eksik alan '{field}', varsayılan atanıyor")
            if field in ALLOWED_VALUES:
                resp[field] = ALLOWED_VALUES[field][-1]
            elif field == "medgemma_confidence":
                resp[field] = 0.0
            elif field == "safe_for_ai_shadow_approval":
                resp[field] = False
            elif field == "evidence_gaps":
                resp[field] = []
            elif field == "reasoning_summary":
                resp[field] = "Eksik alan nedeniyle varsayılan atandı."
            else:
                resp[field] = ""

    return resp


def extract_content_text(api_response: dict[str, Any]) -> str:
    """vLLM API yanıtından content text'ini çıkar."""
    choices = api_response.get("choices", [])
    if not choices:
        raise ValueError("API yanıtında choices boş")
    return choices[0].get("message", {}).get("content", "")


async def process_request(
    client: _Client, req: dict[str, Any], idx: int, total: int
) -> dict[str, Any]:
    """Tek bir review isteğini işle."""

    request_id = req["request_id"]
    prompt_text = req.get("prompt", "")
    if not prompt_text:
        log(f"  [{idx}/{total}] {request_id}: prompt alanı boş → blocked")
        return build_blocked_response(req)

    try:
        t0 = time.monotonic()
        api_resp = await client.chat(
            system=SYSTEM_PROMPT,
            user=prompt_text,
            temperature=0.1,
            max_tokens=2048,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        raw_text = extract_content_text(api_resp)
        if not raw_text.strip():
            log(f"  [{idx}/{total}] {request_id}: boş yanıt → blocked ({elapsed_ms}ms)")
            return build_blocked_response(req)

        resp = json.loads(raw_text)
        if not isinstance(resp, dict):
            log(f"  [{idx}/{total}] {request_id}: JSON dict değil → blocked ({elapsed_ms}ms)")
            return build_blocked_response(req)

        resp = validate_and_fix(resp, req)
        conf = resp.get("medgemma_confidence", 0.0)
        alignment = resp.get("clinical_alignment", "?")
        action = resp.get("recommended_action", "?")
        log(f"  [{idx}/{total}] {request_id}: tamamlandı "
            f"confidence={conf:.2f} alignment={alignment} action={action} ({elapsed_ms}ms)")
        return resp

    except json.JSONDecodeError as e:
        log(f"  [{idx}/{total}] {request_id}: JSON parse hatası: {e} → blocked")
        return build_blocked_response(req)
    except Exception as e:
        log(f"  [{idx}/{total}] {request_id}: hata: {e} → blocked")
        return build_blocked_response(req)


async def run(handoff_dir: Path) -> None:
    input_path = handoff_dir / "dgx_medgemma_review_requests.jsonl"
    output_path = handoff_dir / "dgx_medgemma_review_responses.jsonl"

    if not input_path.is_file():
        log(f"HATA: Girdi dosyası bulunamadı: {input_path}")
        sys.exit(1)

    requests: list[dict[str, Any]] = []
    for line in input_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            requests.append(json.loads(line))

    total = len(requests)
    log(f"Toplam {total} istek okundu: {input_path}")

    done_ids: set[str] = set()
    if output_path.is_file():
        for line in output_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    done_ids.add(json.loads(line)["request_id"])
                except (json.JSONDecodeError, KeyError):
                    pass
        if done_ids:
            log(f"Devam modu: {len(done_ids)} istek zaten işlenmiş, atlanacak")

    pending = [r for r in requests if r["request_id"] not in done_ids]
    if not pending:
        log("Tüm istekler zaten işlenmiş. Çıkılıyor.")
        return

    log(f"İşlenecek istek sayısı: {len(pending)}")

    settings = _get_settings()
    client = _Client(
        base_url=settings["base_url"],
        model=settings["model"],
        timeout=settings["timeout"],
        max_retries=2,
    )
    log(f"vLLM bağlantısı: {settings['base_url']} model={settings['model']}")

    t_start = time.monotonic()
    with open(output_path, "a", encoding="utf-8") as fout:
        for i, req in enumerate(pending, start=len(done_ids) + 1):
            resp = await process_request(client, req, i, total)
            fout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            fout.flush()

    elapsed_total = time.monotonic() - t_start
    log(f"\nTamamlandı: {len(pending)} istek işlendi, "
        f"toplam süre: {elapsed_total:.1f}s")
    log(f"Çıktı dosyası: {output_path}")

    final_count = sum(
        1
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    log(f"Çıktı satır sayısı: {final_count}/{total}")


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Kullanım: python {sys.argv[0]} <handoff_klasörü>", file=sys.stderr)
        sys.exit(1)

    handoff_dir = Path(sys.argv[1])
    if not handoff_dir.is_dir():
        print(f"HATA: Klasör bulunamadı: {handoff_dir}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(handoff_dir))


if __name__ == "__main__":
    main()
