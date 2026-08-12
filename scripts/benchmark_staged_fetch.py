"""无缓存验证分阶段抓取耗时、访问顺序和字段覆盖率。"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from qinchecker.models.review import FieldKey, SpeciesRecord
from qinchecker.services.cache import SourceCache
from qinchecker.services.iplant import IPlantScraper, ScraperSettings
from qinchecker.services.parsing import CountyIndex, HabitatGlossary, SourceParser


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    commands: list[str] = []
    settings = ScraperSettings(
        browser_executable=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    )
    cache = SourceCache(ROOT / "sessions" / "staged_fetch_probe_20260810_v7" / "cache")
    started = perf_counter()
    with IPlantScraper(
        settings,
        cache,
        on_activity=lambda command, _detail: commands.append(command),
    ) as scraper:
        snapshot = scraper.get_or_fetch_with_fallback("Populus adenopoda", "响叶杨")
        elapsed = perf_counter() - started
        fallback_commands: list[str] = []
        scraper._on_activity = lambda command, _detail: fallback_commands.append(command)
        fallback_started = perf_counter()
        fallback_snapshot = scraper.get_or_fetch_with_fallback("Populus invalidus", "响叶杨")
        fallback_elapsed = perf_counter() - fallback_started
        blocked_resources = scraper._blocked_resource_count

    record = SpeciesRecord(
        "Sheet1",
        2,
        {field.value: "-" for field in FieldKey}
        | {FieldKey.SPECIES_LATIN.value: "Populus adenopoda", FieldKey.SPECIES_CHINESE.value: "响叶杨"},
    )
    parser = SourceParser(
        HabitatGlossary.from_csv(ROOT / "config" / "habitat_terms.csv"),
        CountyIndex.from_csv(ROOT / "config" / "qinling_counties.csv", ROOT / "config" / "admin_aliases.csv"),
    )
    proposals = parser.parse(record, snapshot).proposals
    print(f"elapsed_seconds={elapsed:.2f}")
    print(f"commands={commands}")
    print(
        f"foc={snapshot.foc.status.value}/HTTP {snapshot.foc.http_status}, "
        f"county={snapshot.county_distribution.status.value}/HTTP "
        f"{snapshot.county_distribution.http_status}"
    )
    print(f"nomenclature={snapshot.nomenclature.status.value}, required={snapshot.nomenclature_required}")
    print(f"complete={snapshot.is_complete}")
    print(f"field_count={len(proposals)}, unique_fields={len({item.field for item in proposals})}")
    print(f"blocked_resources={blocked_resources}")
    print(f"fallback_elapsed_seconds={fallback_elapsed:.2f}")
    print(f"fallback_commands={fallback_commands}")
    print(
        f"fallback_used={fallback_snapshot.used_chinese_fallback}, "
        f"searched_name={fallback_snapshot.searched_name}, complete={fallback_snapshot.is_complete}"
    )


if __name__ == "__main__":
    main()
