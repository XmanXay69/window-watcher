"""
make_icon.py: draws the Window Watcher app icon.

Run it once to (re)create icon.png and icon.icns:
    .venv/bin/python make_icon.py

The icon is a dark rounded tile with a simple app window on it, and an
"eye" in the window (the app watches windows).
"""

import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ACCENT = QColor("#0a84ff")  # macOS system blue
SIZE = 1024


def draw_icon():
    image = QImage(SIZE, SIZE, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)

    # The tile: Apple's icon grid uses an 824 px rounded square on a 1024 canvas.
    tile = QRectF(100, 100, 824, 824)
    gradient = QLinearGradient(tile.topLeft(), tile.bottomLeft())
    gradient.setColorAt(0, QColor("#3a3c42"))
    gradient.setColorAt(1, QColor("#1b1c1f"))
    p.setPen(Qt.NoPen)
    p.setBrush(gradient)
    p.drawRoundedRect(tile, 185, 185)

    # The app window: a light outline with a title bar and three dots.
    window = QRectF(232, 282, 560, 440)
    p.setBrush(QColor("#26272b"))
    p.setPen(QPen(QColor("#e8e8ed"), 22))
    p.drawRoundedRect(window, 48, 48)
    p.drawLine(QPointF(window.left(), 382), QPointF(window.right(), 382))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#e8e8ed"))
    for x in (292, 340, 388):
        p.drawEllipse(QPointF(x, 332), 14, 14)

    # The eye: an almond shape with a blue iris and a dark pupil.
    center = QPointF(512, 552)
    eye = QPainterPath()
    eye.moveTo(center.x() - 170, center.y())
    eye.quadTo(center.x(), center.y() - 150, center.x() + 170, center.y())
    eye.quadTo(center.x(), center.y() + 150, center.x() - 170, center.y())
    p.setBrush(QColor("#e8e8ed"))
    p.drawPath(eye)
    p.setBrush(ACCENT)
    p.drawEllipse(center, 62, 62)
    p.setBrush(QColor("#111214"))
    p.drawEllipse(center, 26, 26)
    p.setBrush(QColor(255, 255, 255, 220))
    p.drawEllipse(QPointF(center.x() + 22, center.y() - 22), 11, 11)

    p.end()
    return image


def main():
    app = QGuiApplication([])
    here = Path(__file__).parent
    image = draw_icon()
    image.save(str(here / "icon.png"))

    # macOS wants a folder of sizes (an "iconset"), which iconutil turns into .icns.
    iconset = here / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            image.scaled(size * scale, size * scale, Qt.KeepAspectRatio,
                         Qt.SmoothTransformation).save(str(iconset / name))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(here / "icon.icns")], check=True)
    shutil.rmtree(iconset)
    print("Made icon.png and icon.icns")


if __name__ == "__main__":
    main()
