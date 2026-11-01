#!/bin/zsh
# Builds "Window Watcher.app".
#   ./build_dmg.sh            makes dist/WindowWatcher.dmg to share (signed "ad hoc")
#   ./build_dmg.sh --install  signs it with your own Apple Development certificate
#                             and puts it in /Applications (for your Mac only)
# Run from the project folder after the one-time setup.
set -e
cd "$(dirname "$0")"

VERSION=1.5.0

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

# Sign the app. macOS remembers Screen Recording permission per signature.
if [ "$1" = "--install" ]; then
    # Your free "Apple Development" certificate (from signing into Xcode with
    # your Apple ID) keeps the permission across updates. Its name includes
    # your Apple ID email, so this build is only for your own Mac.
    IDENTITY=$(security find-identity -v -p codesigning | grep -m1 "Apple Development" | awk '{print $2}')
    if [ -z "$IDENTITY" ]; then
        echo "No Apple Development certificate found. Sign into Xcode with your Apple ID first."
        exit 1
    fi
    codesign --force --deep --sign "$IDENTITY" "dist/Window Watcher.app"
    codesign --verify --deep --strict "dist/Window Watcher.app"
    osascript -e 'quit app "Window Watcher"' 2>/dev/null || true
    rm -rf "/Applications/Window Watcher.app"
    cp -R "dist/Window Watcher.app" /Applications/
    echo "Installed /Applications/Window Watcher.app"
    exit 0
fi

# "Ad hoc" signing needs no certificate and contains no personal info, but
# macOS treats every rebuild as a new app, so the permission must be given again.
codesign --force --deep --sign - "dist/Window Watcher.app"
codesign --verify --deep --strict "dist/Window Watcher.app"

# Make the disk image: the app plus a shortcut to Applications to drag it onto.
mkdir -p dist/dmg
cp -R "dist/Window Watcher.app" dist/dmg/
ln -s /Applications dist/dmg/Applications
hdiutil create -volname "Window Watcher" -srcfolder dist/dmg -ov -format UDZO dist/WindowWatcher.dmg
rm -rf dist/dmg
echo "Built dist/WindowWatcher.dmg"
