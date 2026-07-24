"""Doğrudan vLLM/MedGemma çağrısı — provizyon pipeline'ından tamamen bağımsız.

- Redis, kuyruk, OCR cache, Qdrant veya nihai karar motoruna yazmaz.
- PDF sayfaları yalnızca geçici dizinde görsele render edilir (metin/OCR çıkarımı yok).
- Görseller (PNG/JPEG vb.) olduğu gibi modele gönderilir.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import settings
from .client import MedGemmaConfig, MedGemmaVisionClient

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"})
PDF_SUFFIXES = frozenset({".pdf"})

DEFAULT_SYSTEM_PROMPT = (
    "Sen tıbbi belge okuma asistanısın. Kullanıcının sorusuna yalnızca verilen "
    "belge görsellerine dayanarak Türkçe yanıt ver. Emin olmadığın bilgileri uydurma."
)

DEFAULT_USER_PROMPT = (
    "Ekteki sağlık belgelerini incele. Hasta adı, tanılar, işlemler, tarihler ve "
    "klinik olarak dikkat çeken noktaları özetle."
)

SIGORTA_SYSTEM_PROMPT = (
    "Sen özel sağlık sigortası (TZH Vakfı / tamamlayıcı sağlık) provizyon ve fatura "
    "denetim uzmanısın. Ekteki belge görsellerine dayanarak her işlem ve genel fatura "
    "için ödeme uygunluğu değerlendirirsin. "
    "Değerlendirmede: tanı–işlem klinik uyumu, şikayet/epikriz–tetkik uyumu, belgede "
    "görünen önceki iade/red/ödenmez ifadeleri, yaş/cinsiyet endikasyonu, gereksiz "
    "tekrar/tetkik ve belge kanıt yeterliliğini dikkate al. "
    "Belge tutarlılığı tek başına ödeme onayı değildir. "
    "Emin olmadığın alanlarda skoru düşür, kararı belirsiz veya kısmen ödenir yap. "
    "SADECE geçerli JSON döndür; markdown veya açıklama ekleme."
)

SIGORTA_USER_PROMPT = """Ekteki provizyon belgelerini sigortacılık (ödeme denetimi) perspektifinden değerlendir.

KURALLAR:
- Yalnızca belgede gördüğün bilgileri kullan; ICD katalog listesi veya uydurma kod yazma.
- tanilar: belgede açıkça geçen en fazla 5 tanı kodu/metni.
- islemler: fatura/provizyon formunda görünen işlemler, en fazla 15 satır; tekrar eden satırları birleştir.
- Her işlem için odeme_karari: odenir | kismen_odenir | odenmez | belirsiz
- odeme_skoru 0–100 (100=eksiksiz ödenebilir, 0=kesinlikle ödenmez).
- Belgelerde iade/red/ödenmez yazışması varsa mutlaka belirt ve genel skoru düşür.
- Şikayet–tetkik veya tanı–işlem uyumsuzluğunda odenmez veya kismen_odenir de.
- eksik_evrak_bulgulari: epikriz, fatura, rapor, onam, kimlik vb. zorunlu/ beklenen evrak
  eksikse veya yalnızca kısmi görüldüyse burada listele; genel skoru düşür.

ZORUNLU JSON (kısa gerekçeler, max 15 işlem):
{
  "hasta": {"ad_soyad": null, "yas": null, "cinsiyet": null},
  "tanilar": [],
  "islemler": [
    {"kod": null, "ad": "", "odeme_karari": "belirsiz", "odeme_skoru": 0, "gerekce": ""}
  ],
  "genel_odeme_karari": "odenir|kismen_odenir|odenmez|belirsiz",
  "odeme_skoru": 0,
  "guven": "high|medium|low",
  "iade_red_bulgulari": [],
  "klinik_uyumsuzluklar": [],
  "eksik_evrak_bulgulari": [],
  "ozet_gerekce": ""
}"""

def _popup_context(folder: Path) -> str:
    """Popup PDF'den meta (OCR cache kullanmaz)."""

    try:
        from ..intake.folder_intake import build_job_from_folder

        job = build_job_from_folder(folder)
        procs = [f"{p.code} {p.name or ''}".strip() for p in job.procedures[:20]]
        return (
            "\n\n--- Popup form referansı (doğrulama için; belge ile çelişirse belgeye öncelik ver) ---\n"
            f"Provizyon: {job.provizyon_id}\n"
            f"Hasta: {job.patient_name or '?'}\n"
            f"Tanılar: {', '.join(job.diagnoses) or '?'}\n"
            f"İşlemler ({len(job.procedures)}): {'; '.join(procs)}\n"
        )
    except Exception:
        return ""


def _extract_sigorta_summary(response: str) -> dict[str, Any]:
    """JSON kesilmiş olsa bile özet alanları çıkarmaya çalışır."""

    import re

    summary: dict[str, Any] = {}
    for key, pat in (
        ("genel_odeme_karari", r'"genel_odeme_karari"\s*:\s*"([^"]+)"'),
        ("odeme_skoru", r'"odeme_skoru"\s*:\s*(\d+)'),
        ("guven", r'"guven"\s*:\s*"([^"]+)"'),
    ):
        m = re.search(pat, response)
        if m:
            summary[key] = int(m.group(1)) if key == "odeme_skoru" else m.group(1)
    m = re.search(r'"ozet_gerekce"\s*:\s*"([^"]*)"', response)
    if m:
        summary["ozet_gerekce"] = m.group(1)
    return summary


# Test seti: intake klasör adı = popup Provizyon No (URL ID= değil)
SIGORTA_TEST_FOLDERS: list[tuple[str, str]] = [
    ("3181514", "3181514"),
    ("3176847", "3176847"),
    ("3247054", "3247054"),
    ("3181844", "3181844"),
    ("3186164", "3186164"),
]


@dataclass
class DirectMedGemmaRequest:
    paths: list[Path]
    user_prompt: str = DEFAULT_USER_PROMPT
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    json_mode: bool = False
    max_pages_per_pdf: int = 0
    max_images: int = 12
    dpi: int = field(default_factory=lambda: settings.PDF_RENDER_DPI)
    label: str = ""
    max_tokens: int | None = None

    @classmethod
    def sigorta(
        cls,
        paths: list[Path],
        *,
        label: str = "",
        user_prompt: str | None = None,
        max_pages_per_pdf: int = 3,
        max_images: int = 0,
    ) -> DirectMedGemmaRequest:
        return cls(
            paths=paths,
            user_prompt=user_prompt or SIGORTA_USER_PROMPT,
            system_prompt=SIGORTA_SYSTEM_PROMPT,
            json_mode=True,
            max_pages_per_pdf=max_pages_per_pdf,
            max_images=max_images,
            max_tokens=4096,
            label=label,
        )


@dataclass
class DirectMedGemmaResult:
    ok: bool
    response: str
    image_paths: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "ok": self.ok,
            "response": self.response,
            "image_paths": self.image_paths,
            "source_files": self.source_files,
            "meta": self.meta,
            "error": self.error,
        }
        if self.meta.get("label"):
            out["label"] = self.meta["label"]
        return out


def collect_document_paths(
    *,
    files: list[Path] | None = None,
    folders: list[Path] | None = None,
) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        if not path.is_file():
            return
        suffix = path.suffix.lower()
        if suffix not in IMAGE_SUFFIXES and suffix not in PDF_SUFFIXES:
            return
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            out.append(path.resolve())

    for raw in files or []:
        add(Path(raw))
    for folder in folders or []:
        root = Path(folder)
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            add(path)
    return out


def _render_pdf_pages(pdf_path: Path, tmp_dir: Path, *, max_pages: int, dpi: int) -> list[Path]:
    try:
        import fitz  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("PyMuPDF (fitz) kurulu değil; PDF render edilemedi.") from exc

    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    images: list[Path] = []
    with fitz.open(str(pdf_path)) as pdf:
        page_count = pdf.page_count
        limit = page_count if max_pages <= 0 else min(page_count, max_pages)
        stem = pdf_path.stem[:48]
        for index in range(limit):
            page = pdf.load_page(index)
            pix = page.get_pixmap(matrix=matrix)
            if pix.n > 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            out = tmp_dir / f"{stem}_p{index + 1:04d}.png"
            pix.save(str(out))
            images.append(out)
    return images


def materialize_images(
    paths: list[Path],
    tmp_dir: Path,
    *,
    max_pages_per_pdf: int,
    max_images: int,
    dpi: int,
) -> tuple[list[Path], list[str], dict[str, Any]]:
    """Belgeleri modele gidecek görsel listesine çevirir (proje cache'i kullanılmaz)."""

    images: list[Path] = []
    sources: list[str] = []
    dropped_sources: list[str] = []
    partial_sources: list[str] = []
    cap = max_images if max_images > 0 else None

    for path in paths:
        if cap is not None and len(images) >= cap:
            dropped_sources.append(str(path))
            continue
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            dest = tmp_dir / f"{path.stem}{suffix}"
            if path.resolve() != dest.resolve():
                shutil.copy2(path, dest)
            images.append(dest)
            sources.append(str(path))
            continue
        if suffix not in PDF_SUFFIXES:
            continue

        remaining = None if cap is None else cap - len(images)
        pdf_max = max_pages_per_pdf
        if remaining is not None and (pdf_max <= 0 or pdf_max > remaining):
            pdf_max = remaining
        if remaining == 0:
            dropped_sources.append(str(path))
            continue

        rendered = _render_pdf_pages(
            path,
            tmp_dir,
            max_pages=pdf_max,
            dpi=dpi,
        )
        if not rendered:
            dropped_sources.append(str(path))
            continue
        try:
            import fitz  # noqa: PLC0415

            with fitz.open(str(path)) as pdf:
                total_pages = pdf.page_count
        except Exception:
            total_pages = len(rendered)
        requested_pages = total_pages if max_pages_per_pdf <= 0 else min(total_pages, max_pages_per_pdf)
        if cap is not None:
            requested_pages = min(requested_pages, remaining)
        if len(rendered) < requested_pages:
            partial_sources.append(str(path))
        images.extend(rendered)
        sources.append(str(path))

    if cap is not None and len(images) > cap:
        images = images[:cap]

    truncation = {
        "total_source_files": len(paths),
        "files_included": len(sources),
        "files_dropped": dropped_sources,
        "files_partial": partial_sources,
        "images_sent": len(images),
        "images_capped": bool(cap is not None and (
            dropped_sources or partial_sources or len(images) >= cap
        )),
        "max_images": max_images,
    }
    return images, sources, truncation


def run_direct_medgemma(request: DirectMedGemmaRequest) -> DirectMedGemmaResult:
    """Belgeleri OCR/pipeline olmadan doğrudan vLLM MedGemma'ya gönderir."""

    paths = [p.resolve() for p in request.paths]
    if not paths:
        return DirectMedGemmaResult(ok=False, response="", error="Gönderilecek belge yok.")

    with tempfile.TemporaryDirectory(prefix="medgemma_direct_") as tmp:
        tmp_dir = Path(tmp)
        try:
            image_paths, sources, truncation = materialize_images(
                paths,
                tmp_dir,
                max_pages_per_pdf=request.max_pages_per_pdf,
                max_images=request.max_images,
                dpi=request.dpi,
            )
        except Exception as exc:
            return DirectMedGemmaResult(
                ok=False,
                response="",
                source_files=[str(p) for p in paths],
                error=str(exc),
            )

        if not image_paths:
            return DirectMedGemmaResult(
                ok=False,
                response="",
                source_files=[str(p) for p in paths],
                error="Görsel üretilemedi (desteklenmeyen format veya boş PDF).",
            )

        client = MedGemmaVisionClient(
            MedGemmaConfig(
                max_tokens=request.max_tokens or settings.MEDGEMMA_MAX_TOKENS,
            )
        )
        if not client.ping():
            return DirectMedGemmaResult(
                ok=False,
                response="",
                image_paths=[str(p) for p in image_paths],
                source_files=sources,
                error=f"vLLM erişilemiyor: {settings.MEDGEMMA_BASE_URL}",
            )

        user_text = request.user_prompt.strip()
        if len(paths) > 1 or any(p.suffix.lower() in PDF_SUFFIXES for p in paths):
            user_text += (
                f"\n\n(Belge sayısı: {truncation['total_source_files']}; "
                f"modele gönderilen dosya: {truncation['files_included']}; "
                f"görsel: {len(image_paths)})"
            )
        if truncation["files_dropped"] or truncation["files_partial"]:
            dropped_names = ", ".join(Path(p).name for p in truncation["files_dropped"][:20])
            partial_names = ", ".join(Path(p).name for p in truncation["files_partial"][:20])
            user_text += (
                "\n\nUYARI — tüm belgeler modele gönderilemedi; eksik evrak değerlendirmesinde dikkate al:"
            )
            if dropped_names:
                user_text += f"\n- Hiç gönderilmeyen belgeler: {dropped_names}"
            if partial_names:
                user_text += f"\n- Kısmi gönderilen belgeler (sayfa sınırı): {partial_names}"

        try:
            raw = client.chat(
                request.system_prompt,
                user_text,
                image_paths=image_paths,
                json_mode=request.json_mode,
            )
        except Exception as exc:
            return DirectMedGemmaResult(
                ok=False,
                response="",
                image_paths=[str(p) for p in image_paths],
                source_files=sources,
                error=str(exc),
            )

        meta: dict[str, Any] = {
            "base_url": settings.MEDGEMMA_BASE_URL,
            "model": settings.MEDGEMMA_MODEL,
            "json_mode": request.json_mode,
            "temp_dir": str(tmp_dir),
            **truncation,
        }
        if request.label:
            meta["label"] = request.label
        call_meta = client.last_call_meta
        if call_meta is not None:
            meta["vision_requested"] = call_meta.vision_requested
            meta["vision_sent"] = call_meta.vision_sent
            meta["vision_dropped"] = call_meta.vision_dropped
            meta["attempts"] = call_meta.attempts

        response = raw
        if request.json_mode:
            try:
                response = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                meta["json_parse_warning"] = "Yanıt geçerli JSON değil; ham metin döndürüldü."

        return DirectMedGemmaResult(
            ok=True,
            response=response,
            image_paths=[str(p) for p in image_paths],
            source_files=sources,
            meta=meta,
        )


def run_sigorta_batch(
    root: Path,
    *,
    folders: list[tuple[str, str]] | None = None,
    max_pages_per_pdf: int = 3,
    max_images: int = 0,
) -> list[DirectMedGemmaResult]:
    """Birden fazla provizyon klasörünü sırayla değerlendirir (pipeline'a yazmaz)."""

    pairs = folders or SIGORTA_TEST_FOLDERS
    results: list[DirectMedGemmaResult] = []
    for folder_name, label in pairs:
        folder = root / folder_name
        paths = collect_document_paths(folders=[folder])
        if not paths:
            results.append(
                DirectMedGemmaResult(
                    ok=False,
                    response="",
                    error=f"Belge yok: {folder}",
                    meta={"label": label, "folder": folder_name},
                )
            )
            continue
        req = DirectMedGemmaRequest.sigorta(
            paths,
            label=f"{label} ({folder_name})",
            user_prompt=SIGORTA_USER_PROMPT + _popup_context(folder),
            max_pages_per_pdf=max_pages_per_pdf,
            max_images=max_images,
        )
        results.append(run_direct_medgemma(req))
    return results
