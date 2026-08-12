"""iPlant 原文快照及其抓取状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class FetchStatus(StrEnum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    TIMEOUT = "timeout"
    NOT_FOUND = "not_found"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    HTTP_ERROR = "http_error"
    CACHED = "cached"


@dataclass(slots=True)
class SourceSection:
    """一个来源板块的原文、URL 和状态。"""

    status: FetchStatus
    requested_url: str
    resolved_url: str = ""
    text: str = ""
    error_message: str = ""
    http_status: int | None = None

    @property
    def usable(self) -> bool:
        return self.status in {FetchStatus.SUCCESS, FetchStatus.CACHED} and bool(self.text)

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.status.value,
            "requested_url": self.requested_url,
            "resolved_url": self.resolved_url,
            "text": self.text,
            "error_message": self.error_message,
            "http_status": self.http_status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "SourceSection":
        return cls(
            status=FetchStatus(data["status"]),
            requested_url=data["requested_url"],
            resolved_url=data.get("resolved_url", ""),
            text=data.get("text", ""),
            error_message=data.get("error_message", ""),
            http_status=(
                int(data["http_status"])
                if data.get("http_status") is not None
                else None
            ),
        )


@dataclass(slots=True)
class SourceSnapshot:
    """一个物种在一次抓取中的 FOC、名称分类和县级分布原文快照。"""

    requested_species_name: str
    foc: SourceSection
    nomenclature: SourceSection
    county_distribution: SourceSection
    searched_name: str = ""
    used_chinese_fallback: bool = False
    nomenclature_required: bool = False
    page_title: str = ""
    captured_at: datetime = field(default_factory=datetime.now)
    attempt_count: int = 1
    from_cache: bool = False

    @property
    def is_complete(self) -> bool:
        return (
            self.foc.usable
            and self.county_distribution.usable
            and (not self.nomenclature_required or self.nomenclature.usable)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "requested_species_name": self.requested_species_name,
            "foc": self.foc.to_dict(),
            "nomenclature": self.nomenclature.to_dict(),
            "county_distribution": self.county_distribution.to_dict(),
            "searched_name": self.searched_name,
            "used_chinese_fallback": self.used_chinese_fallback,
            "nomenclature_required": self.nomenclature_required,
            "page_title": self.page_title,
            "captured_at": self.captured_at.isoformat(),
            "attempt_count": self.attempt_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SourceSnapshot":
        nomenclature = (
            SourceSection.from_dict(dict(data["nomenclature"]))
            if "nomenclature" in data
            else SourceSection(FetchStatus.NO_DATA, "")
        )
        return cls(
            requested_species_name=str(data["requested_species_name"]),
            foc=SourceSection.from_dict(dict(data["foc"])),
            nomenclature=nomenclature,
            county_distribution=SourceSection.from_dict(dict(data["county_distribution"])),
            searched_name=str(data.get("searched_name", data["requested_species_name"])),
            used_chinese_fallback=bool(data.get("used_chinese_fallback", False)),
            nomenclature_required=(
                bool(data["nomenclature_required"])
                if "nomenclature_required" in data
                else not nomenclature.usable
            ),
            page_title=str(data.get("page_title", "")),
            captured_at=datetime.fromisoformat(str(data["captured_at"])),
            attempt_count=int(data.get("attempt_count", 1)),
            from_cache=True,
        )
