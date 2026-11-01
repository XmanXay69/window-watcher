"""
theme.py: Window Watcher's look: its colors and its stylesheet.

A Qt "stylesheet" works like CSS on a web page: it sets colors, rounded
corners and spacing for the app's widgets. Widgets are picked by type
(QPushButton) or by the name we gave them (#card, #primary...).

Colors follow macOS: a light set and a dark set, plus your system accent
color (System Settings > Appearance) as the one highlight color.
"""

from PySide6.QtGui import QColor, QPalette


def colors(palette):
    """The color set to use, based on whether macOS is in dark mode."""
    dark = palette.color(QPalette.Window).lightness() < 128
    accent = palette.color(QPalette.Accent)
    return {
        "dark": dark,
        "accent": accent.name(),
        "accent_soft": f"rgba({accent.red()}, {accent.green()}, {accent.blue()}, 0.16)",
        "background": "#1c1c1e" if dark else "#f5f5f7",
        "card": "#262628" if dark else "#ffffff",
        "border": "rgba(255, 255, 255, 0.07)" if dark else "rgba(0, 0, 0, 0.08)",
        "field": "#1c1c1e" if dark else "#f2f2f4",
        "button": "#3a3a3c" if dark else "#e9e9ed",
        "button_hover": "#48484a" if dark else "#dedee3",
        "text": "#f5f5f7" if dark else "#1d1d1f",
        "secondary": "#98989d" if dark else "#6e6e73",
        "red": "#ff453a" if dark else "#d70015",
    }


def stylesheet(c):
    """The stylesheet for the main window and the floating chat."""
    return f"""
        QWidget#root {{ background: {c['background']}; }}
        QFrame#card {{
            background: {c['card']};
            border: 1px solid {c['border']};
            border-radius: 14px;
        }}
        QLabel {{ color: {c['text']}; background: transparent; }}
        QLabel#title {{ font-size: 19px; font-weight: 600; }}
        QLabel#caption {{
            color: {c['secondary']};
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 0.6px;
        }}
        QLabel#secondary {{ color: {c['secondary']}; }}

        QPushButton {{
            background: {c['button']};
            color: {c['text']};
            border: none;
            border-radius: 8px;
            padding: 6px 13px;
        }}
        QPushButton:hover {{ background: {c['button_hover']}; }}
        QPushButton:disabled {{ color: {c['secondary']}; }}
        QPushButton#primary {{ background: {c['accent']}; color: white; font-weight: 600; }}
        QPushButton#primary:hover {{ background: {QColor(c['accent']).lighter(112).name()}; }}
        QPushButton#link {{
            background: transparent;
            color: {c['secondary']};
            padding: 0 2px;
            font-size: 12px;
        }}
        QPushButton#link:hover {{ color: {c['accent']}; background: transparent; }}
        QPushButton#chip {{
            background: {c['button']};
            color: {c['text']};
            border-radius: 12px;  /* must stay under half the button's height */
            padding: 5px 13px;
        }}
        QPushButton#chip:hover {{ background: {c['button_hover']}; }}

        QLineEdit {{
            background: {c['field']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 17px;
            padding: 7px 14px;
        }}
        QLineEdit:focus {{ border: 1px solid {c['accent']}; }}
        QPlainTextEdit, QTextBrowser {{
            background: transparent;
            color: {c['text']};
            border: none;
        }}
        QCheckBox {{ color: {c['secondary']}; }}

        QListWidget {{
            background: {c['field']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 10px;
            padding: 4px;
            outline: none;
        }}
        QListWidget::item {{ padding: 7px 10px; border-radius: 7px; }}
        QListWidget::item:hover {{ background: {c['button']}; }}
        QListWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}

        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
        QScrollBar::handle:vertical {{ background: {c['button_hover']}; border-radius: 3px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    """
