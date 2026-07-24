"""OCR sonuç önbelleği — kaynak dosya mtime ile invalidasyon."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .. import settings


def _cache_path(source: Path) -> Path:
    cache_dir = settings.WORK_DIR / "ocr_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    resolved = str(source.resolve())
    digest = hashlib.sha256(resolved.encode()).hexdigest()[:16]
    safe = source.name.replace("/", "_")
    return cache_dir / f"{digest}_{safe}.json"


_CACHE_VERSION = 3


def load_cached_ocr(source: Path) -> dict[int, str] | None:
    path = _cache_path(source)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != _CACHE_VERSION:
            return None
        if payload.get("mtime") != source.stat().st_mtime:
            return None
        pages = payload.get("pages") or {}
        return {int(k): str(v) for k, v in pages.items()}
    except Exception:
        return None


def save_cached_ocr(source: Path, pages: dict[int, str]) -> None:
    if not pages:
        return
    path = _cache_path(source)
    payload = {
        "version": _CACHE_VERSION,
        "mtime": source.stat().st_mtime,
        "source": str(source),
        "pages": {str(k): v for k, v in pages.items()},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
