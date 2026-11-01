"""
app.py: Window Watcher, the part you see and click.

Start it by double-clicking "Start Window Watcher.command", or in Terminal:
    .venv/bin/python app.py

How it works:
0. The app starts asleep, as an icon in the menu bar. Clicking it wakes it up.
1. You pick a window. Every few seconds the app takes a screenshot of it.
2. If a screenshot looks the same as the last one, it's thrown away.
3. When you ask a question, the newest screenshot (plus the one from before
   the latest change) goes to Claude along with the recent chat.
4. If you set standing instructions, every meaningful change is sent to
   Claude, which decides whether to alert you.

Only one Claude call runs at a time. Your questions wait in line, and
background checks wait until Claude is free.

A note on threads: taking screenshots and waiting for Claude both take time,
so they run on "background threads" to keep the window responsive. Background
threads aren't allowed to touch the window, so when they finish they send a
Qt "signal", and Qt runs our handler on the main thread. All of the app's
state below is only ever changed on the main thread.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path

# Hide a harmless warning Qt prints about the color info in macOS screenshots.
# (This has to be set before Qt is imported.)
os.environ.setdefault("QT_LOGGING_RULES", "qt.gui.imageio=false")

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QFrame, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QTextBrowser, QVBoxLayout, QWidget,
)

import capture
import claude_bridge
import documents
import hotkeys
import menubar
import theme

# --- Settings you can tweak --------------------------------------------------
CAPTURE_EVERY_SECONDS = 2.5      # how often to take a screenshot
MIN_SECONDS_BETWEEN_CHECKS = 5   # least time between automatic alert checks
SECONDS_AFTER_FAILED_CHECK = 60  # wait this long after a check fails
KEEP_FRAMES = 4                  # how many different screenshots to keep
CHAT_MESSAGES_SENT = 10          # how much recent chat goes with each question
CHANGE_LOG_LENGTH = 8            # how many "what changed" notes to remember
PREVIEW_WIDTH, PREVIEW_HEIGHT = 272, 170

# The four programmable buttons start out with these. Click "Edit Buttons…"
# in the app to change them; your text is saved between launches.
QUICK_PROMPT_DEFAULTS = [
    "What's the answer to this?",
    "Explain this",
    "What just changed?",
    "Summarize what's on screen",
]


# ---------------------------------------------------------------------------
# Small data holders
# ---------------------------------------------------------------------------

def set_space_behavior(widget, *behaviors):
    """Tell macOS which desktops (Spaces) a window may appear on. Window
    Watcher uses this so its windows open over whatever you're looking at,
    even an app in full-screen mode, instead of jumping to another desktop."""
    try:
        import objc
        ns_window = objc.objc_object(c_void_p=int(widget.winId())).window()
        flags = ns_window.collectionBehavior()
        for behavior in behaviors:
            flags |= behavior
        ns_window.setCollectionBehavior_(flags)
    except Exception as error:  # a nice-to-have; never crash over it
        print("Couldn't set where the window appears:", error)


def rounded(pixmap, radius):
    """A copy of a picture with rounded corners."""
    ratio = pixmap.devicePixelRatio()
    result = QPixmap(pixmap.size())
    result.setDevicePixelRatio(ratio)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pixmap.width() / ratio, pixmap.height() / ratio, radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return result


def clock(timestamp):
    """Turn a time.time() number into '14:05:09'."""
    return datetime.fromtimestamp(timestamp).strftime("%H:%M:%S")


@dataclass(eq=False)
class ChatMessage:
    """One line in the chat.
    role is "user", "assistant", "alert", or "note"/"error" (app messages,
    which are never sent to Claude)."""
    role: str
    text: str
    when: float = field(default_factory=time.time)
    number: int = 0          # questions are numbered Q1, Q2, ...
    reply_to: object = None  # for answers: the question (ChatMessage) it answers

    def time_text(self):
        return clock(self.when)


class ChatView(QTextBrowser):
    """The chat transcript. You can select and copy any text, and each
    message has a "Copy" link that copies just that message."""

    def __init__(self, messages):
        super().__init__()
        self.messages = messages  # the app's list of ChatMessages
        self.setOpenLinks(False)  # we handle link clicks ourselves
        self.document().setDefaultStyleSheet("a { color: #8e8e93; text-decoration: none; }")
        self.document().setDocumentMargin(2)
        self.anchorClicked.connect(self.on_link_clicked)

    def on_link_clicked(self, url):
        # Links look like "copy:5", meaning "copy message number 5".
        if url.scheme() == "copy":
            QApplication.clipboard().setText(self.messages[int(url.path())].text)

    def add_html(self, html):
        self.append(html)
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


class QuickPromptBar(QWidget):
    """A row of programmable buttons. Clicking one sends its text to Claude,
    just as if you'd typed it. One bar sits in each chat area."""

    def __init__(self, main_window, columns=4, with_edit_button=True):
        super().__init__()
        self.main_window = main_window
        self.buttons = []
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        for number in range(len(QUICK_PROMPT_DEFAULTS)):
            button = QPushButton()
            button.setObjectName("chip")  # pill-shaped (see theme.py)
            button.clicked.connect(lambda checked=False, n=number: main_window.send_quick_prompt(n))
            grid.addWidget(button, number // columns, number % columns)
            self.buttons.append(button)
        if with_edit_button:
            edit_button = QPushButton("Edit Buttons…")
            edit_button.clicked.connect(main_window.edit_quick_prompts)
            grid.addWidget(edit_button, 0, columns)

    def refresh(self, prompts):
        """Show the current button texts. Empty ones are hidden."""
        for number, (button, text) in enumerate(zip(self.buttons, prompts)):
            short = text if len(text) <= 30 else text[:29] + "…"
            button.setText(short)
            button.setToolTip(f"{text}\nShortcut: ⌘{number + 1}")
            button.setVisible(bool(text))


class EditQuickPromptsDialog(QDialog):
    """Four boxes for typing what each button should send."""

    def __init__(self, parent, prompts):
        super().__init__(parent)
        self.setWindowTitle("Edit Buttons")
        self.resize(460, 0)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Type what each button sends to Claude. Leave a box empty to hide that button."))
        self.boxes = []
        for number, text in enumerate(prompts):
            box = QLineEdit(text)
            box.setPlaceholderText(f"Button {number + 1} (⌘{number + 1})")
            layout.addWidget(box)
            self.boxes.append(box)

        buttons = QHBoxLayout()
        reset_button = QPushButton("Reset to Defaults")
        reset_button.clicked.connect(self.reset)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        save_button = QPushButton("Save")
        save_button.setDefault(True)
        save_button.clicked.connect(self.accept)
        buttons.addWidget(reset_button)
        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)

    def reset(self):
        for box, text in zip(self.boxes, QUICK_PROMPT_DEFAULTS):
            box.setText(text)

    def prompts(self):
        return [box.text().strip() for box in self.boxes]


class FloatingChat(QWidget):
    """A separate chat window that stays on top of other apps, so you can
    keep it next to the window you're watching. Closing it just hides it."""

    def __init__(self, main_window):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.main_window = main_window
        self.setWindowTitle("Window Watcher Chat")
        self.setObjectName("root")  # gets the app's background color (theme.py)
        self.setAttribute(Qt.WA_StyledBackground)  # needed for that on a QWidget subclass
        # Same seamless top as the main window (no separate title strip).
        self.setWindowFlag(Qt.ExpandedClientAreaHint, True)
        self.setWindowFlag(Qt.NoTitleBarBackgroundHint, True)
        self.resize(440, 560)

        self.chat_view = ChatView(main_window.chat)
        # A 2x2 grid of programmable buttons keeps this window narrow.
        self.prompt_bar = main_window.make_quick_prompt_bar(columns=2, with_edit_button=False)
        self.question_box = QLineEdit()
        self.question_box.setPlaceholderText("Ask about the window…")
        self.question_box.returnPressed.connect(self.on_ask)
        ask_button = QPushButton("Ask")
        ask_button.setObjectName("primary")
        ask_button.clicked.connect(self.on_ask)
        copy_all_button = QPushButton("Copy All")
        copy_all_button.clicked.connect(main_window.copy_all_chat)
        hide_button = QPushButton("Hide")
        hide_button.setToolTip("Shortcut: ⌘⇧C")
        hide_button.clicked.connect(main_window.hide_floating_chat)

        ask_row = QHBoxLayout()
        ask_row.addWidget(self.question_box, 1)
        ask_row.addWidget(ask_button)
        bottom_row = QHBoxLayout()
        edit_buttons_button = QPushButton("Edit Buttons…")
        edit_buttons_button.clicked.connect(main_window.edit_quick_prompts)
        bottom_row.addWidget(copy_all_button)
        bottom_row.addWidget(edit_buttons_button)
        bottom_row.addStretch()
        bottom_row.addWidget(hide_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.chat_view, 1)
        layout.addWidget(self.prompt_bar)
        layout.addLayout(ask_row)
        layout.addLayout(bottom_row)

    def on_ask(self):
        self.main_window.ask(self.question_box.text())
        self.question_box.clear()

    def hideEvent(self, event):
        self.main_window.update_chat_button()
        super().hideEvent(event)

    def showEvent(self, event):
        self.main_window.update_chat_button()
        super().showEvent(event)
        # Show on every desktop, including over full-screen apps (like a game).
        from AppKit import NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary
        set_space_behavior(self, NSWindowCollectionBehaviorCanJoinAllSpaces,
                           NSWindowCollectionBehaviorFullScreenAuxiliary)


@dataclass(eq=False)
class Frame:
    """One saved screenshot."""
    path: Path
    when: float
    thumbnail: object = None  # small grayscale copy, used to spot changes

    def time_text(self):
        return clock(self.when)


@dataclass(eq=False)
class CaptureResult:
    """What a background screenshot sends back to the main thread."""
    generation: int
    path: Path
    window: object = None
    thumbnail: object = None
    error: str = None


@dataclass(eq=False)
class ClaudeJob:
    """One call to Claude: either your question or a background check."""
    kind: str                  # "question" or "check"
    prompt: str
    generation: int
    files_to_delete: list
    schema: dict = None
    checked_frame: Frame = None  # for checks: the screenshot Claude looked at
    question: ChatMessage = None  # for questions: the message being answered
    answer: object = None
    error: str = None


class Signals(QObject):
    """Messages from background threads to the main thread."""
    frame_ready = Signal(object)    # a CaptureResult
    claude_done = Signal(object)    # a finished ClaudeJob
    claude_status = Signal(object)  # the startup "is Claude set up?" result
    documents_ready = Signal(object)  # (documents, problems) after adding files


def delete_file(path):
    """Delete a file if it exists. Never complains."""
    if path:
        Path(path).unlink(missing_ok=True)


def clean_old_temp_folders():
    """Delete screenshot folders left behind if the app ever crashed."""
    for folder in Path(tempfile.gettempdir()).glob("window-watcher-*"):
        if time.time() - folder.stat().st_mtime > 3600:
            shutil.rmtree(folder, ignore_errors=True)


# ---------------------------------------------------------------------------
# The "choose a window" dialog
# ---------------------------------------------------------------------------

class WindowPicker(QDialog):
    """Shows the open windows and lets you pick one.

    Normally it's a regular dialog. Given under_rect (the menu bar icon's
    position), it becomes a dropdown panel hanging under the icon, like the
    menu bar's Wi-Fi or Control Center panels: no title bar, rounded corners,
    and clicking anywhere else closes it."""

    def __init__(self, parent=None, under_rect=None):
        super().__init__(parent)
        self.setWindowTitle("Choose a Window")
        self.windows = []
        self.chosen = None
        self.under_rect = under_rect

        # Same look as the main window (colors and styles from theme.py).
        self.setStyleSheet(theme.stylesheet(theme.colors(self.palette())))

        # Everything sits on a "panel", so the dropdown can have round corners.
        panel = QFrame()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        if under_rect is not None:
            # Popup: no title bar, closes when you click elsewhere.
            self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
            self.setAttribute(Qt.WA_TranslucentBackground)  # lets the corners be round
            panel.setObjectName("card")  # a rounded card, like in the main window
            self.resize(430, 380)
        else:
            panel.setObjectName("root")
            self.resize(540, 440)

        caption = QLabel("WINDOW WATCHER")
        caption.setObjectName("caption")
        title = QLabel("Which window should I watch?")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(caption)
        layout.addWidget(title)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list)

        buttons = QHBoxLayout()
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        self.watch_button = QPushButton("Watch This Window")
        self.watch_button.setObjectName("primary")
        self.watch_button.setDefault(True)
        self.watch_button.clicked.connect(self.accept)
        buttons.addWidget(refresh_button)
        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.watch_button)
        layout.addLayout(buttons)

        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self.under_rect is not None:
            self.move_under_icon()

    def move_under_icon(self):
        """Center the panel just below the menu bar icon, kept on screen."""
        icon = self.under_rect
        screen = QApplication.screenAt(icon.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = icon.center().x() - self.width() // 2
        x = max(area.left() + 8, min(x, area.right() - self.width() - 8))
        self.move(x, max(icon.bottom() + 6, area.top() + 4))

    def refresh(self):
        """Reload the list of open windows."""
        self.list.clear()
        try:
            self.windows = capture.list_windows()
        except capture.CaptureError as error:
            self.windows = []
            QMessageBox.warning(self, "Couldn't list windows", str(error))

        for window in self.windows:
            self.list.addItem(window.label())
        if self.windows:
            self.list.setCurrentRow(0)
        self.watch_button.setEnabled(bool(self.windows))

    def accept(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.windows):
            self.chosen = self.windows[row]
            super().accept()


# ---------------------------------------------------------------------------
# The main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Window Watcher")
        self.resize(760, 680)

        # Screenshots live in a private temporary folder that's deleted on quit.
        clean_old_temp_folders()
        self.frames_dir = Path(tempfile.mkdtemp(prefix="window-watcher-")).resolve()

        # --- What the app is doing (only changed on the main thread) ---
        self.window = None            # the WindowInfo we're watching
        self.generation = 0           # goes up each time you switch windows
        self.paused = False
        self.capture_in_flight = False
        self.capture_count = 0
        self.capture_problem = None   # text of the last capture error, if any
        self.latest = None            # the newest screenshot (a Frame)
        self.history = []             # recent screenshots that differ from each other
        self.last_change_time = None

        self.dormant = True           # asleep in the menu bar (see wake/go_dormant)
        self.menu_icon = None         # the menu bar icon, set up in main()
        self.claude_checked = False
        self.busy = False             # True while a Claude call is running
        self.busy_kind = None
        self.job_count = 0
        self.pending_questions = []   # ChatMessages waiting for Claude
        self.chat = []                # every ChatMessage, oldest first
        # The programmable buttons' text, saved between launches by QSettings.
        self.settings = QSettings("XmanXay69", "Window Watcher")
        self.quick_prompts = [str(self.settings.value(f"quick_prompt_{n + 1}", default))
                              for n, default in enumerate(QUICK_PROMPT_DEFAULTS)]
        self.prompt_bars = []         # every QuickPromptBar on screen
        self.question_count = 0

        self.instructions = []        # standing instructions, one per line
        self.documents = []           # reference documents you attached (documents.Document)
        self.check_needed = False     # the screen changed since the last check
        self.last_checked = None      # the Frame Claude checked last time
        self.next_check_allowed = 0.0
        self.change_log = []          # (time, summary) from background checks
        self.recent_alerts = []
        self.last_check_error = None

        # Background threads report back through these signals.
        self.signals = Signals()
        self.signals.frame_ready.connect(self.on_frame_ready)
        self.signals.claude_done.connect(self.on_claude_done)
        self.signals.claude_status.connect(self.on_claude_status)
        self.signals.documents_ready.connect(self.on_documents_ready)

        self.build_ui()

        # The system-wide ⌘⇧C. On by default; turn it off in the View menu.
        self.previous_app = None  # the app you were in before ⌘⇧C opened the chat
        try:
            self.global_hotkey = hotkeys.GlobalHotKey(hotkeys.KEY_C, hotkeys.COMMAND | hotkeys.SHIFT,
                                                      self.on_global_shortcut)
        except OSError as error:
            self.global_hotkey = None
            print(error)
        self.global_shortcut_action.setEnabled(self.global_hotkey is not None)
        wanted = str(self.settings.value("global_shortcut", "true")) == "true"
        self.global_shortcut_action.setChecked(wanted and self.global_hotkey is not None)
        self.update_documents_label()

        # The timer that takes a screenshot every few seconds.
        self.capture_timer = QTimer(self)
        self.capture_timer.timeout.connect(self.capture_tick)
        # (The timer starts when you wake the app up from the menu bar.)

        self.update_status()

    # --- Building the window ---------------------------------------------

    def build_ui(self):
        self.colors = theme.colors(self.palette())
        self.accent = self.colors["accent"]
        self.gray = "#8e8e93"  # macOS system gray; readable in light and dark mode
        self.red = self.colors["red"]
        # Modern macOS look: the window's content runs up under the title bar,
        # so there's no separate gray title strip.
        self.setWindowFlag(Qt.ExpandedClientAreaHint, True)
        self.setWindowFlag(Qt.NoTitleBarBackgroundHint, True)

        # Keyboard shortcuts. Window Watcher is a menu bar app with no menus at
        # the top of the screen, so these "actions" are attached to its windows
        # (see attach_shortcuts) and also listed in the eye's menu (menubar.py).
        # ("Ctrl" in Qt means the Command key on a Mac.)
        self.chat_action = QAction("Show Chat Window", self)
        self.chat_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self.chat_action.setShortcutContext(Qt.ApplicationShortcut)
        self.chat_action.triggered.connect(self.toggle_floating_chat)
        # ⌘⇧C can also work while other apps are in front (see hotkeys.py).
        self.global_shortcut_action = QAction("⌘⇧C Works in Other Apps", self)
        self.global_shortcut_action.setCheckable(True)
        self.global_shortcut_action.toggled.connect(self.set_global_shortcut)
        # The programmable buttons, with Cmd+1 to Cmd+4.
        self.quick_prompt_actions = []
        for number in range(len(QUICK_PROMPT_DEFAULTS)):
            action = QAction(self)
            action.setShortcut(QKeySequence(f"Ctrl+{number + 1}"))
            action.setShortcutContext(Qt.ApplicationShortcut)
            action.triggered.connect(lambda checked=False, n=number: self.send_quick_prompt(n))
            self.quick_prompt_actions.append(action)

        # --- Header card: live preview, window name, status, buttons ---
        self.preview = QLabel("No window yet")
        self.preview.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setObjectName("secondary")

        watching_caption = QLabel("WATCHING")
        watching_caption.setObjectName("caption")
        self.window_label = QLabel("Not watching anything")
        self.window_label.setObjectName("title")
        self.window_label.setWordWrap(True)
        self.status_label = QLabel()  # a colored "pill", styled in update_status()
        self.detail_label = QLabel()
        self.detail_label.setObjectName("secondary")
        self.detail_label.setWordWrap(True)

        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.on_pause)
        self.change_button = QPushButton("Change Window…")
        self.change_button.clicked.connect(self.on_change_window)
        self.chat_button = QPushButton("Show Chat Window")
        self.chat_button.clicked.connect(self.toggle_floating_chat)
        self.chat_button.setToolTip("Shortcut: ⌘⇧C")
        top_buttons = QHBoxLayout()
        top_buttons.setSpacing(8)
        top_buttons.addWidget(self.pause_button)
        top_buttons.addWidget(self.change_button)
        top_buttons.addWidget(self.chat_button)
        top_buttons.addStretch()

        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        info = QVBoxLayout()
        info.setSpacing(6)
        info.addWidget(watching_caption)
        info.addWidget(self.window_label)
        info.addLayout(status_row)
        info.addWidget(self.detail_label)
        info.addStretch()
        info.addLayout(top_buttons)

        header = self.make_card()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 14, 18, 14)
        header_layout.setSpacing(18)
        header_layout.addWidget(self.preview)
        header_layout.addLayout(info, 1)

        # --- Standing instructions card ---
        self.instructions_box = QPlainTextEdit()
        self.instructions_box.setPlaceholderText(
            "One per line, for example:\n"
            "Tell me whenever a new message appears\n"
            "Warn me if the health bar gets low")
        self.instructions_box.setFixedHeight(62)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self.on_save_instructions)
        self.speak_checkbox = QCheckBox("Read alerts aloud")
        alerts = self.make_card()
        alerts_layout = QVBoxLayout(alerts)
        alerts_layout.setContentsMargins(16, 12, 16, 12)
        alerts_layout.addWidget(self.make_caption("STANDING INSTRUCTIONS"))
        alerts_layout.addWidget(self.instructions_box)
        alerts_bottom = QHBoxLayout()
        alerts_bottom.addWidget(self.speak_checkbox)
        alerts_bottom.addStretch()
        alerts_bottom.addWidget(save_button)
        alerts_layout.addLayout(alerts_bottom)

        # --- Reference documents card: extra background info Claude can read ---
        self.add_docs_button = QPushButton("Add…")
        self.add_docs_button.setToolTip("Add reference documents (up to 500 MB each)")
        self.add_docs_button.clicked.connect(self.on_add_documents)
        self.clear_docs_button = QPushButton("Remove All")
        self.clear_docs_button.clicked.connect(self.on_clear_documents)
        self.docs_label = QLabel()
        self.docs_label.setObjectName("secondary")
        self.docs_label.setWordWrap(True)
        self.docs_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        docs = self.make_card()
        docs_layout = QVBoxLayout(docs)
        docs_layout.setContentsMargins(16, 12, 16, 12)
        docs_layout.addWidget(self.make_caption("REFERENCE DOCUMENTS"))
        docs_layout.addWidget(self.docs_label, 1)
        docs_bottom = QHBoxLayout()
        docs_bottom.addWidget(self.add_docs_button)
        docs_bottom.addStretch()
        docs_bottom.addWidget(self.clear_docs_button)
        docs_layout.addLayout(docs_bottom)

        middle = QHBoxLayout()
        middle.setSpacing(12)
        middle.addWidget(alerts, 3)
        middle.addWidget(docs, 2)

        # --- Chat card: the conversation, quick buttons and the question box ---
        self.chat_view = ChatView(self.chat)
        # The pop-out chat window (hidden until you click "Show Chat Window").
        self.floating_chat = FloatingChat(self)
        self.question_box = QLineEdit()
        self.question_box.setPlaceholderText("Ask about the window, e.g. “What just changed?”")
        self.question_box.returnPressed.connect(self.on_ask)
        ask_button = QPushButton("Ask")
        ask_button.setObjectName("primary")
        ask_button.clicked.connect(self.on_ask)
        ask_row = QHBoxLayout()
        ask_row.addWidget(self.question_box, 1)
        ask_row.addWidget(ask_button)

        chat_card = self.make_card()
        chat_layout = QVBoxLayout(chat_card)
        chat_layout.setContentsMargins(16, 12, 16, 14)
        chat_layout.setSpacing(10)
        chat_layout.addWidget(self.make_caption("CHAT"))
        chat_layout.addWidget(self.chat_view, 1)
        chat_layout.addWidget(self.make_quick_prompt_bar())
        chat_layout.addLayout(ask_row)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 10, 16, 16)
        layout.setSpacing(12)
        layout.addWidget(header)
        layout.addLayout(middle)
        layout.addWidget(chat_card, 1)
        central = QWidget()
        central.setObjectName("root")
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.resize(820, 800)
        self.attach_shortcuts(self)
        self.attach_shortcuts(self.floating_chat)
        self.apply_theme()
        self.refresh_quick_prompts()

    # --- Look ------------------------------------------------------------------

    def attach_shortcuts(self, window):
        """Make ⌘⇧C and ⌘1–⌘4 work while this window is in front."""
        window.addAction(self.chat_action)
        for action in self.quick_prompt_actions:
            window.addAction(action)

    def make_card(self):
        """A rounded panel that groups related things (styled in theme.py)."""
        card = QFrame()
        card.setObjectName("card")
        return card

    def make_caption(self, text):
        """A small gray heading like "CHAT" at the top of a card."""
        label = QLabel(text)
        label.setObjectName("caption")
        return label

    def apply_theme(self):
        """Apply the colors for light or dark mode to both windows."""
        self.colors = theme.colors(self.palette())
        self.accent = self.colors["accent"]
        self.red = self.colors["red"]
        self.setStyleSheet(theme.stylesheet(self.colors))
        self.floating_chat.setStyleSheet(theme.stylesheet(self.colors))
        self.update_status()

    def changeEvent(self, event):
        # macOS switched between light and dark mode: recolor.
        if event.type() in (QEvent.ApplicationPaletteChange, QEvent.ThemeChange):
            dark = self.palette().color(QPalette.Window).lightness() < 128
            if hasattr(self, "colors") and dark != self.colors["dark"]:
                self.apply_theme()
        super().changeEvent(event)

    # --- Startup -------------------------------------------------------------

    # --- Asleep in the menu bar / awake --------------------------------------

    def wake(self):
        """Wake up from the menu bar: check permission, pick a window, start."""
        if not self.dormant:
            self.bring_to_front()
            return
        self.activate_app()  # so the dropdown gets the keyboard
        # Screen Recording is only checked now, when you ask to start.
        if not capture.can_capture_screen() and not ask_for_screen_permission():
            return
        icon_rect = self.menu_icon.geometry()  # where the eye is in the menu bar
        picker = WindowPicker(self, under_rect=icon_rect if icon_rect.isValid() else None)
        if not (picker.exec() and picker.chosen):
            return  # cancelled: stay asleep

        self.dormant = False
        self.menu_icon.update_menu()
        self.bring_to_front()
        self.capture_timer.start(int(CAPTURE_EVERY_SECONDS * 1000))
        self.start_watching(picker.chosen)
        if not self.claude_checked:
            self.claude_checked = True
            # Check that Claude Code is installed and logged in (in the
            # background, because it takes a second or two).
            threading.Thread(
                target=lambda: self.signals.claude_status.emit(claude_bridge.check_claude()),
                daemon=True,
            ).start()

    def go_dormant(self):
        """Go back to sleep in the menu bar: stop taking screenshots, stop
        Claude, delete the screenshots, and hide the windows. Your chat,
        instructions, documents and buttons are kept for next time."""
        if self.dormant:
            return
        self.dormant = True
        self.capture_timer.stop()
        claude_bridge.stop_running_call()
        self.pending_questions.clear()
        self.generation += 1  # results that were still on their way get ignored
        self.forget_frames()
        self.window = None
        self.capture_problem = None
        self.window_label.setText("Not watching anything")
        self.preview.clear()
        self.preview.setText("Asleep")
        self.floating_chat.hide()
        self.hide()
        self.add_chat("note", "Went to sleep. Click the eye in the menu bar to watch a window again.")
        self.update_status()
        if self.menu_icon is not None:
            self.menu_icon.update_menu()

    def bring_to_front(self):
        """Show the main window over whatever you're looking at."""
        self.show()
        self.raise_()
        self.activate_app()
        self.activateWindow()

    def change_window_from_menu_bar(self):
        self.bring_to_front()
        self.on_change_window()

    def make_menu_bar_app(self):
        """Run as an "accessory" app: no Dock icon and no menus at the top of
        the screen, just the eye. This is also what lets macOS show the app's
        windows over another app that's in full-screen mode (like Spotlight)."""
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def showEvent(self, event):
        super().showEvent(event)
        # Open on the desktop you're on (even over a full-screen app) instead of
        # jumping back to the desktop where the window was last.
        from AppKit import NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowCollectionBehaviorMoveToActiveSpace
        set_space_behavior(self, NSWindowCollectionBehaviorMoveToActiveSpace,
                           NSWindowCollectionBehaviorFullScreenAuxiliary)

    def activate_app(self):
        from AppKit import NSApp
        if hasattr(NSApp, "activate"):
            NSApp.activate()  # macOS 14 and newer
        else:
            NSApp.activateIgnoringOtherApps_(True)

    def quit_app(self):
        QApplication.quit()  # shutdown() runs on the way out

    def on_claude_status(self, problem):
        if problem:
            self.add_chat("error", problem)

    # --- Chat ----------------------------------------------------------------

    def add_chat(self, role, text, reply_to=None):
        """Add a message to the chat and show it in both chat views."""
        message = ChatMessage(role, text, reply_to=reply_to)
        if role == "user":
            self.question_count += 1
            message.number = self.question_count
        index = len(self.chat)
        self.chat.append(message)

        body = escape(text).replace("\n", "<br>")
        if role == "note":
            html = f'<p style="color:{self.gray}; margin-top:6px; margin-bottom:6px;">{body}</p>'
        else:
            name, color = {
                "user": (f"You · Q{message.number}", None),
                "assistant": ("Claude", self.accent),
                "alert": ("Alert", self.accent),
                "error": ("Problem", self.red),
            }[role]
            name_html = f'<b style="color:{color};">{name}</b>' if color else f"<b>{name}</b>"
            # Answers say which question they answer, e.g. "↳ Q2: What's my score?"
            answering = ""
            if reply_to is not None:
                short = escape(reply_to.text[:60] + ("…" if len(reply_to.text) > 60 else ""))
                answering = f'<br><span style="color:{self.gray};">↳ Q{reply_to.number}: {short}</span>'
            details = (f'<span style="color:{self.gray}; font-size:small;">{message.time_text()} · '
                       f'<a href="copy:{index}">Copy</a></span>')
            html = (f'<p style="margin-top:8px; margin-bottom:8px; line-height:130%;">'
                    f"{name_html} &nbsp;{details}{answering}<br>{body}</p>")
        self.chat_view.add_html(html)
        self.floating_chat.chat_view.add_html(html)
        return message

    def copy_all_chat(self):
        """Copy the whole conversation as plain text."""
        lines = []
        for m in self.chat:
            if m.role == "user":
                lines.append(f"[{m.time_text()}] You (Q{m.number}): {m.text}")
            elif m.role in ("assistant", "alert", "error"):
                label = {"assistant": "Claude", "alert": "Alert", "error": "Problem"}[m.role]
                about = f" (answer to Q{m.reply_to.number})" if m.reply_to else ""
                lines.append(f"[{m.time_text()}] {label}{about}: {m.text}")
        QApplication.clipboard().setText("\n\n".join(lines))

    # --- Programmable buttons ---------------------------------------------------

    def make_quick_prompt_bar(self, columns=4, with_edit_button=True):
        bar = QuickPromptBar(self, columns, with_edit_button)
        self.prompt_bars.append(bar)
        return bar

    def refresh_quick_prompts(self):
        """Update every button bar and the Buttons menu with the current text."""
        for bar in self.prompt_bars:
            bar.refresh(self.quick_prompts)
        for action, text in zip(self.quick_prompt_actions, self.quick_prompts):
            action.setText(text or "(empty)")
            action.setEnabled(bool(text))

    def send_quick_prompt(self, number):
        """A programmable button was clicked (or ⌘1–⌘4 pressed)."""
        text = self.quick_prompts[number]
        if text:
            self.ask(text)

    def edit_quick_prompts(self):
        dialog = EditQuickPromptsDialog(self.floating_chat if self.floating_chat.isActiveWindow() else self,
                                        self.quick_prompts)
        if dialog.exec():
            self.quick_prompts = dialog.prompts()
            for number, text in enumerate(self.quick_prompts):
                self.settings.setValue(f"quick_prompt_{number + 1}", text)
            self.refresh_quick_prompts()

    # --- The system-wide shortcut ---------------------------------------------

    def set_global_shortcut(self, on):
        """Turn the system-wide ⌘⇧C on or off (from the View menu)."""
        if self.global_hotkey is None:
            return
        if on and not self.global_hotkey.turn_on():
            self.add_chat("error", "Another app is already using ⌘⇧C everywhere, so it only "
                                   "works inside Window Watcher.")
            self.global_shortcut_action.setChecked(False)
            return
        if not on:
            self.global_hotkey.turn_off()
        self.settings.setValue("global_shortcut", "true" if on else "false")

    def on_global_shortcut(self):
        """⌘⇧C was pressed, whichever app is in front."""
        from AppKit import NSApp, NSWorkspace
        if self.dormant:
            self.wake()  # asleep: same as clicking the menu bar icon
            return
        if self.floating_chat.isVisible() and NSApp.isActive():
            self.hide_floating_chat()
            return
        # Remember where you were, so hiding the chat can take you back there.
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is not None and front.processIdentifier() != os.getpid():
            self.previous_app = front
        self.bring_up_floating_chat()

    def bring_up_floating_chat(self):
        """Show the chat in front, ready to type in."""
        from AppKit import NSApp
        self.floating_chat.show()
        self.floating_chat.raise_()
        if hasattr(NSApp, "activate"):
            NSApp.activate()  # bring Window Watcher to the front (macOS 14 and newer)
        else:
            NSApp.activateIgnoringOtherApps_(True)
        self.floating_chat.activateWindow()
        self.floating_chat.question_box.setFocus()

    def hide_floating_chat(self):
        """Hide the chat and, if ⌘⇧C opened it, go back to the app you were in."""
        self.floating_chat.hide()
        app, self.previous_app = self.previous_app, None
        if app is not None and not app.isTerminated():
            from AppKit import NSApp
            if hasattr(NSApp, "yieldActivationToApplication_"):
                NSApp.yieldActivationToApplication_(app)  # macOS 14+: hand focus over politely
            app.activateWithOptions_(0)

    def toggle_floating_chat(self):
        if self.floating_chat.isVisible():
            self.hide_floating_chat()
        else:
            self.floating_chat.show()
            self.floating_chat.raise_()

    def update_chat_button(self):
        visible = self.floating_chat.isVisible()
        label = "Hide Chat Window" if visible else "Show Chat Window"
        self.chat_button.setText(label)
        self.chat_action.setText(label)

    def on_ask(self):
        """You pressed Ask (or Return) in the main window."""
        self.ask(self.question_box.text())
        self.question_box.clear()

    def ask(self, question):
        """Send a question (from either chat box) to Claude."""
        question = question.strip()
        if not question:
            return
        if self.window is None:
            self.add_chat("error", "Choose a window to watch first (click “Change Window…”).")
            return

        message = self.add_chat("user", question)
        self.pending_questions.append(message)
        if self.busy:
            self.add_chat("note", "Claude is busy. Your question is next in line.")
        # Take a fresh screenshot so the answer is about right now. When it
        # arrives, on_frame_ready() starts the question.
        self.capture_tick(forced=True)
        self.start_next_job()

    # --- Reference documents ---------------------------------------------------

    def on_add_documents(self):
        """Let you pick files that give Claude extra background info.
        Any size works: big ones are searched instead of read whole."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Reference Documents", str(Path.home()),
            "Documents (*.txt *.md *.pdf *.docx *.doc *.rtf *.odt *.csv *.json *.html "
            "*.png *.jpg *.jpeg);;All files (*)")
        if not paths:
            return
        names = ", ".join(Path(path).name for path in paths)
        self.add_chat("note", f"Adding {names}… (big files can take a little while)")
        self.add_docs_button.setEnabled(False)
        threading.Thread(target=self.prepare_documents_in_background, args=(paths,), daemon=True).start()

    def prepare_documents_in_background(self, paths):
        """Runs on a background thread: copy and convert the files, which can
        take a while for a big PDF, without freezing the window."""
        docs_dir = self.frames_dir / "docs"  # inside the folder Claude may read
        docs_dir.mkdir(exist_ok=True)
        added, problems = [], []
        for path in paths:
            try:
                added += documents.prepare_document(path, docs_dir)
            except Exception as error:  # report it, don't crash
                problems.append(f"Couldn't add {Path(path).name}: {error}")
        self.signals.documents_ready.emit((added, problems))

    def on_documents_ready(self, result):
        """The files are ready (back on the main thread)."""
        added, problems = result
        self.add_docs_button.setEnabled(True)
        self.documents += added
        for problem in problems:
            self.add_chat("error", problem)
        for document in added:
            if document.warning:
                self.add_chat("note", document.warning)
        names = list(dict.fromkeys(d.shown_name for d in added))  # unique, in order
        if names:
            self.add_chat("note", f"Added {', '.join(names)}. Claude will use "
                                  f"{'them' if len(names) > 1 else 'it'} when answering.")
        self.update_documents_label()

    def on_clear_documents(self):
        for document in self.documents:
            delete_file(document.path)
        self.documents = []
        self.add_chat("note", "Removed all reference documents.")
        self.update_documents_label()

    def update_documents_label(self):
        if self.documents:
            names = dict.fromkeys(d.shown_name for d in self.documents)  # unique, in order
            self.docs_label.setText("Using: " + ", ".join(names))
        else:
            self.docs_label.setText("No reference documents. Add notes, guides, PDFs or Word files "
                                    "(up to 500 MB) to give Claude more context.")
        self.clear_docs_button.setEnabled(bool(self.documents))

    def on_save_instructions(self):
        lines = [line.strip() for line in self.instructions_box.toPlainText().splitlines()]
        self.instructions = [line for line in lines if line]
        self.recent_alerts.clear()
        self.forget_last_checked()  # check the current screen fresh
        if self.instructions:
            count = len(self.instructions)
            self.add_chat("note", f"Saved {count} standing instruction{'s' if count > 1 else ''}. "
                                  "I'll check the screen whenever it changes.")
            self.check_needed = True
        else:
            self.add_chat("note", "Standing instructions cleared. No more automatic alerts.")
            self.check_needed = False
        self.update_status()
        self.start_next_job()

    # --- Watching ------------------------------------------------------------

    def on_change_window(self):
        picker = WindowPicker(self)
        if picker.exec() and picker.chosen:
            self.start_watching(picker.chosen)

    def start_watching(self, window):
        self.generation += 1  # results from the old window will be ignored
        self.forget_frames()
        self.window = window
        self.capture_problem = None
        self.change_log.clear()
        self.recent_alerts.clear()
        self.check_needed = bool(self.instructions)
        self.last_change_time = None
        self.window_label.setText(window.label())
        self.preview.setText("Taking the first screenshot…")
        self.add_chat("note", f"Now watching: {window.label()}")
        self.set_paused(False)
        self.capture_tick()

    def on_pause(self):
        self.set_paused(not self.paused)
        if self.window:
            self.add_chat("note", "Paused. Questions still use a fresh screenshot."
                          if self.paused else "Watching again.")

    def set_paused(self, paused):
        self.paused = paused
        self.pause_button.setText("Resume" if paused else "Pause")
        if not paused:
            self.capture_tick()
        self.update_status()

    def capture_tick(self, forced=False):
        """Take a screenshot in the background (called by the timer)."""
        if self.window is None or self.capture_in_flight:
            return
        if self.paused and not forced:
            return
        self.capture_in_flight = True
        self.capture_count += 1
        path = self.frames_dir / f"frame-{self.capture_count}.png"
        threading.Thread(
            target=self.capture_in_background,
            args=(self.window.window_id, path, self.generation),
            daemon=True,
        ).start()

    def capture_in_background(self, window_id, path, generation):
        """Runs on a background thread. Never touches the window directly."""
        result = CaptureResult(generation, path)
        try:
            result.window = capture.capture_window(window_id, path)
            result.thumbnail = capture.make_thumbnail(path)
        except capture.CaptureError as error:
            result.error = str(error)
        except Exception as error:  # anything unexpected; report it, don't crash
            result.error = f"Unexpected screenshot problem: {error}"
        self.signals.frame_ready.emit(result)

    def on_frame_ready(self, result):
        """A screenshot finished (back on the main thread)."""
        self.capture_in_flight = False

        if result.generation != self.generation:
            delete_file(result.path)  # it's from a window we stopped watching
            return

        if result.error:
            delete_file(result.path)
            self.capture_problem = result.error
            if self.latest is None:
                # We have no screenshot at all, so waiting questions can't be answered.
                for message in self.pending_questions:
                    self.add_chat("error", f"I couldn't see the window: {result.error}")
                self.pending_questions.clear()
            self.update_status()
            self.start_next_job()
            return

        self.capture_problem = None
        self.window = result.window  # the title may have changed
        self.window_label.setText(self.window.label())

        new_frame = Frame(result.path, time.time(), result.thumbnail)
        previous_latest = self.latest
        self.latest = new_frame

        # Did the screen meaningfully change since the last frame we kept?
        last_kept = self.history[-1] if self.history else None
        if capture.screen_changed(last_kept.thumbnail if last_kept else None, new_frame.thumbnail):
            self.history.append(new_frame)
            if last_kept:
                self.last_change_time = new_frame.when
            self.check_needed = True
            while len(self.history) > KEEP_FRAMES:
                delete_file(self.history.pop(0).path)

        # Throw away the previous screenshot unless we're keeping it.
        if previous_latest and previous_latest not in self.history:
            delete_file(previous_latest.path)

        self.show_preview(new_frame.path)
        self.update_status()
        self.start_next_job()

    def show_preview(self, path):
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            ratio = self.devicePixelRatioF()  # draw sharp on Retina screens
            scaled = pixmap.scaled(int(PREVIEW_WIDTH * ratio), int(PREVIEW_HEIGHT * ratio),
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            self.preview.setPixmap(rounded(scaled, 10))

    def forget_frames(self):
        """Delete every screenshot of the current window."""
        for frame in self.history + [self.latest]:
            if frame:
                delete_file(frame.path)
        self.history = []
        self.latest = None
        self.forget_last_checked()

    def forget_last_checked(self):
        if self.last_checked:
            delete_file(self.last_checked.path)
        self.last_checked = None

    # --- Talking to Claude -----------------------------------------------------

    def start_next_job(self):
        """Start the next Claude call, if Claude is free. Questions go first."""
        if self.busy or self.window is None or self.latest is None:
            return
        if self.pending_questions:
            if not self.capture_in_flight:  # wait for the fresh screenshot
                self.start_question(self.pending_questions.pop(0))
        elif (self.check_needed and self.instructions and not self.paused
              and time.time() >= self.next_check_allowed):
            self.start_check()

    def copy_for_claude(self, frame, name):
        """Give Claude its own copy of a screenshot, so cleaning up old
        screenshots can't delete a file Claude is still reading."""
        copy = self.frames_dir / name
        shutil.copyfile(frame.path, copy)
        return copy

    def start_question(self, message):
        if self.capture_problem:
            self.add_chat("note", f"I couldn't take a fresh screenshot ({self.capture_problem}) "
                                  f"so I'm using the one from {self.latest.time_text()}.")
        self.job_count += 1
        now_copy = self.copy_for_claude(self.latest, f"ask-{self.job_count}-now.png")
        before = self.history[-2] if len(self.history) >= 2 else None
        before_copy = self.copy_for_claude(before, f"ask-{self.job_count}-before.png") if before else None

        chat_so_far = [m for m in self.chat if m.role in ("user", "assistant", "alert") and m is not message]
        prompt = claude_bridge.question_prompt(
            window_label=self.window.label(),
            question=message.text,
            now_file=str(now_copy),
            now_time=self.latest.time_text(),
            before_file=str(before_copy) if before_copy else None,
            before_time=before.time_text() if before else None,
            instructions=self.instructions,
            change_log=self.change_log,
            documents=[(str(d.path), d.description) for d in self.documents],
            chat_history=chat_so_far[-CHAT_MESSAGES_SENT:],
        )
        self.run_job(ClaudeJob("question", prompt, self.generation, [now_copy, before_copy], question=message))

    def start_check(self):
        self.job_count += 1
        now_copy = self.copy_for_claude(self.latest, f"check-{self.job_count}.png")
        before = self.last_checked
        prompt = claude_bridge.check_prompt(
            window_label=self.window.label(),
            now_file=str(now_copy),
            now_time=self.latest.time_text(),
            before_file=str(before.path) if before else None,
            before_time=before.time_text() if before else None,
            instructions=self.instructions,
            change_log=self.change_log,
            recent_alerts=self.recent_alerts,
            documents=[(str(d.path), d.description) for d in self.documents],
        )
        self.check_needed = False
        self.next_check_allowed = time.time() + MIN_SECONDS_BETWEEN_CHECKS
        job = ClaudeJob("check", prompt, self.generation, [], schema=claude_bridge.CHECK_SCHEMA,
                        checked_frame=Frame(now_copy, self.latest.when))
        self.run_job(job)

    def run_job(self, job):
        self.busy = True
        self.busy_kind = job.kind
        self.update_status()
        threading.Thread(target=self.claude_in_background, args=(job,), daemon=True).start()

    def claude_in_background(self, job):
        """Runs on a background thread: wait for Claude's answer."""
        try:
            job.answer = claude_bridge.run_claude(job.prompt, str(self.frames_dir), job.schema)
        except claude_bridge.ClaudeError as error:
            job.error = str(error)
        except Exception as error:  # anything unexpected; report it, don't crash
            job.error = f"Unexpected problem: {error}"
        self.signals.claude_done.emit(job)

    def on_claude_done(self, job):
        """Claude finished (back on the main thread)."""
        self.busy = False
        self.busy_kind = None
        for path in job.files_to_delete:
            delete_file(path)

        stale = job.generation != self.generation  # you switched windows meanwhile
        if job.kind == "question":
            if job.error:
                self.add_chat("error", job.error, reply_to=job.question)
            else:
                self.add_chat("assistant", job.answer or "(Claude sent an empty answer.)", reply_to=job.question)
        elif job.error or stale:
            delete_file(job.checked_frame.path)
            if job.error:
                # Don't fill the chat with the same error every few seconds.
                if job.error != self.last_check_error:
                    self.add_chat("error", f"Background check failed: {job.error}")
                self.last_check_error = job.error
                self.next_check_allowed = time.time() + SECONDS_AFTER_FAILED_CHECK
                self.check_needed = True
        else:
            self.handle_check_result(job)

        self.update_status()
        self.start_next_job()

    def handle_check_result(self, job):
        self.last_check_error = None
        self.forget_last_checked()
        self.last_checked = job.checked_frame  # next check compares against this

        summary = str(job.answer.get("change_summary", "")).strip()
        if summary:
            self.change_log.append((job.checked_frame.time_text(), summary))
            del self.change_log[:-CHANGE_LOG_LENGTH]

        message = str(job.answer.get("message", "")).strip()
        if job.answer.get("alert") and message:
            self.add_chat("alert", message)
            self.recent_alerts.append(message)
            del self.recent_alerts[:-5]
            QApplication.alert(self)  # bounce the Dock icon if the app is in the background
            if self.speak_checkbox.isChecked():
                subprocess.Popen(["say", message])

    # --- Status line -------------------------------------------------------------

    def update_status(self):
        if self.window is None:
            state, active = "Idle", False
        elif self.busy:
            doing = "answering your question" if self.busy_kind == "question" else "checking your alerts"
            state, active = f"Thinking… ({doing})", True
        elif self.paused:
            state, active = "Paused", False
        else:
            state, active = "Watching", True
        # A rounded "pill": accent-tinted while active, gray otherwise.
        c = self.colors
        self.status_label.setText(f"●  {state}")
        self.status_label.setStyleSheet(
            f"background: {c['accent_soft'] if active else c['button']};"
            f" color: {c['accent'] if active else c['secondary']};"
            " border-radius: 11px; padding: 3px 11px; font-weight: 600;")

        details = []
        if self.capture_problem:
            details.append(self.capture_problem)
        if self.window is None:
            details.append("Click “Change Window…” to pick a window.")
        elif self.last_change_time:
            details.append(f"Last change at {clock(self.last_change_time)}")
        if self.instructions:
            count = len(self.instructions)
            details.append(f"{count} standing instruction{'s' if count > 1 else ''} on")
        if self.pending_questions:
            details.append(f"{len(self.pending_questions)} question(s) waiting")
        self.detail_label.setText(" · ".join(details))
        self.change_button.setText("Change Window…" if self.window else "Choose Window…")
        self.pause_button.setEnabled(self.window is not None)

    # --- Quitting ----------------------------------------------------------------

    def closeEvent(self, event):
        """The window's close button puts the app to sleep instead of quitting.
        To quit, use the menu bar icon's menu or ⌘Q."""
        event.ignore()
        self.go_dormant()

    def shutdown(self):
        """Clean up when the app really quits."""
        if self.global_hotkey is not None:
            self.global_hotkey.turn_off()
        self.capture_timer.stop()
        claude_bridge.stop_running_call()
        self.floating_chat.close()
        if self.menu_icon is not None:
            self.menu_icon.hide()
        shutil.rmtree(self.frames_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Starting the app
# ---------------------------------------------------------------------------

def relaunch_app():
    """Start a fresh copy of Window Watcher a moment after this one quits.
    macOS only applies a new Screen Recording permission after a restart."""
    bundle = Path(sys.executable).resolve().parents[2]  # .../Window Watcher.app
    # --wake makes the new copy go straight to "pick a window".
    subprocess.Popen(["/bin/sh", "-c", 'sleep 1; open -n "$0" --args --wake', str(bundle)], start_new_session=True)


def ask_for_screen_permission():
    """Explain how to turn on Screen Recording. Returns True once it works,
    False if you cancelled (or chose Quit & Reopen, which restarts the app)."""
    capture.request_screen_recording_permission()  # adds the app to the list in System Settings
    frozen = getattr(sys, "frozen", False)  # True for the installed app, False for the script
    who = "Window Watcher" if frozen else "Terminal (or whichever app you started Window Watcher from)"

    while True:
        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Screen Recording permission needed")
        box.setText("Window Watcher needs permission to see your screen.")
        box.setInformativeText(
            "1. Click “Open System Settings”.\n"
            f"2. Under Screen & System Audio Recording, turn on {who}.\n"
            f"3. Click “{'Quit & Reopen' if frozen else 'Quit'}”"
            f"{'' if frozen else ', quit Terminal, and start Window Watcher again'}.\n\n"
            + ("Already turned on? macOS is remembering an older copy of the app. Click "
               "“Reset Permission”, allow it again, then click “Quit & Reopen”." if frozen else
               "Already turned on? Select Terminal in that list, click the − button to remove it, "
               "then do steps 2 and 3 again."))
        open_button = box.addButton("Open System Settings", QMessageBox.ActionRole)
        reset_button = box.addButton("Reset Permission", QMessageBox.ActionRole) if frozen else None
        check_button = box.addButton("Check Again", QMessageBox.ActionRole)
        reopen_button = box.addButton("Quit & Reopen", QMessageBox.AcceptRole) if frozen else None
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked == open_button:
            capture.open_screen_recording_settings()
        elif reset_button is not None and clicked == reset_button:
            from Foundation import NSBundle
            capture.reset_screen_recording_permission(NSBundle.mainBundle().bundleIdentifier())
            capture.open_screen_recording_settings()
        elif clicked == check_button:
            if capture.can_capture_screen():
                return True
        elif reopen_button is not None and clicked == reopen_button:
            relaunch_app()
            QApplication.quit()
            return False
        else:
            return False


def main():
    # One-time command, same as the menu's "Open at Login" item:
    #   "Window Watcher" --open-at-login=on   (or =off)
    for arg in sys.argv[1:]:
        if arg.startswith("--open-at-login="):
            problem = menubar.set_open_at_login(arg.split("=", 1)[1] == "on")
            print(problem or f"Open at Login is now {'on' if menubar.opens_at_login() else 'off'}.")
            return

    try:
        # Show "Window Watcher" in the menu bar instead of "Python".
        from Foundation import NSBundle
        NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "Window Watcher"
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("Window Watcher")
    # Our own Dock icon instead of Python's (the DMG app has it built in).
    icon_path = Path(__file__).parent / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # Keep running in the menu bar when no windows are open.
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()  # created hidden and asleep
    window.menu_icon = menubar.MenuBarIcon(window)
    window.menu_icon.show()
    window.make_menu_bar_app()  # just the eye in the menu bar, no Dock icon
    app.aboutToQuit.connect(window.shutdown)
    if "--wake" in sys.argv:  # reopened after granting Screen Recording
        QTimer.singleShot(300, window.wake)
    app.exec()


if __name__ == "__main__":
    main()
