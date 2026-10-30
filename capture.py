"""
capture.py: finding windows and taking screenshots of them.

This uses ScreenCaptureKit, Apple's current framework for screen capture. We
call it from Python through PyObjC, a bridge that lets Python use Apple's
Objective-C frameworks.

ScreenCaptureKit can capture one window by itself, even when other windows
cover it. It can't capture a minimized window or one on another Space
(desktop), because macOS doesn't draw those.
"""

import os
import subprocess
import threading
from dataclasses import dataclass

import objc
import Quartz
import ScreenCaptureKit as SCK
from Foundation import NSURL
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

# Claude shrinks images larger than about this size before looking at them,
# so capturing more pixels only makes the app slower.
MAX_LONG_EDGE = 1568
MAX_PIXELS = 1_150_000

# --- Change detection settings ---------------------------------------------
# To decide whether the screen "meaningfully changed", we shrink each frame to
# a small grayscale copy and count how many of its pixels changed.
THUMB_WIDTH = 160        # width of the small copy, in pixels
PIXEL_TOLERANCE = 18     # brightness difference (0-255) that counts as "changed"
CHANGE_THRESHOLD = 0.002 # changed if at least 0.2% of the small copy changed
                         # (raise this if blinking cursors or clocks cause
                         # too many updates; lower it to catch tinier changes)


@dataclass
class WindowInfo:
    """The basic facts about one window."""
    window_id: int
    app_name: str
    title: str

    def label(self):
        """A friendly name like 'Safari — Apple'."""
        return f"{self.app_name} — {self.title}" if self.title else f"{self.app_name} — (no title)"


class CaptureError(Exception):
    """Raised with a message that's safe to show the user."""


# ---------------------------------------------------------------------------
# Screen Recording permission
# ---------------------------------------------------------------------------

def has_screen_recording_permission():
    """True if macOS lets this app capture the screen."""
    return bool(Quartz.CGPreflightScreenCaptureAccess())


def request_screen_recording_permission():
    """Ask macOS for permission. The first time, this shows the system prompt
    and adds the app (usually Terminal) to the list in System Settings."""
    Quartz.CGRequestScreenCaptureAccess()


def open_screen_recording_settings():
    """Open System Settings at Privacy & Security > Screen Recording."""
    subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"])


# ---------------------------------------------------------------------------
# Talking to ScreenCaptureKit
# ---------------------------------------------------------------------------

def _wait_for_callback(start_call, timeout=10):
    """
    ScreenCaptureKit doesn't return results directly. Instead you give it a
    "completion handler" (a callback function), and it calls that function
    later on another thread. This helper starts the call, waits for the
    callback, and returns whatever the callback received.
    """
    finished = threading.Event()
    results = []

    def callback(*args):
        results.extend(args)
        finished.set()

    start_call(callback)
    if not finished.wait(timeout):
        raise CaptureError("macOS took too long to respond to a screen capture request.")
    return results


def _get_all_windows(on_screen_only):
    """Ask macOS for every window we're allowed to capture."""
    content, error = _wait_for_callback(
        lambda callback: SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            True, on_screen_only, callback
        )
    )
    if error is not None or content is None:
        if not has_screen_recording_permission():
            raise CaptureError("Screen Recording permission is turned off.")
        raise CaptureError(f"macOS wouldn't list the windows: {error}")
    return content.windows()


def _to_window_info(window):
    app = window.owningApplication()
    return WindowInfo(
        window_id=window.windowID(),
        app_name=app.applicationName() if app else "Unknown app",
        title=window.title() or "",
    )


def list_windows():
    """
    Return a list of WindowInfo for the normal app windows that are open on
    screen right now, sorted by app name. Menu bars, the Dock, tiny helper
    windows and this app's own windows are left out.
    """
    windows = []
    for window in _get_all_windows(on_screen_only=True):
        app = window.owningApplication()
        size = window.frame().size
        if app is None or app.processID() == os.getpid():
            continue  # no owner, or it's one of our own windows
        if window.windowLayer() != 0:
            continue  # layer 0 is normal app windows; others are menus, Dock, etc.
        if size.width < 120 or size.height < 80:
            continue  # too small to be a real window
        windows.append(_to_window_info(window))

    windows.sort(key=lambda w: (w.app_name.lower(), w.title.lower()))
    return windows


def _output_size(window, filter_):
    """Pick a screenshot size: sharp (Retina) when the window is small,
    shrunk to Claude's preferred maximum when it's big."""
    points = window.frame().size
    scale = filter_.pointPixelScale() or 1.0   # 2.0 on Retina screens
    width, height = points.width * scale, points.height * scale

    shrink = min(1.0, MAX_LONG_EDGE / max(width, height), (MAX_PIXELS / (width * height)) ** 0.5)
    return max(1, int(width * shrink)), max(1, int(height * shrink))


def _save_png(cg_image, path):
    """Write a CoreGraphics image to a PNG file."""
    url = NSURL.fileURLWithPath_(str(path))
    destination = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(destination, cg_image, None)
    if not Quartz.CGImageDestinationFinalize(destination):
        raise CaptureError("Couldn't save the screenshot file.")


def capture_window(window_id, save_path):
    """
    Take a screenshot of one window and save it as a PNG at save_path.
    Returns fresh WindowInfo (the title may have changed since you picked it).
    Raises CaptureError with a friendly message if it can't.
    """
    # Background threads need their own "autorelease pool" so macOS objects
    # get cleaned up. Without it, memory would slowly leak.
    with objc.autorelease_pool():
        # Look the window up again each time so we notice if it was closed.
        matches = [w for w in _get_all_windows(on_screen_only=False) if w.windowID() == window_id]
        if not matches:
            raise CaptureError("The window you were watching was closed. Choose another window.")
        window = matches[0]
        if not window.isOnScreen():
            raise CaptureError("The window is minimized or on another desktop, so macOS can't capture it right now.")

        # "Desktop independent window" means: just this window, even when
        # other windows are on top of it.
        filter_ = SCK.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)
        config = SCK.SCStreamConfiguration.alloc().init()
        width, height = _output_size(window, filter_)
        config.setWidth_(width)
        config.setHeight_(height)
        config.setShowsCursor_(False)  # the mouse pointer would count as "changes"

        image, error = _wait_for_callback(
            lambda callback: SCK.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                filter_, config, callback
            )
        )
        if error is not None or image is None:
            if not has_screen_recording_permission():
                raise CaptureError("Screen Recording permission is turned off.")
            raise CaptureError(f"macOS couldn't capture the window: {error}")

        _save_png(image, save_path)
        return _to_window_info(window)


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def make_thumbnail(path):
    """Load a screenshot and shrink it to a small grayscale copy for comparing."""
    image = QImage(str(path))
    small = image.scaledToWidth(THUMB_WIDTH, Qt.SmoothTransformation)
    return small.convertToFormat(QImage.Format_Grayscale8)


def screen_changed(old_thumb, new_thumb):
    """True if the two small copies differ by more than our threshold."""
    if old_thumb is None:
        return True
    if old_thumb.size() != new_thumb.size():
        return True  # the window was resized

    # Compare row by row (each row of pixels can have padding bytes at the
    # end, so we only look at the first `width` bytes of each).
    width, height = new_thumb.width(), new_thumb.height()
    changed_pixels = 0
    for y in range(height):
        old_row = old_thumb.constScanLine(y).tobytes()[:width]
        new_row = new_thumb.constScanLine(y).tobytes()[:width]
        changed_pixels += sum(1 for a, b in zip(old_row, new_row) if abs(a - b) > PIXEL_TOLERANCE)

    return changed_pixels >= CHANGE_THRESHOLD * width * height
