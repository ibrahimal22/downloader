"""Light/dark themes built from a small set of color tokens."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

TOKENS = {
    "dark": {
        "bg": "#16181d", "surface": "#1e2128", "surface2": "#262a33", "border": "#323743",
        "text": "#e6e8ee", "muted": "#9aa1b1", "accent": "#4f8cff", "accent_text": "#ffffff",
        "danger": "#ef5b5b", "success": "#3ecf8e", "warning": "#f5b94a", "selection": "#2c4a80",
    },
    "light": {
        "bg": "#f5f6f8", "surface": "#ffffff", "surface2": "#eef0f4", "border": "#d9dde5",
        "text": "#1b1f27", "muted": "#5d6576", "accent": "#2f6fed", "accent_text": "#ffffff",
        "danger": "#d64545", "success": "#1f9d68", "warning": "#b7791f", "selection": "#cfe0ff",
    },
}

QSS = """
* {{ font-size: 10pt; }}
QWidget {{ background: {bg}; color: {text}; }}
QMainWindow, QDialog {{ background: {bg}; }}
QToolTip {{ background: {surface2}; color: {text}; border: 1px solid {border}; padding: 4px; }}

#Sidebar {{ background: {surface}; border: none; padding-top: 8px; outline: 0; }}
#Sidebar::item {{ padding: 10px 14px; margin: 2px 8px; border-radius: 6px; color: {muted}; }}
#Sidebar::item:selected {{ background: {surface2}; color: {text}; }}
#Sidebar::item:hover:!selected {{ background: {surface2}; }}
#SidebarPanel {{ background: {surface}; border-right: 1px solid {border}; }}
#Facets::item {{ padding: 5px 6px; border-radius: 4px; }}
#Facets::item:selected {{ background: {selection}; }}
#Brand {{ font-size: 13pt; font-weight: 600; padding: 14px 16px 6px 16px; background: {surface}; }}

#PageTitle {{ font-size: 15pt; font-weight: 600; }}
#Muted, QLabel[muted="true"] {{ color: {muted}; }}
#Card {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; }}

QPushButton, QToolButton {{
    background: {surface2}; border: 1px solid {border}; border-radius: 6px;
    padding: 6px 12px; color: {text};
}}
QPushButton:hover, QToolButton:hover {{ border-color: {accent}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {selection}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {muted}; border-color: {surface2}; }}
QPushButton[primary="true"] {{ background: {accent}; color: {accent_text}; border-color: {accent}; }}
QPushButton[danger="true"] {{ color: {danger}; }}
QToolButton {{ padding: 5px 8px; }}
QToolButton::menu-indicator {{ image: none; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit,
QTimeEdit {{
    background: {surface}; border: 1px solid {border}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {selection};
}}
QSpinBox, QDoubleSpinBox {{ max-width: 220px; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {accent}; }}
QComboBox QAbstractItemView {{ background: {surface}; border: 1px solid {border};
                               selection-background-color: {selection}; }}

QTableView, QTreeView, QListView {{
    background: {surface}; alternate-background-color: {surface2};
    border: 1px solid {border}; border-radius: 8px; gridline-color: transparent;
    selection-background-color: {selection}; selection-color: {text}; outline: 0;
}}
QHeaderView::section {{
    background: {surface}; color: {muted}; border: none; border-bottom: 1px solid {border};
    padding: 6px 8px; font-weight: 600;
}}
QProgressBar {{
    background: {surface2}; border: none; border-radius: 4px; text-align: center;
    color: {text}; height: 16px;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}

QTabWidget::pane {{ border: 1px solid {border}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 14px; color: {muted};
                border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {text}; border-bottom-color: {accent}; }}

QGroupBox {{ border: 1px solid {border}; border-radius: 8px; margin-top: 14px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {muted}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QStatusBar {{ background: {surface}; border-top: 1px solid {border}; color: {muted}; }}
QStatusBar QLabel {{ background: transparent; color: {muted}; padding: 0 8px; }}
QMenu {{ background: {surface}; border: 1px solid {border}; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {selection}; }}
QSplitter::handle {{ background: {border}; }}
"""

_current: dict[str, str] = TOKENS["dark"]


def resolve(mode: str) -> str:
    if mode in ("dark", "light"):
        return mode
    scheme = QGuiApplication.styleHints().colorScheme()
    return "light" if scheme == Qt.ColorScheme.Light else "dark"


def apply(app: QApplication, mode: str) -> None:
    global _current
    name = resolve(mode)
    tokens = TOKENS[name]
    _current = tokens
    app.setStyle("Fusion")
    font = app.font()
    for family in ("Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Inter", "Noto Sans"):
        if family in QFontDatabase.families():
            font.setFamily(family)
            break
    app.setFont(font)
    palette = QPalette()
    for role, key in (
        (QPalette.ColorRole.Window, "bg"), (QPalette.ColorRole.Base, "surface"),
        (QPalette.ColorRole.AlternateBase, "surface2"), (QPalette.ColorRole.Text, "text"),
        (QPalette.ColorRole.WindowText, "text"), (QPalette.ColorRole.Button, "surface2"),
        (QPalette.ColorRole.ButtonText, "text"), (QPalette.ColorRole.Highlight, "selection"),
        (QPalette.ColorRole.HighlightedText, "text"), (QPalette.ColorRole.PlaceholderText, "muted"),
        (QPalette.ColorRole.ToolTipBase, "surface2"), (QPalette.ColorRole.ToolTipText, "text"),
        (QPalette.ColorRole.Link, "accent"),
    ):
        palette.setColor(role, QColor(tokens[key]))
    app.setPalette(palette)
    app.setStyleSheet(QSS.format(**tokens))


def color(key: str) -> QColor:
    return QColor(_current[key])
