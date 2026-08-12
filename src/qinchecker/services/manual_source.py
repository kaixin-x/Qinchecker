"""单植物人工来源文本的独立存储及字段解析适配。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from json import JSONDecodeError
from pathlib import Path

from qinchecker.models.review import FieldKey, FieldProposal, ReviewState, SpeciesRecord
from qinchecker.models.source import FetchStatus, SourceSection, SourceSnapshot
from qinchecker.services.parsing import SourceParser


COUNTY_FIELDS = {
    FieldKey.COUNTIES,
    FieldKey.ENDEMIC_QINLING,
    FieldKey.SOUTH_NORTH,
    FieldKey.QINLING_DISTRIBUTION,
}


@dataclass(slots=True)
class ManualSection:
    text: str = ""
    source_name: str = ""
    source_url: str = ""


@dataclass(slots=True)
class ManualSource:
    worksheet_name: str
    excel_row: int
    latin_name: str
    chinese_name: str
    foc: ManualSection = field(default_factory=ManualSection)
    nomenclature: ManualSection = field(default_factory=ManualSection)
    county_distribution: ManualSection = field(default_factory=ManualSection)
    imported_at: datetime = field(default_factory=datetime.now)
    schema_version: int = 1

    @property
    def has_text(self) -> bool:
        return any(
            section.text.strip()
            for section in (self.foc, self.nomenclature, self.county_distribution)
        )

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["imported_at"] = self.imported_at.isoformat()
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ManualSource":
        def section(name: str) -> ManualSection:
            raw = dict(data.get(name, {}))
            return ManualSection(
                text=str(raw.get("text", "")),
                source_name=str(raw.get("source_name", "")),
                source_url=str(raw.get("source_url", "")),
            )

        return cls(
            worksheet_name=str(data["worksheet_name"]),
            excel_row=int(data["excel_row"]),
            latin_name=str(data.get("latin_name", "")),
            chinese_name=str(data.get("chinese_name", "")),
            foc=section("foc"),
            nomenclature=section("nomenclature"),
            county_distribution=section("county_distribution"),
            imported_at=datetime.fromisoformat(str(data["imported_at"])),
            schema_version=int(data.get("schema_version", 1)),
        )


class ManualSourceStore:
    """按当前批次、工作表和Excel行保存单植物人工来源。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path_for(self, worksheet_name: str, excel_row: int) -> Path:
        safe_sheet = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in worksheet_name
        )[:60] or "sheet"
        return self.directory / f"{safe_sheet}_{excel_row}.json"

    def load(self, worksheet_name: str, excel_row: int) -> ManualSource | None:
        path = self.path_for(worksheet_name, excel_row)
        if not path.exists():
            return None
        try:
            return ManualSource.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def save(self, source: ManualSource) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(source.worksheet_name, source.excel_row)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(source.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)
        return path

    def delete(self, worksheet_name: str, excel_row: int) -> bool:
        path = self.path_for(worksheet_name, excel_row)
        if not path.exists():
            return False
        path.unlink()
        return True


class ManualSourceService:
    def __init__(self, parser: SourceParser) -> None:
        self.parser = parser

    def parse(self, record: SpeciesRecord, source: ManualSource) -> list[FieldProposal]:
        snapshot = self.snapshot(source)
        proposals = self.parser.parse(record, snapshot).proposals
        for proposal in proposals:
            section, label = self._section_for(proposal.field, source)
            custom_name = section.source_name.strip()
            proposal.source_name = (
                f"人工导入 · {label} · {custom_name}"
                if custom_name
                else f"人工导入 · {label}"
            )
            proposal.source_url = section.source_url.strip()
            if proposal.field is FieldKey.SOURCE:
                proposal.suggested_value = section.source_name.strip() or "人工导入来源"
                if str(proposal.suggested_value).strip() == str(proposal.original_value or "").strip():
                    proposal.final_value = proposal.original_value
                    proposal.state = ReviewState.NO_CHANGE
                elif proposal.confidence.value == "high":
                    proposal.set_auto_ready()
            proposal.note = self._manual_note(proposal.note, source.imported_at)
            proposal.parser_rule = self._manual_note(proposal.parser_rule, source.imported_at)
        return proposals

    @staticmethod
    def snapshot(source: ManualSource) -> SourceSnapshot:
        def section(value: ManualSection, key: str) -> SourceSection:
            text = value.text.strip()
            url = value.source_url.strip() or f"manual://{key}"
            return SourceSection(
                FetchStatus.SUCCESS if text else FetchStatus.NO_DATA,
                url,
                value.source_url.strip(),
                text=text,
                error_message="用户未导入该类文本" if not text else "",
            )

        foc = section(source.foc, "foc")
        nomenclature = section(source.nomenclature, "nomenclature")
        county = section(source.county_distribution, "county")
        return SourceSnapshot(
            requested_species_name=source.latin_name,
            searched_name=source.latin_name,
            foc=foc,
            nomenclature=nomenclature,
            county_distribution=county,
            nomenclature_required=not foc.usable and nomenclature.usable,
            page_title="人工导入来源",
            captured_at=source.imported_at,
        )

    @staticmethod
    def _section_for(
        field: FieldKey, source: ManualSource
    ) -> tuple[ManualSection, str]:
        if field in COUNTY_FIELDS:
            return source.county_distribution, "区县分布"
        if field in {
            FieldKey.FAMILY_LATIN,
            FieldKey.FAMILY_CHINESE,
            FieldKey.SPECIES_LATIN,
            FieldKey.SPECIES_CHINESE,
        } and not source.foc.text.strip() and source.nomenclature.text.strip():
            return source.nomenclature, "名称信息"
        return source.foc, "FOC文本"

    @staticmethod
    def _manual_note(note: str, imported_at: datetime) -> str:
        prefix = f"人工导入来源（{imported_at:%Y-%m-%d %H:%M}）"
        return f"{prefix}；{note}" if note else prefix
