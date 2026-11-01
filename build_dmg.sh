#!/bin/zsh
# Builds "Window Watcher.app".
#   ./build_dmg.sh            makes dist/WindowWatcher.dmg to share
#   ./build_dmg.sh --install  installs it on this Mac, signed so that the
#                             Screen Recording permission survives updates
# Run from the project folder after the one-time setup.
set -e
cd "$(dirname "$0")"

VERSION=1.6.2

# PyInstaller bundles Python, Qt and PyObjC into a normal Mac app.
.venv/bin/pip install -q pyinstaller
rm -rf build dist
.venv/bin/pyinstaller --noconfirm --windowed \
    --name "Window Watcher" \
    --osx-bundle-identifier com.xmanxay.windowwatcher \
    --icon icon.icns \
    --hidden-import ScreenCaptureKit --hidden-import Quartz \
    --hidden-import Foundation --hidden-import AppKit \
    app.py

# Put the version in the app's Info.plist.
PLIST="dist/Window Watcher.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST"

# Sign the app. macOS remembers Screen Recording permission by signature.
if [ "$1" = "--install" ]; then
    # For this Mac: your free "Apple Development" certificate (you get it by
    # signing into Xcode with your Apple ID) gives every build the same
    # identity, so the permission survives updates. Its name contains your
    # Apple ID email, so these builds stay on your Mac and never go on GitHub.
    IDENTITY=$(security find-identity -v -p codesigning | grep -m1 "Apple Development" | awk '{print $2}')
    if [ -n "$IDENTITY" ]; then
        codesign --force --deep --sign "$IDENTITY" "dist/Window Watcher.app"
    else
        echo "No Apple Development certificate found, so signing ad hoc."
        echo "You'll need to allow Screen Recording again after each update."
        codesign --force --deep --sign - "dist/Window Watcher.app"
    fi
    codesign --verify --deep --strict "dist/Window Watcher.app"
    # Close the running app (a permission message can block a polite quit).
    osascript -e 'quit app "Window Watcher"' 2>/dev/null || true
    pkill -f "Window Watcher.app/Contents/MacOS/Window Watcher" 2>/dev/null || true
    rm -rf "/Applications/Window Watcher.app"
    cp -R "dist/Window Watcher.app" /Applications/
    echo "Installed /Applications/Window Watcher.app"
    exit 0
fi

# For sharing: "ad hoc" signing needs no certificate and holds no personal
# info. (A free self-made certificate doesn't help: macOS ignores it for
# Screen Recording unless it's marked trusted.) macOS sees each build as a new
# app, so after updating, click "Reset Permission" in the app and allow it again.
codesign --force --deep --sign - "dist/Window Watcher.app"
codesign --verify --deep --strict "dist/Window Watcher.app"

# Make the disk image: the app plus a shortcut to Applications to drag it onto.
mkdir -p dist/dmg
cp -R "dist/Window Watcher.app" dist/dmg/
ln -s /Applications dist/dmg/Applications
hdiutil create -volname "Window Watcher" -srcfolder dist/dmg -ov -format UDZO dist/WindowWatcher.dmg
rm -rf dist/dmg
echo "Built dist/WindowWatcher.dmg"
