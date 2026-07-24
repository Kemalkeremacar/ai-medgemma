#!/usr/bin/env python3
"""Tam orkestratör + MedGemma ile gerçek provizyon klasörlerini çalıştırır."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from provizyon_engine import settings
from provizyon_engine.intake.folder_intake import build_job_from_folder
from provizyon_engine.orchestrator import OrchestratorConfig, ProvizyonOrchestrator


def _layer_summary(result) -> dict:
    out = {}
    for name in ("belge_hasta", "zorunlu_evrak", "tani_kurali", "sut_tani_kurali", "sut_kurali"):
        layer = getattr(result, name, None)
        if layer:
            out[name] = {"status": layer.status.value, "message": layer.message[:200]}
    if result.medgemma:
        mg = result.medgemma
        out["medgemma"] = {
            "guven": mg.guven,
            "islem_belge_destekli": mg.islem_belge_destekli,
            "tani_belge_destekli": mg.tani_belge_destekli,
            "manuel_inceleme_gerekli": mg.manuel_inceleme_gerekli,
            "gerekce": (mg.gerekce or "")[:200],
        }
    else:
        out["medgemma"] = None
    return out


def run_folder(folder: Path, *, persistence: bool) -> dict:
    t0 = time.time()
    job = build_job_from_folder(folder)
    config = OrchestratorConfig(
        enable_diagnosis=True,
        enable_sut_rules=True,
        enable_medgemma=True,
        enable_persistence=persistence,
        use_qdrant_rag=True,
        include_vision=True,
    )
    orch = ProvizyonOrchestrator(config=config)
    result = orch.run(job)
    elapsed = round(time.time() - t0, 1)
    return {
        "folder": folder.name,
        "provizyon_id": result.provizyon_id,
        "hasta": job.patient_name,
        "elapsed_sec": elapsed,
        "status": result.status.value,
        "nihai_karar": result.nihai_karar.value,
        "gerekce": result.gerekce,
        "warnings": result.warnings,
        "asamalar": _layer_summary(result),
        "medgemma_skipped": result.medgemma is None,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--persist", action="store_true", help="Qdrant'a yaz (varsayılan: kapalı)")
    ap.add_argument("--compare", type=Path, help="Deterministik audit JSON dosyası")
    ap.add_argument("folders", nargs="*")
    args = ap.parse_args(argv)

    folders: list[Path] = []
    if args.all:
        folders = sorted(p for p in settings.INTAKE_WATCH_DIR.iterdir() if p.is_dir())
    else:
        folders = [Path(f).resolve() for f in args.folders]

    det_map: dict[str, str] = {}
    if args.compare and args.compare.exists():
        for row in json.loads(args.compare.read_text(encoding="utf-8")):
            if "nihai_karar_deterministik" in row:
                det_map[row["folder"]] = row["nihai_karar_deterministik"]["karar"]

    results = []
    for folder in folders:
        print(f"Çalışıyor: {folder.name}...", flush=True)
        try:
            row = run_folder(folder, persistence=args.persist)
            if folder.name in det_map:
                row["deterministik_karar"] = det_map[folder.name]
                row["degisti"] = row["nihai_karar"] != det_map[folder.name]
            results.append(row)
        except Exception as exc:
            results.append({"folder": folder.name, "error": str(exc)})

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*70}")
        print("TAM ORKESTRATÖR + MedGemma SONUÇLARI")
        print(f"{'='*70}")
        for r in results:
            if "error" in r:
                print(f"\n{r['folder']}: HATA — {r['error']}")
                continue
            det = r.get("deterministik_karar", "?")
            chg = " (DEĞİŞTİ)" if r.get("degisti") else ""
            print(f"\n{r['folder']} | {r['hasta']} | {r['elapsed_sec']}s")
            print(f"  Deterministik: {det}")
            print(f"  MedGemma ile : {r['nihai_karar']}{chg}")
            print(f"  Gerekçe      : {r['gerekce'][:150]}")
            mg = r["asamalar"].get("medgemma")
            if mg:
                print(f"  MedGemma     : guven={mg['guven']}, islem={mg['islem_belge_destekli']}, tani={mg['tani_belge_destekli']}")
            elif r.get("medgemma_skipped"):
                print(f"  MedGemma     : ATLANDI")
            for w in r.get("warnings", [])[:3]:
                print(f"  Uyarı        : {w}")

    out = Path(settings.INTAKE_WATCH_DIR.parent.parent / "logs" / f"full-orchestrator-{time.strftime('%Y%m%d')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nKaydedildi: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
