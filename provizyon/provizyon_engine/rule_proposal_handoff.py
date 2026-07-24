"""DGX kural önerileri demo handoff — Provizyon üzerinden read-only köprü.

Handoff paketindeki ``app/data_store.py`` kullanılır; kaynak JSON/CSV yazılmaz.
``restricted/`` yalnız ``PROVIZYON_RULE_PROPOSAL_ENABLE_RAW=1`` iken açılır.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import settings

DEFAULT_HANDOFF_REL = Path("data/handoffs/kural-onerileri")
API_MOUNT = "/rule-proposal-demo"


class RuleProposalHandoffError(Exception):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


def resolve_handoff_root() -> Path:
    override = os.environ.get("PROVIZYON_RULE_PROPOSAL_HANDOFF_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (settings.GEMMA_ROOT / DEFAULT_HANDOFF_REL).resolve()


def raw_enabled() -> bool:
    return os.environ.get("PROVIZYON_RULE_PROPOSAL_ENABLE_RAW", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def static_dir() -> Path:
    root = resolve_handoff_root()
    path = root / "app" / "static"
    if not path.is_dir():
        raise RuleProposalHandoffError(
            f"Demo static klasörü yok: {path}",
            status_code=503,
        )
    return path


@lru_cache(maxsize=1)
def _load_data_store_class():
    root = resolve_handoff_root()
    module_path = root / "app" / "data_store.py"
    if not module_path.is_file():
        raise RuleProposalHandoffError(
            f"Handoff app/data_store.py yok: {module_path}. "
            "Zip'i açın veya PROVIZYON_RULE_PROPOSAL_HANDOFF_ROOT ayarlayın.",
            status_code=503,
        )
    spec = importlib.util.spec_from_file_location(
        "dgx_rule_proposal_data_store", module_path
    )
    if spec is None or spec.loader is None:
        raise RuleProposalHandoffError("data_store yüklenemedi", status_code=503)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DataStore


_store = None
_store_lock = threading.Lock()
_store_raw_flag: bool | None = None


def get_store():
    """Lazy singleton DataStore (read-only indexes)."""
    global _store, _store_raw_flag
    enable_raw = raw_enabled()
    with _store_lock:
        if _store is not None and _store_raw_flag == enable_raw:
            return _store
        root = resolve_handoff_root()
        if not (root / "HANDOFF_MANIFEST.json").is_file():
            raise RuleProposalHandoffError(
                f"Kural önerisi handoff yok: {root}. "
                "data/handoffs/kural-onerileri paketini yerleştirin "
                "veya PROVIZYON_RULE_PROPOSAL_HANDOFF_ROOT ayarlayın.",
                status_code=503,
            )
        DataStore = _load_data_store_class()
        _store = DataStore(root=root, enable_raw=enable_raw)
        _store_raw_flag = enable_raw
        return _store


def render_index_html() -> str:
    """Serve demo index with Provizyon API/static mount prefix."""
    index_path = static_dir() / "index.html"
    html = index_path.read_text(encoding="utf-8")
    html = html.replace(
        '<meta name="api-base" content="" />',
        f'<meta name="api-base" content="{API_MOUNT}" />',
    )
    html = html.replace('href="app.css"', f'href="{API_MOUNT}/app.css"')
    html = html.replace('src="app.js"', f'src="{API_MOUNT}/app.js"')
    return html


def read_static_file(name: str) -> tuple[bytes, str]:
    """Read a file from demo static dir; path traversal protected."""
    base = static_dir().resolve()
    candidate = (base / name).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise RuleProposalHandoffError("not_found", status_code=404) from exc
    if not candidate.is_file():
        raise RuleProposalHandoffError("not_found", status_code=404)
    suffix = candidate.suffix.lower()
    ctype = {
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }.get(suffix, "application/octet-stream")
    return candidate.read_bytes(), ctype


def store_call(method: str, **kwargs: Any) -> Any:
    store = get_store()
    fn = getattr(store, method)
    return fn(**kwargs)
