"""只读抓取批次的进度记录与断点续跑。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
from json import JSONDecodeError
from pathlib import Path
from typing import Callable

from qinchecker.models.source import SourceSnapshot
from qinchecker.services.iplant import IPlantScraper, ScraperCancelled


@dataclass(frozen=True, slots=True)
class SpeciesTask:
    excel_row: int
    species_name: str
    fallback_name: str = ""


@dataclass(slots=True)
class BatchProgress:
    requested_start_excel_row: int
    requested_count: int | None
    completed_rows: list[int] = field(default_factory=list)
    skipped_rows: list[int] = field(default_factory=list)
    failed_rows: list[int] = field(default_factory=list)
    updated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["updated_at"] = self.updated_at.isoformat()
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "BatchProgress":
        return cls(
            requested_start_excel_row=int(data["requested_start_excel_row"]),
            requested_count=data.get("requested_count"),
            completed_rows=[int(row) for row in data.get("completed_rows", [])],
            skipped_rows=[int(row) for row in data.get("skipped_rows", [])],
            failed_rows=[int(row) for row in data.get("failed_rows", [])],
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
        )


class BatchRunner:
    """每成功或失败一条任务立即写进度，可随时重新运行。"""

    def __init__(self, scraper: IPlantScraper, progress_path: Path) -> None:
        self.scraper = scraper
        self.progress_path = progress_path
        self.cancelled = False

    def load_or_create_progress(self, start_excel_row: int, count: int | None) -> BatchProgress:
        if self.progress_path.exists():
            try:
                progress = BatchProgress.from_dict(
                    json.loads(self.progress_path.read_text(encoding="utf-8"))
                )
            except (OSError, JSONDecodeError, KeyError, TypeError, ValueError):
                # 完整网页缓存仍在；损坏进度从本批次范围重新核对即可快速恢复。
                progress = BatchProgress(start_excel_row, count)
                self._save_progress(progress)
                return progress
            if (
                progress.requested_start_excel_row != start_excel_row
                or progress.requested_count != count
            ):
                raise ValueError("已有进度文件与当前起始行或处理数量不一致，请创建新批次会话。")
            return progress
        progress = BatchProgress(start_excel_row, count)
        self._save_progress(progress)
        return progress

    def run(
        self,
        tasks: list[SpeciesTask],
        start_excel_row: int,
        count: int | None,
        on_snapshot: Callable[[SpeciesTask, SourceSnapshot], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        retain_results: bool = True,
    ) -> dict[int, SourceSnapshot]:
        self.cancelled = False
        progress = self.load_or_create_progress(start_excel_row, count)
        results: dict[int, SourceSnapshot] = {}
        end_row = None if count is None else start_excel_row + count - 1
        already_finished = set(progress.completed_rows) | set(progress.skipped_rows)
        for task in tasks:
            if cancel_requested is not None and cancel_requested():
                self.cancelled = True
                break
            if task.excel_row < start_excel_row or (end_row is not None and task.excel_row > end_row):
                continue
            if task.excel_row in already_finished:
                cache = getattr(self.scraper, "cache", None)
                if cache is not None:
                    cached = cache.load(task.species_name)
                    if cached is not None:
                        if retain_results:
                            results[task.excel_row] = cached
                        if on_snapshot is not None:
                            on_snapshot(task, cached)
                continue
            if not task.species_name.strip():
                progress.skipped_rows.append(task.excel_row)
                self._save_progress(progress)
                continue
            try:
                fallback = getattr(self.scraper, "get_or_fetch_with_fallback", None)
                if task.fallback_name and callable(fallback):
                    snapshot = fallback(task.species_name, task.fallback_name)
                else:
                    snapshot = self.scraper.get_or_fetch(task.species_name)
            except ScraperCancelled:
                self.cancelled = True
                break
            if retain_results:
                results[task.excel_row] = snapshot
            if on_snapshot is not None:
                on_snapshot(task, snapshot)
            if snapshot.is_complete:
                progress.completed_rows.append(task.excel_row)
                if task.excel_row in progress.failed_rows:
                    progress.failed_rows.remove(task.excel_row)
            else:
                if task.excel_row not in progress.failed_rows:
                    progress.failed_rows.append(task.excel_row)
            self._save_progress(progress)
        return results

    def _save_progress(self, progress: BatchProgress) -> None:
        progress.updated_at = datetime.now()
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.progress_path.with_suffix(self.progress_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(progress.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.progress_path)
