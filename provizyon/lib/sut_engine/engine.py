from __future__ import annotations

import json
from pathlib import Path


def load_index(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _norm(value: str) -> str:
    return " ".join((value or "").casefold().split())


def find_records_by_code(index: dict, code: str) -> list[dict]:
    wanted = _norm(code)
    matches: list[dict] = []
    for record in index.get("records", []):
        if _norm(record.get("code", "")) == wanted:
            matches.append(record)
            continue
        fields = record.get("fields", {})
        if any(_norm(value) == wanted for value in fields.values()):
            matches.append(record)
    return matches


def search_records(index: dict, query: str, limit: int = 20) -> list[dict]:
    terms = [term for term in _norm(query).split() if term]
    if not terms:
        return []

    scored: list[tuple[int, dict]] = []
    for record in index.get("records", []):
        text = _norm(record.get("text", ""))
        score = sum(1 for term in terms if term in text)
        if score:
            scored.append((score, record))

    scored.sort(key=lambda item: (item[0], item[1].get("source_list", "")), reverse=True)
    return [record for _, record in scored[:limit]]


def search_sections(index: dict, query: str, limit: int = 10) -> list[dict]:
    terms = [term for term in _norm(query).split() if term]
    if not terms:
        return []

    scored: list[tuple[int, dict]] = []
    for section in index.get("sections", []):
        text = _norm(section.get("text", ""))
        score = sum(1 for term in terms if term in text)
        if score:
            scored.append((score, section))

    scored.sort(key=lambda item: (item[0], -item[1].get("order", 0)), reverse=True)
    return [section for _, section in scored[:limit]]


def summarize_record(record: dict) -> str:
    lines = [
        f"Kod: {record.get('code')}",
        f"Ad: {record.get('name')}",
        f"Liste: {record.get('source_list')} ({record.get('source_file')} satır {record.get('source_row')})",
    ]
    if record.get("points"):
        lines.append(f"Puan/Fiyat Alanı: {record.get('points')}")
    if record.get("group"):
        lines.append(f"Grup: {record.get('group')}")
    if record.get("description"):
        lines.append(f"Açıklama: {record.get('description')}")

    rules = record.get("rules", [])
    if rules:
        lines.append("Kurallar:")
        for rule in rules:
            details = [rule.get("rule_type", "?")]
            if rule.get("target_codes"):
                details.append("hedef=" + ",".join(rule["target_codes"]))
            if rule.get("period"):
                details.append(f"period={rule.get('period')}")
            if rule.get("limit"):
                details.append(f"limit={rule.get('limit')}")
            lines.append(f"- {'; '.join(details)} :: {rule.get('text_quote')}")
    return "\n".join(lines)
