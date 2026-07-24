from __future__ import annotations

import json
import sys
from pathlib import Path

_LIB_ROOT = Path(__file__).resolve().parent.parent
if str(_LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIB_ROOT))

from .models import HUVRecord, SUTRecord
from .normalization import fold, split_sut_codes


def _header_map(header: list[str]) -> dict[str, int]:
    return {fold(name): index for index, name in enumerate(header)}


def _cell(row: list[str], mapping: dict[str, int], name: str) -> str:
    index = mapping.get(fold(name))
    if index is None or index >= len(row):
        return ""
    return row[index].strip()


def load_huv_records(path: Path) -> list[HUVRecord]:
    from sut_engine.xlsx_reader import read_xlsx_rows
    _, rows = read_xlsx_rows(path)
    if not rows:
        return []
    mapping = _header_map(rows[0])
    records: list[HUVRecord] = []
    for source_row, row in enumerate(rows[1:], start=2):
        huv_code = _cell(row, mapping, "Huv Kodu")
        if not huv_code:
            continue
        records.append(
            HUVRecord(
                huv_code=huv_code,
                name=_cell(row, mapping, "İşlem"),
                unit=_cell(row, mapping, "Birim"),
                section=_cell(row, mapping, "Bölüm"),
                direct_sut_code_raw=_cell(row, mapping, "Sut Kodu"),
                update_date=_cell(row, mapping, "Güncelleme Tarihi"),
                add_date=_cell(row, mapping, "Ekleme Tarihi"),
                top_title=_cell(row, mapping, "Üst Başlık"),
                note=_cell(row, mapping, "Not"),
                note_update_date=_cell(row, mapping, "Açıklama Güncelleme Tarihi"),
                status=_cell(row, mapping, "Durum"),
                source_row=source_row,
            )
        )
    return records


def load_sut_records(path: Path, source_list: str | None = None) -> list[SUTRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records: list[SUTRecord] = []
    seen: set[tuple[str, str]] = set()
    for item in data.get("records", []):
        code = str(item.get("code") or "").strip().upper()
        name = str(item.get("name") or "").strip()
        if not code or not name:
            continue
        item_source_list = str(item.get("source_list") or "")
        if source_list and item_source_list.upper() != source_list.upper():
            continue
        key = (code, item_source_list)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            SUTRecord(
                code=code,
                name=name,
                source_list=item_source_list,
                source_file=str(item.get("source_file") or ""),
                source_row=item.get("source_row") or "",
                description=str(item.get("description") or ""),
                raw=item,
            )
        )
    return records


def sut_by_code(records: list[SUTRecord]) -> dict[str, SUTRecord]:
    result: dict[str, SUTRecord] = {}
    for record in records:
        result.setdefault(record.code.upper(), record)
    return result


def direct_huv_sut_pairs(records: list[HUVRecord]) -> list[tuple[HUVRecord, str]]:
    pairs: list[tuple[HUVRecord, str]] = []
    for record in records:
        for sut_code in split_sut_codes(record.direct_sut_code_raw):
            pairs.append((record, sut_code))
    return pairs


def load_rules_by_source_code(path: Path) -> dict[str, list[dict]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, list[dict]] = {}
    for rule in data.get("rules", []):
        code = str(rule.get("source_code") or "").upper()
        if code:
            result.setdefault(code, []).append(rule)
    return result

