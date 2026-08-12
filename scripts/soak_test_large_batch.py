"""使用本地缓存模拟300/500种批次，验证内存、流式解析和分页。"""

from __future__ import annotations

import gc
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import psutil
from openpyxl import Workbook
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from qinchecker.models import FetchStatus, SourceSection, SourceSnapshot
from qinchecker.models.review import FieldKey
from qinchecker.services.cache import SourceCache
from qinchecker.services.review_pipeline import ReviewPipeline
from qinchecker.ui.main_window import MainWindow


ROOT = Path(__file__).resolve().parents[1]


def cached_snapshot() -> SourceSnapshot:
    return SourceSnapshot(
        requested_species_name="Populus adenopoda",
        foc=SourceSection(
            FetchStatus.SUCCESS,
            "https://www.iplant.cn/info/Populus%20adenopoda?t=foc",
            text=(
                "FOC >> Vol.4 >> Salicaceae >> Populus\n"
                "9. Populus adenopoda Maximowicz\n"
                "Trees; mountain slopes and forests; 300-2500 m. China."
            ),
            http_status=200,
        ),
        nomenclature=SourceSection(
            FetchStatus.NO_DATA,
            "https://www.iplant.cn/info/Populus%20adenopoda?t=n",
            error_message="FOC 名称标题完整，无需访问名称分类页",
        ),
        county_distribution=SourceSection(
            FetchStatus.SUCCESS,
            "https://www.iplant.cn/info/Populus%20adenopoda?t=f",
            text="陕西：周至县、洋县、太白县",
            http_status=200,
        ),
        nomenclature_required=False,
    )


def create_workbook(path: Path, count: int) -> None:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Sheet1")
    headers = [field.value for field in FieldKey] + ["备注"]
    sheet.append(headers)
    for _ in range(count):
        values = {field.value: "-" for field in FieldKey}
        values[FieldKey.SPECIES_LATIN.value] = "Populus adenopoda"
        values[FieldKey.SPECIES_CHINESE.value] = "响叶杨"
        sheet.append([values.get(header, "-") for header in headers])
    workbook.save(path)


def run_case(app: QApplication, count: int) -> None:
    with TemporaryDirectory() as directory:
        temp_root = Path(directory)
        input_path = temp_root / f"soak_{count}.xlsx"
        create_workbook(input_path, count)
        storage = temp_root / "data"
        SourceCache(storage / "sessions" / "cache").save(cached_snapshot())
        memory_messages: list[str] = []
        process = psutil.Process()
        before_mb = process.memory_info().rss / (1024 * 1024)
        started = perf_counter()
        run = ReviewPipeline(ROOT, storage_root=storage).run(
            input_path,
            "Sheet1",
            2,
            count,
            on_activity=lambda command, detail: (
                memory_messages.append(f"{command} | {detail}")
                if command.startswith("MEMORY ")
                else None
            ),
        )
        elapsed = perf_counter() - started
        after_pipeline_mb = process.memory_info().rss / (1024 * 1024)
        window = MainWindow(
            QSettings(str(temp_root / "settings.ini"), QSettings.Format.IniFormat)
        )
        window.review_run = run
        window.current_row = None
        window._populate_table()
        app.processEvents()
        after_ui_mb = process.memory_info().rss / (1024 * 1024)
        print(
            f"count={count} proposals={len(run.proposals)} table_rows={window.table.rowCount()} "
            f"elapsed={elapsed:.2f}s app_before={before_mb:.1f}MB "
            f"after_pipeline={after_pipeline_mb:.1f}MB after_ui={after_ui_mb:.1f}MB"
        )
        if memory_messages:
            print(f"last_monitor={memory_messages[-1]}")
        window.close()
        del window, run
        gc.collect()


def main() -> None:
    app = QApplication.instance() or QApplication([])
    run_case(app, 300)
    run_case(app, 500)


if __name__ == "__main__":
    main()
