#!/usr/bin/env python3
"""Local read-only demo server for DGX rule-proposal handoff."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from data_store import DataStore  # noqa: E402


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


class DemoHandler(BaseHTTPRequestHandler):
    store: DataStore

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        status, body, ctype = _json_bytes(payload, status)
        self._send(status, body, ctype)

    def _serve_static(self, rel: str) -> None:
        if rel in ("", "/"):
            rel = "/index.html"
        # Never expose restricted/ or data/ via static.
        candidate = (STATIC_DIR / rel.lstrip("/")).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send_json({"error": "not_found"}, 404)
            return
        if not candidate.is_file():
            self._send_json({"error": "not_found"}, 404)
            return
        ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype = f"{ctype}; charset=utf-8"
        self._send(200, candidate.read_bytes(), ctype)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        def q(name: str, default: str = "") -> str:
            vals = qs.get(name) or []
            return vals[0] if vals else default

        def q_int(name: str, default: int) -> int:
            try:
                return int(q(name, str(default)))
            except ValueError:
                return default

        if path.startswith("/api/"):
            self._handle_api(path, q, q_int)
            return
        self._serve_static(path)

    def _handle_api(self, path: str, q, q_int) -> None:
        store = self.store
        if path == "/api/summary":
            self._send_json(store.get_summary())
            return
        if path == "/api/help":
            self._send_json(
                {
                    "title": "Uzman Yardım Rehberi",
                    "format": "markdown",
                    "markdown": store.get_help_markdown(),
                }
            )
            return
        if path == "/api/proposals":
            self._send_json(
                store.list_proposals(
                    q=q("q"),
                    rule_type=q("ruleType"),
                    priority=q("priority"),
                    quality_flag=q("qualityFlag"),
                    completeness=q("completeness"),
                    liste_tipi=q("listeTipi"),
                    page=q_int("page", 1),
                    page_size=q_int("pageSize", 25),
                )
            )
            return
        if path.startswith("/api/proposals/") and path.endswith("/example-rules"):
            proposal_id = path[len("/api/proposals/") : -len("/example-rules")]
            detail = store.get_example_rules(proposal_id)
            if not detail:
                self._send_json({"error": "not_found"}, 404)
                return
            self._send_json(detail)
            return
        if path.startswith("/api/proposals/"):
            proposal_id = path[len("/api/proposals/") :]
            if "/" in proposal_id:
                self._send_json({"error": "not_found"}, 404)
                return
            detail = store.get_proposal(proposal_id)
            if not detail:
                self._send_json({"error": "not_found"}, 404)
                return
            self._send_json(detail)
            return
        if path == "/api/ai":
            self._send_json(
                store.list_ai(
                    q=q("q"),
                    status=q("status"),
                    stage=q("stage"),
                    outcome=q("outcome"),
                    page=q_int("page", 1),
                    page_size=q_int("pageSize", 25),
                )
            )
            return
        if path.startswith("/api/ai/"):
            packet_id = path[len("/api/ai/") :]
            detail = store.get_ai(packet_id)
            if not detail:
                self._send_json({"error": "not_found"}, 404)
                return
            self._send_json(detail)
            return
        if path.startswith("/api/raw/"):
            if not store.enable_raw:
                self._send_json(
                    {
                        "error": "raw_disabled",
                        "message": "Ham cevaplar kapalı. Sunucuyu --enable-raw ile başlatın.",
                    },
                    403,
                )
                return
            packet_id = path[len("/api/raw/") :]
            detail = store.get_raw(packet_id)
            if not detail:
                self._send_json({"error": "not_found"}, 404)
                return
            self._send_json(detail)
            return
        self._send_json({"error": "not_found"}, 404)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DGX kural önerileri demo sunucusu")
    p.add_argument("--host", default="127.0.0.1", help="Bind adresi (varsayılan 127.0.0.1)")
    p.add_argument("--port", type=int, default=8080, help="Port (varsayılan 8080)")
    p.add_argument(
        "--enable-raw",
        action="store_true",
        help="restricted/ ham model cevaplarına API erişimi aç",
    )
    p.add_argument(
        "--handoff-root",
        default=str(APP_DIR.parent),
        help="Handoff kök dizini",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.handoff_root).resolve()
    if not (root / "HANDOFF_MANIFEST.json").exists():
        print(f"Handoff kökü geçersiz: {root}", file=sys.stderr)
        return 2
    store = DataStore(root=root, enable_raw=args.enable_raw)
    DemoHandler.store = store
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    raw_state = "AÇIK" if args.enable_raw else "kapalı"
    print(
        f"Demo hazır: http://{args.host}:{args.port}/  (raw={raw_state}, proposals={len(store.proposals)})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDurduruldu.", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
