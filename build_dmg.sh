#!/bin/zsh
# Builds "Window Watcher.app" and packs it into dist/WindowWatcher.dmg.
# Run from the project folder after the one-time setup:  ./build_dmg.sh
set -e
cd "$(dirname "$0")"

VERSION=1.2.0

# PyInstaller bundles Python, Qt and PyObjC into a normal Mac app.
.venv/bin/pip install -q pyinstaller
rm -rf build dist
.venv/bin/pyinstaller --noconfirm --windowed \
    --name "Window Watcher" \
    --osx-bundle-identifier com.xmanxay.windowwatcher \
    --hidden-import ScreenCaptureKit --hidden-import Quartz \
    --hidden-import Foundation --hidden-import AppKit \
    app.py

# Put the version in the app's Info.plist.
PLIST="dist/Window Watcher.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST"

# Sign it "ad hoc" (no Apple Developer account needed) so macOS can remember
# its Screen Recording permission.
codesign --force --deep --sign - "dist/Window Watcher.app"

# Make the disk image: the app plus a shortcut to Applications to drag it onto.
mkdir -p dist/dmg
cp -R "dist/Window Watcher.app" dist/dmg/
ln -s /Applications dist/dmg/Applications
hdiutil create -volname "Window Watcher" -srcfolder dist/dmg -ov -format UDZO dist/WindowWatcher.dmg
rm -rf dist/dmg
echo "Built dist/WindowWatcher.dmg"
