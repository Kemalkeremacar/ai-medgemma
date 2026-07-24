from __future__ import annotations

import json
from pathlib import Path


class PipelineState:
    """Uzun SUT pipeline koşuları için basit checkpoint/resume state dosyası."""

    def __init__(self, path: Path):
        self.path = path
        self.data = {
            "processed": {},
            "errors": {},
        }
        self.load()

    def load(self) -> None:
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("processed", {})
            self.data.setdefault("errors", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def is_processed(self, item_id: str) -> bool:
        return item_id in self.data.get("processed", {})

    def mark_success(self, item_id: str, payload: dict | None = None) -> None:
        self.data.setdefault("processed", {})[item_id] = payload or {"status": "ok"}
        self.data.get("errors", {}).pop(item_id, None)
        self.save()

    def mark_error(self, item_id: str, error: str, payload: dict | None = None) -> None:
        self.data.setdefault("errors", {})[item_id] = {
            "error": error,
            "payload": payload or {},
        }
        self.save()

    @property
    def processed_count(self) -> int:
        return len(self.data.get("processed", {}))

    @property
    def error_count(self) -> int:
        return len(self.data.get("errors", {}))


def append_jsonl(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows
