from __future__ import annotations

from datetime import datetime
import tempfile
import unittest
from pathlib import Path

from qinchecker.models import FetchStatus, SourceSection, SourceSnapshot
from qinchecker.services.cache import SourceCache


class SourceCacheTests(unittest.TestCase):
    def make_snapshot(self) -> SourceSnapshot:
        return SourceSnapshot(
            requested_species_name="Populus adenopoda",
            foc=SourceSection(
                FetchStatus.SUCCESS,
                "https://example.test/foc",
                text="FOC original text",
                http_status=200,
            ),
            nomenclature=SourceSection(
                FetchStatus.SUCCESS,
                "https://example.test/nomenclature",
                text="学名：Populus adenopoda",
            ),
            county_distribution=SourceSection(
                FetchStatus.SUCCESS,
                "https://example.test/distribution",
                text="陕西：洋县",
            ),
            captured_at=datetime(2026, 8, 3, 10, 0, 0),
        )

    def test_cache_round_trip_keeps_source_text_and_urls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = SourceCache(Path(directory))
            cache.save(self.make_snapshot())
            restored = cache.load("  populus   adenopoda ")
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertTrue(restored.from_cache)
            self.assertEqual(restored.foc.text, "FOC original text")
            self.assertEqual(restored.foc.http_status, 200)
            self.assertEqual(restored.county_distribution.requested_url, "https://example.test/distribution")

    def test_cache_key_ignores_case_and_extra_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = SourceCache(Path(directory))
            self.assertEqual(
                cache.path_for("Populus adenopoda"),
                cache.path_for("  POPULUS   adenopoda  "),
            )

    def test_corrupted_cache_is_ignored_and_can_be_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = SourceCache(Path(directory))
            path = cache.path_for("Populus adenopoda")
            path.write_text("{incomplete", encoding="utf-8")
            self.assertIsNone(cache.load("Populus adenopoda"))
            cache.save(self.make_snapshot())
            self.assertIsNotNone(cache.load("Populus adenopoda"))

    def test_incomplete_snapshot_can_be_identified_for_retry(self) -> None:
        snapshot = self.make_snapshot()
        snapshot.foc = SourceSection(FetchStatus.NO_DATA, "https://example.test/foc")
        self.assertFalse(snapshot.is_complete)

    def test_name_page_is_optional_unless_foc_title_requires_it(self) -> None:
        snapshot = self.make_snapshot()
        snapshot.nomenclature = SourceSection(FetchStatus.NO_DATA, "https://example.test/nomenclature")
        snapshot.nomenclature_required = False
        self.assertTrue(snapshot.is_complete)
        snapshot.nomenclature_required = True
        self.assertFalse(snapshot.is_complete)

    def test_cache_round_trip_keeps_search_fallback_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = SourceCache(Path(directory))
            snapshot = self.make_snapshot()
            snapshot.searched_name = "响叶杨"
            snapshot.used_chinese_fallback = True
            cache.save(snapshot)
            restored = cache.load("Populus adenopoda")
            assert restored is not None
            self.assertEqual(restored.searched_name, "响叶杨")
            self.assertTrue(restored.used_chinese_fallback)


if __name__ == "__main__":
    unittest.main()
