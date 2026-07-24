"""Demo provizyon fixture'larını yükler ve kuyruğa alır."""

from __future__ import annotations

import json
from pathlib import Path

from provizyon_engine.models import ProvizyonJob

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"


def list_fixture_ids() -> list[str]:
    if not FIXTURES_ROOT.is_dir():
        return []
    return sorted(
        p.name for p in FIXTURES_ROOT.iterdir() if p.is_dir() and (p / "job.json").is_file()
    )


def load_job_from_fixture(fixture_id: str) -> ProvizyonJob:
    folder = FIXTURES_ROOT / fixture_id
    job_path = folder / "job.json"
    if not job_path.is_file():
        raise FileNotFoundError(f"Fixture job.json yok: {job_path}")
    data = json.loads(job_path.read_text(encoding="utf-8"))
    docs = data.get("documents") or []
    for doc in docs:
        rel = doc.get("path")
        if rel and not Path(rel).is_absolute():
            doc["path"] = str((folder / rel).resolve())
    return ProvizyonJob.model_validate(data)


def enqueue_fixtures(queue) -> list[str]:
    ids: list[str] = []
    for fixture_id in list_fixture_ids():
        job = load_job_from_fixture(fixture_id)
        added = queue.enqueue(job.provizyon_id, job.model_dump(mode="json"))
        if added:
            ids.append(job.provizyon_id)
    return ids
