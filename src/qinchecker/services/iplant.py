"""使用 Playwright 渲染并读取 iPlant 的 FOC 与县级分布板块。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
import random
import re
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import psutil
from playwright.sync_api import Browser, Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from qinchecker.models.source import FetchStatus, SourceSection, SourceSnapshot
from qinchecker.services.cache import SourceCache


IPLANT_INFO_URL = "https://www.iplant.cn/info/{species}?t={tab}"
FOC_SELECTOR = "#foccontext"
NOMENCLATURE_SELECTOR = "#plant_names_right"
DISTRIBUTION_SELECTOR = "#fenbuinfo"
LOADING_TEXT = "数据加载中"


class ScraperCancelled(RuntimeError):
    """由界面发出的安全停止请求。"""


@dataclass(frozen=True, slots=True)
class ScraperSettings:
    navigation_timeout_seconds: int = 30
    latin_foc_probe_timeout_seconds: int = 12
    selector_attach_timeout_seconds: int = 8
    foc_wait_seconds: int = 12
    nomenclature_wait_seconds: int = 12
    distribution_wait_seconds: int = 20
    retry_count: int = 2
    minimum_delay_seconds: float = 1.5
    maximum_delay_seconds: float = 3.0
    block_nonessential_resources: bool = True
    negative_cache_hours: int = 24
    browser_recycle_species_count: int = 50
    browser_recycle_failure_count: int = 2
    browser_memory_limit_mb: int = 800
    browser_executable: Path | None = None


def find_system_browser() -> Path:
    candidates = (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("未找到可用的 Chrome 或 Edge 浏览器。")


class IPlantScraper(AbstractContextManager["IPlantScraper"]):
    """单线程、限速、可重试的 iPlant 只读抓取器。"""

    def __init__(
        self,
        settings: ScraperSettings | None = None,
        cache: SourceCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
        cancel_requested: Callable[[], bool] | None = None,
        on_activity: Callable[[str, str], None] | None = None,
    ) -> None:
        self.settings = settings or ScraperSettings()
        self.cache = cache
        self._sleep = sleep
        self._cancel_requested = cancel_requested
        self._on_activity = on_activity
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._blocked_resource_count = 0
        self._network_request_count = 0
        self._consecutive_failures = 0
        self._species_since_recycle = 0
        self._browser_launch_count = 0

    def __enter__(self) -> "IPlantScraper":
        self._playwright = sync_playwright().start()
        try:
            self._launch_browser()
        except Exception:
            self._playwright.stop()
            self._playwright = None
            raise
        return self

    def _launch_browser(self) -> None:
        if self._playwright is None:
            raise RuntimeError("Playwright 尚未启动")
        browser_path = self.settings.browser_executable
        if browser_path is None:
            try:
                browser_path = find_system_browser()
            except FileNotFoundError:
                # 安装版会随 Playwright Chromium 一起发布；没有系统浏览器时让
                # Playwright 使用其内置浏览器路径。
                browser_path = None
        try:
            self._browser = self._playwright.chromium.launch(
                executable_path=str(browser_path) if browser_path is not None else None,
                headless=True,
            )
        except PlaywrightError as exc:
            raise RuntimeError("无法启动抓取浏览器；请重新安装 QinChecker 绿色版") from exc
        self._page = self._browser.new_page(viewport={"width": 1440, "height": 1000})
        if self.settings.block_nonessential_resources:
            self._page.route("**/*", self._route_request)
        self._page.set_default_timeout(self.settings.navigation_timeout_seconds * 1000)
        self._species_since_recycle = 0
        self._consecutive_failures = 0
        self._browser_launch_count += 1

    def __exit__(self, *_: object) -> None:
        self._close_browser()
        if self._playwright is not None:
            self._playwright.stop()
        self._playwright = None

    def _close_browser(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except PlaywrightError:
                pass
        self._page = None
        self._browser = None

    def get_or_fetch(self, species_name: str) -> SourceSnapshot:
        return self._get_or_fetch(species_name, stop_after_unusable_foc=False)

    def _get_or_fetch(self, species_name: str, *, stop_after_unusable_foc: bool) -> SourceSnapshot:
        self._ensure_not_cancelled()
        if self.cache is not None:
            cached = self.cache.load(species_name)
            if cached is not None and self._cached_snapshot_reusable(cached):
                cache_kind = "COMPLETE" if cached.is_complete else "NEGATIVE"
                self._announce(
                    f"CACHE {cache_kind} {species_name}",
                    f"复用本地缓存（采集于 {cached.captured_at:%Y-%m-%d %H:%M:%S}）",
                )
                return cached
        recycle_reason = self._browser_recycle_reason()
        if recycle_reason:
            self._recycle_browser(recycle_reason)
        snapshot = self.fetch(
            species_name,
            cached if self.cache is not None else None,
            stop_after_unusable_foc=stop_after_unusable_foc,
        )
        if self.cache is not None:
            self.cache.save(snapshot)
        return snapshot

    def _cached_snapshot_reusable(self, snapshot: SourceSnapshot) -> bool:
        """复用完整结果，或在有限时效内复用网站明确返回的无数据结果。"""
        if snapshot.is_complete:
            return True
        if self.settings.negative_cache_hours <= 0:
            return False
        if snapshot.foc.status not in {FetchStatus.NO_DATA, FetchStatus.NOT_FOUND}:
            return False
        # 快速探测超时只是网络/页面迟缓，不代表网站明确没有该物种。
        if "探测超时" in snapshot.foc.error_message:
            return False
        # 旧版缓存没有 HTTP 状态，无法证明是正常页面返回的明确无数据。
        if snapshot.foc.http_status not in {200, 404}:
            return False
        age = datetime.now() - snapshot.captured_at
        return timedelta(0) <= age <= timedelta(hours=self.settings.negative_cache_hours)

    def get_or_fetch_with_fallback(self, latin_name: str, chinese_name: str = "") -> SourceSnapshot:
        """优先使用拉丁名；FOC 未找到时再使用中文名，并合并可用板块。"""
        primary = self._get_or_fetch(
            latin_name,
            stop_after_unusable_foc=bool(chinese_name.strip()),
        )
        if primary.foc.usable or not chinese_name.strip():
            return primary
        if primary.foc.status not in {FetchStatus.NO_DATA, FetchStatus.NOT_FOUND}:
            self._announce(
                f"FALLBACK SKIPPED {latin_name}",
                f"拉丁名访问状态为 {primary.foc.status.value}，为避免限流加重，本次不再请求中文名",
            )
            return primary
        self._announce(
            f"FALLBACK {chinese_name.strip()}",
            f"拉丁名 {latin_name} 未找到可用 FOC，改用中文名 {chinese_name.strip()} 搜索",
        )
        fallback = self.get_or_fetch(chinese_name.strip())
        if not fallback.foc.usable:
            return primary
        merged = SourceSnapshot(
            requested_species_name=latin_name,
            searched_name=chinese_name.strip(),
            used_chinese_fallback=True,
            nomenclature_required=fallback.nomenclature_required,
            foc=fallback.foc,
            nomenclature=fallback.nomenclature,
            county_distribution=(
                fallback.county_distribution
                if fallback.county_distribution.usable
                else primary.county_distribution
            ),
            page_title=fallback.page_title,
            attempt_count=primary.attempt_count + fallback.attempt_count,
        )
        if self.cache is not None:
            self.cache.save(merged)
        return merged

    def fetch(
        self,
        species_name: str,
        existing: SourceSnapshot | None = None,
        *,
        stop_after_unusable_foc: bool = False,
    ) -> SourceSnapshot:
        self._ensure_not_cancelled()
        if self._page is None:
            raise RuntimeError("抓取器尚未启动；请在 with IPlantScraper(...) 中调用。")
        attempts = self.settings.retry_count + 1
        last_snapshot = existing
        for attempt in range(1, attempts + 1):
            self._ensure_not_cancelled()
            snapshot = self._fetch_once(
                species_name,
                attempt,
                last_snapshot,
                stop_after_unusable_foc=stop_after_unusable_foc,
            )
            last_snapshot = snapshot
            if snapshot.is_complete or not self._should_retry(snapshot) or attempt == attempts:
                self._record_snapshot_health(species_name, snapshot)
                return snapshot
            retry_delay = self._retry_delay_seconds(snapshot, attempt)
            self._interruptible_delay(
                retry_delay,
                f"{species_name} 第 {attempt} 次访问异常，等待后重试",
            )
        assert last_snapshot is not None
        return last_snapshot

    @staticmethod
    def _should_retry(snapshot: SourceSnapshot) -> bool:
        transient_statuses = {
            FetchStatus.TIMEOUT,
            FetchStatus.ERROR,
            FetchStatus.RATE_LIMITED,
            FetchStatus.HTTP_ERROR,
        }
        return (
            snapshot.foc.status in transient_statuses
            or snapshot.county_distribution.status in transient_statuses
            or (
                snapshot.nomenclature_required
                and snapshot.nomenclature.status in transient_statuses
            )
        )

    def _fetch_once(
        self,
        species_name: str,
        attempt: int,
        existing: SourceSnapshot | None,
        *,
        stop_after_unusable_foc: bool = False,
    ) -> SourceSnapshot:
        foc_url = self._make_url(species_name, "foc")
        nomenclature_url = self._make_url(species_name, "n")
        distribution_url = self._make_url(species_name, "f")
        network_request_made = False
        if existing is not None and existing.foc.usable:
            foc = existing.foc
        else:
            self._announce(f"GET {foc_url}", f"正在读取 {species_name} 的中国植物志（FOC）信息")
            foc = self._timed_read(
                "FOC",
                lambda: self._read_foc(
                    foc_url,
                    fast_miss=stop_after_unusable_foc,
                ),
            )
            network_request_made = True
        self._ensure_not_cancelled()

        existing_nomenclature = existing.nomenclature if existing is not None else None
        existing_distribution = existing.county_distribution if existing is not None else None
        if not foc.usable and stop_after_unusable_foc:
            return SourceSnapshot(
                requested_species_name=species_name,
                searched_name=species_name,
                foc=foc,
                nomenclature=existing_nomenclature or self._skipped_section(
                    nomenclature_url, "拉丁名 FOC 无有效结果，名称页等待中文名回退"
                ),
                county_distribution=existing_distribution or self._skipped_section(
                    distribution_url, "拉丁名 FOC 无有效结果，县级页等待中文名回退"
                ),
                nomenclature_required=False,
                attempt_count=attempt,
            )

        nomenclature_required = not foc.usable or not self._foc_has_taxonomy(foc.text)
        if existing_nomenclature is not None and existing_nomenclature.usable:
            nomenclature = existing_nomenclature
        elif nomenclature_required:
            if network_request_made:
                self._polite_delay()
            self._announce(f"GET {nomenclature_url}", f"FOC 名称标题不足，正在补充读取 {species_name} 的名称分类信息")
            nomenclature = self._timed_read(
                "名称页", lambda: self._read_nomenclature(nomenclature_url)
            )
            network_request_made = True
            self._ensure_not_cancelled()
        else:
            nomenclature = existing_nomenclature or self._skipped_section(
                nomenclature_url, "FOC 名称标题完整，无需访问名称分类页"
            )

        if network_request_made and not (existing_distribution is not None and existing_distribution.usable):
            self._polite_delay()
        if existing_distribution is None or not existing_distribution.usable:
            self._announce(f"GET {distribution_url}", f"正在读取 {species_name} 的区县分布信息")
        distribution = (
            existing_distribution
            if existing_distribution is not None and existing_distribution.usable
            else self._timed_read(
                "县级页", lambda: self._read_distribution(distribution_url)
            )
        )
        self._ensure_not_cancelled()
        title = self._page.title() if self._page is not None else ""
        return SourceSnapshot(
            requested_species_name=species_name,
            searched_name=species_name,
            foc=foc,
            nomenclature=nomenclature,
            county_distribution=distribution,
            nomenclature_required=nomenclature_required,
            page_title=title,
            attempt_count=attempt,
        )

    @staticmethod
    def _foc_has_taxonomy(text: str) -> bool:
        """与解析器一致：FOC 已能提供至少一项名称分类证据时跳过名称页。"""
        taxon = re.search(r"(?m)^\s*\d+\.\s*([A-Z][^\n]+)$", text)
        family = re.search(r"(?m)^FOC\s*>>.*?>>\s*([A-Z][A-Za-z-]+aceae)\s*>>", text)
        return bool(taxon or family)

    @staticmethod
    def _skipped_section(url: str, reason: str) -> SourceSection:
        return SourceSection(FetchStatus.NO_DATA, url, error_message=reason)

    @staticmethod
    def _make_url(species_name: str, tab: str) -> str:
        return IPLANT_INFO_URL.format(species=quote(species_name, safe=""), tab=tab)

    def _read_foc(self, url: str, *, fast_miss: bool = False) -> SourceSection:
        assert self._page is not None
        probe_deadline = (
            time.monotonic() + self.settings.latin_foc_probe_timeout_seconds
            if fast_miss
            else None
        )
        try:
            response = self._page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=(
                    self.settings.latin_foc_probe_timeout_seconds * 1000
                    if fast_miss
                    else self.settings.navigation_timeout_seconds * 1000
                ),
            )
            http_failure = self._http_failure_section(response, url)
            if http_failure is not None:
                return http_failure
            locator = self._page.locator(FOC_SELECTOR)
            try:
                selector_timeout_ms = self.settings.selector_attach_timeout_seconds * 1000
                if probe_deadline is not None:
                    selector_timeout_ms = min(
                        selector_timeout_ms,
                        max(1, int((probe_deadline - time.monotonic()) * 1000)),
                    )
                locator.wait_for(
                    state="attached",
                    timeout=selector_timeout_ms,
                )
            except PlaywrightTimeoutError:
                abnormal = self._abnormal_page_section(url, getattr(response, "status", None))
                if abnormal is not None:
                    return abnormal
                probe_timed_out = (
                    probe_deadline is not None and time.monotonic() >= probe_deadline
                )
                return SourceSection(
                    FetchStatus.NO_DATA,
                    url,
                    self._page.url,
                    error_message=(
                        "拉丁名 FOC 探测超时，立即转中文名"
                        if probe_timed_out
                        else "页面已打开，但未找到 FOC 板块"
                    ),
                    http_status=getattr(response, "status", None),
                )
            deadline = time.monotonic() + self.settings.foc_wait_seconds
            if probe_deadline is not None:
                deadline = min(deadline, probe_deadline)
            text = self._read_locator_text(locator, deadline)
            while not text and time.monotonic() < deadline:
                self._sleep(0.5)
                text = self._read_locator_text(locator, deadline)
            if not text:
                return SourceSection(
                    FetchStatus.NO_DATA,
                    url,
                    self._page.url,
                    error_message=(
                        "拉丁名 FOC 探测超时，立即转中文名"
                        if probe_deadline is not None and time.monotonic() >= probe_deadline
                        else ""
                    ),
                    http_status=getattr(response, "status", None),
                )
            return SourceSection(
                FetchStatus.SUCCESS,
                url,
                self._page.url,
                text=text,
                http_status=getattr(response, "status", None),
            )
        except PlaywrightTimeoutError as error:
            return SourceSection(
                FetchStatus.NO_DATA if fast_miss else FetchStatus.TIMEOUT,
                url,
                self._page.url,
                error_message=(
                    "拉丁名 FOC 探测超时，立即转中文名"
                    if fast_miss
                    else str(error)
                ),
            )
        except PlaywrightError as error:
            return SourceSection(FetchStatus.ERROR, url, self._page.url, error_message=str(error))

    def _read_nomenclature(self, url: str) -> SourceSection:
        assert self._page is not None
        try:
            response = self._page.goto(url, wait_until="domcontentloaded")
            http_failure = self._http_failure_section(response, url)
            if http_failure is not None:
                return http_failure
            locator = self._page.locator(NOMENCLATURE_SELECTOR)
            try:
                locator.wait_for(
                    state="attached",
                    timeout=self.settings.selector_attach_timeout_seconds * 1000,
                )
            except PlaywrightTimeoutError:
                abnormal = self._abnormal_page_section(url, getattr(response, "status", None))
                if abnormal is not None:
                    return abnormal
                return SourceSection(
                    FetchStatus.NO_DATA,
                    url,
                    self._page.url,
                    error_message="页面已打开，但未找到名称分类板块",
                    http_status=getattr(response, "status", None),
                )
            deadline = time.monotonic() + self.settings.nomenclature_wait_seconds
            text = self._read_locator_text(locator, deadline)
            while not text and time.monotonic() < deadline:
                self._sleep(0.5)
                text = self._read_locator_text(locator, deadline)
            if not text:
                return SourceSection(
                    FetchStatus.NO_DATA,
                    url,
                    self._page.url,
                    http_status=getattr(response, "status", None),
                )
            return SourceSection(
                FetchStatus.SUCCESS,
                url,
                self._page.url,
                text=text,
                http_status=getattr(response, "status", None),
            )
        except PlaywrightTimeoutError as error:
            return SourceSection(FetchStatus.TIMEOUT, url, self._page.url, error_message=str(error))
        except PlaywrightError as error:
            return SourceSection(FetchStatus.ERROR, url, self._page.url, error_message=str(error))

    def _read_distribution(self, url: str) -> SourceSection:
        assert self._page is not None
        try:
            response = self._page.goto(url, wait_until="domcontentloaded")
            http_failure = self._http_failure_section(response, url)
            if http_failure is not None:
                return http_failure
            locator = self._page.locator(DISTRIBUTION_SELECTOR)
            try:
                locator.wait_for(
                    state="attached",
                    timeout=self.settings.selector_attach_timeout_seconds * 1000,
                )
            except PlaywrightTimeoutError:
                abnormal = self._abnormal_page_section(url, getattr(response, "status", None))
                if abnormal is not None:
                    return abnormal
                return SourceSection(
                    FetchStatus.NO_DATA,
                    url,
                    self._page.url,
                    error_message="页面已打开，但未找到县级分布板块",
                    http_status=getattr(response, "status", None),
                )
            deadline = time.monotonic() + self.settings.distribution_wait_seconds
            text = self._read_locator_text(locator, deadline)
            while LOADING_TEXT in text and time.monotonic() < deadline:
                self._sleep(0.5)
                text = self._read_locator_text(locator, deadline)
            if LOADING_TEXT in text:
                return SourceSection(FetchStatus.TIMEOUT, url, self._page.url, error_message="县级分布异步加载超时")
            if not text:
                return SourceSection(
                    FetchStatus.NO_DATA,
                    url,
                    self._page.url,
                    http_status=getattr(response, "status", None),
                )
            return SourceSection(
                FetchStatus.SUCCESS,
                url,
                self._page.url,
                text=text,
                http_status=getattr(response, "status", None),
            )
        except PlaywrightTimeoutError as error:
            return SourceSection(FetchStatus.TIMEOUT, url, self._page.url, error_message=str(error))
        except PlaywrightError as error:
            return SourceSection(FetchStatus.ERROR, url, self._page.url, error_message=str(error))

    def _polite_delay(self) -> None:
        minimum = self.settings.minimum_delay_seconds
        maximum = self.settings.maximum_delay_seconds
        if maximum < minimum:
            raise ValueError("最大抓取间隔不能小于最小抓取间隔")
        self._sleep(random.uniform(minimum, maximum))

    def _http_failure_section(self, response: object | None, url: str) -> SourceSection | None:
        if response is None:
            return None
        status = int(getattr(response, "status", 0) or 0)
        resolved_url = self._page.url if self._page is not None else ""
        if status == 404:
            return SourceSection(
                FetchStatus.NOT_FOUND, url, resolved_url, http_status=status
            )
        if status in {403, 429}:
            return SourceSection(
                FetchStatus.RATE_LIMITED,
                url,
                resolved_url,
                error_message=f"iPlant 返回 HTTP {status}，可能为访问限制或安全验证",
                http_status=status,
            )
        if status >= 400:
            return SourceSection(
                FetchStatus.HTTP_ERROR,
                url,
                resolved_url,
                error_message=f"iPlant 返回 HTTP {status}",
                http_status=status,
            )
        return None

    def _abnormal_page_section(
        self, url: str, http_status: int | None
    ) -> SourceSection | None:
        """仅在目标板块缺失时检查是否为限流、验证码或维护页面。"""
        if self._page is None:
            return None
        try:
            title = self._page.title()
            body = self._page.locator("body").inner_text(timeout=1_000)[:1_500]
        except PlaywrightError:
            return None
        evidence = f"{title}\n{body}".casefold()
        rate_markers = (
            "访问频繁",
            "访问过于频繁",
            "too many requests",
            "rate limit",
            "验证码",
            "captcha",
            "安全验证",
            "人机验证",
        )
        error_markers = (
            "access denied",
            "forbidden",
            "service unavailable",
            "系统维护",
            "暂时无法访问",
        )
        if any(marker in evidence for marker in rate_markers):
            return SourceSection(
                FetchStatus.RATE_LIMITED,
                url,
                self._page.url,
                error_message=f"页面疑似访问限制或安全验证：{title or '无标题'}",
                http_status=http_status,
            )
        if any(marker in evidence for marker in error_markers):
            return SourceSection(
                FetchStatus.HTTP_ERROR,
                url,
                self._page.url,
                error_message=f"页面疑似服务异常：{title or '无标题'}",
                http_status=http_status,
            )
        return None

    @staticmethod
    def _retry_delay_seconds(snapshot: SourceSnapshot, attempt: int) -> float:
        sections = (snapshot.foc, snapshot.nomenclature, snapshot.county_distribution)
        if any(section.status is FetchStatus.RATE_LIMITED for section in sections):
            return float(15 * attempt)
        if any(section.status is FetchStatus.HTTP_ERROR for section in sections):
            return float(min(5 * (2 ** (attempt - 1)), 20))
        return float(min(2 ** (attempt - 1), 4))

    def _interruptible_delay(self, seconds: float, reason: str) -> None:
        remaining = max(0.0, seconds)
        while remaining > 0:
            self._ensure_not_cancelled()
            self._announce(
                f"WAIT RETRY {remaining:.0f}s",
                f"{reason}；剩余约 {remaining:.0f} 秒",
            )
            step = min(5.0, remaining)
            self._sleep(step)
            remaining -= step

    def _record_snapshot_health(self, species_name: str, snapshot: SourceSnapshot) -> None:
        self._species_since_recycle += 1
        if snapshot.is_complete:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
        statuses = "/".join(
            section.status.value
            for section in (snapshot.foc, snapshot.nomenclature, snapshot.county_distribution)
        )
        self._announce(
            f"HEALTH {species_name}",
            f"板块状态 {statuses}；连续不完整 {self._consecutive_failures} 个物种",
        )

    def _browser_recycle_reason(self) -> str:
        if self._browser is None or self._page is None:
            return "浏览器未运行"
        if (
            self.settings.browser_recycle_species_count > 0
            and self._species_since_recycle >= self.settings.browser_recycle_species_count
        ):
            return f"已连续处理 {self._species_since_recycle} 次物种抓取"
        if (
            self.settings.browser_recycle_failure_count > 0
            and self._consecutive_failures >= self.settings.browser_recycle_failure_count
        ):
            return f"已连续出现 {self._consecutive_failures} 个不完整结果"
        memory_mb = self._browser_child_memory_mb()
        if (
            self.settings.browser_memory_limit_mb > 0
            and memory_mb >= self.settings.browser_memory_limit_mb
        ):
            return f"浏览器/驱动子进程内存达到 {memory_mb:.1f} MB"
        return ""

    @staticmethod
    def _browser_child_memory_mb() -> float:
        try:
            children = psutil.Process().children(recursive=True)
            total = 0
            for child in children:
                try:
                    total += child.memory_info().rss
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    continue
            return total / (1024 * 1024)
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            return 0.0

    def _recycle_browser(self, reason: str) -> None:
        self._ensure_not_cancelled()
        self._announce(
            f"BROWSER RECYCLE #{self._browser_launch_count + 1}",
            f"正在重建抓取浏览器：{reason}；缓存和批次进度不受影响",
        )
        self._close_browser()
        self._launch_browser()

    @staticmethod
    def _read_locator_text(locator: object, deadline: float) -> str:
        """短轮询读取文本，避免单次 inner_text 落入 Playwright 默认30秒超时。"""
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        try:
            return locator.inner_text(timeout=min(1000, remaining_ms)).strip()
        except PlaywrightTimeoutError:
            return ""

    def _route_request(self, route: object) -> None:
        """屏蔽不参与文本解析的大体积资源，保留脚本、接口请求和样式。"""
        request = getattr(route, "request")
        if request.resource_type in {"image", "media", "font"}:
            route.abort()
            self._blocked_resource_count += 1
        else:
            route.continue_()

    def _timed_read(
        self, label: str, reader: Callable[[], SourceSection]
    ) -> SourceSection:
        started = time.perf_counter()
        blocked_before = self._blocked_resource_count
        section = reader()
        self._network_request_count += 1
        elapsed = time.perf_counter() - started
        blocked = self._blocked_resource_count - blocked_before
        self._announce(
            f"DONE {label} {elapsed:.2f}s",
            f"{label}读取完成：{section.status.value}；HTTP {section.http_status or '-'}；"
            f"累计请求 {self._network_request_count}；屏蔽无用资源 {blocked} 个；"
            f"页面 {section.resolved_url or section.requested_url}",
        )
        return section

    def _ensure_not_cancelled(self) -> None:
        if self._cancel_requested is not None and self._cancel_requested():
            raise ScraperCancelled("已收到停止请求")

    def _announce(self, command: str, detail: str) -> None:
        if self._on_activity is not None:
            self._on_activity(command, detail)
