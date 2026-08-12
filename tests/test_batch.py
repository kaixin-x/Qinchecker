from __future__ import annotations

from datetime import datetime
import tempfile
import unittest
from pathlib import Path

from qinchecker.models import FetchStatus, SourceSection, SourceSnapshot
from qinchecker.services.batch import BatchRunner, SpeciesTask


def complete_snapshot(species_name: str) -> SourceSnapshot:
    return SourceSnapshot(
        requested_species_name=species_name,
        foc=SourceSection(FetchStatus.SUCCESS, "https://example.test/foc", text="foc"),
        nomenclature=SourceSection(
            FetchStatus.SUCCESS, "https://example.test/nomenclature", text="学名：Species"
        ),
        county_distribution=SourceSection(
            FetchStatus.SUCCESS, "https://example.test/distribution", text="陕西：洋县"
        ),
        captured_at=datetime(2026, 8, 3, 10, 0, 0),
    )


class FakeScraper:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def get_or_fetch(self, species_name: str) -> SourceSnapshot:
        self.requests.append(species_name)
        return complete_snapshot(species_name)


class FallbackScraper(FakeScraper):
    def __init__(self) -> None:
        super().__init__()
        self.fallback_requests: list[tuple[str, str]] = []

    def get_or_fetch_with_fallback(self, latin_name: str, chinese_name: str) -> SourceSnapshot:
        self.fallback_requests.append((latin_name, chinese_name))
        return complete_snapshot(latin_name)


class BatchRunnerTests(unittest.TestCase):
    def test_resume_skips_completed_and_blank_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scraper = FakeScraper()
            runner = BatchRunner(scraper, Path(directory) / "progress.json")  # type: ignore[arg-type]
            tasks = [
                SpeciesTask(2, "Ginkgo biloba"),
                SpeciesTask(3, ""),
                SpeciesTask(4, "Populus adenopoda"),
            ]
            first = runner.run(tasks, start_excel_row=2, count=3)
            self.assertEqual(set(first), {2, 4})
            self.assertEqual(scraper.requests, ["Ginkgo biloba", "Populus adenopoda"])

            second = runner.run(tasks, start_excel_row=2, count=3)
            self.assertEqual(second, {})
            self.assertEqual(scraper.requests, ["Ginkgo biloba", "Populus adenopoda"])

    def test_rejects_resume_with_different_batch_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = BatchRunner(FakeScraper(), Path(directory) / "progress.json")  # type: ignore[arg-type]
            runner.load_or_create_progress(start_excel_row=2, count=10)
            with self.assertRaises(ValueError):
                runner.load_or_create_progress(start_excel_row=3, count=10)

    def test_corrupted_progress_restarts_same_range_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            path.write_text("{incomplete", encoding="utf-8")
            runner = BatchRunner(FakeScraper(), path)  # type: ignore[arg-type]
            progress = runner.load_or_create_progress(start_excel_row=2, count=10)
            self.assertEqual(progress.completed_rows, [])
            self.assertEqual(progress.requested_start_excel_row, 2)

    def test_cancel_stops_before_starting_the_next_species(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scraper = FakeScraper()
            runner = BatchRunner(scraper, Path(directory) / "progress.json")  # type: ignore[arg-type]
            tasks = [SpeciesTask(2, "Ginkgo biloba"), SpeciesTask(3, "Populus adenopoda")]
            results = runner.run(
                tasks,
                start_excel_row=2,
                count=2,
                cancel_requested=lambda: len(scraper.requests) >= 1,
            )
            self.assertTrue(runner.cancelled)
            self.assertEqual(set(results), {2})
            self.assertEqual(scraper.requests, ["Ginkgo biloba"])

    def test_passes_latin_name_first_and_chinese_name_as_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scraper = FallbackScraper()
            runner = BatchRunner(scraper, Path(directory) / "progress.json")  # type: ignore[arg-type]
            runner.run([SpeciesTask(2, "Populus adenopoda", "响叶杨")], 2, 1)
            self.assertEqual(scraper.fallback_requests, [("Populus adenopoda", "响叶杨")])

    def test_streaming_mode_reports_snapshot_without_retaining_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scraper = FakeScraper()
            runner = BatchRunner(scraper, Path(directory) / "progress.json")  # type: ignore[arg-type]
            reported: list[str] = []
            results = runner.run(
                [SpeciesTask(2, "Ginkgo biloba")],
                2,
                1,
                on_snapshot=lambda task, _snapshot: reported.append(task.species_name),
                retain_results=False,
            )
            self.assertEqual(results, {})
            self.assertEqual(reported, ["Ginkgo biloba"])


if __name__ == "__main__":
    unittest.main()
