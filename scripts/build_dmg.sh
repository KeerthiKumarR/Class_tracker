#!/bin/bash
set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "[DMG] Staging ClassTracker installer..."
DMG_STAGING="$PROJECT_ROOT/dist/dmg_staging"
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"

if [ ! -d "$PROJECT_ROOT/dist/ClassTracker.app" ]; then
    echo "[ERROR] dist/ClassTracker.app not found. Please run PyInstaller first."
    exit 1
fi

cp -R "$PROJECT_ROOT/dist/ClassTracker.app" "$DMG_STAGING/"
ln -s /Applications "$DMG_STAGING/Applications"

DMG_OUT="$PROJECT_ROOT/ClassTracker_Installer.dmg"
rm -f "$DMG_OUT"

echo "[DMG] Packaging disk image with Applications shortcut..."
hdiutil create -volname "ClassTracker" \
  -srcfolder "$DMG_STAGING" \
  -ov -format UDZO \
  "$DMG_OUT"

rm -rf "$DMG_STAGING"
echo "[SUCCESS] ✅ Turnkey DMG created at $DMG_OUT"
