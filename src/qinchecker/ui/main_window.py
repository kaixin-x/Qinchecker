"""可复核、可逐字段编辑的 QinChecker 桌面界面。"""

from __future__ import annotations

from html import escape
from pathlib import Path
import shutil
from threading import Event
from datetime import datetime
import time
from typing import Callable

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QBrush,
    QDesktopServices,
    QFont,
    QKeySequence,
    QShortcut,
    QTextOption,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from qinchecker.models.review import FieldKey, FieldProposal, ProposalAction, ReviewState, SpeciesRecord
from qinchecker.resources.theme import APP_STYLE, STATUS_COLORS
from qinchecker.runtime import resource_root, user_data_dir
from qinchecker.services.review_pipeline import ReviewPipeline, ReviewRun
from qinchecker.services.manual_source import ManualSection, ManualSource
from qinchecker.services.workbook import WorkbookBridge, WorkbookService


STATE_LABELS = {
    ReviewState.AUTO_READY: "自动就绪",
    ReviewState.PENDING_REVIEW: "待复核",
    ReviewState.ACCEPTED_SOURCE: "已接受",
    ReviewState.KEPT_ORIGINAL: "保留原值",
    ReviewState.MANUALLY_CONFIRMED: "手动确认",
    ReviewState.NO_CHANGE: "无变化",
    ReviewState.FAILED: "抓取失败",
    ReviewState.UNPROCESSED: "未处理",
}

DEFAULT_SHORTCUTS = {
    "accept": "Alt+A",
    "keep": "Alt+K",
    "manual": "Alt+E",
}
SHORTCUT_LABELS = {
    "accept": "接受来源",
    "keep": "保留原值",
    "manual": "手动编辑",
}
REVIEW_QUEUE_STATES = {
    ReviewState.AUTO_READY,
    ReviewState.PENDING_REVIEW,
    ReviewState.FAILED,
}
ALL_SPECIES_PAGE_SIZE = 300


class ManualSourceDialog(QDialog):
    """仅为当前植物录入三类文本，并在应用前预览17字段。"""

    SECTION_CONFIG = (
        ("foc", "FOC文本", "粘贴FOC英文原文或中文译文"),
        ("nomenclature", "名称信息", "粘贴学名、中文名和科名等名称资料"),
        ("county_distribution", "区县分布", "粘贴省、市、县级分布文字"),
    )

    def __init__(
        self,
        record: SpeciesRecord,
        parse_source: Callable[[ManualSource], list[FieldProposal]],
        existing: ManualSource | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.record = record
        self._parse_source = parse_source
        self.result_source: ManualSource | None = None
        self.result_proposals: list[FieldProposal] = []
        self._preview_signature: tuple[str, ...] | None = None
        self.current_stage = 1
        self.setWindowTitle("单植物人工来源文本")
        self.setModal(True)
        self.resize(1380, 820)
        self.setMinimumSize(1080, 700)
        layout = QVBoxLayout(self)
        identity = QLabel(
            f"当前植物：{record.latin_name or '-'}　中文名：{record.chinese_name or '-'}　"
            f"Excel第 {record.excel_row} 行"
        )
        identity.setStyleSheet("font-weight: 700; color: #1D2939;")
        layout.addWidget(identity)
        layout.addWidget(QLabel("文本只绑定当前植物。请按类型分别粘贴；至少填写一个文本区域。"))

        stage_layout = QHBoxLayout()
        self.stage_labels: list[QLabel] = []
        for index, title in enumerate(("1  填写文本", "2  预览识别", "3  应用结果"), start=1):
            label = QLabel(title)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setProperty("stageState", "upcoming")
            self.stage_labels.append(label)
            stage_layout.addWidget(label)
        layout.addLayout(stage_layout)

        self.inline_message = QLabel("请先填写文本，再预览全部17个字段。")
        self.inline_message.setObjectName("inlineMessage")
        self.inline_message.setProperty("noticeKind", "info")
        self.inline_message.setWordWrap(True)
        layout.addWidget(self.inline_message)

        section_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.section_editors: dict[str, tuple[QPlainTextEdit, QLineEdit, QLineEdit, QLabel]] = {}
        for key, title, placeholder in self.SECTION_CONFIG:
            container = QFrame(objectName="manualSourceColumn")
            tab_layout = QVBoxLayout(container)
            tab_layout.addWidget(QLabel(title, objectName="manualColumnTitle"))
            form = QFormLayout()
            source_name = QLineEdit()
            source_name.setPlaceholderText(f"例如：人工复制的{title}")
            source_url = QLineEdit()
            source_url.setPlaceholderText("可为空；如有原网址请填写")
            source_name.textChanged.connect(self._invalidate_preview)
            source_url.textChanged.connect(self._invalidate_preview)
            form.addRow("来源名称", source_name)
            form.addRow("来源网址", source_url)
            tab_layout.addLayout(form)
            editor = QPlainTextEdit()
            editor.setPlaceholderText(placeholder)
            editor.textChanged.connect(self._invalidate_preview)
            tab_layout.addWidget(editor, stretch=1)
            footer = QHBoxLayout()
            load_button = QPushButton("从TXT读取")
            load_button.clicked.connect(
                lambda _checked=False, section_key=key: self._load_text_file(section_key)
            )
            count_label = QLabel("0 个字符")
            count_label.setObjectName("subtitle")
            editor.textChanged.connect(
                lambda section_key=key: self._update_character_count(section_key)
            )
            footer.addWidget(load_button)
            footer.addStretch()
            footer.addWidget(count_label)
            tab_layout.addLayout(footer)
            self.section_editors[key] = (editor, source_name, source_url, count_label)
            section_splitter.addWidget(container)
        section_splitter.setChildrenCollapsible(False)
        section_splitter.setSizes([400, 400, 400])
        layout.addWidget(section_splitter, stretch=3)

        layout.addWidget(QLabel("17字段识别预览", objectName="subtitle"))
        self.preview_table = QTableWidget(0, 5)
        self.preview_table.setHorizontalHeaderLabels(
            ["状态", "字段", "Excel原值", "识别值", "识别依据"]
        )
        self.preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.preview_table.setWordWrap(True)
        self.preview_table.verticalHeader().setDefaultSectionSize(48)
        self.preview_table.verticalHeader().setMinimumSectionSize(38)
        self.preview_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.preview_table.setColumnWidth(0, 90)
        self.preview_table.setColumnWidth(1, 175)
        self.preview_table.setColumnWidth(2, 160)
        self.preview_table.setColumnWidth(3, 160)
        layout.addWidget(self.preview_table, stretch=2)

        actions = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        preview = QPushButton("预览识别")
        preview.clicked.connect(self._preview)
        self.apply_button = QPushButton("应用到当前植物")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(preview)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)

        self._set_stage(1)

        if existing is not None:
            for key, *_ in self.SECTION_CONFIG:
                section = getattr(existing, key)
                editor, source_name, source_url, _label = self.section_editors[key]
                editor.setPlainText(section.text)
                source_name.setText(section.source_name)
                source_url.setText(section.source_url)

    def _set_stage(self, stage: int) -> None:
        self.current_stage = stage
        for index, label in enumerate(self.stage_labels, start=1):
            state = "done" if index < stage else "active" if index == stage else "upcoming"
            label.setProperty("stageState", state)
            label.style().unpolish(label)
            label.style().polish(label)

    def _show_inline_message(self, message: str, kind: str = "info") -> None:
        self.inline_message.setText(message)
        self.inline_message.setProperty("noticeKind", kind)
        self.inline_message.style().unpolish(self.inline_message)
        self.inline_message.style().polish(self.inline_message)

    def _source_from_form(self) -> ManualSource:
        values: dict[str, ManualSection] = {}
        for key, *_ in self.SECTION_CONFIG:
            editor, source_name, source_url, _label = self.section_editors[key]
            values[key] = ManualSection(
                editor.toPlainText().strip(),
                source_name.text().strip(),
                source_url.text().strip(),
            )
        return ManualSource(
            self.record.worksheet_name,
            self.record.excel_row,
            self.record.latin_name,
            self.record.chinese_name,
            foc=values["foc"],
            nomenclature=values["nomenclature"],
            county_distribution=values["county_distribution"],
        )

    def _signature(self) -> tuple[str, ...]:
        source = self._source_from_form()
        return tuple(
            item
            for section in (source.foc, source.nomenclature, source.county_distribution)
            for item in (section.text, section.source_name, section.source_url)
        )

    def _invalidate_preview(self) -> None:
        self._preview_signature = None
        self.apply_button.setEnabled(False)
        if hasattr(self, "stage_labels"):
            self._set_stage(1)
            self._show_inline_message("文本已变化，请重新预览识别。", "info")

    def _update_character_count(self, key: str) -> None:
        editor, _name, _url, label = self.section_editors[key]
        label.setText(f"{len(editor.toPlainText())} 个字符")

    def _load_text_file(self, key: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "读取TXT文本", "", "文本文件 (*.txt);;所有文件 (*)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            self._show_inline_message(f"读取失败：无法按 UTF-8 读取该文件。{error}", "error")
            return
        self.section_editors[key][0].setPlainText(text)
        self._show_inline_message(f"已读取 {Path(path).name}，请预览识别。", "success")

    def _preview(self) -> None:
        source = self._source_from_form()
        if not source.has_text:
            self._set_stage(1)
            self._show_inline_message("请至少填写一个人工来源文本区域。", "warning")
            return
        self._set_stage(2)
        self._show_inline_message("正在识别全部17个字段……", "info")
        proposals = self._parse_source(source)
        self.preview_table.setRowCount(len(proposals))
        for row, proposal in enumerate(proposals):
            values = (
                STATE_LABELS[proposal.state],
                proposal.field.value,
                MainWindow._text(proposal.original_value),
                MainWindow._text(proposal.suggested_value),
                proposal.source_excerpt or proposal.parser_rule,
            )
            for column, value in enumerate(values):
                self.preview_table.setItem(row, column, QTableWidgetItem(value))
        self.result_source = source
        self.result_proposals = proposals
        self._preview_signature = self._signature()
        self.apply_button.setEnabled(True)
        self._set_stage(3)
        recognized = sum(proposal.suggested_value is not None for proposal in proposals)
        self._show_inline_message(
            f"预览完成：已检查 {len(proposals)} 个字段，其中 {recognized} 个取得识别值。请核对后应用。",
            "success",
        )

    def _apply(self) -> None:
        if self._preview_signature is None or self._preview_signature != self._signature():
            self._set_stage(1)
            self._show_inline_message("文本或来源信息已变化，请重新预览后再应用。", "warning")
            return
        self.accept()


class ShortcutSettingsDialog(QDialog):
    """录入并校验三项字段复核快捷键。"""

    def __init__(self, values: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("快捷键设置")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.shortcut_values = dict(values)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("点击输入框后按下新的组合键；三项快捷键不能相同。"))
        form = QFormLayout()
        self.editors: dict[str, QKeySequenceEdit] = {}
        for key in ("accept", "keep", "manual"):
            editor = QKeySequenceEdit(QKeySequence(values[key]))
            editor.setClearButtonEnabled(True)
            self.editors[key] = editor
            form.addRow(SHORTCUT_LABELS[key], editor)
        layout.addLayout(form)
        reset = QPushButton("恢复默认")
        reset.clicked.connect(self._restore_defaults)
        layout.addWidget(reset, alignment=Qt.AlignmentFlag.AlignLeft)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _restore_defaults(self) -> None:
        for key, value in DEFAULT_SHORTCUTS.items():
            self.editors[key].setKeySequence(QKeySequence(value))

    def accept(self) -> None:
        values = {
            key: editor.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            for key, editor in self.editors.items()
        }
        if any(not value for value in values.values()):
            QMessageBox.warning(self, "快捷键无效", "每项操作都必须设置快捷键。")
            return
        normalized = [value.casefold() for value in values.values()]
        if len(set(normalized)) != len(normalized):
            QMessageBox.warning(self, "快捷键冲突", "三项操作不能使用相同的快捷键。")
            return
        self.shortcut_values = values
        super().accept()


class ReviewWorker(QObject):
    progress = Signal(int, int, str)
    activity = Signal(str, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        root: Path,
        storage_root: Path,
        input_path: Path,
        start_row: int,
        count: int | None,
        cancel_event: Event,
    ) -> None:
        super().__init__()
        self.root = root
        self.storage_root = storage_root
        self.input_path = input_path
        self.start_row = start_row
        self.count = count
        self.cancel_event = cancel_event

    def run(self) -> None:
        try:
            outcome = ReviewPipeline(self.root, storage_root=self.storage_root).run(
                self.input_path, "Sheet1", self.start_row, self.count,
                on_progress=lambda current, total, label: self.progress.emit(current, total, label),
                cancel_requested=self.cancel_event.is_set,
                on_activity=lambda command, detail: self.activity.emit(command, detail),
            )
        except Exception as error:  # 向界面报告，不让工作线程异常终止。
            self.failed.emit(str(error))
            return
        self.completed.emit(outcome)


class ExportWorker(QObject):
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, input_path: Path, output_path: Path, records: list[SpeciesRecord], proposals: list[FieldProposal]) -> None:
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path
        self.records = records
        self.proposals = proposals

    def run(self) -> None:
        try:
            service = WorkbookService(WorkbookBridge())
            plan = service.build_export_plan(self.records, self.proposals)
            service.bridge.export(self.input_path, self.output_path, plan)
            text_path = self.output_path.with_name(f"{self.output_path.stem}说明.txt")
            text_path.write_text(service.summary_text(self.input_path, plan), encoding="utf-8")
        except Exception as error:
            self.failed.emit(str(error))
            return
        self.completed.emit(str(self.output_path))


class MainWindow(QMainWindow):
    """目录、字段差异和证据三栏布局；每个建议可单独决定。"""

    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.project_root = resource_root()
        self.data_root = user_data_dir()
        self._settings = settings or QSettings(
            str(self.data_root / "settings.ini"), QSettings.Format.IniFormat
        )
        self._shortcut_values: dict[str, str] = {}
        self._shortcuts: dict[str, QShortcut] = {}
        self.input_path: Path | None = None
        self.review_run: ReviewRun | None = None
        self.current_row: int | None = None
        self._table_page = 0
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._cancel_event: Event | None = None
        self._last_activity_at: float | None = None
        self._batch_started_at: float | None = None
        self._notification_tray: QSystemTrayIcon | None = None
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(lambda: self.notice_banner.setVisible(False))
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(1_000)
        self._watchdog.timeout.connect(self._check_processing_timeout)
        self.setWindowTitle("QinChecker · 秦岭植物 FOC 核对工作台")
        self.resize(1520, 920)
        self.setStyleSheet(APP_STYLE)
        self._build_ui()
        self._load_shortcuts()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 12, 16, 12)
        root_layout.setSpacing(12)
        root_layout.addWidget(self._build_top_bar())
        workflow = QFrame(objectName="workflowStages")
        workflow_layout = QHBoxLayout(workflow)
        workflow_layout.setContentsMargins(6, 4, 6, 4)
        workflow_layout.setSpacing(6)
        self.workflow_stage_labels: list[QLabel] = []
        for title in ("1  选择数据", "2  抓取比对", "3  字段复核", "4  导出结果"):
            label = QLabel(title)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setProperty("stageState", "upcoming")
            self.workflow_stage_labels.append(label)
            workflow_layout.addWidget(label)
        root_layout.addWidget(workflow)
        self.notice_banner = QLabel()
        self.notice_banner.setObjectName("noticeBanner")
        self.notice_banner.setWordWrap(True)
        self.notice_banner.setVisible(False)
        root_layout.addWidget(self.notice_banner)
        root_layout.addWidget(self._build_workspace(), stretch=1)
        self.setCentralWidget(root)
        status = QStatusBar()
        status.showMessage("请选择 Excel 文件、起始行和处理数量后开始。")
        self.setStatusBar(status)
        self._set_workflow_stage(1)

    def _set_workflow_stage(self, stage: int) -> None:
        for index, label in enumerate(self.workflow_stage_labels, start=1):
            state = "done" if index < stage else "active" if index == stage else "upcoming"
            label.setProperty("stageState", state)
            label.style().unpolish(label)
            label.style().polish(label)

    def _notify(self, message: str, kind: str = "info", duration_ms: int = 5_000) -> None:
        """Show a non-blocking in-window message; confirmations remain modal."""
        self.notice_banner.setText(message)
        self.notice_banner.setProperty("noticeKind", kind)
        self.notice_banner.style().unpolish(self.notice_banner)
        self.notice_banner.style().polish(self.notice_banner)
        self.notice_banner.setVisible(True)
        self.statusBar().showMessage(message)
        self._notice_timer.start(duration_ms)

    def _build_top_bar(self) -> QFrame:
        bar = QFrame(objectName="topBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(QLabel("QinChecker", objectName="title"))
        title_box.addWidget(QLabel("FOC 字段级核对 · 先复核，再导出", objectName="subtitle"))
        layout.addLayout(title_box)

        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        self.file_path.setPlaceholderText("选择需要核对的 Excel 文件")
        self.file_path.setMinimumWidth(270)
        layout.addWidget(self.file_path, stretch=1)
        browse = QPushButton("选择 Excel")
        browse.clicked.connect(self._choose_file)
        layout.addWidget(browse)

        layout.addWidget(QLabel("起始行"))
        self.start_row = QSpinBox()
        self.start_row.setRange(2, 1_000_000)
        self.start_row.setValue(2)
        layout.addWidget(self.start_row)
        layout.addWidget(QLabel("处理条数"))
        self.batch_size = QSpinBox()
        self.batch_size.setRange(0, 1_000_000)
        self.batch_size.setValue(50)
        self.batch_size.setSpecialValueText("至末尾")
        self.batch_size.setToolTip("填 0 表示从起始行处理至末尾")
        layout.addWidget(self.batch_size)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索物种或行号")
        self.search.setMaximumWidth(190)
        self.search.textChanged.connect(self._populate_directory)
        layout.addWidget(self.search)
        self.start_button = QPushButton("开始处理")
        self.start_button.clicked.connect(self._start_review)
        layout.addWidget(self.start_button)
        self.stop_button = QPushButton("停止处理")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._request_stop)
        layout.addWidget(self.stop_button)
        self.export_button = QPushButton("导出核对结果")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export)
        layout.addWidget(self.export_button)
        self.export_log_button = QPushButton("导出执行日志")
        self.export_log_button.clicked.connect(self._export_execution_log)
        layout.addWidget(self.export_log_button)
        self.shortcut_settings_button = QPushButton("快捷键设置")
        self.shortcut_settings_button.clicked.connect(self._edit_shortcuts)
        layout.addWidget(self.shortcut_settings_button)
        return bar

    def _build_workspace(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_catalog_panel())
        splitter.addWidget(self._build_diff_panel())
        splitter.addWidget(self._build_evidence_panel())
        splitter.setSizes([205, 955, 390])
        return splitter

    def _load_shortcuts(self) -> None:
        values = {
            key: str(self._settings.value(f"shortcuts/{key}", default) or "").strip()
            for key, default in DEFAULT_SHORTCUTS.items()
        }
        if any(not value for value in values.values()) or len({item.casefold() for item in values.values()}) != len(values):
            values = dict(DEFAULT_SHORTCUTS)
        self._apply_shortcut_values(values, persist=False)

    def _apply_shortcut_values(self, values: dict[str, str], *, persist: bool) -> None:
        callbacks = {
            "accept": self._accept_source,
            "keep": self._keep_original,
            "manual": self._manual_edit,
        }
        buttons = {
            "accept": self.accept_button,
            "keep": self.keep_button,
            "manual": self.manual_button,
        }
        self._shortcut_values = dict(values)
        for key, value in values.items():
            sequence = QKeySequence(value)
            shortcut = self._shortcuts.get(key)
            if shortcut is None:
                shortcut = QShortcut(sequence, self)
                shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
                shortcut.activated.connect(callbacks[key])
                self._shortcuts[key] = shortcut
            else:
                shortcut.setKey(sequence)
            display = sequence.toString(QKeySequence.SequenceFormat.NativeText)
            buttons[key].setText(f"{SHORTCUT_LABELS[key]} ({display})")
            buttons[key].setToolTip(f"快捷键：{display}")
            if persist:
                self._settings.setValue(f"shortcuts/{key}", value)
        if persist:
            self._settings.sync()

    def _edit_shortcuts(self) -> None:
        dialog = ShortcutSettingsDialog(self._shortcut_values, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_shortcut_values(dialog.shortcut_values, persist=True)
            self.statusBar().showMessage("快捷键已保存并立即生效。")

    @staticmethod
    def _panel(title: str, subtitle: str) -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame(objectName="panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 15px; font-weight: 700; color: #1D2939;")
        layout.addWidget(heading)
        layout.addWidget(QLabel(subtitle, objectName="subtitle"))
        return panel, layout

    def _build_catalog_panel(self) -> QFrame:
        panel, layout = self._panel("物种目录", "颜色表示最需要处理的状态；可选择“全部物种”查看全批次。")
        self.catalog_filter = QComboBox()
        self.catalog_filter.addItem("全部状态", "all")
        self.catalog_filter.addItem("待复核", "pending")
        self.catalog_filter.addItem("已修改", "modified")
        self.catalog_filter.addItem("已完成", "completed")
        self.catalog_filter.addItem("抓取失败", "failed")
        self.catalog_filter.addItem("人工来源", "manual")
        self.catalog_filter.currentIndexChanged.connect(self._populate_directory)
        layout.addWidget(self.catalog_filter)
        self.catalog = QListWidget()
        self.catalog.currentItemChanged.connect(self._catalog_selected)
        layout.addWidget(self.catalog, stretch=1)
        self.open_cache_button = QPushButton("打开缓存文件夹")
        self.open_cache_button.clicked.connect(self._open_cache_directory)
        layout.addWidget(self.open_cache_button)
        self.clear_cache_button = QPushButton("清除网页缓存")
        self.clear_cache_button.setToolTip("只清除 iPlant 网页缓存，不删除批次进度、人工决定或 Excel")
        self.clear_cache_button.clicked.connect(self._clear_web_cache)
        layout.addWidget(self.clear_cache_button)
        return panel

    def _build_diff_panel(self) -> QFrame:
        panel, layout = self._panel("字段差异", "每一行可独立决定；选中单个物种后也可批量接受或保留。")
        self.species_identity = QLabel("全部物种 · 分页显示字段")
        self.species_identity.setObjectName("speciesIdentity")
        self.species_identity.setWordWrap(True)
        layout.addWidget(self.species_identity)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["状态", "行号", "物种", "字段", "原始值", "来源建议", "最终值", "置信度"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.verticalHeader().setMinimumSectionSize(38)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        for column, width in enumerate([86, 52, 128, 116]):
            self.table.setColumnWidth(column, width)
        self.table.itemSelectionChanged.connect(self._show_evidence)
        self.table.itemDoubleClicked.connect(self._table_item_double_clicked)
        layout.addWidget(self.table, stretch=1)
        paging = QHBoxLayout()
        self.previous_page_button = QPushButton("上一页")
        self.previous_page_button.clicked.connect(lambda: self._change_table_page(-1))
        self.table_page_label = QLabel("")
        self.table_page_label.setObjectName("subtitle")
        self.next_page_button = QPushButton("下一页")
        self.next_page_button.clicked.connect(lambda: self._change_table_page(1))
        paging.addStretch()
        paging.addWidget(self.previous_page_button)
        paging.addWidget(self.table_page_label)
        paging.addWidget(self.next_page_button)
        paging.addStretch()
        layout.addLayout(paging)
        field_group = QFrame(objectName="actionGroup")
        field_group_layout = QVBoxLayout(field_group)
        field_group_layout.setContentsMargins(10, 7, 10, 7)
        field_group_layout.setSpacing(5)
        field_group_layout.addWidget(QLabel("当前字段", objectName="actionGroupTitle"))
        actions = QHBoxLayout()
        self.accept_button = QPushButton("接受来源")
        self.keep_button = QPushButton("保留原值")
        self.manual_button = QPushButton("手动编辑")
        self.restore_button = QPushButton("恢复建议")
        for button, callback in (
            (self.accept_button, self._accept_source),
            (self.keep_button, self._keep_original),
            (self.manual_button, self._manual_edit),
            (self.restore_button, self._restore_suggestion),
        ):
            button.setEnabled(False)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        field_group_layout.addLayout(actions)
        layout.addWidget(field_group)

        species_group = QFrame(objectName="actionGroup")
        species_group_layout = QVBoxLayout(species_group)
        species_group_layout.setContentsMargins(10, 7, 10, 7)
        species_group_layout.setSpacing(5)
        species_group_layout.addWidget(QLabel("当前植物", objectName="actionGroupTitle"))
        species_actions = QHBoxLayout()
        self.accept_all_button = QPushButton("全部接受")
        self.accept_all_button.setEnabled(False)
        self.accept_all_button.setToolTip("接受当前物种所有尚未处理且具有来源建议的字段")
        self.accept_all_button.clicked.connect(self._accept_all_current_species)
        species_actions.addWidget(self.accept_all_button)
        self.keep_all_button = QPushButton("全部保留")
        self.keep_all_button.setEnabled(False)
        self.keep_all_button.setToolTip("当前物种所有尚未处理的字段均保留原值")
        self.keep_all_button.clicked.connect(self._keep_all_current_species)
        species_actions.addWidget(self.keep_all_button)
        self.restore_all_button = QPushButton("全部恢复修改")
        self.restore_all_button.setEnabled(False)
        self.restore_all_button.setToolTip("撤销当前植物所有人工决定，恢复为程序最初生成的字段建议")
        self.restore_all_button.clicked.connect(self._restore_all_current_species)
        self.restore_all_button.setObjectName("warningButton")
        species_actions.addWidget(self.restore_all_button)
        species_actions.addStretch()
        species_group_layout.addLayout(species_actions)
        layout.addWidget(species_group)
        return panel

    def _build_evidence_panel(self) -> QFrame:
        panel, layout = self._panel("核对辅助", "字段证据、人工文本和执行状态分别查看。")
        self.evidence_tabs = QTabWidget()

        source_container = QWidget()
        source_layout = QVBoxLayout(source_container)
        source_layout.setContentsMargins(0, 0, 0, 0)
        self.evidence = QTextBrowser()
        self.evidence.setOpenExternalLinks(True)
        self.evidence.setHtml("<p style='color:#667085'>选择一个字段后显示原值、新值、来源网址与网站原文。</p>")
        source_layout.addWidget(self.evidence)
        self.toggle_excerpt_button = QPushButton("展开来源原文")
        self.toggle_excerpt_button.setCheckable(True)
        self.toggle_excerpt_button.setEnabled(False)
        self.toggle_excerpt_button.toggled.connect(self._toggle_source_excerpt)
        source_layout.addWidget(self.toggle_excerpt_button)
        self.source_excerpt = QPlainTextEdit()
        self.source_excerpt.setReadOnly(True)
        self.source_excerpt.setWordWrapMode(QTextOption.WrapMode.WordWrap)
        self.source_excerpt.setPlaceholderText("网站未提供该字段原文")
        self.source_excerpt.setVisible(False)
        source_layout.addWidget(self.source_excerpt, stretch=1)
        self.evidence_tabs.addTab(source_container, "字段来源")

        manual_container = QWidget()
        manual_layout = QVBoxLayout(manual_container)
        manual_layout.setContentsMargins(8, 8, 8, 8)
        self.manual_source_status = QLabel("请先选择一个具体植物。")
        self.manual_source_status.setWordWrap(True)
        self.manual_source_status.setObjectName("manualStatus")
        manual_layout.addWidget(self.manual_source_status)
        manual_layout.addStretch()
        manual_actions = QVBoxLayout()
        self.import_manual_button = QPushButton("导入文本")
        self.import_manual_button.clicked.connect(self._import_manual_source)
        self.edit_manual_button = QPushButton("查看/编辑")
        self.edit_manual_button.clicked.connect(self._edit_manual_source)
        self.reparse_manual_button = QPushButton("重新识别")
        self.reparse_manual_button.clicked.connect(self._reparse_manual_source)
        self.delete_manual_button = QPushButton("删除文本")
        self.delete_manual_button.clicked.connect(self._delete_manual_source)
        for button in (
            self.import_manual_button,
            self.edit_manual_button,
            self.reparse_manual_button,
            self.delete_manual_button,
        ):
            button.setEnabled(False)
            manual_actions.addWidget(button)
        manual_layout.addLayout(manual_actions)
        self.evidence_tabs.addTab(manual_container, "人工文本")

        activity_container = QWidget()
        activity_layout = QVBoxLayout(activity_container)
        activity_layout.setContentsMargins(0, 0, 0, 0)
        activity_layout.setSpacing(6)
        activity_layout.addWidget(QLabel("实时进度", objectName="subtitle"))
        self.current_command = QPlainTextEdit()
        self.current_command.setReadOnly(True)
        self.current_command.setMaximumBlockCount(2)
        self.current_command.setFixedHeight(66)
        self.current_command.setFont(QFont("Consolas", 9))
        self.current_command.setPlaceholderText("等待处理指令……")
        activity_layout.addWidget(self.current_command)
        self.activity_detail = QLabel("等待开始处理。")
        self.activity_detail.setWordWrap(True)
        self.activity_detail.setObjectName("subtitle")
        activity_layout.addWidget(self.activity_detail)
        self.execution_log = QPlainTextEdit()
        self.execution_log.setReadOnly(True)
        self.execution_log.setFont(QFont("Microsoft YaHei UI", 9))
        self.execution_log.setMaximumBlockCount(500)
        self.evidence_tabs.addTab(activity_container, "执行状态")
        layout.addWidget(self.evidence_tabs, stretch=1)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        activity_layout.addWidget(self.progress)
        return panel

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择待核对 Excel", str(self.project_root.parent), "Excel 文件 (*.xlsx)")
        if not path:
            return
        self.input_path = Path(path)
        self.file_path.setText(path)
        self._set_workflow_stage(1)
        self.statusBar().showMessage("已选择文件。请确认起始行和处理条数。")
        self._log_activity(f"已选择输入文件：{self.input_path.name}")

    def _start_review(self) -> None:
        if self.input_path is None:
            self._choose_file()
            if self.input_path is None:
                return
        self.start_button.setEnabled(False)
        self._set_workflow_stage(2)
        self.export_button.setEnabled(False)
        self.open_cache_button.setEnabled(False)
        self.clear_cache_button.setEnabled(False)
        for button in (
            self.import_manual_button,
            self.edit_manual_button,
            self.reparse_manual_button,
            self.delete_manual_button,
        ):
            button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        count = self.batch_size.value() or None
        self._cancel_event = Event()
        self._batch_started_at = time.perf_counter()
        self.stop_button.setEnabled(True)
        self._set_activity("BATCH START", f"正在准备从第 {self.start_row.value()} 行开始处理。")
        self._watchdog.start()
        self._thread = QThread(self)
        worker = ReviewWorker(
            self.project_root,
            self.data_root,
            self.input_path,
            self.start_row.value(),
            count,
            self._cancel_event,
        )
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.progress.connect(self._review_progress)
        worker.activity.connect(self._review_activity)
        worker.completed.connect(self._review_complete)
        worker.failed.connect(self._review_failed)
        worker.completed.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._thread.finished.connect(lambda: self._release_worker(worker))
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self.statusBar().showMessage("正在读取 Excel 并抓取 iPlant；可安全关闭窗口后下次继续相同批次。")
        self._log_activity(f"开始处理：从第 {self.start_row.value()} 行起，处理 {count or '至末尾'} 条。")

    def _request_stop(
        self,
        command: str = "CANCEL REQUESTED",
        detail: str = "已请求停止：当前网络请求返回后将安全停止，已完成记录会保留缓存。",
    ) -> None:
        if self._cancel_event is None or self._cancel_event.is_set():
            return
        self._cancel_event.set()
        self.stop_button.setEnabled(False)
        self._watchdog.stop()
        self.statusBar().showMessage("已请求停止；完成当前物种的网络请求后将安全停止。")
        self._set_activity(command, detail)

    def _review_progress(self, current: int, total: int, label: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(current)
        self.statusBar().showMessage(f"正在处理 {current}/{total}：{label}")
        self._set_activity(f"COMPLETED {current}/{total}", f"已完成：{label}")

    def _review_activity(self, command: str, detail: str) -> None:
        self._set_activity(command, detail)

    def _review_complete(self, outcome: object) -> None:
        elapsed_seconds = self._batch_elapsed_seconds()
        self.review_run = outcome  # type: ignore[assignment]
        self.current_row = None
        self.progress.setVisible(False)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.open_cache_button.setEnabled(True)
        self.clear_cache_button.setEnabled(True)
        self._cancel_event = None
        self._watchdog.stop()
        self.export_button.setEnabled(True)
        self._set_workflow_stage(3)
        self._populate_directory()
        if self.catalog.count() > 1:
            self.catalog.setCurrentRow(1)
        else:
            self._populate_table()
        if self.review_run.was_cancelled:
            message = (
                f"处理已停止：保留 {len(self.review_run.proposals)} 条已获得的字段建议，"
                f"累计用时 {elapsed_seconds:.2f} 秒，可继续复核或导出。"
            )
            self._set_activity(
                f"BATCH STOPPED · {elapsed_seconds:.2f}s",
                message,
            )
        else:
            completed_species = len(self.review_run.records)
            completion_notice = (
                f"完成了 {completed_species} 种植物比对，用时 {elapsed_seconds:.2f} 秒。"
            )
            message = (
                f"处理完成：{completed_species} 个物种，"
                f"{len(self.review_run.proposals)} 条字段建议；用时 {elapsed_seconds:.2f} 秒。"
            )
            self._set_activity(
                f"BATCH COMPLETE {completed_species} SPECIES · {elapsed_seconds:.2f}s",
                completion_notice,
            )
            self._show_completion_notification(completion_notice)
        self.statusBar().showMessage(message)
        self._batch_started_at = None
        self._notify(message, "warning" if self.review_run.was_cancelled else "success", 8_000)

    def _review_failed(self, detail: str) -> None:
        elapsed_seconds = self._batch_elapsed_seconds()
        self.progress.setVisible(False)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.open_cache_button.setEnabled(True)
        self.clear_cache_button.setEnabled(True)
        self._cancel_event = None
        self._watchdog.stop()
        self.statusBar().showMessage("处理失败。")
        self._set_activity(
            f"BATCH FAILED · {elapsed_seconds:.2f}s",
            f"处理失败：{detail}；用时 {elapsed_seconds:.2f} 秒。",
        )
        self._batch_started_at = None
        self._set_workflow_stage(1)
        self._notify(f"处理失败：{detail}", "error", 12_000)

    def _cache_directory(self) -> Path:
        data_root = self.data_root.resolve()
        cache_directory = (data_root / "sessions" / "cache").resolve()
        if not cache_directory.is_relative_to(data_root):
            raise RuntimeError("缓存目录超出 QinChecker 数据目录，已拒绝操作")
        return cache_directory

    def _open_cache_directory(self) -> None:
        try:
            cache_directory = self._cache_directory()
            cache_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._notify(f"无法打开缓存文件夹：{error}", "error", 10_000)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(cache_directory))):
            self._notify(f"系统未能打开缓存目录，请手动访问：{cache_directory}", "warning", 10_000)
            return
        self.statusBar().showMessage(f"已打开缓存文件夹：{cache_directory}")
        self._log_activity(f"已打开网页缓存文件夹：{cache_directory}")

    def _clear_web_cache(self) -> None:
        if self._cancel_event is not None:
            self._notify("处理任务运行期间不能清除网页缓存。", "warning")
            return
        try:
            cache_directory = self._cache_directory()
            cached_files = (
                sum(1 for path in cache_directory.rglob("*") if path.is_file())
                if cache_directory.exists()
                else 0
            )
        except OSError as error:
            self._notify(f"无法检查缓存：{error}", "error", 10_000)
            return
        if cached_files == 0:
            cache_directory.mkdir(parents=True, exist_ok=True)
            self._notify("当前网页缓存已经是空的。", "info")
            return
        answer = QMessageBox.question(
            self,
            "确认清除网页缓存",
            f"将删除 {cached_files} 个 iPlant 网页缓存文件。\n\n"
            "批次进度、人工复核决定和 Excel 不会被删除。\n"
            "下次处理相应植物时需要重新访问网站。\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(cache_directory)
            cache_directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self._notify(f"清除缓存失败：{error}", "error", 10_000)
            return
        message = f"网页缓存已清除：删除 {cached_files} 个文件。"
        self.statusBar().showMessage(message)
        self._set_activity("CACHE CLEARED", message)
        self._notify(message, "success")

    def _batch_elapsed_seconds(self) -> float:
        if self._batch_started_at is None:
            return 0.0
        return max(0.0, time.perf_counter() - self._batch_started_at)

    def _show_completion_notification(self, message: str) -> None:
        """通过系统托盘发送非阻塞完成通知；不可用时仍保留界面和日志提示。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._log_activity("系统通知不可用；完成信息已显示在右下角实时进度中。")
            return
        if self._notification_tray is None:
            self._notification_tray = QSystemTrayIcon(self)
            icon = self.windowIcon()
            if icon.isNull():
                icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
            self._notification_tray.setIcon(icon)
            self._notification_tray.setToolTip("QinChecker")
        self._notification_tray.show()
        self._notification_tray.showMessage(
            "QinChecker 比对完成",
            message,
            QSystemTrayIcon.MessageIcon.Information,
            10_000,
        )

    def _populate_directory(self) -> None:
        selected_row = self.current_row
        self.catalog.blockSignals(True)
        self.catalog.clear()
        if self.review_run is None:
            self.catalog.addItem("尚未加载物种")
            self.catalog.blockSignals(False)
            return
        query = self.search.text().strip().casefold()
        filter_key = str(self.catalog_filter.currentData() or "all")
        all_item = QListWidgetItem(f"全部物种（{len(self.review_run.records)}）")
        all_item.setData(Qt.ItemDataRole.UserRole, None)
        self.catalog.addItem(all_item)
        for record in self.review_run.records:
            display = f"{record.excel_row:>4}  {record.latin_name}  {record.chinese_name}"
            if query and query not in display.casefold() and query not in str(record.excel_row):
                continue
            proposals = self._proposals_for_row(record.excel_row)
            state = self._record_state(record)
            unresolved_count = sum(
                proposal.state in REVIEW_QUEUE_STATES for proposal in proposals
            )
            changed_count = sum(
                proposal.state in {
                    ReviewState.ACCEPTED_SOURCE,
                    ReviewState.MANUALLY_CONFIRMED,
                } and proposal.has_change
                for proposal in proposals
            )
            has_failed = any(proposal.state is ReviewState.FAILED for proposal in proposals)
            has_manual = record.excel_row in self.review_run.manual_sources
            completed = bool(proposals) and not unresolved_count
            matches_filter = {
                "all": True,
                "pending": unresolved_count > 0,
                "modified": changed_count > 0,
                "completed": completed,
                "failed": has_failed,
                "manual": has_manual,
            }.get(filter_key, True)
            if not matches_filter:
                continue
            manual_badge = "  [人工]" if has_manual else ""
            review_badge = f"待复核 {unresolved_count}" if unresolved_count else "已完成"
            item = QListWidgetItem(
                f"{record.latin_name or '-'}{manual_badge}\n"
                f"第{record.excel_row}行 · {record.chinese_name or '-'} · {review_badge}"
            )
            item.setForeground(QBrush(QColor(STATUS_COLORS[state.value])))
            item.setData(Qt.ItemDataRole.UserRole, record.excel_row)
            self.catalog.addItem(item)
        target_catalog_row = next(
            (
                index
                for index in range(self.catalog.count())
                if self.catalog.item(index).data(Qt.ItemDataRole.UserRole) == selected_row
            ),
            0,
        )
        self.catalog.setCurrentRow(target_catalog_row)
        self.catalog.blockSignals(False)

    def _catalog_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self.current_row = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self._table_page = 0
        self._populate_table()

    def _record_state(self, record: SpeciesRecord) -> ReviewState:
        proposals = self._proposals_for_row(record.excel_row)
        states = {proposal.state for proposal in proposals}
        for state in (ReviewState.FAILED, ReviewState.PENDING_REVIEW, ReviewState.MANUALLY_CONFIRMED,
                      ReviewState.ACCEPTED_SOURCE, ReviewState.AUTO_READY, ReviewState.NO_CHANGE):
            if state in states:
                return state
        return ReviewState.UNPROCESSED

    def _proposals_for_row(self, excel_row: int | None) -> list[FieldProposal]:
        if self.review_run is None:
            return []
        proposals = self.review_run.proposals
        return [proposal for proposal in proposals if excel_row is None or proposal.excel_row == excel_row]

    def _populate_table(self) -> None:
        all_proposals = self._proposals_for_row(self.current_row)
        if self.current_row is None:
            self.species_identity.setText("全部物种 · 分页显示字段")
            self.table.setColumnHidden(1, False)
            self.table.setColumnHidden(2, False)
            total_pages = max(
                1, (len(all_proposals) + ALL_SPECIES_PAGE_SIZE - 1) // ALL_SPECIES_PAGE_SIZE
            )
            self._table_page = min(self._table_page, total_pages - 1)
            start = self._table_page * ALL_SPECIES_PAGE_SIZE
            proposals = all_proposals[start:start + ALL_SPECIES_PAGE_SIZE]
            paging_visible = len(all_proposals) > ALL_SPECIES_PAGE_SIZE
            self.previous_page_button.setVisible(paging_visible)
            self.next_page_button.setVisible(paging_visible)
            self.table_page_label.setVisible(paging_visible)
            self.previous_page_button.setEnabled(self._table_page > 0)
            self.next_page_button.setEnabled(self._table_page + 1 < total_pages)
            self.table_page_label.setText(
                f"第 {self._table_page + 1}/{total_pages} 页 · 本页 {len(proposals)} / 总 {len(all_proposals)} 个字段"
            )
        else:
            record = self._current_record()
            self.species_identity.setText(
                f"{record.latin_name or '-'} · {record.chinese_name or '-'} · Excel第{record.excel_row}行"
                if record is not None
                else f"Excel第{self.current_row}行"
            )
            self.table.setColumnHidden(1, True)
            self.table.setColumnHidden(2, True)
            proposals = all_proposals
            self.previous_page_button.setVisible(False)
            self.next_page_button.setVisible(False)
            self.table_page_label.setVisible(False)
        self.table.setRowCount(len(proposals))
        for row, proposal in enumerate(proposals):
            values = [
                STATE_LABELS[proposal.state], str(proposal.excel_row), proposal.species_name,
                proposal.field.value, self._text(proposal.original_value), self._text(proposal.suggested_value),
                self._text(proposal.final_value), proposal.confidence.value,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setForeground(QBrush(QColor(STATUS_COLORS[proposal.state.value])))
                elif (
                    column in {3, 5, 6}
                    and proposal.has_change
                    and proposal.state in {
                        ReviewState.AUTO_READY,
                        ReviewState.ACCEPTED_SOURCE,
                        ReviewState.MANUALLY_CONFIRMED,
                    }
                ):
                    item.setForeground(QBrush(QColor("#D92D20")))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, proposal)
                self.table.setItem(row, column, item)
        enabled = bool(proposals)
        for button in (self.accept_button, self.keep_button, self.manual_button, self.restore_button):
            button.setEnabled(enabled)
        unresolved = [proposal for proposal in proposals if proposal.state in REVIEW_QUEUE_STATES]
        single_species = self.current_row is not None
        self._update_manual_source_buttons()
        self.accept_all_button.setEnabled(
            single_species and any(proposal.suggested_value is not None for proposal in unresolved)
        )
        self.keep_all_button.setEnabled(single_species and bool(unresolved))
        self.restore_all_button.setEnabled(
            single_species and any(proposal.action is not ProposalAction.NONE for proposal in proposals)
        )
        if proposals:
            self.table.selectRow(0)
        else:
            self.evidence.setHtml("<p style='color:#667085'>此物种尚无可显示的字段建议。</p>")

    def _table_item_double_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 6:
            self._manual_edit()

    def _change_table_page(self, delta: int) -> None:
        if self.current_row is not None:
            return
        total = len(self._proposals_for_row(None))
        total_pages = max(1, (total + ALL_SPECIES_PAGE_SIZE - 1) // ALL_SPECIES_PAGE_SIZE)
        target = min(max(self._table_page + delta, 0), total_pages - 1)
        if target == self._table_page:
            return
        self._table_page = target
        self._populate_table()
        self.statusBar().showMessage(
            f"全部物种字段视图：第 {self._table_page + 1}/{total_pages} 页。"
        )

    def _current_record(self) -> SpeciesRecord | None:
        if self.review_run is None or self.current_row is None:
            return None
        return next(
            (record for record in self.review_run.records if record.excel_row == self.current_row),
            None,
        )

    def _update_manual_source_buttons(self) -> None:
        record = self._current_record()
        running = self._cancel_event is not None
        has_manual = bool(
            record is not None
            and self.review_run is not None
            and record.excel_row in self.review_run.manual_sources
        )
        if record is None:
            self.manual_source_status.setText("请先在左侧目录中选择一个具体植物。")
        elif not has_manual:
            self.manual_source_status.setText(
                f"{record.latin_name or '-'}\n当前植物尚未导入人工来源。"
            )
        else:
            source = self.review_run.manual_sources[record.excel_row]  # type: ignore[union-attr]
            imported_sections = [
                label
                for key, label in (
                    ("foc", "FOC文本"),
                    ("nomenclature", "名称信息"),
                    ("county_distribution", "区县分布"),
                )
                if getattr(source, key).text.strip()
            ]
            self.manual_source_status.setText(
                f"{record.latin_name or '-'}\n"
                f"已导入：{'、'.join(imported_sections) or '无'}\n"
                f"导入时间：{source.imported_at:%Y-%m-%d %H:%M}"
            )
        self.import_manual_button.setEnabled(record is not None and not running and not has_manual)
        for button in (
            self.edit_manual_button,
            self.reparse_manual_button,
            self.delete_manual_button,
        ):
            button.setEnabled(record is not None and not running and has_manual)

    def _import_manual_source(self) -> None:
        self._show_manual_source_dialog(edit_existing=False)

    def _edit_manual_source(self) -> None:
        self._show_manual_source_dialog(edit_existing=True)

    def _show_manual_source_dialog(self, *, edit_existing: bool) -> None:
        record = self._current_record()
        run = self.review_run
        if record is None or run is None:
            self.statusBar().showMessage("请先在左侧目录中选择一个植物。")
            return
        if run.manual_source_store is None or run.manual_source_service is None:
            self._notify("当前批次缺少人工来源服务，请重新处理该批次。", "warning", 8_000)
            return
        existing = run.manual_sources.get(record.excel_row) if edit_existing else None
        if not edit_existing:
            web_proposals = self._proposals_for_row(record.excel_row)
            has_usable_web = any(
                proposal.source_name.startswith("iPlant")
                and proposal.state is not ReviewState.FAILED
                for proposal in web_proposals
            )
            if has_usable_web:
                answer = QMessageBox.question(
                    self,
                    "已有网页结果",
                    "当前植物已有可用网页结果。人工文本将替代当前植物全部17字段建议，是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer is not QMessageBox.StandardButton.Yes:
                    return
        dialog = ManualSourceDialog(
            record,
            lambda source: run.manual_source_service.parse(record, source),
            existing,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        assert dialog.result_source is not None
        self._apply_manual_source(record, dialog.result_source, dialog.result_proposals)

    def _apply_manual_source(
        self,
        record: SpeciesRecord,
        source: ManualSource,
        proposals: list[FieldProposal],
        *,
        confirm_decisions: bool = True,
    ) -> None:
        run = self.review_run
        if run is None or run.manual_source_store is None:
            return
        existing_decisions = sum(
            1
            for proposal in self._proposals_for_row(record.excel_row)
            if proposal.action is not ProposalAction.NONE
        )
        if existing_decisions and confirm_decisions:
            answer = QMessageBox.question(
                self,
                "重新生成当前植物",
                f"将清除当前植物 {existing_decisions} 个已有人工决定，并重新生成17字段。\n"
                "其他植物不受影响。是否应用？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
        for proposal in proposals:
            proposal.capture_baseline()
        run.proposals = [
            proposal for proposal in run.proposals if proposal.excel_row != record.excel_row
        ] + proposals
        run.manual_source_store.save(source)
        run.manual_sources[record.excel_row] = source
        run.decision_store.save(run.proposals)
        self.current_row = record.excel_row
        self._select_proposal(
            next(
                (proposal for proposal in proposals if proposal.state in REVIEW_QUEUE_STATES),
                proposals[0],
            )
        )
        message = f"第 {record.excel_row} 行已应用人工来源，重新生成 {len(proposals)} 个字段。"
        self.statusBar().showMessage(message)
        self._set_activity("MANUAL SOURCE APPLIED", message)

    def _reparse_manual_source(self) -> None:
        record = self._current_record()
        run = self.review_run
        if record is None or run is None or run.manual_source_service is None:
            return
        source = run.manual_sources.get(record.excel_row)
        if source is None:
            return
        proposals = run.manual_source_service.parse(record, source)
        if any(
            proposal.action is not ProposalAction.NONE
            for proposal in self._proposals_for_row(record.excel_row)
        ):
            answer = QMessageBox.question(
                self,
                "重新识别当前植物",
                "重新识别将清除当前植物已有的接受、保留和手动编辑决定。\n"
                "其他植物不受影响。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
        self._apply_manual_source(record, source, proposals, confirm_decisions=False)

    def _delete_manual_source(self) -> None:
        record = self._current_record()
        run = self.review_run
        if record is None or run is None or record.excel_row not in run.manual_sources:
            return
        answer = QMessageBox.question(
            self,
            "删除人工来源",
            "将删除当前植物的人工来源和相关复核决定，并恢复原网页缓存结果。\n"
            "如果网页没有结果，17字段将恢复为抓取失败。其他植物不受影响。\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return
        assert run.manual_source_store is not None
        run.manual_source_store.delete(record.worksheet_name, record.excel_row)
        run.manual_sources.pop(record.excel_row, None)
        snapshot = run.source_cache.load(record.latin_name) if run.source_cache is not None else None
        if snapshot is not None and run.manual_source_service is not None:
            proposals = run.manual_source_service.parser.parse(record, snapshot).proposals
        else:
            proposals = ReviewPipeline._failed_proposals(record, "删除人工来源后未找到原网页缓存")
        for proposal in proposals:
            proposal.capture_baseline()
        run.proposals = [
            proposal for proposal in run.proposals if proposal.excel_row != record.excel_row
        ] + proposals
        run.decision_store.save(run.proposals)
        self._select_proposal(proposals[0])
        message = f"第 {record.excel_row} 行人工来源已删除，已恢复原网页结果。"
        self.statusBar().showMessage(message)
        self._set_activity("MANUAL SOURCE DELETED", message)

    @staticmethod
    def _text(value: object) -> str:
        return "—" if value in (None, "") else str(value)

    def _selected_proposal(self) -> FieldProposal | None:
        selected = self.table.selectedItems()
        if not selected:
            return None
        item = self.table.item(selected[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _show_evidence(self) -> None:
        proposal = self._selected_proposal()
        if proposal is None:
            return
        url = escape(proposal.source_url)
        link = f'<a href="{url}">{url}</a>' if url else "无可访问网址"
        new_value = proposal.final_value if proposal.final_value is not None else proposal.suggested_value
        excerpt = proposal.source_excerpt or "网站未提供该字段原文"
        excerpt_summary = excerpt if len(excerpt) <= 180 else f"{excerpt[:180].rstrip()}……"
        self.evidence.setHtml(
            f"<h3>{escape(proposal.species_name)} · {escape(proposal.field.value)}</h3>"
            f"<p><b>原值：</b><br>{escape(self._text(proposal.original_value))}</p>"
            f"<p><b>新值：</b><br>{escape(self._text(new_value))}</p>"
            f"<p><b>来源类型：</b><br>{escape(proposal.source_name or '未标明')}</p>"
            f"<hr><p><b>网址：</b><br>{link}</p>"
            f"<p><b>原文摘要：</b><br>{escape(excerpt_summary)}</p>"
        )
        self.source_excerpt.setPlainText(excerpt)
        self.toggle_excerpt_button.blockSignals(True)
        self.toggle_excerpt_button.setChecked(False)
        self.toggle_excerpt_button.blockSignals(False)
        self.toggle_excerpt_button.setText("展开来源原文")
        self.toggle_excerpt_button.setEnabled(bool(proposal.source_excerpt))
        self.source_excerpt.setVisible(False)
        self.evidence_tabs.setCurrentIndex(0)
        self.accept_button.setEnabled(proposal.suggested_value is not None)
        self.keep_button.setEnabled(True)
        self.manual_button.setEnabled(True)
        self.restore_button.setEnabled(True)

    def _toggle_source_excerpt(self, expanded: bool) -> None:
        self.source_excerpt.setVisible(expanded)
        self.toggle_excerpt_button.setText("收起来源原文" if expanded else "展开来源原文")

    def _apply_decision(self, operation: str) -> None:
        proposal = self._selected_proposal()
        if proposal is None or self.review_run is None:
            return
        try:
            if operation == "accept":
                proposal.accept_source()
            elif operation == "keep":
                proposal.keep_original()
            elif operation == "restore":
                proposal.reset_to_suggestion()
        except ValueError as error:
            self._notify(f"无法执行：{error}", "warning")
            return
        self.review_run.decision_store.save(self.review_run.proposals)
        self._log_activity(f"第 {proposal.excel_row} 行 {proposal.field.value}：{operation}")
        self._refresh_after_decision(proposal, advance=operation in {"accept", "keep"})

    def _accept_source(self) -> None:
        self._apply_decision("accept")

    def _keep_original(self) -> None:
        self._apply_decision("keep")

    def _restore_suggestion(self) -> None:
        self._apply_decision("restore")

    def _accept_all_current_species(self) -> None:
        self._apply_bulk_species_decision(accept_source=True)

    def _keep_all_current_species(self) -> None:
        self._apply_bulk_species_decision(accept_source=False)

    def _restore_all_current_species(self) -> None:
        if self.review_run is None or self.current_row is None:
            self.statusBar().showMessage("请先在左侧目录中选择一个物种。")
            return
        row_proposals = self._proposals_for_row(self.current_row)
        modified = [
            proposal for proposal in row_proposals
            if proposal.action is not ProposalAction.NONE
        ]
        if not modified:
            self.statusBar().showMessage("当前植物没有需要恢复的人工修改。")
            return
        for proposal in row_proposals:
            proposal.reset_to_suggestion()
        self.review_run.decision_store.save(self.review_run.proposals)
        restored_count = len(modified)
        current_row = self.current_row
        self._log_activity(f"第 {current_row} 行：全部恢复修改 {restored_count} 个字段")
        self._populate_table()
        self._select_proposal(row_proposals[0])
        self.statusBar().showMessage(
            f"当前植物已恢复：撤销 {restored_count} 个字段的人工修改。"
        )

    def _apply_bulk_species_decision(self, *, accept_source: bool) -> None:
        if self.review_run is None or self.current_row is None:
            self.statusBar().showMessage("请先在左侧目录中选择一个物种。")
            return
        row_proposals = self._proposals_for_row(self.current_row)
        unresolved = [proposal for proposal in row_proposals if proposal.state in REVIEW_QUEUE_STATES]
        targets = (
            [proposal for proposal in unresolved if proposal.suggested_value is not None]
            if accept_source
            else unresolved
        )
        if not targets:
            self.statusBar().showMessage(
                "当前物种没有可批量接受的来源建议。"
                if accept_source
                else "当前物种已经没有尚未处理的字段。"
            )
            return
        for proposal in targets:
            if accept_source:
                proposal.accept_source()
            else:
                proposal.keep_original()
        self.review_run.decision_store.save(self.review_run.proposals)
        action = "全部接受" if accept_source else "全部保留"
        self._log_activity(f"第 {self.current_row} 行：{action} {len(targets)} 个字段")
        next_proposal = self._next_review_after_species(self.current_row)
        self._select_proposal(next_proposal or row_proposals[0])
        if next_proposal is not None:
            self.statusBar().showMessage(
                f"{action}完成 {len(targets)} 个字段；已跳转到第 "
                f"{next_proposal.excel_row} 行 {next_proposal.field.value}。"
            )
        else:
            self.statusBar().showMessage(
                f"{action}完成 {len(targets)} 个字段；当前批次已没有需要复核的字段。"
            )

    def _manual_edit(self) -> None:
        proposal = self._selected_proposal()
        if proposal is None or self.review_run is None:
            return
        initial = self._text(proposal.final_value if proposal.final_value is not None else proposal.suggested_value)
        value, accepted = QInputDialog.getText(self, "手动编辑最终值", f"{proposal.field.value}：", text=initial)
        if not accepted:
            return
        proposal.confirm_manual_value(value)
        self.review_run.decision_store.save(self.review_run.proposals)
        self._log_activity(f"第 {proposal.excel_row} 行 {proposal.field.value}：手动编辑")
        self._refresh_after_decision(proposal, advance=True)

    def _next_review_proposal(self, current: FieldProposal) -> FieldProposal | None:
        if self.review_run is None:
            return None
        proposals = self.review_run.proposals
        current_index = next(
            (index for index, item in enumerate(proposals) if item is current or item.key == current.key),
            -1,
        )
        if current_index < 0:
            ordered = proposals
        else:
            ordered = proposals[current_index + 1:] + proposals[:current_index]
        return next((item for item in ordered if item.state in REVIEW_QUEUE_STATES), None)

    def _next_review_after_species(self, excel_row: int) -> FieldProposal | None:
        if self.review_run is None:
            return None
        proposals = self.review_run.proposals
        same_species = [
            proposal for proposal in proposals
            if proposal.excel_row == excel_row and proposal.state in REVIEW_QUEUE_STATES
        ]
        if same_species:
            return same_species[0]
        row_indexes = [index for index, proposal in enumerate(proposals) if proposal.excel_row == excel_row]
        start = max(row_indexes) + 1 if row_indexes else 0
        ordered = proposals[start:] + proposals[:start]
        return next((item for item in ordered if item.state in REVIEW_QUEUE_STATES), None)

    def _select_proposal(self, proposal: FieldProposal) -> None:
        self._populate_directory()
        catalog_row = next(
            (
                row for row in range(self.catalog.count())
                if self.catalog.item(row).data(Qt.ItemDataRole.UserRole) == proposal.excel_row
            ),
            -1,
        )
        if catalog_row < 0 and self.search.text():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self._populate_directory()
            catalog_row = next(
                (
                    row for row in range(self.catalog.count())
                    if self.catalog.item(row).data(Qt.ItemDataRole.UserRole) == proposal.excel_row
                ),
                -1,
            )
        if catalog_row >= 0:
            self.catalog.setCurrentRow(catalog_row)
        else:
            self.current_row = proposal.excel_row
            self._populate_table()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            row_proposal = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if row_proposal is proposal or (row_proposal is not None and row_proposal.key == proposal.key):
                self.table.selectRow(row)
                self.table.scrollToItem(item)
                break

    def _refresh_after_decision(self, proposal: FieldProposal, *, advance: bool) -> None:
        target = self._next_review_proposal(proposal) if advance else None
        self._select_proposal(target or proposal)
        if target is not None:
            self.statusBar().showMessage(
                f"字段决定已保存；已跳转到第 {target.excel_row} 行 {target.field.value}。"
            )
        elif advance:
            self.statusBar().showMessage("字段决定已保存；当前批次已没有需要复核的字段。")
        else:
            self.statusBar().showMessage("字段建议已恢复。")

    def _export(self) -> None:
        if self.review_run is None or self.input_path is None:
            return
        default = self.input_path.with_name("已核对.xlsx")
        path, _ = QFileDialog.getSaveFileName(self, "导出已核对文件", str(default), "Excel 文件 (*.xlsx)")
        if not path:
            return
        output_path = Path(path)
        self.export_button.setEnabled(False)
        self._thread = QThread(self)
        worker = ExportWorker(self.input_path, output_path, self.review_run.records, self.review_run.proposals)
        self._worker = worker
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.completed.connect(self._export_complete)
        worker.failed.connect(self._export_failed)
        worker.completed.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._thread.finished.connect(lambda: self._release_worker(worker))
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self._set_workflow_stage(4)
        self.statusBar().showMessage("正在导出新 Excel 和同名说明 TXT……")
        self._log_activity(f"开始导出：{output_path.name}")

    def _export_complete(self, path: str) -> None:
        self.export_button.setEnabled(True)
        self.statusBar().showMessage("导出完成。")
        self._log_activity(f"导出完成：{Path(path).name}")
        self._notify(f"导出完成：已创建 {path} 以及同名说明 TXT。", "success", 10_000)

    def _export_failed(self, detail: str) -> None:
        self.export_button.setEnabled(True)
        self.statusBar().showMessage("导出失败。")
        self._log_activity(f"导出失败：{detail}")
        self._set_workflow_stage(3)
        self._notify(f"导出失败：{detail}", "error", 12_000)

    def _export_execution_log(self) -> None:
        default_name = "QinChecker执行日志.txt"
        if self.input_path is not None:
            default_name = f"{self.input_path.stem}_执行日志.txt"
        default_path = self.data_root / default_name
        path, _ = QFileDialog.getSaveFileName(self, "导出执行日志", str(default_path), "文本文件 (*.txt)")
        if not path:
            return
        content = self.execution_log.toPlainText().strip()
        header = f"QinChecker 执行日志\n导出时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
        try:
            Path(path).write_text(header + (content or "（暂无日志）") + "\n", encoding="utf-8")
        except OSError as error:
            self._log_activity(f"执行日志导出失败：{error}")
            self._notify(f"执行日志导出失败：{error}", "error", 10_000)
            return
        self._log_activity(f"执行日志已导出：{Path(path).name}")
        self._notify(f"执行日志已保存：{path}", "success", 8_000)

    def _log_activity(self, message: str) -> None:
        self.execution_log.appendPlainText(message)
        scrollbar = self.execution_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _set_activity(self, command: str, detail: str) -> None:
        self._last_activity_at = time.monotonic()
        self.current_command.setPlainText(command)
        self.activity_detail.setText(detail)
        self._log_activity(f"{command}\n{detail}")

    def _check_processing_timeout(self) -> None:
        if self._last_activity_at is None or self._cancel_event is None or self._cancel_event.is_set():
            return
        idle_seconds = time.monotonic() - self._last_activity_at
        if idle_seconds >= 60:
            self._request_stop(
                "TIMEOUT > 60s",
                "超过 60 秒没有任何进度更新，已自动请求安全停止。",
            )

    def _release_worker(self, worker: QObject) -> None:
        """在线程结束后才放开 Python 对 Worker 的强引用。"""
        if self._worker is worker:
            self._worker = None
