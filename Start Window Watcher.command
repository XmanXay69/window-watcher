#!/bin/zsh
# Double-click this file in Finder to start Window Watcher.
# The first time, it sets everything up (takes a minute or two).

cd "$(dirname "$0")"   # run from this project folder

# Use Homebrew's Python if it's there, otherwise any python3.
PYTHON=/opt/homebrew/bin/python3
[ -x "$PYTHON" ] || PYTHON=python3

# First run (or requirements changed): make a private Python environment
# in .venv and install the libraries into it.
if [ ! -f .venv/.installed ] || [ requirements.txt -nt .venv/.installed ]; then
    echo "Setting up Window Watcher (first run only)..."
    "$PYTHON" -m venv .venv || { echo "Couldn't create the Python environment. Is Python 3 installed?"; exit 1; }
    .venv/bin/pip install --upgrade pip -q
    .venv/bin/pip install -r requirements.txt || { echo "Installing libraries failed (see above)."; exit 1; }
    touch .venv/.installed
fi

echo "Starting Window Watcher... (keep this Terminal window open while you use it)"
.venv/bin/python app.py
