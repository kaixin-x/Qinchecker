from __future__ import annotations

from unittest import TestCase

from qinchecker.models import Confidence, FetchStatus, FieldKey, ReviewState, SourceSection, SourceSnapshot
from qinchecker.models.review import SpeciesRecord
from qinchecker.services.parsing import CountyIndex, HabitatGlossary, SourceParser


class SourceParserTests(TestCase):
    def setUp(self) -> None:
        self.record = SpeciesRecord(
            worksheet_name="Sheet1",
            excel_row=2,
            values={
                "Family": "Salicaceae",
                "科": "杨柳科",
                "Species": "Populus adenopoda",
                "物种": "响叶杨",
                "Lowest elevation (m)": "-",
                "Highest elevation (m)": "-",
                "区县": "蓝田",
                "Habitat": "-",
                "Other occurrence": "-",
                "Earliest flowering": "-",
                "Latest flowering": "-",
            },
        )
        self.snapshot = SourceSnapshot(
            requested_species_name="Populus adenopoda",
            foc=SourceSection(
                FetchStatus.SUCCESS,
                "https://example.test/foc",
                text=(
                    "Trees to 30 m tall. Fl. Mar-Apr, fr. Apr-May. "
                    "Mountain slopes; 300-2500 m. Anhui, Fujian, Shaanxi, Sichuan."
                ),
            ),
            nomenclature=SourceSection(
                FetchStatus.SUCCESS,
                "https://example.test/n",
                text=(
                    "学名：\tPopulus adenopoda Maxim.\n中文名：\t响叶杨\n"
                    "科 Salicaceae-杨柳科(yang liu ke)"
                ),
            ),
            county_distribution=SourceSection(
                FetchStatus.SUCCESS,
                "https://example.test/f",
                text="陕西：\t蓝田、户县、佛坪\n甘肃：\t文县",
            ),
        )
        self.parser = SourceParser(
            HabitatGlossary({"mountain slopes": "山坡"}),
            CountyIndex({"蓝田": "蓝田", "户县": "鄠邑区", "鄠邑区": "鄠邑区", "佛坪": "佛坪"}),
        )

    def test_parses_high_confidence_foc_fields(self) -> None:
        parsed = self.parser.parse(self.record, self.snapshot)
        self.assertEqual(len(parsed.proposals), len(FieldKey))
        self.assertEqual({item.field for item in parsed.proposals}, set(FieldKey))
        proposals = {item.field: item for item in parsed.proposals}
        self.assertEqual(proposals[FieldKey.LOWEST_ELEVATION].suggested_value, 300)
        self.assertEqual(proposals[FieldKey.HIGHEST_ELEVATION].suggested_value, 2500)
        self.assertEqual(proposals[FieldKey.EARLIEST_FLOWERING].suggested_value, 3)
        self.assertEqual(proposals[FieldKey.LATEST_FLOWERING].suggested_value, 4)
        self.assertEqual(proposals[FieldKey.HABITAT].suggested_value, "山坡")
        self.assertEqual(proposals[FieldKey.OTHER_OCCURRENCE].suggested_value, "安徽、福建、陕西、四川")
        self.assertEqual(proposals[FieldKey.LOWEST_ELEVATION].confidence, Confidence.HIGH)

    def test_counties_merge_and_standardize_aliases(self) -> None:
        proposals = {item.field: item for item in self.parser.parse(self.record, self.snapshot).proposals}
        self.assertEqual(proposals[FieldKey.COUNTIES].suggested_value, "蓝田、鄠邑区、佛坪")

    def test_existing_old_county_name_is_replaced_and_noted(self) -> None:
        self.record.values[FieldKey.COUNTIES.value] = "户县"
        proposals = {item.field: item for item in self.parser.parse(self.record, self.snapshot).proposals}
        county = proposals[FieldKey.COUNTIES]
        self.assertNotIn("户县", str(county.suggested_value))
        self.assertIn("鄠邑区", str(county.suggested_value))
        self.assertIn("行政区名称更新：户县→鄠邑区", county.note)

    def test_candidate_county_is_not_automatically_merged(self) -> None:
        index = CountyIndex({"蓝田": "蓝田"}, {"户县": "鄠邑区"})
        found, unknown = index.extract("陕西：\t蓝田、户县")
        self.assertEqual(found, ["蓝田"])
        self.assertEqual(unknown, ["户县"])

    def test_county_extraction_ignores_provinces_outside_configured_scope(self) -> None:
        index = CountyIndex({"蓝田": "蓝田"}, target_provinces={"陕西"})
        found, unknown = index.extract("湖北：\t武汉、宜昌\n陕西：\t蓝田、户县")
        self.assertEqual(found, ["蓝田"])
        self.assertEqual(unknown, ["户县"])

    def test_foc_taxonomy_overrides_conflicting_name_page(self) -> None:
        self.snapshot.foc.text = (
            "FOC >> Vol.4 (1999) >> Salicaceae >> Populus\n"
            "9.Populus adenopoda Maximowicz\n响叶杨 xiang ye yang\n\n"
            "Mountain slopes; 300-2500 m."
        )
        self.snapshot.nomenclature.text = "学名：\tPopulus wrongus\n中文名：\t错误杨"
        proposals = {item.field: item for item in self.parser.parse(self.record, self.snapshot).proposals}
        self.assertEqual(proposals[FieldKey.SPECIES_LATIN].suggested_value, "Populus adenopoda")
        self.assertEqual(proposals[FieldKey.SPECIES_LATIN].confidence, Confidence.HIGH)
        self.assertEqual(proposals[FieldKey.FAMILY_LATIN].suggested_value, "Salicaceae")

    def test_similar_foc_habitat_keeps_existing_chinese_value(self) -> None:
        self.record.values["Habitat"] = "山坡疏林"
        self.snapshot.foc.text = "Broad-leaved forests and valleys; 300-2500 m."
        parser = SourceParser(HabitatGlossary(), self.parser.county_index)
        proposals = {item.field: item for item in parser.parse(self.record, self.snapshot).proposals}
        habitat = proposals[FieldKey.HABITAT]
        self.assertEqual(habitat.state, ReviewState.NO_CHANGE)
        self.assertEqual(habitat.suggested_value, "山坡疏林")

    def test_single_bound_elevation_rules(self) -> None:
        lower = SourceParser._extract_elevation("Forest margins, below 300 m.")
        upper = SourceParser._extract_elevation("Mountain slopes, above 500 m.")
        exact = SourceParser._extract_elevation("At 1200 m.")
        self.assertEqual(lower[:2], (1, 300))  # type: ignore[index]
        self.assertEqual(upper[:2], (500, "-"))  # type: ignore[index]
        self.assertEqual(exact[:2], (1200, 1200))  # type: ignore[index]

    def test_missing_source_still_displays_every_field(self) -> None:
        self.snapshot.county_distribution = SourceSection(
            FetchStatus.NO_DATA, "https://example.test/distribution"
        )
        parsed = self.parser.parse(self.record, self.snapshot)
        self.assertEqual(len(parsed.proposals), len(FieldKey))
        proposals = {item.field: item for item in parsed.proposals}
        self.assertEqual(proposals[FieldKey.COUNTIES].state, ReviewState.FAILED)
        self.assertEqual(proposals[FieldKey.QINLING_DISTRIBUTION].state, ReviewState.FAILED)

    def test_chinese_fallback_is_recorded_on_every_field(self) -> None:
        self.snapshot.used_chinese_fallback = True
        self.snapshot.searched_name = "响叶杨"
        parsed = self.parser.parse(self.record, self.snapshot)
        self.assertTrue(all("改用中文名“响叶杨”" in item.note for item in parsed.proposals))
