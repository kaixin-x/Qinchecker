"""字段级核对模型。

模型与界面、抓取器和 Excel 文件解耦，使每个字段都可以单独接受、保留或手动编辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias


CellValue: TypeAlias = str | int | float | None


class FieldKey(StrEnum):
    FAMILY_LATIN = "Family"
    FAMILY_CHINESE = "科"
    SPECIES_LATIN = "Species"
    SPECIES_CHINESE = "物种"
    LOWEST_ELEVATION = "Lowest elevation (m)"
    HIGHEST_ELEVATION = "Highest elevation (m)"
    COUNTIES = "区县"
    ENDEMIC_QINLING = "EndemicQinling"
    SOUTH_NORTH = "South/north"
    HABITAT = "Habitat"
    OTHER_OCCURRENCE = "Other occurrence"
    ENDEMIC_CHINA = "Endemic China"
    EARLIEST_FLOWERING = "Earliest flowering"
    LATEST_FLOWERING = "Latest flowering"
    SOURCE = "source"
    QINLING_DISTRIBUTION = "Distribution in the Qinling Moutains"
    ALPINE_2800_2900 = "Alpine2800/2900"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class ReviewState(StrEnum):
    UNPROCESSED = "unprocessed"
    AUTO_READY = "auto_ready"
    PENDING_REVIEW = "pending_review"
    ACCEPTED_SOURCE = "accepted_source"
    KEPT_ORIGINAL = "kept_original"
    MANUALLY_CONFIRMED = "manually_confirmed"
    NO_CHANGE = "no_change"
    FAILED = "failed"


class ProposalAction(StrEnum):
    NONE = "none"
    ACCEPT_SOURCE = "accept_source"
    KEEP_ORIGINAL = "keep_original"
    MANUAL_EDIT = "manual_edit"


@dataclass(slots=True)
class FieldProposal:
    """一个 Excel 单元格的来源建议与最终人工决定。"""

    worksheet_name: str
    excel_row: int
    species_name: str
    field: FieldKey
    original_value: CellValue
    suggested_value: CellValue = None
    final_value: CellValue = None
    confidence: Confidence = Confidence.NONE
    state: ReviewState = ReviewState.UNPROCESSED
    action: ProposalAction = ProposalAction.NONE
    source_name: str = ""
    source_url: str = ""
    source_excerpt: str = ""
    parser_rule: str = ""
    captured_at: datetime | None = None
    note: str = ""
    baseline_state: ReviewState | None = None
    baseline_final_value: CellValue = None

    @property
    def key(self) -> str:
        return f"{self.worksheet_name}:{self.excel_row}:{self.field.value}"

    @property
    def has_change(self) -> bool:
        return self.final_value != self.original_value

    def set_auto_ready(self) -> None:
        if self.confidence is not Confidence.HIGH:
            raise ValueError("只有高置信建议可以标记为自动就绪")
        if self.suggested_value is None:
            raise ValueError("自动就绪字段必须具有建议值")
        self.final_value = self.suggested_value
        self.state = ReviewState.AUTO_READY
        self.action = ProposalAction.NONE

    def accept_source(self) -> None:
        if self.suggested_value is None:
            raise ValueError("没有来源建议可接受")
        self.final_value = self.suggested_value
        self.state = ReviewState.ACCEPTED_SOURCE
        self.action = ProposalAction.ACCEPT_SOURCE

    def keep_original(self) -> None:
        self.final_value = self.original_value
        self.state = ReviewState.KEPT_ORIGINAL
        self.action = ProposalAction.KEEP_ORIGINAL

    def confirm_manual_value(self, value: CellValue) -> None:
        self.final_value = value
        self.state = ReviewState.MANUALLY_CONFIRMED
        self.action = ProposalAction.MANUAL_EDIT

    def capture_baseline(self) -> None:
        """记录网页解析刚完成、尚未恢复任何人工决定时的状态。"""
        self.baseline_state = self.state
        self.baseline_final_value = self.final_value

    def reset_to_suggestion(self) -> None:
        if self.baseline_state is not None:
            self.state = self.baseline_state
            self.final_value = self.baseline_final_value
        elif self.suggested_value is None:
            self.state = ReviewState.PENDING_REVIEW
            self.final_value = None
        elif str(self.suggested_value).strip() == str(self.original_value or "").strip():
            self.state = ReviewState.NO_CHANGE
            self.final_value = self.original_value
        elif self.confidence is Confidence.HIGH:
            self.state = ReviewState.AUTO_READY
            self.final_value = self.suggested_value
        else:
            self.state = ReviewState.PENDING_REVIEW
            self.final_value = None
        self.action = ProposalAction.NONE


@dataclass(slots=True)
class SpeciesRecord:
    """输入表的一行物种记录；原始字段保持不丢失。"""

    worksheet_name: str
    excel_row: int
    values: dict[str, CellValue]
    column_indexes: dict[str, int] = field(default_factory=dict)

    @property
    def latin_name(self) -> str:
        value = self.values.get(FieldKey.SPECIES_LATIN.value)
        return str(value or "").strip()

    @property
    def chinese_name(self) -> str:
        value = self.values.get(FieldKey.SPECIES_CHINESE.value)
        return str(value or "").strip()
