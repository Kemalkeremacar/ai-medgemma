from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class HUVRecord:
    huv_code: str
    name: str
    unit: str = ""
    section: str = ""
    direct_sut_code_raw: str = ""
    update_date: str = ""
    add_date: str = ""
    top_title: str = ""
    note: str = ""
    note_update_date: str = ""
    status: str = ""
    source_row: int = 0

    def text_context(self) -> str:
        return " ".join(
            part
            for part in (
                self.huv_code,
                self.name,
                self.section,
                self.top_title,
                self.note,
            )
            if part
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SUTRecord:
    code: str
    name: str
    source_list: str = ""
    source_file: str = ""
    source_row: int | str = ""
    description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def text_context(self) -> str:
        return " ".join(
            part
            for part in (
                self.code,
                self.name,
                self.source_list,
                self.description,
            )
            if part
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


@dataclass
class TaxonomyPath:
    level1_domain: str = "belirsiz"
    level2_specialty: str = "belirsiz"
    level3_service_group: str = "belirsiz"
    level4_procedure_family: str = "belirsiz"
    level5_variant: str = "genel"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class Candidate:
    sut_code: str
    sut_name: str
    score: float = 0.0
    sources: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    relation_hint: str = ""

    def add_signal(self, source: str, score: float, signal: str) -> None:
        if source not in self.sources:
            self.sources.append(source)
        self.score = max(self.score, score)
        if signal and signal not in self.signals:
            self.signals.append(signal)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CrosswalkEntry:
    huv_code: str = ""
    huv_name: str = ""
    sut_code: str = ""
    sut_name: str = ""
    relation_type: str = "needs_review"
    confidence: str = "low"
    confidence_score: float = 0.0
    decision_source: str = "not_evaluated"
    reason: str = ""
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    taxonomy: TaxonomyPath = field(default_factory=TaxonomyPath)
    huv_section: str = ""
    huv_top_title: str = ""
    huv_note: str = ""
    huv_source_row: int = 0
    sut_source_list: str = ""
    sut_source_row: int | str = ""

    def canonical_id(self) -> str:
        left = self.huv_code or "no-huv"
        right = self.sut_code or "no-sut"
        return f"service::{left}::{right}"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["canonical_id"] = self.canonical_id()
        data["taxonomy"] = self.taxonomy.to_dict()
        return data

