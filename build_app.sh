#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP="$ROOT/dist/SightOps.app"
BUILD="$ROOT/.build/release/SightOps"

swift build -c release
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BUILD" "$APP/Contents/MacOS/SightOps"
cp "$ROOT/sightops/hermes_bridge.py" "$APP/Contents/Resources/hermes_bridge.py"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>SightOps</string>
    <key>CFBundleDisplayName</key><string>SightOps</string>
    <key>CFBundleIdentifier</key><string>ai.sightbox.sightops</string>
    <key>CFBundleVersion</key><string>0.1.0</string>
    <key>CFBundleShortVersionString</key><string>0.1.0</string>
    <key>CFBundleExecutable</key><string>SightOps</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict>
</plist>
PLIST
codesign --force --deep --sign - "$APP" >/dev/null
printf 'Built %s\n' "$APP"
