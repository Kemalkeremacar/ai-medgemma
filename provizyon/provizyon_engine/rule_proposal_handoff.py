"""DGX kural önerileri demo handoff — Provizyon üzerinden read-only köprü.

Handoff paketindeki ``app/data_store.py`` kullanılır; kaynak JSON/CSV yazılmaz.
``restricted/`` yalnız ``PROVIZYON_RULE_PROPOSAL_ENABLE_RAW=1`` iken açılır.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path
from typing import Any

from . import settings

DEFAULT_HANDOFF_REL = Path("data/handoffs/kural-onerileri")
API_MOUNT = "/rule-proposal-demo"
_HANDOFF_MODULE_NAMES = (
    "example_rules",
    "dgx_rule_proposal_data_store",
)


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


def _import_handoff_module(module_name: str, path: Path):
    """Load a handoff app module from disk (fresh), registering it in sys.modules."""
    if not path.is_file():
        raise RuleProposalHandoffError(
            f"Handoff modülü yok: {path}",
            status_code=503,
        )
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuleProposalHandoffError(
            f"Handoff modülü yüklenemedi: {path}",
            status_code=503,
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_data_store_class():
    """Load example_rules + data_store from handoff app/ (not a stale sys.modules copy)."""
    root = resolve_handoff_root()
    app_dir = root / "app"
    # Drop previous handoff modules so sibling imports resolve to this package.
    for name in _HANDOFF_MODULE_NAMES:
        sys.modules.pop(name, None)
    # example_rules first — data_store imports it by bare name.
    _import_handoff_module("example_rules", app_dir / "example_rules.py")
    store_mod = _import_handoff_module(
        "dgx_rule_proposal_data_store", app_dir / "data_store.py"
    )
    return store_mod.DataStore


_store = None
_store_lock = threading.Lock()
_store_raw_flag: bool | None = None
_store_root: Path | None = None


def reset_store() -> None:
    """Drop cached DataStore (e.g. after handoff code/data update)."""
    global _store, _store_raw_flag, _store_root
    with _store_lock:
        _store = None
        _store_raw_flag = None
        _store_root = None
        for name in _HANDOFF_MODULE_NAMES:
            sys.modules.pop(name, None)


def get_store():
    """Lazy singleton DataStore (read-only indexes)."""
    global _store, _store_raw_flag, _store_root
    enable_raw = raw_enabled()
    with _store_lock:
        root = resolve_handoff_root()
        if (
            _store is not None
            and _store_raw_flag == enable_raw
            and _store_root == root
        ):
            return _store
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
        _store_root = root
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
