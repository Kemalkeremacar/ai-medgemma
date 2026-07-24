#!/usr/bin/env python3
"""Belgeleri OCR/pipeline olmadan doğrudan vLLM MedGemma'ya gönderir.

Proje verilerine (Redis, OCR cache, provizyon sonuçları, kuyruk) yazmaz.

Örnekler:
  python scripts/medgemma_direct.py belge.pdf
  python scripts/medgemma_direct.py --folder /path/intake/3181514 --max-pages 3
  python scripts/medgemma_direct.py a.pdf b.jpg --prompt "Tanı kodlarını listele"
  python scripts/medgemma_direct.py a.pdf --json --output /tmp/mg.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from provizyon_engine import settings  # noqa: E402
from provizyon_engine.medgemma.direct import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_USER_PROMPT,
    SIGORTA_SYSTEM_PROMPT,
    SIGORTA_USER_PROMPT,
    DirectMedGemmaRequest,
    collect_document_paths,
    run_direct_medgemma,
    run_sigorta_batch,
    _extract_sigorta_summary,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Doğrudan vLLM MedGemma — pipeline/OCR/cache kullanmaz.",
    )
    parser.add_argument("files", nargs="*", help="PDF veya görsel dosya yolları")
    parser.add_argument("--folder", action="append", default=[], help="Belge klasörü (yalnızca üst seviye dosyalar)")
    parser.add_argument("--prompt", default=DEFAULT_USER_PROMPT, help="Kullanıcı sorusu / talimat")
    parser.add_argument("--system", default=DEFAULT_SYSTEM_PROMPT, help="System prompt")
    parser.add_argument("--json", dest="json_mode", action="store_true", help="JSON yanıt modu")
    parser.add_argument(
        "--sigorta",
        action="store_true",
        help="Sigorta ödeme kararı + skor JSON şeması (json_mode açık)",
    )
    parser.add_argument(
        "--batch-test",
        metavar="ROOT",
        help="5 test provizyon klasörünü sırayla değerlendir (ROOT=AI_PROVIZYONLARI veya intake)",
    )
    parser.add_argument("--max-pages", type=int, default=0, help="PDF başına max sayfa (0=tümü, sigorta:3)")
    parser.add_argument("--max-images", type=int, default=0, help="Modele gidecek max görsel (0=sınırsız)")
    parser.add_argument("--dpi", type=int, default=0, help="PDF render DPI (0=varsayılan ayar)")
    parser.add_argument("--output", "-o", help="Sonucu JSON dosyasına yaz (stdout yerine)")
    parser.add_argument("--ping-only", action="store_true", help="Yalnızca vLLM erişimini kontrol et")
    args = parser.parse_args()

    if args.ping_only:
        from provizyon_engine.medgemma.client import MedGemmaVisionClient

        ok = MedGemmaVisionClient().ping()
        print("vLLM:", "ok" if ok else "erişilemiyor")
        return 0 if ok else 1

    if args.batch_test:
        root = Path(args.batch_test)
        max_pages = args.max_pages if args.max_pages > 0 else 3
        max_images = args.max_images
        results = run_sigorta_batch(root, max_pages_per_pdf=max_pages, max_images=max_images)
        summary = []
        for r in results:
            label = r.meta.get("label", "?")
            row = {"label": label, "ok": r.ok, "error": r.error}
            if r.meta:
                row["belge_sayisi"] = r.meta.get("total_source_files")
                row["gorsel_gonderilen"] = r.meta.get("images_sent")
                row["belge_dusuruldu"] = r.meta.get("files_dropped") or []
            if r.ok:
                try:
                    parsed = json.loads(r.response)
                    row["genel_odeme_karari"] = parsed.get("genel_odeme_karari")
                    row["odeme_skoru"] = parsed.get("odeme_skoru")
                    row["guven"] = parsed.get("guven")
                    row["ozet_gerekce"] = parsed.get("ozet_gerekce")
                    row["islem_sayisi"] = len(parsed.get("islemler") or [])
                    row["iade_red"] = parsed.get("iade_red_bulgulari")
                    row["eksik_evrak"] = parsed.get("eksik_evrak_bulgulari")
                except json.JSONDecodeError:
                    row["parse_error"] = True
                    row.update(_extract_sigorta_summary(r.response))
            summary.append(row)
            print(f"\n{'='*60}\n{label}\n{'='*60}")
            if r.ok:
                print(r.response)
            else:
                print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))

        out_dir = settings.GEMMA_ROOT / "logs" / "medgemma-direct"
        out_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_file = out_dir / f"sigorta_batch_{stamp}.json"
        payload = {
            "summary": summary,
            "results": [r.to_dict() for r in results],
        }
        out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n\nÖzet kaydedildi: {out_file}", file=sys.stderr)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if all(r.ok for r in results) else 1

    paths = collect_document_paths(
        files=[Path(p) for p in args.files],
        folders=[Path(p) for p in args.folder],
    )
    if not paths:
        print("Hata: PDF/JPEG/PNG belgesi bulunamadı.", file=sys.stderr)
        return 2

    kwargs: dict = {
        "paths": paths,
        "user_prompt": args.prompt,
        "system_prompt": args.system,
        "json_mode": args.json_mode or args.sigorta,
        "max_pages_per_pdf": args.max_pages,
        "max_images": args.max_images,
    }
    if args.sigorta:
        kwargs["user_prompt"] = SIGORTA_USER_PROMPT
        kwargs["system_prompt"] = SIGORTA_SYSTEM_PROMPT
        if kwargs["max_pages_per_pdf"] <= 0:
            kwargs["max_pages_per_pdf"] = 3
    if args.dpi > 0:
        kwargs["dpi"] = args.dpi

    result = run_direct_medgemma(DirectMedGemmaRequest(**kwargs))
    payload = result.to_dict()

    if args.output:
        out = Path(args.output)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Yazıldı: {out}", file=sys.stderr)
    else:
        if result.ok:
            print(result.response)
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))

    if not result.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
