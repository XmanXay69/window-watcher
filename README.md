# Window Watcher

A Mac app that watches one window you choose and answers questions about it
in real time, using Claude through the Claude Code CLI. It uses your Claude
subscription, so there's no API key.

- Pick any open window (app name + title).
- It screenshots that window every 2.5 seconds, even when other windows
  partly cover it, and throws away screenshots that haven't changed.
- Ask questions in the chat ("What's my score?", "What just changed?").
- Add standing instructions ("Warn me if the health bar gets low") and it
  speaks up in the chat by itself when one is triggered.

## Option A: install the app (DMG)

1. Do setup steps 1 and 2 below (install Claude Code and log in).
2. Download **WindowWatcher.dmg** from the
   [latest release](https://github.com/XmanXay69/window-watcher/releases/latest),
   open it, and drag **Window Watcher** into **Applications**.
3. Open it from Applications. Because the app isn't notarized by Apple, the
   first launch gets blocked: go to **System Settings → Privacy & Security**,
   scroll down, and click **Open Anyway** next to "Window Watcher".
4. When asked, allow Screen Recording for **Window Watcher** (see step 4
   below, but turn on *Window Watcher* instead of Terminal), then reopen it.

## Option B: run from the source code

## One-time setup

**1. Install Claude Code** (skip if `claude --version` already works in Terminal).
Open Terminal (Cmd+Space, type "Terminal", press Return) and run:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Close Terminal and open it again so it finds the new `claude` command.

**2. Log in to Claude Code** with your Claude account:

```bash
claude auth login
```

A browser window opens. Sign in and approve. Check that it worked:

```bash
claude auth status
```

You should see `"loggedIn": true`.

**3. Start Window Watcher once** (this also installs everything it needs).
In Finder, open `Desktop/Coding/WindowWatcher` and double-click
**Start Window Watcher.command**. The first start takes a minute or two.

**4. Turn on Screen Recording.** The first time, macOS asks whether Terminal
may record the screen, and Window Watcher shows instructions:

1. Open **System Settings → Privacy & Security → Screen & System Audio Recording**.
2. Turn on **Terminal**. (If you start the app from a different app,
   like iTerm or VS Code, turn on that one instead.)
3. Pick **Quit & Reopen** when macOS asks, or quit Terminal yourself (Cmd+Q).
4. Double-click **Start Window Watcher.command** again.

## Everyday use

1. Double-click **Start Window Watcher.command**. Leave the Terminal window it
   opens running in the background, because closing it quits the app.
2. Pick a window from the list and click **Watch This Window**.
3. Type a question at the bottom and press Return.
4. Optional: type standing instructions (one per line) in the box and click
   **Save Instructions**. Tick **Read alerts aloud** to hear alerts.
5. Click **Show Chat Window** for a separate chat that floats on top of other
   apps. Answers are labeled with the question they answer (Q1, Q2…). Select
   any text to copy it, click **Copy** next to a message, or click **Copy All**
   for the whole conversation. **Hide** (or the window's close button) hides it.
   Shortcut: press **⌘⇧C** (Command-Shift-C) to show or hide it while Window
   Watcher is the active app (also in the menu bar under **View**).
6. Click **Add Documents…** to give Claude extra background info, like a game
   guide, notes, a cheat sheet, or a PDF manual. Claude reads them when they help
   answer a question or check an alert. Text, Markdown, PDF, CSV, JSON, HTML and
   images all work. **Remove All** detaches them. Copies are kept in the app's
   temp folder and deleted when you quit; your original files aren't changed.
7. **Pause** stops the automatic screenshots and alerts. **Change Window…**
   switches to a different window.

To try it safely first, run the practice window in a second Terminal window:

```bash
cd ~/Desktop/Coding/WindowWatcher && .venv/bin/python practice_window.py
```

Then pick **Python — Practice Game** in Window Watcher.

## Good to know

- **The window must be on your current desktop.** macOS can't capture a
  window that's minimized or on another desktop (Space). Full-screen apps live
  on their own Space, so run games windowed or in Split View. The status line
  tells you when this happens, and watching resumes on its own.
- **Partly covered is fine; fully covered may freeze.** Many apps stop drawing
  while they're completely hidden, so the screenshot stops updating until
  part of the window is visible again.
- **Speed:** each answer takes several seconds, because Claude Code starts up
  and then looks at the image.
- **Usage:** questions and background checks count toward your Claude plan's
  usage limits. Background checks only run while you have standing
  instructions, only when the screen changes, and at most every 5 seconds.
- Screenshots go in a private temp folder that's deleted when you quit.

## Settings you can change

- `claude_bridge.py`: `MODEL` (`"haiku"` is fastest, `"opus"` smartest) and `EFFORT`.
- `app.py`: `CAPTURE_EVERY_SECONDS`, `MIN_SECONDS_BETWEEN_CHECKS`.
- `capture.py`: `CHANGE_THRESHOLD`. Raise it if blinking cursors or clocks
  set off too many checks, or lower it to catch smaller changes.

## How it works

| File | What it does |
|------|--------------|
| `app.py` | The window: preview, status, chat, and the logic for when to ask Claude |
| `capture.py` | Lists windows and screenshots one with ScreenCaptureKit; spots changes |
| `claude_bridge.py` | Runs `claude -p` and builds the prompts |
| `practice_window.py` | A pretend game for testing |
| `make_icon.py` | Draws the app icon (`icon.png`, `icon.icns`) |

Each Claude call runs:

```
claude -p --output-format json --system-prompt "…" --tools Read --allowedTools Read
       --strict-mcp-config --no-session-persistence --effort low  (prompt on stdin)
```

The prompt lists the screenshot file paths, recent chat, standing
instructions, and recent changes, and Claude opens the images with its Read
tool. Claude only gets the Read tool, so it can't run commands or change
files. Background checks also pass `--json-schema`, so the answer comes back
as `{alert, message, change_summary}`.

## Building the DMG yourself

```bash
./build_dmg.sh
```

This creates `dist/WindowWatcher.dmg` using PyInstaller.

To install a build on your own Mac that keeps its Screen Recording permission
across updates (needs the free Apple Development certificate you get by
signing into Xcode with your Apple ID):

```bash
./build_dmg.sh --install
```

## Troubleshooting

| Message | Fix |
|---------|-----|
| "Claude Code isn't installed" | Do setup step 1, then restart the app |
| "Claude Code isn't logged in" | Run `claude auth login` in Terminal |
| "too old for this app" | Run `claude update` |
| "Screen Recording permission is turned off" | Do setup step 4 |
| Asks for Screen Recording but it's already on | macOS is remembering an older copy. In System Settings → Privacy & Security → Screen & System Audio Recording, select Window Watcher, click **−**, then reopen the app and allow it again |
| "minimized or on another desktop" | Bring the window onto your current desktop |
