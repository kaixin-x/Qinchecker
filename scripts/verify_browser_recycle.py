"""使用两个真实物种验证浏览器重建后仍能继续抓取。"""

from __future__ import annotations

from pathlib import Path

from qinchecker.services.cache import SourceCache
from qinchecker.services.iplant import IPlantScraper, ScraperSettings


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    activities: list[tuple[str, str]] = []
    settings = ScraperSettings(
        browser_executable=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        browser_recycle_species_count=1,
        browser_recycle_failure_count=0,
        browser_memory_limit_mb=0,
    )
    cache = SourceCache(ROOT / "sessions" / "browser_recycle_probe_20260810_v1" / "cache")
    with IPlantScraper(
        settings,
        cache,
        on_activity=lambda command, detail: activities.append((command, detail)),
    ) as scraper:
        first = scraper.get_or_fetch("Populus adenopoda")
        second = scraper.get_or_fetch("Ginkgo biloba")
    recycles = [item for item in activities if item[0].startswith("BROWSER RECYCLE")]
    print(
        f"first={first.is_complete}/{first.foc.http_status}; "
        f"second={second.is_complete}/{second.foc.http_status}; recycles={len(recycles)}"
    )
    for command, detail in recycles:
        print(f"{command} | {detail}")


if __name__ == "__main__":
    main()
