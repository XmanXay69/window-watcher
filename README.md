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
   Then eject the DMG (the eject button next to it in Finder's sidebar) and always
   open the app from Applications. Opening the copy inside the DMG makes macOS
   treat it as a different app, so Screen Recording won't work.
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

1. Open **Window Watcher** from Applications (or double-click **Start Window
   Watcher.command** if you run it from source; leave that Terminal window
   open). It starts **asleep** as a small eye icon in the menu bar at the top
   of the screen: no windows, no Dock icon, no screenshots, no Claude.
2. Click the eye (or press **⌘⇧C**). Window Watcher wakes up, checks Screen
   Recording permission, and drops down a panel under the eye asking which
   window to watch (click anywhere else to close it and stay asleep). Pick one and click
   **Watch This Window**. The main window opens right where you are, even over
   an app in full-screen mode, instead of jumping to another desktop.
   Window Watcher is a menu bar app: it never has a Dock icon or menus at the
   top of the screen (and isn't in ⌘Tab). Everything is in the eye's menu
   while it's awake: Show Window Watcher, Show Chat, Change Window, **Quick
   Buttons** (and Edit Buttons), **⌘⇧C Works in Other Apps**, **Go to Sleep**,
   **Open at Login** (keeps the eye in the menu bar after a restart), and
   **Quit**. Closing the main window also puts it to sleep: screenshots stop
   and are deleted, while your chat, instructions, documents and buttons are kept.
3. Type a question at the bottom and press Return. Or click one of the four
   programmable buttons above the question box (like **What's the answer to
   this?** or **Explain this**) to send that message in one click, or press
   **⌘1**–**⌘4**. Click **Edit Buttons…** to type your own text into them.
   Your buttons are saved for next time.
4. Optional: type standing instructions (one per line) in the box and click
   **Save**. Tick **Read alerts aloud** to hear alerts.
5. Click **Show Chat Window** for a separate chat that floats on top of other
   apps. Answers are labeled with the question they answer (Q1, Q2…). Select
   any text to copy it, click **Copy** next to a message, or click **Copy All**
   for the whole conversation. **Hide** (or the window's close button) hides it.
   Shortcut: press **⌘⇧C** (Command-Shift-C) to show or hide it, even while
   another app or game is in front. Hiding it takes you back to the app you
   were using. The chat window shows up on every desktop, including over apps
   in full-screen mode, so you can keep a full-screen game visible (and watched)
   while you chat. Because ⌘⇧C then belongs to Window Watcher everywhere (in
   Chrome it normally opens Inspect Element), you can turn this off with
   **⌘⇧C Works in Other Apps** in the eye's menu. It then only works inside Window
   Watcher. Apps that block outside shortcuts on purpose still block this one.
6. Click **Add…** under Reference Documents to give Claude extra background info, like a game
   guide, notes, a cheat sheet, or a manual. Files can be up to **500 MB**.
   Text, Markdown, PDF, Word (.docx/.doc), RTF, CSV, JSON, HTML and images work.
   PDFs and Word files are turned into searchable text, and for big documents
   Claude searches for the parts that match your question instead of reading the
   whole thing. **Remove All** detaches them. Copies are kept in the app's temp
   folder and deleted when you quit; your original files aren't changed.
   (Scanned PDFs, which are pictures of pages, have no text to search.)
7. **Pause** stops the automatic screenshots and alerts. **Change Window…**
   switches to a different window.

To try it safely first, run the practice window in a second Terminal window:

```bash
cd ~/Desktop/Coding/WindowWatcher && .venv/bin/python practice_window.py
```

Then pick **Python — Practice Game** in Window Watcher.

You can also turn Open at Login on or off from Terminal:

```bash
"/Applications/Window Watcher.app/Contents/MacOS/Window Watcher" --open-at-login=on
```

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
| `documents.py` | Prepares reference documents (PDF/Word to text, size limits) |
| `hotkeys.py` | The system-wide ⌘⇧C shortcut (Apple's RegisterEventHotKey) |
| `menubar.py` | The menu bar icon, its menu, and Open at Login |
| `theme.py` | Colors (light and dark) and the stylesheet for the app's look |
| `practice_window.py` | A pretend game for testing |
| `make_icon.py` | Draws the app icon (`icon.png`, `icon.icns`) |

Each Claude call runs:

```
claude -p --output-format json --system-prompt "…" --tools Read,Grep --allowedTools Read,Grep
       --strict-mcp-config --no-session-persistence --effort low  (prompt on stdin)
```

The prompt lists the screenshot file paths, recent chat, standing
instructions, and recent changes, and Claude opens the images with its Read
tool. Claude only gets the Read and Grep (search) tools, so it can't run
commands or change files. Background checks also pass `--json-schema`, so the answer comes back
as `{alert, message, change_summary}`.

## Building the DMG yourself

```bash
./build_dmg.sh
```

This creates `dist/WindowWatcher.dmg` using PyInstaller.

To build and install straight into Applications on your own Mac instead:

```bash
./build_dmg.sh --install
```

**Why two ways:** macOS remembers Screen Recording permission by the app's
signature. `--install` signs with your free Apple Development certificate
(from signing into Xcode with your Apple ID), so every build has the same
identity and **the permission survives updates**. That certificate's name
contains your Apple ID email, so those builds stay on your Mac. The shared DMG
is signed "ad hoc" (no certificate, no personal info), so macOS treats each
release as a new app: after installing an update from GitHub, click **Reset
Permission** in the app's permission message and allow it again.

## Troubleshooting

| Message | Fix |
|---------|-----|
| "Claude Code isn't installed" | Do setup step 1, then restart the app |
| "Claude Code isn't logged in" | Run `claude auth login` in Terminal |
| "too old for this app" | Run `claude update` |
| "Screen Recording permission is turned off" | Do setup step 4 |
| Asks for Screen Recording but it's already on | macOS is remembering an older copy. Click **Reset Permission** in the app's message, allow it again, then click **Quit & Reopen** |
| "minimized or on another desktop" | Bring the window onto your current desktop |
