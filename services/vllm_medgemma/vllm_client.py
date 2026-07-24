"""vLLM (OpenAI-uyumlu) HTTP istemcisi.

Pipeline script'leri vLLM sunucusuna `/v1/chat/completions` çağrısı yapar.
Bu sınıf:
- HTTP timeout/retry mantığını tek yerde toplar
- `messages` formatını destekler (system+user veya direkt messages)
- İstenirse JSON mode (`response_format: json_object`) ile çıktıyı JSON'a zorlar
"""

from __future__ import annotations

import asyncio
import base64
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx


def image_to_data_url(path: Path | str, *, max_side: int = 1280) -> str:
    """Görseli okuyup OpenAI/vLLM uyumlu `data:image/...;base64,...` üretir.

    Pillow varsa uzun kenarı `max_side` ile sınırlayıp JPEG sıkıştırır (token tasarrufu).
    Yoksa ham dosyayı base64 kodlar.
    """

    p = Path(path)
    raw = p.read_bytes()
    try:
        from PIL import Image

        im = Image.open(BytesIO(raw))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        m = max(w, h)
        if m > max_side:
            s = max_side / float(m)
            im = im.resize((int(w * s), int(h * s)), Image.Resampling.LANCZOS)
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=85, optimize=True)
        b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        b64 = base64.standard_b64encode(raw).decode("ascii")
        ext = p.suffix.lower()
        mt = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
        return f"data:{mt};base64,{b64}"


class VllmClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 120,
        *,
        max_retries: int = 2,
    ):
        """Yeni istemci oluştur.

        - **base_url**: vLLM API kökü (örn: `http://127.0.0.1:8000`)
        - **model**: vLLM'in beklediği model adı/path'i
        - **timeout_seconds**: istek başına timeout (saniye)
        - **max_retries**: network/timeout hatalarında tekrar sayısı
        """

        self.base_url = base_url.rstrip("/")
        self.model = model
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_retries = max_retries

    async def chat_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        """`/v1/chat/completions` çağrısı (messages ile)."""

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    t0 = time.monotonic()
                    r = await client.post(
                        f"{self.base_url}/v1/chat/completions", json=payload
                    )
                    if r.status_code >= 400:
                        try:
                            err = r.json()
                            detail = err.get("message") or err.get("error") or r.text
                        except Exception:
                            detail = r.text
                        raise RuntimeError(f"vLLM HTTP {r.status_code}: {detail}")
                    data = r.json()
                    data["_latency_ms"] = int((time.monotonic() - t0) * 1000)
                    return data
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                if attempt >= self._max_retries:
                    break
                await asyncio.sleep(min(1 + attempt, 3))
        assert last_exc is not None
        raise last_exc

    async def chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.1,
        max_tokens: int = 512,
        json_mode: bool = False,
        image_paths: list[Path] | None = None,
        max_image_side: int = 1280,
    ) -> dict[str, Any]:
        """Kolay kullanım: system + user (ve isteğe bağlı sayfa görselleri) → messages.

        `image_paths` doluysa user mesajı OpenAI tarzı çok parçalı içerik olur
        (metin + `image_url` data URI). vLLM sunucusundaki model **görüntü girebilen**
        (vision) bir VLM olmalıdır; aksi halde API hata dönebilir.
        """

        if not image_paths:
            return await self.chat_messages(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )

        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for img in image_paths:
            url = image_to_data_url(img, max_side=max_image_side)
            content.append({"type": "image_url", "image_url": {"url": url}})

        return await self.chat_messages(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

