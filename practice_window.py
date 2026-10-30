"""
practice_window.py: a pretend game window for trying out Window Watcher.

It shows a score that goes up, a health bar that drains, and chat messages
that arrive every so often. Run it in a second Terminal window:

    .venv/bin/python practice_window.py

Then pick "Practice Game" in Window Watcher and try questions like
"What's my score?" or the instruction "Warn me if health drops below 30%".
"""

import random
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QProgressBar, QVBoxLayout, QWidget

MESSAGES = [
    ("Sam", "gg, that last round was close"),
    ("Riley", "anyone want to queue again?"),
    ("Jordan", "watch out for the boss on the left"),
    ("Sam", "nice shot!"),
    ("Alex", "brb, grabbing water"),
    ("Riley", "who has the extra potion?"),
]


class PracticeGame(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Practice Game")
        self.resize(460, 380)
        self.score = 0
        self.health = 100
        self.next_message = 0

        self.score_label = QLabel()
        self.score_label.setStyleSheet("font-size: 28px; font-weight: 600;")
        self.health_label = QLabel()
        self.health_bar = QProgressBar()
        self.health_bar.setTextVisible(False)
        self.chat = QListWidget()

        layout = QVBoxLayout(self)
        layout.addWidget(self.score_label)
        layout.addWidget(self.health_label)
        layout.addWidget(self.health_bar)
        layout.addWidget(QLabel("Team chat"))
        layout.addWidget(self.chat)

        # Every 3 seconds: score goes up, health goes down.
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self.tick)
        self.tick_timer.start(3000)
        # Every 20 seconds: a new chat message arrives.
        self.message_timer = QTimer(self)
        self.message_timer.timeout.connect(self.add_message)
        self.message_timer.start(20000)

        self.add_message()
        self.refresh()

    def tick(self):
        self.score += random.choice([10, 15, 20, 25])
        self.health -= 6
        if self.health <= 0:
            self.health = 100  # respawn with full health
        self.refresh()

    def add_message(self):
        name, text = MESSAGES[self.next_message % len(MESSAGES)]
        self.next_message += 1
        self.chat.addItem(f"{name}: {text}")
        self.chat.scrollToBottom()
        print(f"message: {name}: {text}", flush=True)

    def refresh(self):
        self.score_label.setText(f"Score: {self.score}")
        self.health_label.setText(f"Health: {self.health}%")
        self.health_bar.setValue(self.health)
        print(f"score={self.score} health={self.health}", flush=True)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    game = PracticeGame()
    game.show()
    sys.exit(app.exec())
