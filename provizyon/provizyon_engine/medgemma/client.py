"""Vision-yetenekli MedGemma istemcisi (karar sırası.txt Adım 5).

OpenAI-uyumlu vLLM endpoint'ine metin + görsel (multimodal) chat isteği gönderir.
Context taşması (32K) durumunda görsel sayısını kademeli azaltır; son çare metne düşer.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import settings


@dataclass
class MedGemmaConfig:
    base_url: str = settings.MEDGEMMA_BASE_URL
    api_key: str = settings.MEDGEMMA_API_KEY
    model: str = settings.MEDGEMMA_MODEL
    timeout: int = settings.MEDGEMMA_TIMEOUT
    temperature: float = settings.MEDGEMMA_TEMPERATURE
    max_tokens: int = settings.MEDGEMMA_MAX_TOKENS
    vision_mode: str = settings.MEDGEMMA_VISION_MODE


@dataclass
class MedGemmaCallMeta:
    vision_requested: int = 0
    vision_sent: int = 0
    vision_dropped: bool = False
    fallback_reason: str | None = None
    attempts: int = 0
    json_mode_used: bool = False


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(suffix, "image/png")


def _image_data_url(path: Path) -> str:
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{_guess_mime(path)};base64,{b64}"


def _is_context_overflow(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "maximum context length" in msg
        or "context length" in msg
        or "too many tokens" in msg
        or "input length" in msg
    )


def _is_transient_server_error(exc: Exception) -> bool:
    """vLLM MM cache assert / 500 gibi geçici sunucu hataları."""
    msg = str(exc).lower()
    return (
        "internal server error" in msg
        or "internalservererror" in msg
        or "error code: 500" in msg
        or "mm_hash" in msg
        or "expected a cached item" in msg
    )


def _should_retry_tier(exc: Exception, *, tier: int) -> bool:
    return _is_context_overflow(exc) or _is_transient_server_error(exc) or tier > 0


def _image_tiers(total: int) -> list[int]:
    """Denenecek görsel sayıları (azalan). 0 = yalnızca metin."""

    if total <= 0:
        return [0]
    tiers: list[int] = []
    for n in (total, max(total // 2, 4), 4, 2, 0):
        if n not in tiers:
            tiers.append(n)
    return tiers


class MedGemmaVisionClient:
    """Metin + görsel destekli MedGemma sohbet istemcisi."""

    def __init__(self, config: MedGemmaConfig | None = None) -> None:
        self.config = config or MedGemmaConfig()
        self.last_call_meta: MedGemmaCallMeta | None = None
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "openai paketi kurulu değil. Kurmak için: python -m pip install openai"
            ) from exc
        self.client = OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=self.config.timeout,
        )

    def chat(
        self,
        system_prompt: str,
        user_text: str,
        *,
        image_paths: list[Path] | None = None,
        json_mode: bool = True,
    ) -> str:
        """Metin (+ opsiyonel görsel) ile chat; ham içerik string döner."""

        image_paths = [p for p in (image_paths or []) if p.exists()]
        meta = MedGemmaCallMeta(vision_requested=len(image_paths))
        self.last_call_meta = meta

        send_images = bool(image_paths) and self.config.vision_mode != "off"
        if not send_images:
            meta.vision_sent = 0
            return self._request(system_prompt, user_text, [], json_mode=json_mode, meta=meta)

        last_error: Exception | None = None
        tiers = _image_tiers(len(image_paths))
        for tier_idx, tier in enumerate(tiers):
            subset = image_paths[:tier] if tier > 0 else []
            json_modes = (json_mode, False) if json_mode else (False,)
            abort_all = False
            for use_json in json_modes:
                for attempt in range(2):
                    try:
                        meta.attempts += 1
                        return self._request(
                            system_prompt,
                            user_text,
                            subset,
                            json_mode=use_json,
                            meta=meta,
                        )
                    except Exception as exc:
                        last_error = exc
                        meta.fallback_reason = str(exc)[:240]
                        if attempt == 0 and _is_transient_server_error(exc):
                            time.sleep(2.0)
                            continue
                        if not _should_retry_tier(exc, tier=tier):
                            abort_all = True
                        break
                if abort_all:
                    break
            if abort_all:
                break
            if tier_idx < len(tiers) - 1 and tier > 0:
                time.sleep(0.5)

        meta.vision_dropped = True
        meta.vision_sent = 0
        meta.attempts += 1
        try:
            return self._request(system_prompt, user_text, [], json_mode=False, meta=meta)
        except Exception:
            if last_error is not None:
                raise last_error
            raise

    def _request(
        self,
        system_prompt: str,
        user_text: str,
        image_paths: list[Path],
        *,
        json_mode: bool,
        meta: MedGemmaCallMeta,
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        attached = 0
        for path in image_paths:
            try:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(path)},
                    }
                )
                attached += 1
            except Exception:
                continue

        meta.vision_sent = attached
        meta.json_mode_used = json_mode
        if meta.vision_requested > 0 and attached == 0:
            meta.vision_dropped = True

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content if attached else user_text},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    def ping(self) -> bool:
        try:
            self.client.models.list()
            return True
        except Exception:
            return False
