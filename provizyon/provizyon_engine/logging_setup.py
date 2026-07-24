"""Provizyon süreçleri için ortak log yapılandırması.

- Gürültülü HTTP istemci loglarını susturur (httpx / httpcore / qdrant).
- Dosyaya RotatingFileHandler yazar (boyut sınırlı).
- Konsola da aynı formatı basar (foreground / systemd uyumu).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ~5 MB × 5 yedek ≈ 30 MB üst sınır / worker
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5

_NOISY = (
    "httpx",
    "httpcore",
    "urllib3",
    "qdrant_client",
    "qdrant_client.http",
    "openai",
    "httpcore.connection",
    "httpcore.http11",
)


def configure_logging(
    *,
    process_name: str,
    log_file: Path | None = None,
    level: int = logging.INFO,
) -> None:
    """Kök logger'ı yapılandırır. Bir kez çağrılmalıdır."""

    root = logging.getLogger()
    if getattr(root, "_provizyon_configured", False):
        return

    root.setLevel(level)
    fmt = logging.Formatter(
        f"%(asctime)s [{process_name}] %(levelname)s %(message)s"
    )

    # Mevcut handler'ları temizle (basicConfig / uvicorn kalıntısı)
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(level)
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(level)
        root.addHandler(file_handler)

    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)

    root._provizyon_configured = True  # type: ignore[attr-defined]
