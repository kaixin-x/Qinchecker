from __future__ import annotations

from datetime import datetime, timedelta
from unittest import TestCase

from qinchecker.models import FetchStatus, SourceSection, SourceSnapshot
from qinchecker.services.iplant import IPlantScraper, ScraperSettings


def snapshot(name: str, foc_status: FetchStatus) -> SourceSnapshot:
    foc_text = "FOC text" if foc_status is FetchStatus.SUCCESS else ""
    return SourceSnapshot(
        requested_species_name=name,
        foc=SourceSection(foc_status, f"https://example.test/{name}/foc", text=foc_text),
        nomenclature=SourceSection(FetchStatus.SUCCESS, f"https://example.test/{name}/n", text="name"),
        county_distribution=SourceSection(
            FetchStatus.SUCCESS, f"https://example.test/{name}/county", text="陕西：户县"
        ),
    )


class StubScraper(IPlantScraper):
    def __init__(self) -> None:
        super().__init__(sleep=lambda _seconds: None)
        self.requests: list[tuple[str, bool]] = []

    def _get_or_fetch(self, species_name: str, *, stop_after_unusable_foc: bool) -> SourceSnapshot:
        self.requests.append((species_name, stop_after_unusable_foc))
        return snapshot(
            species_name,
            FetchStatus.NO_DATA if species_name == "Populus old" else FetchStatus.SUCCESS,
        )


class IPlantFallbackTests(TestCase):
    def test_uses_chinese_only_after_latin_foc_has_no_result(self) -> None:
        scraper = StubScraper()
        result = scraper.get_or_fetch_with_fallback("Populus old", "旧杨")
        self.assertEqual(scraper.requests, [("Populus old", True), ("旧杨", False)])
        self.assertEqual(result.requested_species_name, "Populus old")
        self.assertEqual(result.searched_name, "旧杨")
        self.assertTrue(result.used_chinese_fallback)
        self.assertTrue(result.foc.usable)

    def test_does_not_use_chinese_when_latin_foc_succeeds(self) -> None:
        scraper = StubScraper()
        result = scraper.get_or_fetch_with_fallback("Populus adenopoda", "响叶杨")
        self.assertEqual(scraper.requests, [("Populus adenopoda", True)])
        self.assertFalse(result.used_chinese_fallback)

    def test_does_not_add_chinese_request_when_latin_is_rate_limited(self) -> None:
        scraper = StubScraper()

        def limited(species_name: str, *, stop_after_unusable_foc: bool) -> SourceSnapshot:
            scraper.requests.append((species_name, stop_after_unusable_foc))
            return snapshot(species_name, FetchStatus.RATE_LIMITED)

        scraper._get_or_fetch = limited  # type: ignore[method-assign]
        result = scraper.get_or_fetch_with_fallback("Populus limited", "受限杨")
        self.assertEqual(scraper.requests, [("Populus limited", True)])
        self.assertEqual(result.foc.status, FetchStatus.RATE_LIMITED)


class DummyPage:
    @staticmethod
    def title() -> str:
        return "test"


class StagedScraper(IPlantScraper):
    def __init__(self, foc: SourceSection) -> None:
        super().__init__(sleep=lambda _seconds: None)
        self._page = DummyPage()  # type: ignore[assignment]
        self.foc_result = foc
        self.calls: list[str] = []
        self.delays = 0

    def _read_foc(self, _url: str, *, fast_miss: bool = False) -> SourceSection:
        self.calls.append("foc")
        return self.foc_result

    def _read_nomenclature(self, url: str) -> SourceSection:
        self.calls.append("nomenclature")
        return SourceSection(FetchStatus.SUCCESS, url, text="学名：Populus test")

    def _read_distribution(self, url: str) -> SourceSection:
        self.calls.append("distribution")
        return SourceSection(FetchStatus.SUCCESS, url, text="陕西：户县")

    def _polite_delay(self) -> None:
        self.delays += 1


class IPlantStagedFetchTests(TestCase):
    def test_complete_foc_title_skips_nomenclature_page(self) -> None:
        foc = SourceSection(
            FetchStatus.SUCCESS,
            "https://example.test/foc",
            text="FOC >> Vol.4 >> Salicaceae >> Populus\n9. Populus adenopoda Maximowicz",
        )
        scraper = StagedScraper(foc)
        result = scraper._fetch_once("Populus adenopoda", 1, None)
        self.assertEqual(scraper.calls, ["foc", "distribution"])
        self.assertEqual(scraper.delays, 1)
        self.assertFalse(result.nomenclature_required)
        self.assertTrue(result.is_complete)

    def test_unusable_latin_foc_stops_before_other_latin_pages(self) -> None:
        scraper = StagedScraper(SourceSection(FetchStatus.NO_DATA, "https://example.test/foc"))
        result = scraper._fetch_once(
            "Populus old", 1, None, stop_after_unusable_foc=True
        )
        self.assertEqual(scraper.calls, ["foc"])
        self.assertFalse(result.foc.usable)

    def test_missing_foc_taxonomy_fetches_name_page_before_distribution(self) -> None:
        foc = SourceSection(
            FetchStatus.SUCCESS, "https://example.test/foc", text="Mountain slopes; 300-2500 m."
        )
        scraper = StagedScraper(foc)
        result = scraper._fetch_once("Populus test", 1, None)
        self.assertEqual(scraper.calls, ["foc", "nomenclature", "distribution"])
        self.assertEqual(scraper.delays, 2)
        self.assertTrue(result.nomenclature_required)
        self.assertTrue(result.is_complete)

    def test_definitive_no_data_does_not_retry(self) -> None:
        incomplete = snapshot("Populus missing", FetchStatus.NO_DATA)
        incomplete.county_distribution = SourceSection(FetchStatus.NO_DATA, "https://example.test/county")
        self.assertFalse(IPlantScraper._should_retry(incomplete))


class FakeRequest:
    def __init__(self, resource_type: str) -> None:
        self.resource_type = resource_type


class FakeRoute:
    def __init__(self, resource_type: str) -> None:
        self.request = FakeRequest(resource_type)
        self.aborted = False
        self.continued = False

    def abort(self) -> None:
        self.aborted = True

    def continue_(self) -> None:
        self.continued = True


class IPlantPerformanceTests(TestCase):
    def test_blocks_only_nonessential_resource_types(self) -> None:
        scraper = IPlantScraper(sleep=lambda _seconds: None)
        image_route = FakeRoute("image")
        script_route = FakeRoute("script")
        scraper._route_request(image_route)
        scraper._route_request(script_route)
        self.assertTrue(image_route.aborted)
        self.assertFalse(image_route.continued)
        self.assertFalse(script_route.aborted)
        self.assertTrue(script_route.continued)
        self.assertEqual(scraper._blocked_resource_count, 1)

    def test_recent_definitive_no_data_cache_is_reused(self) -> None:
        scraper = IPlantScraper(sleep=lambda _seconds: None)
        result = snapshot("Populus missing", FetchStatus.NO_DATA)
        result.foc.http_status = 200
        result.captured_at = datetime.now() - timedelta(hours=23)
        self.assertTrue(scraper._cached_snapshot_reusable(result))

    def test_expired_or_transient_negative_cache_is_not_reused(self) -> None:
        scraper = IPlantScraper(sleep=lambda _seconds: None)
        expired = snapshot("Populus old missing", FetchStatus.NO_DATA)
        expired.captured_at = datetime.now() - timedelta(hours=25)
        timeout = snapshot("Populus timeout", FetchStatus.TIMEOUT)
        probe_timeout = snapshot("Populus probe", FetchStatus.NO_DATA)
        probe_timeout.foc.error_message = "拉丁名 FOC 探测超时，立即转中文名"
        self.assertFalse(scraper._cached_snapshot_reusable(expired))
        self.assertFalse(scraper._cached_snapshot_reusable(timeout))
        self.assertFalse(scraper._cached_snapshot_reusable(probe_timeout))

    def test_negative_cache_can_be_disabled(self) -> None:
        scraper = IPlantScraper(
            ScraperSettings(negative_cache_hours=0), sleep=lambda _seconds: None
        )
        result = snapshot("Populus missing", FetchStatus.NOT_FOUND)
        self.assertFalse(scraper._cached_snapshot_reusable(result))

    def test_old_negative_cache_without_http_proof_is_not_reused(self) -> None:
        scraper = IPlantScraper(sleep=lambda _seconds: None)
        result = snapshot("Populus old cache", FetchStatus.NO_DATA)
        self.assertIsNone(result.foc.http_status)
        self.assertFalse(scraper._cached_snapshot_reusable(result))

    def test_http_rate_limit_and_server_error_are_transient(self) -> None:
        scraper = IPlantScraper(sleep=lambda _seconds: None)

        class Response:
            def __init__(self, status: int) -> None:
                self.status = status

        rate_limited = scraper._http_failure_section(Response(429), "https://example.test")
        server_error = scraper._http_failure_section(Response(503), "https://example.test")
        self.assertIsNotNone(rate_limited)
        self.assertIsNotNone(server_error)
        assert rate_limited is not None and server_error is not None
        self.assertEqual(rate_limited.status, FetchStatus.RATE_LIMITED)
        self.assertEqual(rate_limited.http_status, 429)
        self.assertEqual(server_error.status, FetchStatus.HTTP_ERROR)
        self.assertEqual(server_error.http_status, 503)

    def test_rate_limit_uses_longer_retry_delay(self) -> None:
        limited = snapshot("Populus limited", FetchStatus.RATE_LIMITED)
        self.assertEqual(IPlantScraper._retry_delay_seconds(limited, 1), 15.0)
        self.assertEqual(IPlantScraper._retry_delay_seconds(limited, 2), 30.0)

    def test_browser_recycles_after_configured_species_count(self) -> None:
        scraper = IPlantScraper(
            ScraperSettings(
                browser_recycle_species_count=2,
                browser_recycle_failure_count=0,
                browser_memory_limit_mb=0,
            ),
            sleep=lambda _seconds: None,
        )
        scraper._browser = object()  # type: ignore[assignment]
        scraper._page = object()  # type: ignore[assignment]
        scraper._species_since_recycle = 2
        self.assertIn("连续处理 2", scraper._browser_recycle_reason())
