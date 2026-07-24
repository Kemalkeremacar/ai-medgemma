from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


root = Path(__file__).resolve().parents[1]
manifest_path = root / "HANDOFF_MANIFEST.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
errors: list[str] = []
for item in manifest["files"]:
    path = root / item["path"]
    if not path.is_file():
        errors.append(f"missing:{item['path']}")
        continue
    if path.stat().st_size != item["bytes"]:
        errors.append(f"size:{item['path']}")
    if sha256_file(path) != item["sha256"]:
        errors.append(f"sha256:{item['path']}")
for path in root.rglob("*.json"):
    try:
        json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"json:{path.relative_to(root).as_posix()}:{exc}")
if errors:
    print("HANDOFF_INVALID")
    for error in errors:
        print(error)
    raise SystemExit(1)
print(
    "HANDOFF_OK "
    f"files={len(manifest['files'])} "
    f"snapshotPackets={manifest['snapshot']['completedPackets']} "
    "writesToDatabase=0 callsModel=0"
)
