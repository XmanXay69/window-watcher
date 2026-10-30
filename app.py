"""
app.py: Window Watcher, the part you see and click.

Start it by double-clicking "Start Window Watcher.command", or in Terminal:
    .venv/bin/python app.py

How it works:
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

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QTextBrowser, QVBoxLayout, QWidget,
)

import capture
import claude_bridge

# --- Settings you can tweak --------------------------------------------------
CAPTURE_EVERY_SECONDS = 2.5      # how often to take a screenshot
MIN_SECONDS_BETWEEN_CHECKS = 5   # least time between automatic alert checks
SECONDS_AFTER_FAILED_CHECK = 60  # wait this long after a check fails
KEEP_FRAMES = 4                  # how many different screenshots to keep
CHAT_MESSAGES_SENT = 10          # how much recent chat goes with each question
CHANGE_LOG_LENGTH = 8            # how many "what changed" notes to remember
PREVIEW_WIDTH, PREVIEW_HEIGHT = 300, 190


# ---------------------------------------------------------------------------
# Small data holders
# ---------------------------------------------------------------------------

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

    def time_text(self):
        return clock(self.when)


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
    answer: object = None
    error: str = None


class Signals(QObject):
    """Messages from background threads to the main thread."""
    frame_ready = Signal(object)    # a CaptureResult
    claude_done = Signal(object)    # a finished ClaudeJob
    claude_status = Signal(object)  # the startup "is Claude set up?" result


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
    """Shows the open windows and lets you pick one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose a Window")
        self.resize(540, 420)
        self.windows = []
        self.chosen = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Which window should Window Watcher look at?"))

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list)

        buttons = QHBoxLayout()
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        self.watch_button = QPushButton("Watch This Window")
        self.watch_button.setDefault(True)
        self.watch_button.clicked.connect(self.accept)
        buttons.addWidget(refresh_button)
        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(self.watch_button)
        layout.addLayout(buttons)

        self.refresh()

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

        self.busy = False             # True while a Claude call is running
        self.busy_kind = None
        self.job_count = 0
        self.pending_questions = []   # ChatMessages waiting for Claude
        self.chat = []                # every ChatMessage, oldest first

        self.instructions = []        # standing instructions, one per line
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

        self.build_ui()

        # The timer that takes a screenshot every few seconds.
        self.capture_timer = QTimer(self)
        self.capture_timer.timeout.connect(self.capture_tick)
        self.capture_timer.start(int(CAPTURE_EVERY_SECONDS * 1000))

        self.update_status()

    # --- Building the window ---------------------------------------------

    def build_ui(self):
        palette = self.palette()
        self.accent = palette.color(QPalette.Accent).name()
        self.gray = "#8e8e93"  # macOS system gray; readable in light and dark mode
        self.red = "#ff453a" if palette.color(QPalette.Window).lightness() < 128 else "#d70015"

        # Top: the live preview, with the window name, status and buttons.
        self.preview = QLabel("No window yet")
        self.preview.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet(f"background: {palette.color(QPalette.AlternateBase).name()};"
                                   f" border-radius: 6px; color: {self.gray};")

        self.window_label = QLabel("Not watching anything")
        self.window_label.setWordWrap(True)
        self.window_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet(f"color: {self.gray};")

        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.on_pause)
        self.change_button = QPushButton("Change Window…")
        self.change_button.clicked.connect(self.on_change_window)
        top_buttons = QHBoxLayout()
        top_buttons.addWidget(self.pause_button)
        top_buttons.addWidget(self.change_button)
        top_buttons.addStretch()

        info = QVBoxLayout()
        info.addWidget(self.window_label)
        info.addWidget(self.status_label)
        info.addWidget(self.detail_label)
        info.addStretch()
        info.addLayout(top_buttons)

        top = QHBoxLayout()
        top.addWidget(self.preview)
        top.addSpacing(12)
        top.addLayout(info, 1)

        # Middle: standing instructions.
        self.instructions_box = QPlainTextEdit()
        self.instructions_box.setPlaceholderText(
            "Standing instructions, one per line. For example:\n"
            "Tell me whenever a new message appears\n"
            "Warn me if the health bar gets low")
        self.instructions_box.setFixedHeight(64)
        save_button = QPushButton("Save Instructions")
        save_button.clicked.connect(self.on_save_instructions)
        self.speak_checkbox = QCheckBox("Read alerts aloud")
        instruction_side = QVBoxLayout()
        instruction_side.addWidget(save_button)
        instruction_side.addWidget(self.speak_checkbox)
        instruction_side.addStretch()
        instructions_row = QHBoxLayout()
        instructions_row.addWidget(self.instructions_box, 1)
        instructions_row.addLayout(instruction_side)

        # Bottom: the chat and the question box.
        self.chat_view = QTextBrowser()
        self.chat_view.setOpenExternalLinks(True)
        self.question_box = QLineEdit()
        self.question_box.setPlaceholderText("Ask about the window, e.g. “What just changed?”")
        self.question_box.returnPressed.connect(self.on_ask)
        ask_button = QPushButton("Ask")
        ask_button.clicked.connect(self.on_ask)
        ask_row = QHBoxLayout()
        ask_row.addWidget(self.question_box, 1)
        ask_row.addWidget(ask_button)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addSpacing(6)
        layout.addLayout(instructions_row)
        layout.addWidget(self.chat_view, 1)
        layout.addLayout(ask_row)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

    # --- Startup -------------------------------------------------------------

    def first_run(self):
        """Runs right after the window appears."""
        # Check that Claude Code is installed and logged in (in the background,
        # because it takes a second or two).
        threading.Thread(
            target=lambda: self.signals.claude_status.emit(claude_bridge.check_claude()),
            daemon=True,
        ).start()
        self.on_change_window()

    def on_claude_status(self, problem):
        if problem:
            self.add_chat("error", problem)

    # --- Chat ----------------------------------------------------------------

    def add_chat(self, role, text):
        """Add a message to the chat and show it."""
        message = ChatMessage(role, text)
        self.chat.append(message)

        body = escape(text).replace("\n", "<br>")
        stamp = f'<span style="color:{self.gray};">{message.time_text()}</span>'
        if role == "note":
            html = f'<p style="color:{self.gray};">{body}</p>'
        else:
            name, color = {
                "user": ("You", None),
                "assistant": ("Claude", self.accent),
                "alert": ("Alert", self.accent),
                "error": ("Problem", self.red),
            }[role]
            name_html = f'<b style="color:{color};">{name}</b>' if color else f"<b>{name}</b>"
            html = f"<p>{name_html} &nbsp;{stamp}<br>{body}</p>"
        self.chat_view.append(html)
        scrollbar = self.chat_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        return message

    def on_ask(self):
        """You pressed Ask (or Return)."""
        question = self.question_box.text().strip()
        if not question:
            return
        self.question_box.clear()
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
            self.preview.setPixmap(pixmap.scaled(
                PREVIEW_WIDTH, PREVIEW_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation))

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
            chat_history=chat_so_far[-CHAT_MESSAGES_SENT:],
        )
        self.run_job(ClaudeJob("question", prompt, self.generation, [now_copy, before_copy]))

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
                self.add_chat("error", job.error)
            else:
                self.add_chat("assistant", job.answer or "(Claude sent an empty answer.)")
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
            state, color = "Idle", self.gray
        elif self.busy:
            doing = "answering your question" if self.busy_kind == "question" else "checking your alerts"
            state, color = f"Thinking… ({doing})", self.accent
        elif self.paused:
            state, color = "Paused", self.gray
        else:
            state, color = "Watching", self.accent
        self.status_label.setText(f'<span style="color:{color};">●</span> {state}')

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
        self.capture_timer.stop()
        claude_bridge.stop_running_call()
        shutil.rmtree(self.frames_dir, ignore_errors=True)
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Starting the app
# ---------------------------------------------------------------------------

def explain_missing_permission():
    """Show how to turn on Screen Recording, then quit."""
    capture.request_screen_recording_permission()  # adds the app to the list in System Settings
    box = QMessageBox()
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Screen Recording permission needed")
    box.setText("Window Watcher needs permission to see your screen.")
    # The installed app asks for itself; the script version asks for Terminal.
    if getattr(sys, "frozen", False):
        who = "Window Watcher"
    else:
        who = "Terminal (or whichever app you started Window Watcher from)"
    box.setInformativeText(
        "1. Click “Open System Settings” below.\n"
        f"2. Under Screen & System Audio Recording, turn on {who}.\n"
        "3. If macOS asks, choose “Quit & Reopen”. Otherwise, quit it yourself.\n"
        "4. Start Window Watcher again.")
    open_button = box.addButton("Open System Settings", QMessageBox.AcceptRole)
    box.addButton("Quit", QMessageBox.RejectRole)
    box.exec()
    if box.clickedButton() == open_button:
        capture.open_screen_recording_settings()


def main():
    try:
        # Show "Window Watcher" in the menu bar instead of "Python".
        from Foundation import NSBundle
        NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "Window Watcher"
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("Window Watcher")

    if not capture.has_screen_recording_permission():
        explain_missing_permission()
        return

    window = MainWindow()
    window.show()
    QTimer.singleShot(0, window.first_run)
    app.exec()


if __name__ == "__main__":
    main()
