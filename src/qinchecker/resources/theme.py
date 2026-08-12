"""现代科研数据工作台的基础主题。"""

APP_STYLE = """
QMainWindow { background: #F6F8FB; font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif; }
QFrame#topBar { background: #FFFFFF; border-bottom: 1px solid #DDE3EA; }
QFrame#panel { background: #FFFFFF; border: 1px solid #DDE3EA; border-radius: 10px; }
QLabel#title { color: #163D36; font-size: 20px; font-weight: 700; }
QLabel#subtitle { color: #667085; font-size: 12px; }
QLabel#speciesIdentity { background: #EFF8F6; color: #164C42; border: 1px solid #CDE7E1; border-radius: 7px; padding: 8px 10px; font-size: 14px; font-weight: 700; }
QLabel#actionGroupTitle { color: #475467; font-size: 12px; font-weight: 700; }
QLabel#manualStatus { background: #F4F8FF; color: #254E80; border: 1px solid #D5E3F7; border-radius: 8px; padding: 10px; }
QLabel#noticeBanner, QLabel#inlineMessage { background: #EFF8FF; color: #175CD3; border: 1px solid #B2DDFF; border-radius: 7px; padding: 8px 11px; }
QLabel#noticeBanner[noticeKind="success"], QLabel#inlineMessage[noticeKind="success"] { background: #ECFDF3; color: #027A48; border-color: #ABEFC6; }
QLabel#noticeBanner[noticeKind="warning"], QLabel#inlineMessage[noticeKind="warning"] { background: #FFFAEB; color: #B54708; border-color: #FEDF89; }
QLabel#noticeBanner[noticeKind="error"], QLabel#inlineMessage[noticeKind="error"] { background: #FEF3F2; color: #B42318; border-color: #FECDCA; }
QLabel[stageState="upcoming"] { background: #F2F4F7; color: #667085; border-radius: 7px; padding: 8px; }
QLabel[stageState="active"] { background: #DFF2ED; color: #164C42; border: 1px solid #9FD7CA; border-radius: 7px; padding: 8px; font-weight: 700; }
QLabel[stageState="done"] { background: #ECFDF3; color: #027A48; border-radius: 7px; padding: 8px; font-weight: 700; }
QFrame#manualSourceColumn { background: #F8FAFC; border: 1px solid #E4E9EF; border-radius: 8px; }
QLabel#manualColumnTitle { color: #164C42; font-size: 14px; font-weight: 700; }
QFrame#actionGroup { background: #F8FAFC; border: 1px solid #E4E9EF; border-radius: 8px; }
QPushButton { background: #176B5B; color: white; border: 0; border-radius: 7px; padding: 7px 12px; }
QPushButton#warningButton { background: #FFFFFF; color: #B42318; border: 1px solid #FDA29B; }
QPushButton#warningButton:hover { background: #FEF3F2; }
QPushButton:disabled { background: #C8D3D1; color: #5E6B68; }
QLineEdit { background: #FFFFFF; border: 1px solid #C9D4D2; border-radius: 7px; padding: 6px 8px; }
QComboBox { background: #FFFFFF; border: 1px solid #C9D4D2; border-radius: 7px; padding: 6px 8px; }
QTabWidget::pane { border: 1px solid #DDE3EA; border-radius: 7px; background: #FFFFFF; }
QTabBar::tab { background: #EEF2F5; color: #475467; padding: 8px 11px; margin-right: 2px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { background: #DFF2ED; color: #164C42; font-weight: 700; }
QTableWidget { background: #FFFFFF; border: 0; gridline-color: #E9EEF3; selection-background-color: #DFF2ED; }
QHeaderView::section { background: #F0F4F6; color: #344054; border: 0; border-bottom: 1px solid #DDE3EA; padding: 7px; font-weight: 600; }
QListWidget { background: #FFFFFF; border: 0; outline: 0; }
QListWidget::item { padding: 8px; border-radius: 6px; }
QListWidget::item:selected { background: #DFF2ED; color: #123B33; }
QStatusBar { background: #FFFFFF; color: #667085; border-top: 1px solid #DDE3EA; }
"""

STATUS_COLORS = {
    "auto_ready": "#15803D",
    "pending_review": "#D97706",
    "accepted_source": "#15803D",
    "kept_original": "#475467",
    "manually_confirmed": "#2563EB",
    "no_change": "#98A2B3",
    "failed": "#DC2626",
    "unprocessed": "#101828",
}
