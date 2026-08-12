"""安装版与源码运行共用的资源、用户数据目录定位。"""

from __future__ import annotations

import os
from pathlib import Path
import sys


def resource_root() -> Path:
    """返回程序随附的只读资源目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def user_data_dir() -> Path:
    """返回可写的用户数据目录，不向安装目录写入缓存或人工决定。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    path = base / "QinChecker"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_bundled_browser() -> None:
    """让绿色版中的 Playwright 使用同目录附带的 Chromium。"""
    browser_dir = resource_root() / "browsers"
    if browser_dir.exists():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browser_dir))
