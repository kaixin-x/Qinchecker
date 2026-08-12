"""对单个物种执行只读 iPlant 集成验证。

此脚本只用于开发阶段的连通性检查；它会将原文快照保存到指定会话缓存。
"""

from pathlib import Path

from qinchecker.services import IPlantScraper, ScraperSettings, SourceCache


def main() -> None:
    cache = SourceCache(Path("sessions/phase2_probe/cache"))
    settings = ScraperSettings(
        browser_executable=Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    )
    with IPlantScraper(settings, cache) as scraper:
        snapshot = scraper.get_or_fetch("Populus adenopoda")

    print(f"foc={snapshot.foc.status.value}, chars={len(snapshot.foc.text)}")
    print(f"nomenclature={snapshot.nomenclature.status.value}, chars={len(snapshot.nomenclature.text)}")
    print(
        "distribution="
        f"{snapshot.county_distribution.status.value}, chars={len(snapshot.county_distribution.text)}"
    )
    print(f"complete={snapshot.is_complete}")
    print(f"foc_url={snapshot.foc.resolved_url}")
    print(f"distribution_url={snapshot.county_distribution.resolved_url}")
    print(f"cache={cache.path_for('Populus adenopoda')}")


if __name__ == "__main__":
    main()
