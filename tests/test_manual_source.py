from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from qinchecker.models.review import FieldKey, SpeciesRecord
from qinchecker.services.manual_source import (
    ManualSection,
    ManualSource,
    ManualSourceService,
    ManualSourceStore,
)
from qinchecker.services.parsing import CountyIndex, HabitatGlossary, SourceParser
from qinchecker.services.review_pipeline import ReviewPipeline
from qinchecker.services.workbook import WorkbookData, WorkbookService


ROOT = Path(__file__).resolve().parents[1]


class ManualSourceTests(TestCase):
    def setUp(self) -> None:
        self.record = SpeciesRecord(
            "Sheet1",
            26,
            {field.value: "-" for field in FieldKey}
            | {"Species": "Populus manualis", "物种": "人工杨"},
        )
        parser = SourceParser(
            HabitatGlossary.from_csv(ROOT / "config" / "habitat_terms.csv"),
            CountyIndex.from_csv(
                ROOT / "config" / "qinling_counties.csv",
                ROOT / "config" / "admin_aliases.csv",
            ),
        )
        self.service = ManualSourceService(parser)

    def source(self) -> ManualSource:
        return ManualSource(
            "Sheet1",
            26,
            "Populus manualis",
            "人工杨",
            foc=ManualSection(
                "FOC >> Vol.4 >> Salicaceae >> Populus\n"
                "9. Populus manualis Author\n"
                "Trees; mountain slopes and forests; 300-2500 m. China.",
                "人工复制FOC",
                "https://example.test/manual-foc",
            ),
            county_distribution=ManualSection(
                "陕西：周至县、洋县、户县",
                "调查资料",
                "https://example.test/counties",
            ),
        )

    def test_store_is_row_bound_and_round_trips(self) -> None:
        with TemporaryDirectory() as directory:
            store = ManualSourceStore(Path(directory))
            source = self.source()
            path = store.save(source)
            restored = store.load("Sheet1", 26)
            self.assertTrue(path.exists())
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.foc.text, source.foc.text)
            self.assertEqual(restored.foc.source_name, "人工复制FOC")
            self.assertIsNone(store.load("Sheet1", 27))
            self.assertTrue(store.delete("Sheet1", 26))
            self.assertIsNone(store.load("Sheet1", 26))

    def test_manual_text_reuses_parser_and_displays_all_fields(self) -> None:
        proposals = self.service.parse(self.record, self.source())
        self.assertEqual(len(proposals), len(FieldKey))
        self.assertEqual({proposal.field for proposal in proposals}, set(FieldKey))
        by_field = {proposal.field: proposal for proposal in proposals}
        self.assertEqual(by_field[FieldKey.LOWEST_ELEVATION].suggested_value, 300)
        self.assertEqual(by_field[FieldKey.HIGHEST_ELEVATION].suggested_value, 2500)
        self.assertIn("鄠邑区", str(by_field[FieldKey.COUNTIES].suggested_value))
        self.assertTrue(by_field[FieldKey.HABITAT].source_name.startswith("人工导入"))
        self.assertEqual(
            by_field[FieldKey.HABITAT].source_url,
            "https://example.test/manual-foc",
        )
        self.assertEqual(
            by_field[FieldKey.COUNTIES].source_url,
            "https://example.test/counties",
        )

    def test_nomenclature_text_works_without_foc(self) -> None:
        source = ManualSource(
            "Sheet1",
            26,
            "Populus manualis",
            "人工杨",
            nomenclature=ManualSection(
                "学名：Populus accepted\n中文名：接受杨\n科 Salicaceae-杨柳科",
                "名称资料",
            ),
        )
        proposals = self.service.parse(self.record, source)
        by_field = {proposal.field: proposal for proposal in proposals}
        self.assertEqual(by_field[FieldKey.SPECIES_LATIN].suggested_value, "Populus accepted")
        self.assertEqual(by_field[FieldKey.SPECIES_CHINESE].suggested_value, "接受杨")
        self.assertIn("名称信息", by_field[FieldKey.SPECIES_LATIN].source_name)

    def test_same_batch_reopens_manual_source_without_browser_request(self) -> None:
        class Bridge:
            def read(self, _path: Path) -> WorkbookData:
                headers = [field.value for field in FieldKey] + ["备注"]
                row = [self_value.record.values.get(header, "-") for header in headers]
                return WorkbookData({"Sheet1": [headers, row]})

        self_value = self
        with TemporaryDirectory() as directory:
            storage = Path(directory) / "data"
            input_path = Path(directory) / "input.xlsx"
            input_path.touch()
            pipeline = ReviewPipeline(
                ROOT,
                workbook_service=WorkbookService(Bridge()),
                storage_root=storage,
            )
            batch_key = pipeline._batch_key(input_path, "Sheet1", 2, 1)
            store = ManualSourceStore(storage / "sessions" / batch_key / "manual_sources")
            source = self.source()
            source.excel_row = 2
            store.save(source)

            run = pipeline.run(input_path, "Sheet1", 2, 1)

            self.assertIn(2, run.manual_sources)
            self.assertEqual(len(run.proposals), len(FieldKey))
            self.assertTrue(all(item.source_name.startswith("人工导入") for item in run.proposals))
