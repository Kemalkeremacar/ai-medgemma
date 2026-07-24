#!/usr/bin/env python3
"""Belgesiz pilot koşusu — seçilmiş provizyon ID listesini kuyruğa alır ve özetler.

100'lük batch'ten bağımsızdır. Manifest JSON'dan ID okur veya --ids ile verilir.

Örnek:
  python scripts/run_docless_pilot.py \\
      --manifest data/pilots/belgesiz_pilot10_v1.json \\
      --enqueue --wait

  python scripts/run_docless_pilot.py --ids 2476721,2569510 --enqueue
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_API = "http://127.0.0.1:8020"
DEFAULT_JSONL = Path("/home/monassist1/GemmaApp/logs/provizyon-results.jsonl")


def _http_json(method: str, url: str, body: dict | None = None, timeout: float = 60) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _load_ids(args: argparse.Namespace) -> tuple[str, list[str]]:
    if args.ids:
        ids = [x.strip() for x in args.ids.split(",") if x.strip()]
        return "cli-ids", ids
    if not args.manifest:
        raise SystemExit("--manifest veya --ids gerekli")
    path = Path(args.manifest)
    if not path.is_absolute():
        path = ROOT / path
    manifest = json.loads(path.read_text(encoding="utf-8"))
    ids = list(manifest.get("provizyon_ids") or [])
    if not ids and manifest.get("cases"):
        ids = [c["id"] for c in manifest["cases"]]
    pilot_id = str(manifest.get("pilot_id") or path.stem)
    return pilot_id, ids


def _clear_attempts(ids: list[str]) -> int:
    """Power-cut / önceki koşudan kalan attempts anahtarlarını sil (yeniden enqueue için)."""
    try:
        import redis
    except ImportError:
        return 0
    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    n = 0
    for pid in ids:
        if r.delete(f"provizyon:attempts:{pid}"):
            n += 1
    return n


def _enqueue(api: str, ids: list[str], *, clear_attempts: bool) -> dict:
    if clear_attempts:
        cleared = _clear_attempts(ids)
    else:
        cleared = 0
    queued: list[str] = []
    errors: list[dict[str, str]] = []
    for pid in ids:
        try:
            data = _http_json(
                "POST",
                f"{api.rstrip('/')}/provizyon/intake-db",
                {
                    "provizyon_id": pid,
                    "enqueue": True,
                    "skip_documents": True,
                },
            )
            queued.extend(data.get("queued_ids") or [pid])
        except Exception as exc:  # noqa: BLE001 — pilot CLI
            errors.append({"provizyon_id": pid, "error": str(exc)})
    return {"queued": queued, "errors": errors, "attempts_cleared": cleared}


def _wait(api: str, ids: list[str], jsonl: Path, baseline: int, timeout_s: int) -> list[dict]:
    want = set(ids)
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            depth = (_http_json("GET", f"{api.rstrip('/')}/queue/stats").get("depth") or {})
            pending = int(depth.get("pending") or 0)
            processing = int(depth.get("processing") or 0)
        except Exception:
            pending = processing = -1
        rows = []
        if jsonl.exists():
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        batch = [r for r in rows[baseline:] if r.get("provizyon_id") in want]
        done_ids = {r["provizyon_id"] for r in batch}
        print(
            f"t={int(time.time()-start)}s pending={pending} processing={processing} "
            f"pilot_done={len(done_ids)}/{len(want)}",
            flush=True,
        )
        if len(done_ids) >= len(want) and pending == 0 and processing == 0:
            # last write wins per id
            by_id: dict[str, dict] = {}
            for r in batch:
                by_id[r["provizyon_id"]] = r
            return [by_id[i] for i in ids if i in by_id]
        time.sleep(20)
    raise SystemExit(f"timeout: pilot tamamlanmadı ({timeout_s}s)")


def _summarize(rows: list[dict], *, pilot_id: str, ids: list[str]) -> dict:
    by_id = {r["provizyon_id"]: r for r in rows}
    ordered = [by_id[i] for i in ids if i in by_id]
    karar = Counter(r.get("nihai_karar") for r in ordered)
    tani = Counter((r.get("layers") or {}).get("tani_kurali") for r in ordered)
    med = Counter((r.get("layers") or {}).get("medgemma") for r in ordered)
    cases = []
    for r in ordered:
        L = r.get("layers") or {}
        cases.append(
            {
                "provizyon_id": r.get("provizyon_id"),
                "nihai_karar": r.get("nihai_karar"),
                "tani_kurali": L.get("tani_kurali"),
                "sut_kurali": L.get("sut_kurali"),
                "medgemma": L.get("medgemma"),
                "elapsed_ms": r.get("elapsed_ms"),
            }
        )
    return {
        "pilot_id": pilot_id,
        "n": len(ordered),
        "karar": dict(karar),
        "tani_kurali": dict(tani),
        "medgemma": dict(med),
        "cases": cases,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default="data/pilots/belgesiz_pilot10_v1.json")
    ap.add_argument("--ids", default="", help="Virgülle ID listesi (manifest yerine)")
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--jsonl", type=Path, default=DEFAULT_JSONL)
    ap.add_argument("--enqueue", action="store_true")
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--timeout-s", type=int, default=3600)
    ap.add_argument("--clear-attempts", action="store_true", default=True)
    ap.add_argument("--no-clear-attempts", action="store_false", dest="clear_attempts")
    ap.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Özet JSON yazılacak yol (varsayılan: data/pilots/<pilot_id>_report.json)",
    )
    args = ap.parse_args()

    pilot_id, ids = _load_ids(args)
    if not ids:
        raise SystemExit("ID listesi boş")
    print(f"pilot={pilot_id} n={len(ids)}")
    print("ids=", ", ".join(ids))

    baseline = 0
    if args.jsonl.exists():
        baseline = sum(1 for line in args.jsonl.open() if line.strip())

    if args.enqueue:
        result = _enqueue(args.api, ids, clear_attempts=args.clear_attempts)
        print(
            f"enqueued={len(result['queued'])} errors={len(result['errors'])} "
            f"attempts_cleared={result['attempts_cleared']}"
        )
        if result["errors"]:
            print("errors:", json.dumps(result["errors"], ensure_ascii=False, indent=2))
        stats = _http_json("GET", f"{args.api.rstrip('/')}/queue/stats")
        print("queue=", stats.get("depth"))

    if args.wait:
        if not args.enqueue:
            raise SystemExit("--wait için --enqueue gerekli")
        rows = _wait(args.api, ids, args.jsonl, baseline, args.timeout_s)
        summary = _summarize(rows, pilot_id=pilot_id, ids=ids)
        report = args.report
        if report is None:
            report = ROOT / "data" / "pilots" / f"{pilot_id}_report.json"
        elif not report.is_absolute():
            report = ROOT / report
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n=== PILOT REPORT ===")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print("wrote", report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as exc:
        print(f"API erişilemedi: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
