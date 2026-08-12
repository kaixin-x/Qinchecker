"""把 Excel 导入、iPlant 抓取、FOC 解析和用户决定恢复组成一个可复用批次。"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Callable

import psutil

from qinchecker.models.review import Confidence, FieldKey, FieldProposal, ReviewState, SpeciesRecord
from qinchecker.models.source import SourceSnapshot
from qinchecker.runtime import user_data_dir
from qinchecker.services.batch import BatchRunner, SpeciesTask
from qinchecker.services.cache import SourceCache
from qinchecker.services.decision_store import DecisionStore
from qinchecker.services.iplant import IPlantScraper
from qinchecker.services.manual_source import (
    ManualSource,
    ManualSourceService,
    ManualSourceStore,
)
from qinchecker.services.parsing import CountyIndex, HabitatGlossary, SourceParser
from qinchecker.services.workbook import WorkbookBridge, WorkbookService


ProgressCallback = Callable[[int, int, str], None]
ActivityCallback = Callable[[str, str], None]


@dataclass(slots=True)
class ReviewRun:
    records: list[SpeciesRecord]
    proposals: list[FieldProposal]
    decision_store: DecisionStore
    was_cancelled: bool = False
    manual_source_store: ManualSourceStore | None = None
    manual_source_service: ManualSourceService | None = None
    source_cache: SourceCache | None = None
    manual_sources: dict[int, ManualSource] = field(default_factory=dict)


class ReviewPipeline:
    def __init__(
        self,
        project_root: Path,
        workbook_service: WorkbookService | None = None,
        storage_root: Path | None = None,
    ) -> None:
        self.project_root = project_root
        self.storage_root = storage_root or user_data_dir()
        self.workbook_service = workbook_service or WorkbookService(WorkbookBridge())

    def run(
        self,
        input_path: Path,
        worksheet_name: str,
        start_excel_row: int,
        requested_count: int | None,
        on_progress: ProgressCallback | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        on_activity: ActivityCallback | None = None,
    ) -> ReviewRun:
        records = self.workbook_service.load_records(input_path, worksheet_name, start_excel_row, requested_count)
        if not records:
            raise ValueError("所选范围内没有可处理的拉丁学名")
        batch_key = self._batch_key(input_path, worksheet_name, start_excel_row, requested_count)
        session_dir = self.storage_root / "sessions" / batch_key
        cache = SourceCache(self.storage_root / "sessions" / "cache")
        parser = SourceParser(
            HabitatGlossary.from_csv(self.project_root / "config" / "habitat_terms.csv"),
            CountyIndex.from_csv(
                self.project_root / "config" / "qinling_counties.csv",
                self.project_root / "config" / "admin_aliases.csv",
            ),
        )
        manual_store = ManualSourceStore(session_dir / "manual_sources")
        manual_service = ManualSourceService(parser)
        manual_sources = {
            record.excel_row: manual
            for record in records
            if (manual := manual_store.load(record.worksheet_name, record.excel_row)) is not None
        }
        proposals: list[FieldProposal] = []
        parsed_rows: set[int] = set()
        records_by_row = {record.excel_row: record for record in records}
        completed = 0
        total_records = len(records)

        for record in records:
            manual = manual_sources.get(record.excel_row)
            if manual is None:
                continue
            proposals.extend(manual_service.parse(record, manual))
            parsed_rows.add(record.excel_row)
            completed += 1
            if on_progress is not None:
                on_progress(
                    completed,
                    total_records,
                    f"第 {record.excel_row} 行：{record.latin_name}（人工来源）",
                )

        tasks = [
            SpeciesTask(record.excel_row, record.latin_name, record.chinese_name)
            for record in records
            if record.excel_row not in manual_sources
        ]

        def report(task: SpeciesTask, snapshot: object) -> None:
            nonlocal completed
            completed += 1
            record = records_by_row.get(task.excel_row)
            if record is not None and isinstance(snapshot, SourceSnapshot):
                manual = manual_sources.get(record.excel_row)
                proposals.extend(
                    manual_service.parse(record, manual)
                    if manual is not None
                    else parser.parse(record, snapshot).proposals
                )
                parsed_rows.add(task.excel_row)
            if on_progress is not None:
                on_progress(completed, total_records, f"第 {task.excel_row} 行：{task.species_name}")
            if on_activity is not None and (
                completed == 1 or completed % 10 == 0 or completed == total_records
            ):
                on_activity(
                    f"MEMORY {completed}/{total_records}",
                    self._process_memory_summary(),
                )

        was_cancelled = False
        if tasks:
            with IPlantScraper(cache=cache, cancel_requested=cancel_requested, on_activity=on_activity) as scraper:
                runner = BatchRunner(scraper, session_dir / "progress.json")
                runner.run(
                    tasks,
                    start_excel_row,
                    requested_count,
                    on_snapshot=report,
                    cancel_requested=cancel_requested,
                    retain_results=False,
                )
            was_cancelled = runner.cancelled

        for record in records:
            if record.excel_row in parsed_rows:
                continue
            snapshot = cache.load(record.latin_name)
            manual = manual_sources.get(record.excel_row)
            if manual is not None:
                proposals.extend(manual_service.parse(record, manual))
                continue
            if snapshot is None:
                if was_cancelled:
                    continue
                proposals.extend(self._failed_proposals(record, "未获得 iPlant 快照"))
                continue
            parsed = parser.parse(record, snapshot)
            proposals.extend(parsed.proposals)

        store = DecisionStore(session_dir / "decisions.json")
        for proposal in proposals:
            proposal.capture_baseline()
        store.restore(proposals)
        return ReviewRun(
            records=records,
            proposals=proposals,
            decision_store=store,
            was_cancelled=was_cancelled,
            manual_source_store=manual_store,
            manual_source_service=manual_service,
            source_cache=cache,
            manual_sources=manual_sources,
        )

    @staticmethod
    def _process_memory_summary() -> str:
        """返回主程序及其浏览器/驱动子进程的常驻内存。"""
        try:
            process = psutil.Process()
            app_rss = process.memory_info().rss
            descendants = process.children(recursive=True)
            child_rss = 0
            readable_children = 0
            for child in descendants:
                try:
                    child_rss += child.memory_info().rss
                    readable_children += 1
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError) as error:
            return f"无法读取进程内存：{error}"
        to_mb = 1024 * 1024
        return (
            f"QinChecker {app_rss / to_mb:.1f} MB；"
            f"浏览器/驱动子进程 {child_rss / to_mb:.1f} MB（{readable_children} 个）；"
            f"合计 {(app_rss + child_rss) / to_mb:.1f} MB"
        )

    @staticmethod
    def _batch_key(input_path: Path, sheet: str, start: int, count: int | None) -> str:
        raw = f"{input_path.resolve()}|{sheet}|{start}|{count}".encode("utf-8")
        return f"review_{hashlib.sha256(raw).hexdigest()[:16]}"

    @staticmethod
    def _failed_proposals(record: SpeciesRecord, reason: str, snapshot: object | None = None) -> list[FieldProposal]:
        url = ""
        if snapshot is not None:
            foc = getattr(snapshot, "foc", None)
            url = getattr(foc, "resolved_url", "") or getattr(foc, "requested_url", "")
        return [
            FieldProposal(
                worksheet_name=record.worksheet_name,
                excel_row=record.excel_row,
                species_name=record.latin_name,
                field=field,
                original_value=record.values.get(field.value),
                confidence=Confidence.LOW,
                state=ReviewState.FAILED,
                source_name="iPlant 中国植物志（修订版，FOC）",
                source_url=url,
                parser_rule="抓取完整性检查",
                note=reason,
            )
            for field in FieldKey
        ]
