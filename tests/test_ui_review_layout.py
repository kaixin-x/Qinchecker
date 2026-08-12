from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFrame, QMessageBox, QSplitter

from qinchecker.models.review import Confidence, FieldKey, FieldProposal, ReviewState, SpeciesRecord
from qinchecker.services.decision_store import DecisionStore
from qinchecker.services.review_pipeline import ReviewRun
from qinchecker.services.manual_source import (
    ManualSection,
    ManualSource,
    ManualSourceService,
    ManualSourceStore,
)
from qinchecker.services.parsing import CountyIndex, HabitatGlossary, SourceParser
from qinchecker.services.cache import SourceCache
from qinchecker.ui.main_window import MainWindow, ManualSourceDialog


class ReviewLayoutTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_catalog_is_narrower_and_source_shows_only_values_url_and_original_text(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow()
            values = {field.value: "原始内容" for field in FieldKey}
            values[FieldKey.SPECIES_LATIN.value] = "Populus old"
            values[FieldKey.SPECIES_CHINESE.value] = "旧杨"
            record = SpeciesRecord("Sheet1", 2, values)
            proposal = FieldProposal(
                worksheet_name="Sheet1",
                excel_row=2,
                species_name="Populus old",
                field=FieldKey.HABITAT,
                original_value="原生境很长的内容",
                suggested_value="FOC 建议的新生境内容",
                final_value="FOC 建议的新生境内容",
                confidence=Confidence.HIGH,
                state=ReviewState.AUTO_READY,
                source_url="https://example.test/foc",
                source_excerpt="Mountain slopes and forests.",
                parser_rule="不应显示在来源面板",
            )
            window.review_run = ReviewRun(
                [record], [proposal], DecisionStore(Path(directory) / "decisions.json")
            )
            window._populate_directory()
            window._populate_table()
            window.show()
            self.app.processEvents()

            horizontal = next(
                splitter for splitter in window.findChildren(QSplitter)
                if splitter.orientation().name == "Horizontal"
            )
            sizes = horizontal.sizes()
            self.assertLess(sizes[0], sizes[1])
            self.assertGreater(sizes[1], sizes[2])

            text = window.evidence.toPlainText()
            self.assertIn("原值：", text)
            self.assertIn("原生境很长的内容", text)
            self.assertIn("新值：", text)
            self.assertIn("FOC 建议的新生境内容", text)
            self.assertIn("https://example.test/foc", text)
            self.assertIn("Mountain slopes and forests.", text)
            self.assertNotIn("不应显示在来源面板", text)
            self.assertEqual(window.table.item(0, 3).foreground().color().name(), "#d92d20")
            self.assertEqual(window.table.item(0, 6).foreground().color().name(), "#d92d20")
            window.close()

    def test_first_phase_review_layout_and_single_species_columns(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(
                QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            )
            values = {field.value: "-" for field in FieldKey}
            values.update({"Species": "Populus test", "物种": "测试杨"})
            record = SpeciesRecord("Sheet1", 2, values)
            proposal = FieldProposal(
                "Sheet1", 2, "Populus test", FieldKey.HABITAT, "旧生境",
                suggested_value="新生境", final_value="新生境",
                confidence=Confidence.HIGH, state=ReviewState.PENDING_REVIEW,
            )
            window.review_run = ReviewRun(
                [record], [proposal], DecisionStore(Path(directory) / "decisions.json")
            )

            self.assertEqual(window.evidence_tabs.count(), 3)
            self.assertEqual(len(window.findChildren(QFrame, "actionGroup")), 2)

            window.current_row = 2
            window._populate_table()
            self.assertTrue(window.table.isColumnHidden(1))
            self.assertTrue(window.table.isColumnHidden(2))
            self.assertIn("Populus test", window.species_identity.text())
            self.assertIn("2", window.species_identity.text())

            with patch.object(window, "_manual_edit") as manual_edit:
                window._table_item_double_clicked(window.table.item(0, 6))
                manual_edit.assert_called_once_with()

            window.current_row = None
            window._populate_table()
            self.assertFalse(window.table.isColumnHidden(1))
            self.assertFalse(window.table.isColumnHidden(2))
            window.close()

    def test_second_phase_manual_dialog_has_three_columns_and_stage_indicator(self) -> None:
        values = {field.value: "-" for field in FieldKey}
        values.update({"Species": "Populus manual", "物种": "人工杨"})
        record = SpeciesRecord("Sheet1", 2, values)

        def parse(source: ManualSource) -> list[FieldProposal]:
            return [
                FieldProposal(
                    "Sheet1", 2, "Populus manual", FieldKey.HABITAT, "-",
                    suggested_value="山坡", final_value="山坡",
                    confidence=Confidence.HIGH, state=ReviewState.PENDING_REVIEW,
                    source_excerpt=source.foc.text,
                )
            ]

        dialog = ManualSourceDialog(record, parse)
        columns = dialog.findChildren(QFrame, "manualSourceColumn")
        self.assertEqual(len(columns), 3)
        self.assertEqual(dialog.current_stage, 1)
        dialog.section_editors["foc"][0].setPlainText("Mountain slopes.")
        dialog._preview()
        self.assertEqual(dialog.current_stage, 3)
        self.assertTrue(dialog.apply_button.isEnabled())
        self.assertIn("预览完成", dialog.inline_message.text())
        self.assertGreaterEqual(dialog.preview_table.verticalHeader().defaultSectionSize(), 48)
        dialog.close()

    def test_second_phase_workflow_notice_and_source_excerpt_expand(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(
                QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            )
            values = {field.value: "-" for field in FieldKey}
            values.update({"Species": "Populus test", "物种": "测试杨"})
            record = SpeciesRecord("Sheet1", 2, values)
            long_excerpt = "Mountain forests and slopes. " * 20
            proposal = FieldProposal(
                "Sheet1", 2, "Populus test", FieldKey.HABITAT, "旧值",
                suggested_value="新值", final_value="新值",
                confidence=Confidence.HIGH, state=ReviewState.PENDING_REVIEW,
                source_excerpt=long_excerpt,
            )
            window.review_run = ReviewRun(
                [record], [proposal], DecisionStore(Path(directory) / "decisions.json")
            )
            window.current_row = 2
            window._populate_table()
            window.show()
            self.app.processEvents()

            self.assertEqual(window.workflow_stage_labels[0].property("stageState"), "active")
            self.assertGreaterEqual(window.table.verticalHeader().defaultSectionSize(), 46)
            self.assertIn("……", window.evidence.toPlainText())
            self.assertFalse(window.source_excerpt.isVisible())
            window.toggle_excerpt_button.setChecked(True)
            self.assertTrue(window.source_excerpt.isVisible())
            self.assertEqual(window.source_excerpt.toPlainText(), long_excerpt)

            window._notify("非阻塞提示", "success", 10_000)
            self.assertTrue(window.notice_banner.isVisible())
            self.assertEqual(window.notice_banner.text(), "非阻塞提示")
            window.close()

    def test_manual_source_filter_and_catalog_badge(self) -> None:
        with TemporaryDirectory() as directory:
            window = MainWindow(
                QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            )
            values = {field.value: "-" for field in FieldKey}
            values.update({"Species": "Populus manual", "物种": "人工杨"})
            record = SpeciesRecord("Sheet1", 2, values)
            proposal = FieldProposal(
                "Sheet1", 2, "Populus manual", FieldKey.HABITAT, "-",
                state=ReviewState.PENDING_REVIEW,
            )
            manual = ManualSource("Sheet1", 2, "Populus manual", "人工杨")
            window.review_run = ReviewRun(
                [record], [proposal], DecisionStore(Path(directory) / "decisions.json"),
                manual_sources={2: manual},
            )
            window.catalog_filter.setCurrentIndex(window.catalog_filter.findData("manual"))
            window._populate_directory()

            self.assertEqual(window.catalog.count(), 2)
            self.assertIn("[人工]", window.catalog.item(1).text())
            self.assertIn("\n", window.catalog.item(1).text())
            window.close()

    def test_shortcuts_can_be_customized_and_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.ini"
            window = MainWindow(QSettings(str(settings_path), QSettings.Format.IniFormat))
            self.assertEqual(window._shortcut_values["accept"], "Alt+A")
            window._apply_shortcut_values(
                {"accept": "Ctrl+1", "keep": "Ctrl+2", "manual": "Ctrl+3"},
                persist=True,
            )
            self.assertIn("Ctrl+1", window.accept_button.text())
            window.close()

            restored = MainWindow(QSettings(str(settings_path), QSettings.Format.IniFormat))
            self.assertEqual(restored._shortcut_values["accept"], "Ctrl+1")
            self.assertEqual(restored._shortcut_values["keep"], "Ctrl+2")
            self.assertEqual(restored._shortcut_values["manual"], "Ctrl+3")
            restored.close()

    def test_completed_batch_shows_elapsed_time_and_sends_notification(self) -> None:
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            records = []
            for row, latin_name in ((2, "Populus first"), (3, "Populus second")):
                values = {field.value: "-" for field in FieldKey}
                values[FieldKey.SPECIES_LATIN.value] = latin_name
                records.append(SpeciesRecord("Sheet1", row, values))
            run = ReviewRun(
                records,
                [],
                DecisionStore(Path(directory) / "decisions.json"),
            )
            notifications: list[str] = []
            window._batch_elapsed_seconds = lambda: 12.34  # type: ignore[method-assign]
            window._show_completion_notification = notifications.append  # type: ignore[method-assign]

            window._review_complete(run)

            expected = "完成了 2 种植物比对，用时 12.34 秒。"
            self.assertEqual(window.activity_detail.text(), expected)
            self.assertIn("BATCH COMPLETE 2 SPECIES", window.current_command.toPlainText())
            self.assertIn("用时 12.34 秒", window.statusBar().currentMessage())
            self.assertEqual(notifications, [expected])
            self.assertIn(expected, window.execution_log.toPlainText())
            window.close()

    def test_review_decision_jumps_to_next_field_across_species(self) -> None:
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            first_values = {field.value: "-" for field in FieldKey}
            first_values.update({"Species": "Populus first", "物种": "第一种"})
            second_values = {field.value: "-" for field in FieldKey}
            second_values.update({"Species": "Populus second", "物种": "第二种"})
            first_record = SpeciesRecord("Sheet1", 2, first_values)
            second_record = SpeciesRecord("Sheet1", 3, second_values)
            first = FieldProposal(
                "Sheet1", 2, "Populus first", FieldKey.HABITAT, "-",
                suggested_value="山坡", final_value="山坡", confidence=Confidence.HIGH,
                state=ReviewState.AUTO_READY,
            )
            second = FieldProposal(
                "Sheet1", 3, "Populus second", FieldKey.COUNTIES, "-",
                confidence=Confidence.NONE, state=ReviewState.PENDING_REVIEW,
            )
            window.review_run = ReviewRun(
                [first_record, second_record],
                [first, second],
                DecisionStore(Path(directory) / "decisions.json"),
            )
            window.search.setText("Populus first")
            window._populate_table()
            window.table.selectRow(0)
            window._keep_original()

            self.assertEqual(first.state, ReviewState.KEPT_ORIGINAL)
            self.assertEqual(window.current_row, 3)
            self.assertEqual(window._selected_proposal(), second)
            self.assertEqual(window.search.text(), "")
            self.assertIn("第 3 行", window.statusBar().currentMessage())
            window.close()

    def test_single_species_bulk_accept_and_keep_preserve_prior_decisions(self) -> None:
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            first_values = {field.value: "-" for field in FieldKey}
            first_values.update({"Species": "Populus first", "物种": "第一种"})
            second_values = {field.value: "-" for field in FieldKey}
            second_values.update({"Species": "Populus second", "物种": "第二种"})
            first_record = SpeciesRecord("Sheet1", 2, first_values)
            second_record = SpeciesRecord("Sheet1", 3, second_values)
            available = FieldProposal(
                "Sheet1", 2, "Populus first", FieldKey.HABITAT, "-",
                suggested_value="山坡", final_value="山坡", confidence=Confidence.HIGH,
                state=ReviewState.AUTO_READY,
            )
            missing = FieldProposal(
                "Sheet1", 2, "Populus first", FieldKey.COUNTIES, "-",
                confidence=Confidence.NONE, state=ReviewState.PENDING_REVIEW,
            )
            prior_manual = FieldProposal(
                "Sheet1", 2, "Populus first", FieldKey.FAMILY_LATIN, "OldFamily",
                suggested_value="SuggestedFamily", final_value="ManualFamily",
                confidence=Confidence.MEDIUM, state=ReviewState.MANUALLY_CONFIRMED,
            )
            following = FieldProposal(
                "Sheet1", 3, "Populus second", FieldKey.HABITAT, "-",
                confidence=Confidence.NONE, state=ReviewState.PENDING_REVIEW,
            )
            window.review_run = ReviewRun(
                [first_record, second_record],
                [available, missing, prior_manual, following],
                DecisionStore(Path(directory) / "decisions.json"),
            )
            window._populate_directory()
            window.catalog.setCurrentRow(1)

            self.assertTrue(window.accept_all_button.isEnabled())
            self.assertTrue(window.keep_all_button.isEnabled())
            window._accept_all_current_species()
            self.assertEqual(available.state, ReviewState.ACCEPTED_SOURCE)
            self.assertEqual(missing.state, ReviewState.PENDING_REVIEW)
            self.assertEqual(prior_manual.final_value, "ManualFamily")
            self.assertEqual(window._selected_proposal(), missing)

            window._keep_all_current_species()
            self.assertEqual(missing.state, ReviewState.KEPT_ORIGINAL)
            self.assertEqual(prior_manual.final_value, "ManualFamily")
            self.assertEqual(window.current_row, 3)
            self.assertEqual(window._selected_proposal(), following)
            window.close()

    def test_restore_all_current_species_clears_only_its_manual_decisions(self) -> None:
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            first_values = {field.value: "-" for field in FieldKey}
            first_values.update({"Species": "Populus first", "物种": "第一种"})
            second_values = {field.value: "-" for field in FieldKey}
            second_values.update({"Species": "Populus second", "物种": "第二种"})
            first_record = SpeciesRecord("Sheet1", 2, first_values)
            second_record = SpeciesRecord("Sheet1", 3, second_values)
            automatic = FieldProposal(
                "Sheet1", 2, "Populus first", FieldKey.HABITAT, "-",
                suggested_value="山坡", final_value="山坡", confidence=Confidence.HIGH,
                state=ReviewState.AUTO_READY,
            )
            failed = FieldProposal(
                "Sheet1", 2, "Populus first", FieldKey.COUNTIES, "-",
                confidence=Confidence.NONE, state=ReviewState.FAILED,
            )
            other_species = FieldProposal(
                "Sheet1", 3, "Populus second", FieldKey.HABITAT, "旧值",
                suggested_value="新值", final_value="新值", confidence=Confidence.HIGH,
                state=ReviewState.ACCEPTED_SOURCE,
            )
            for proposal in (automatic, failed):
                proposal.capture_baseline()
            automatic.confirm_manual_value("人工山坡")
            failed.keep_original()
            decision_path = Path(directory) / "decisions.json"
            store = DecisionStore(decision_path)
            run = ReviewRun(
                [first_record, second_record], [automatic, failed, other_species], store
            )
            store.save(run.proposals)
            window.review_run = run
            window._populate_directory()
            window.catalog.setCurrentRow(1)

            self.assertTrue(window.restore_all_button.isEnabled())
            window._restore_all_current_species()

            self.assertEqual(automatic.state, ReviewState.AUTO_READY)
            self.assertEqual(automatic.final_value, "山坡")
            self.assertEqual(failed.state, ReviewState.FAILED)
            self.assertIsNone(failed.final_value)
            self.assertEqual(other_species.state, ReviewState.ACCEPTED_SOURCE)
            self.assertIn(other_species.key, decision_path.read_text(encoding="utf-8"))
            self.assertNotIn(automatic.key, decision_path.read_text(encoding="utf-8"))
            self.assertFalse(window.restore_all_button.isEnabled())
            window.close()

    def test_all_species_table_is_paginated_to_bound_widget_memory(self) -> None:
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            records: list[SpeciesRecord] = []
            proposals: list[FieldProposal] = []
            fields = list(FieldKey)
            for index in range(301):
                excel_row = 2 + index // len(fields)
                if not records or records[-1].excel_row != excel_row:
                    values = {field.value: "-" for field in FieldKey}
                    values[FieldKey.SPECIES_LATIN.value] = f"Species {excel_row}"
                    records.append(SpeciesRecord("Sheet1", excel_row, values))
                proposals.append(
                    FieldProposal(
                        "Sheet1",
                        excel_row,
                        f"Species {excel_row}",
                        fields[index % len(fields)],
                        "-",
                        state=ReviewState.PENDING_REVIEW,
                    )
                )
            window.review_run = ReviewRun(
                records, proposals, DecisionStore(Path(directory) / "decisions.json")
            )
            window.current_row = None
            window._populate_table()
            self.assertEqual(window.table.rowCount(), 300)
            self.assertFalse(window.next_page_button.isHidden())
            window._change_table_page(1)
            self.assertEqual(window.table.rowCount(), 1)
            self.assertIn("第 2/2 页", window.table_page_label.text())
            window.close()

    def test_clear_cache_button_removes_only_web_cache(self) -> None:
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            window.data_root = Path(directory)
            cache_directory = Path(directory) / "sessions" / "cache"
            cache_directory.mkdir(parents=True)
            (cache_directory / "snapshot.json").write_text("{}", encoding="utf-8")
            decision_path = Path(directory) / "sessions" / "review_test" / "decisions.json"
            decision_path.parent.mkdir(parents=True)
            decision_path.write_text("{}", encoding="utf-8")

            with (
                patch(
                    "qinchecker.ui.main_window.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ),
                patch("qinchecker.ui.main_window.QMessageBox.information"),
            ):
                window._clear_web_cache()

            self.assertTrue(cache_directory.exists())
            self.assertEqual(list(cache_directory.iterdir()), [])
            self.assertTrue(decision_path.exists())
            self.assertIn("删除 1 个文件", window.statusBar().currentMessage())
            window.close()

    def test_apply_manual_source_replaces_only_current_species(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            values1 = {field.value: "-" for field in FieldKey} | {
                "Species": "Populus first", "物种": "第一杨"
            }
            values2 = {field.value: "-" for field in FieldKey} | {
                "Species": "Populus second", "物种": "第二杨"
            }
            first_record = SpeciesRecord("Sheet1", 2, values1)
            second_record = SpeciesRecord("Sheet1", 3, values2)
            first_old = FieldProposal(
                "Sheet1", 2, "Populus first", FieldKey.HABITAT, "-",
                confidence=Confidence.NONE, state=ReviewState.FAILED,
            )
            second = FieldProposal(
                "Sheet1", 3, "Populus second", FieldKey.HABITAT, "-",
                suggested_value="原第二建议", final_value="原第二建议",
                confidence=Confidence.HIGH, state=ReviewState.AUTO_READY,
            )
            parser = SourceParser(
                HabitatGlossary.from_csv(Path(__file__).resolve().parents[1] / "config" / "habitat_terms.csv"),
                CountyIndex.from_csv(
                    Path(__file__).resolve().parents[1] / "config" / "qinling_counties.csv",
                    Path(__file__).resolve().parents[1] / "config" / "admin_aliases.csv",
                ),
            )
            service = ManualSourceService(parser)
            store = ManualSourceStore(root / "manual_sources")
            run = ReviewRun(
                [first_record, second_record],
                [first_old, second],
                DecisionStore(root / "decisions.json"),
                manual_source_store=store,
                manual_source_service=service,
                source_cache=SourceCache(root / "cache"),
            )
            source = ManualSource(
                "Sheet1", 2, "Populus first", "第一杨",
                foc=ManualSection("Trees; mountain slopes; 300-2500 m.", "人工FOC"),
            )
            proposals = service.parse(first_record, source)
            window.review_run = run
            window.current_row = 2
            window._apply_manual_source(first_record, source, proposals)

            self.assertEqual(len([item for item in run.proposals if item.excel_row == 2]), len(FieldKey))
            self.assertIs(next(item for item in run.proposals if item.excel_row == 3), second)
            self.assertIn(2, run.manual_sources)
            self.assertIsNotNone(store.load("Sheet1", 2))
            self.assertTrue(all(item.source_name.startswith("人工导入") for item in run.proposals if item.excel_row == 2))
            window.close()
