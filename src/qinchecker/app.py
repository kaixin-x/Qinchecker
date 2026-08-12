"""应用启动入口。"""

from __future__ import annotations

import sys
from pathlib import Path

from qinchecker.runtime import configure_bundled_browser


WINDOWS_CJK_FONTS = (
    Path("C:/Windows/Fonts/msyh.ttc"),
    Path("C:/Windows/Fonts/msyhbd.ttc"),
    Path("C:/Windows/Fonts/simhei.ttf"),
)


def register_cjk_fonts() -> list[str]:
    """Register Windows Chinese fonts explicitly for Qt's bundled runtime.

    Some Qt platform backends do not discover the system font collection by
    themselves.  Registering the available files avoids Chinese text being
    rendered as square placeholders in the application UI.
    """
    from PySide6.QtGui import QFontDatabase

    registered: list[str] = []
    for font_path in WINDOWS_CJK_FONTS:
        if not font_path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id >= 0:
            registered.extend(QFontDatabase.applicationFontFamilies(font_id))
    return registered


def main() -> int:
    configure_bundled_browser()
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as error:
        message = "未安装 PySide6。请先按项目依赖安装桌面界面组件。"
        raise SystemExit(message) from error

    from qinchecker.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("QinChecker")
    register_cjk_fonts()
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
