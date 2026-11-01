"""
menubar.py: Window Watcher's icon in the menu bar (the strip at the top of the
screen with the clock).

The app starts "asleep": just this icon, no windows, no screenshots, no
Claude. Clicking the icon wakes it up (pick a window, start watching). While
it's awake, clicking the icon opens a small menu instead.
"""

import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def make_menu_bar_icon():
    """Draw a small eye. It's black on transparent and marked as a "mask", so
    macOS recolors it to match the menu bar (dark or light)."""
    pixmap = QPixmap(36, 36)  # 18x18 points on a Retina screen
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    eye = QPainterPath()
    eye.moveTo(1.5, 9)
    eye.quadTo(9, 1.5, 16.5, 9)
    eye.quadTo(9, 16.5, 1.5, 9)
    p.setPen(QPen(QColor("black"), 1.6))
    p.drawPath(eye)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("black"))
    p.drawEllipse(QPointF(9, 9), 3.2, 3.2)
    p.end()
    icon = QIcon(pixmap)
    icon.setIsMask(True)
    return icon


# --- Open at Login (Apple's ServiceManagement framework) ---------------------

def can_open_at_login():
    """Only the installed app can be a login item (not the Python script)."""
    return getattr(sys, "frozen", False)


def opens_at_login():
    from ServiceManagement import SMAppService, SMAppServiceStatusEnabled
    return SMAppService.mainAppService().status() == SMAppServiceStatusEnabled


def set_open_at_login(on):
    """Add or remove Window Watcher from System Settings > General > Login Items.
    Returns an error message, or None if it worked."""
    from ServiceManagement import SMAppService
    service = SMAppService.mainAppService()
    if on:
        ok, error = service.registerAndReturnError_(None)
    else:
        ok, error = service.unregisterAndReturnError_(None)
    return None if ok else str(error.localizedDescription() if error else "unknown error")


class MenuBarIcon(QSystemTrayIcon):

    def __init__(self, main_window):
        super().__init__(make_menu_bar_icon(), main_window)
        self.main_window = main_window
        self.setToolTip("Window Watcher")
        self.activated.connect(self.on_click)

        # The menu shown while awake.
        self.menu = QMenu()
        self.menu.addAction("Show Window Watcher", main_window.bring_to_front)
        self.menu.addAction("Show Chat  (⌘⇧C)", main_window.on_global_shortcut)
        self.menu.addAction("Change Window…", main_window.change_window_from_menu_bar)
        self.menu.addSeparator()
        self.menu.addAction("Go to Sleep", main_window.go_dormant)
        self.login_action = self.menu.addAction("Open at Login", self.toggle_open_at_login)
        self.login_action.setCheckable(True)
        self.menu.addSeparator()
        self.menu.addAction("Quit Window Watcher", main_window.quit_app)
        self.update_menu()

    def on_click(self, reason):
        # While asleep there's no menu, so a click wakes the app up.
        if reason == QSystemTrayIcon.Trigger and self.main_window.dormant:
            self.main_window.wake()

    def update_menu(self):
        """Asleep: no menu (a click wakes it). Awake: show the menu."""
        self.setContextMenu(None if self.main_window.dormant else self.menu)
        self.setToolTip("Window Watcher (asleep): click to watch a window"
                        if self.main_window.dormant else "Window Watcher")
        self.login_action.setEnabled(can_open_at_login())
        self.login_action.setChecked(can_open_at_login() and opens_at_login())

    def toggle_open_at_login(self, on):
        problem = set_open_at_login(on)
        if problem:
            self.main_window.add_chat("error", f"Couldn't change Open at Login: {problem}")
        self.update_menu()
