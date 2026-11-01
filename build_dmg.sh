#!/bin/zsh
# Builds "Window Watcher.app".
#   ./build_dmg.sh            makes dist/WindowWatcher.dmg to share
#   ./build_dmg.sh --install  puts the app straight into /Applications
# Both sign it with the same "Window Watcher Signing" certificate (made by
# setup_signing.sh), so Screen Recording permission survives updates either way.
# Run from the project folder after the one-time setup.
set -e
cd "$(dirname "$0")"

VERSION=1.6.0

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

# Sign the app with our own certificate (created the first time).
./setup_signing.sh
KEYCHAIN="$HOME/.window-watcher-signing/signing.keychain-db"
security unlock-keychain -p "$(cat "$HOME/.window-watcher-signing/password")" "$KEYCHAIN"
codesign --force --deep --keychain "$KEYCHAIN" --sign "Window Watcher Signing" "dist/Window Watcher.app"
codesign --verify --deep --strict "dist/Window Watcher.app"

if [ "$1" = "--install" ]; then
    osascript -e 'quit app "Window Watcher"' 2>/dev/null || true
    rm -rf "/Applications/Window Watcher.app"
    cp -R "dist/Window Watcher.app" /Applications/
    echo "Installed /Applications/Window Watcher.app"
    exit 0
fi

# Make the disk image: the app plus a shortcut to Applications to drag it onto.
mkdir -p dist/dmg
cp -R "dist/Window Watcher.app" dist/dmg/
ln -s /Applications dist/dmg/Applications
hdiutil create -volname "Window Watcher" -srcfolder dist/dmg -ov -format UDZO dist/WindowWatcher.dmg
rm -rf dist/dmg
echo "Built dist/WindowWatcher.dmg"
