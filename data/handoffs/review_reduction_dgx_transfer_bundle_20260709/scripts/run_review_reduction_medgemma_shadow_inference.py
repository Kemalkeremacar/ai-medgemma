from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\Projects\ADDQ")
SUT_ROOT = ROOT / "SUT"
if str(SUT_ROOT) not in sys.path:
    sys.path.insert(0, str(SUT_ROOT))

try:
    from sut_engine.settings import (
        DEFAULT_MEDGEMMA_API_KEY,
        DEFAULT_MEDGEMMA_BASE_URL,
        DEFAULT_MEDGEMMA_MAX_TOKENS,
        DEFAULT_MEDGEMMA_MODEL,
        DEFAULT_MEDGEMMA_TEMPERATURE,
        DEFAULT_MEDGEMMA_TIMEOUT,
    )
except Exception:
    DEFAULT_MEDGEMMA_BASE_URL = "http://192.168.1.209:8000/v1"
    DEFAULT_MEDGEMMA_API_KEY = "sk-no-key"
    DEFAULT_MEDGEMMA_MODEL = "/raid/monassist1/medgemma_model_gptq_w4"
    DEFAULT_MEDGEMMA_TIMEOUT = 900
    DEFAULT_MEDGEMMA_TEMPERATURE = 0.1
    DEFAULT_MEDGEMMA_MAX_TOKENS = 1800


DEFAULT_HANDOFF_DIR = ROOT / "SUT/generated/shadow_quality_gate/review_reduction_medgemma_shadow_handoff_20260709"
DEFAULT_REQUESTS_PATH = DEFAULT_HANDOFF_DIR / "medgemma_review_reduction_shadow_requests.jsonl"
DEFAULT_RESPONSES_PATH = DEFAULT_HANDOFF_DIR / "medgemma_review_reduction_shadow_responses.jsonl"
DEFAULT_RAW_RESPONSES_PATH = DEFAULT_HANDOFF_DIR / "medgemma_review_reduction_shadow_raw_responses.jsonl"
DEFAULT_RUN_REPORT_PATH = DEFAULT_HANDOFF_DIR / "medgemma_review_reduction_shadow_inference_report.json"

SCHEMA_VERSION = "review_reduction_medgemma_shadow_inference.v1"
RESPONSE_SCHEMA_VERSION = "review_reduction_medgemma_shadow_response.v1"
SYSTEM_PROMPT = (
    "Türkçe yanıt ver. Verilmeyen klinik, ödeme veya resmi mevzuat bilgisini uydurma. "
    "Kullanıcının verdiği response schema'ya kesin uy ve sadece JSON object döndür."
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            if not isinstance(row, dict):
                raise ValueError(f"jsonl_line_{line_number}_root_not_object")
            rows.append(row)
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def safety_block(*, calls_medgemma: bool) -> dict[str, bool]:
    return {
        "writes_to_production_db": False,
        "writes_to_qdrant": False,
        "live_runtime_override": False,
        "auto_apply": False,
        "exports_case_level_rows": False,
        "calls_medgemma": calls_medgemma,
        "claims_human_admin_approval": False,
    }


def _extract_fenced_json(text: str) -> str:
    stripped = (text or "").strip()
    if "```json" in stripped:
        start = stripped.find("```json") + len("```json")
        end = stripped.find("```", start)
        if end != -1:
            return stripped[start:end].strip()
    if stripped.startswith("```"):
        stripped = stripped[3:]
        end = stripped.rfind("```")
        if end != -1:
            stripped = stripped[:end]
    return stripped.strip()


def extract_json_object_text(text: str) -> str:
    candidate = _extract_fenced_json(text)
    if candidate.startswith("{") and candidate.endswith("}"):
        return candidate

    start = candidate.find("{")
    if start == -1:
        raise ValueError("json_object_not_found")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return candidate[start : index + 1]

    raise ValueError("json_object_not_closed")


def parse_json_object(raw_text: str) -> dict[str, Any]:
    parsed = json.loads(extract_json_object_text(raw_text))
    if not isinstance(parsed, dict):
        raise ValueError("json_root_not_object")
    return parsed


def fallback_error_response(
    request: dict[str, Any],
    *,
    error_type: str,
    error_message: str,
    status: str = "error",
) -> dict[str, Any]:
    candidate = request.get("candidate") or {}
    return {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "request_id": request.get("request_id"),
        "code": candidate.get("code"),
        "medgemma_status": status,
        "clinical_plausibility": "insufficient_evidence",
        "diagnosis_cohort_safety": "insufficient_evidence",
        "mapping_assessment": "canonical_identity_uncertain",
        "confidence": 0.0,
        "confidence_label": "very_low",
        "eligible_for_human_expert_fast_track": False,
        "recommended_triage": "blocked_no_inference",
        "supported_prefixes": [],
        "prefixes_to_keep_review": list(candidate.get("top_diagnosis_prefixes_for_expert_review") or []),
        "missing_evidence": [error_type],
        "risk_notes": [error_message[:500]],
        "reasoning_summary": f"MedGemma shadow inference satırı tamamlanamadı: {error_type}.",
        "no_live_write_ack": True,
        "no_human_approval_claim_ack": True,
        "shadow_only_ack": True,
    }


def chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: int,
    temperature: float,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "replace")
    elapsed_seconds = round(time.time() - started, 3)
    parsed = json.loads(raw)
    content = parsed["choices"][0]["message"]["content"]
    metadata = {
        "elapsed_seconds": elapsed_seconds,
        "response_object": parsed.get("object"),
        "usage": parsed.get("usage") or {},
        "finish_reason": (parsed.get("choices") or [{}])[0].get("finish_reason"),
    }
    return str(content), metadata


def run_inference(
    *,
    requests_path: Path = DEFAULT_REQUESTS_PATH,
    responses_path: Path = DEFAULT_RESPONSES_PATH,
    raw_responses_path: Path = DEFAULT_RAW_RESPONSES_PATH,
    run_report_path: Path = DEFAULT_RUN_REPORT_PATH,
    base_url: str = DEFAULT_MEDGEMMA_BASE_URL,
    api_key: str = DEFAULT_MEDGEMMA_API_KEY,
    model: str = DEFAULT_MEDGEMMA_MODEL,
    timeout: int = DEFAULT_MEDGEMMA_TIMEOUT,
    temperature: float = DEFAULT_MEDGEMMA_TEMPERATURE,
    max_tokens: int = DEFAULT_MEDGEMMA_MAX_TOKENS,
    limit: int | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    requests = load_jsonl(requests_path)
    selected_requests = requests[:limit] if limit else requests
    if overwrite:
        for path in [responses_path, raw_responses_path]:
            if path.exists():
                path.unlink()
    elif responses_path.exists() or raw_responses_path.exists():
        raise FileExistsError(
            "responses_path or raw_responses_path already exists; pass --overwrite to replace existing output"
        )

    status_counts: Counter[str] = Counter()
    parse_status_counts: Counter[str] = Counter()
    total_elapsed = 0.0
    generated_at = now_iso()
    for index, request in enumerate(selected_requests, start=1):
        request_id = str(request.get("request_id") or "")
        raw_row: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at,
            "request_index": index,
            "request_id": request_id,
            "code": (request.get("candidate") or {}).get("code"),
            "medgemma_base_url": base_url,
            "medgemma_model": model,
            "safety": safety_block(calls_medgemma=True),
        }
        try:
            raw_text, metadata = chat_completion(
                base_url=base_url,
                api_key=api_key,
                model=model,
                prompt=str(request.get("prompt") or ""),
                timeout=timeout,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            raw_row["raw_text"] = raw_text
            raw_row["metadata"] = metadata
            total_elapsed += float(metadata.get("elapsed_seconds") or 0.0)
            try:
                parsed_response = parse_json_object(raw_text)
                parse_status = "parsed"
            except Exception as parse_exc:
                parsed_response = fallback_error_response(
                    request,
                    error_type="medgemma_response_parse_error",
                    error_message=str(parse_exc),
                )
                parse_status = "parse_error"
                raw_row["parse_error"] = str(parse_exc)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
            parsed_response = fallback_error_response(
                request,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            parse_status = "api_error"
            raw_row["api_error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}

        append_jsonl(raw_responses_path, raw_row)
        append_jsonl(responses_path, parsed_response)
        status_counts[str(parsed_response.get("medgemma_status") or "")] += 1
        parse_status_counts[parse_status] += 1
        print(
            json.dumps(
                {
                    "index": index,
                    "total": len(selected_requests),
                    "request_id": request_id,
                    "code": (request.get("candidate") or {}).get("code"),
                    "parse_status": parse_status,
                    "medgemma_status": parsed_response.get("medgemma_status"),
                    "confidence": parsed_response.get("confidence"),
                    "recommended_triage": parsed_response.get("recommended_triage"),
                },
                ensure_ascii=False,
            )
        )

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "mode": "review_reduction_medgemma_shadow_inference",
        "source_requests_path": str(requests_path),
        "responses_path": str(responses_path),
        "raw_responses_path": str(raw_responses_path),
        "run_report_path": str(run_report_path),
        "medgemma_base_url": base_url,
        "medgemma_model": model,
        "counts": {
            "requests_available": len(requests),
            "requests_attempted": len(selected_requests),
            "status_counts": dict(status_counts),
            "parse_status_counts": dict(parse_status_counts),
        },
        "timing": {
            "total_model_elapsed_seconds": round(total_elapsed, 3),
        },
        "safety": safety_block(calls_medgemma=True),
        "instructions": {
            "does_not_apply": True,
            "does_not_write_qdrant": True,
            "does_not_write_production_db": True,
            "does_not_claim_human_admin_approval": True,
            "next_gate": "Validate the generated responses with merge_review_reduction_medgemma_shadow_responses.py.",
        },
    }
    write_json(run_report_path, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MedGemma shadow inference for aggregate review-reduction requests."
    )
    parser.add_argument("--requests-path", type=Path, default=DEFAULT_REQUESTS_PATH)
    parser.add_argument("--responses-path", type=Path, default=DEFAULT_RESPONSES_PATH)
    parser.add_argument("--raw-responses-path", type=Path, default=DEFAULT_RAW_RESPONSES_PATH)
    parser.add_argument("--run-report-path", type=Path, default=DEFAULT_RUN_REPORT_PATH)
    parser.add_argument("--base-url", default=DEFAULT_MEDGEMMA_BASE_URL)
    parser.add_argument("--api-key", default=DEFAULT_MEDGEMMA_API_KEY)
    parser.add_argument("--model", default=DEFAULT_MEDGEMMA_MODEL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_MEDGEMMA_TIMEOUT)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_inference(
        requests_path=args.requests_path,
        responses_path=args.responses_path,
        raw_responses_path=args.raw_responses_path,
        run_report_path=args.run_report_path,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        timeout=args.timeout,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        limit=args.limit or None,
        overwrite=args.overwrite,
    )
    if args.print_summary:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"MedGemma review-reduction shadow inference written: {args.responses_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
