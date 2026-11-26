"""Dark Fusion theme with a single accent colour."""
from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

ACCENT = "#3b82f6"
OK = "#34d399"
WARN = "#fbbf24"
BAD = "#f87171"
MUTED = "#8b93a1"

QSS = f"""
QToolTip {{ background:#1f2430; color:#e6e9ef; border:1px solid #343b4a; padding:4px; }}
QGroupBox {{ border:1px solid #303644; border-radius:8px; margin-top:12px; padding:10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left:10px; padding:0 4px; color:{MUTED}; }}
QPushButton {{ background:#272d3a; border:1px solid #363d4e; border-radius:6px; padding:6px 12px; }}
QPushButton:hover {{ background:#2f3646; }}
QPushButton:disabled {{ color:#6b7280; background:#21262f; }}
QPushButton#primary {{ background:{ACCENT}; border-color:{ACCENT}; color:white; font-weight:600; padding:8px 16px; }}
QPushButton#primary:hover {{ background:#5593f7; }}
QPushButton#primary:disabled {{ background:#2b3f66; color:#93a3c2; }}
QPushButton#fix {{ background:#f59e0b; border-color:#f59e0b; color:#1b1500; font-weight:700; padding:6px 14px; }}
QPushButton#fix:hover {{ background:#fbbf24; }}
QPushButton#fix:disabled {{ background:#5a4416; color:#9b8a5c; }}
QPushButton#danger {{ color:{BAD}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QListWidget, QTableWidget, QTreeView {{
    background:#181c25; border:1px solid #303644; border-radius:6px; selection-background-color:{ACCENT};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ padding:5px 8px; }}
QComboBox QAbstractItemView {{ background:#181c25; border:1px solid #303644; selection-background-color:{ACCENT}; }}
QComboBox QAbstractItemView::item:disabled {{ color:#5b6270; }}
QComboBox:disabled {{ color:#6b7280; }}
QTabWidget::pane {{ border:1px solid #303644; border-radius:8px; top:-1px; }}
QTabBar::tab {{ background:transparent; padding:9px 18px; color:{MUTED}; border-bottom:2px solid transparent; }}
QTabBar::tab:selected {{ color:#e6e9ef; border-bottom:2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color:#e6e9ef; }}
QHeaderView::section {{ background:#20252f; color:{MUTED}; border:0; padding:6px; }}
QProgressBar {{ border:1px solid #303644; border-radius:5px; background:#181c25; text-align:center; height:14px; }}
QProgressBar::chunk {{ background:{ACCENT}; border-radius:4px; }}
QFrame#dropzone {{ border:2px dashed #3a4257; border-radius:12px; background:#171b23; }}
QFrame#dropzone[hover="true"] {{ border-color:{ACCENT}; background:#1a2234; }}
QStatusBar {{ color:{MUTED}; }}
QSplitter::handle {{ background:#262c39; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor("#1e222b"))
    p.setColor(QPalette.ColorRole.WindowText, QColor("#e6e9ef"))
    p.setColor(QPalette.ColorRole.Base, QColor("#181c25"))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#1e222b"))
    p.setColor(QPalette.ColorRole.Text, QColor("#e6e9ef"))
    p.setColor(QPalette.ColorRole.Button, QColor("#272d3a"))
    p.setColor(QPalette.ColorRole.ButtonText, QColor("#e6e9ef"))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1f2430"))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor("#e6e9ef"))
    p.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor("#6b7280"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6b7280"))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6b7280"))
    app.setPalette(p)
    app.setStyleSheet(QSS)
