#!/usr/bin/env bash
# Assemble NonyaPet.app = Swift menubar/pet shell + embedded standalone core.
# Output: build/NonyaPet.app  (unsigned — run packaging/sign-notarize.sh to ship)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
# ASCII bundle filename for safe distribution/notarization (a Korean .app name shows as
# \u-escapes in the notarize archive and risks tooling issues). The brand "노냐?" still
# shows in-app (notifications/menu/briefing) via CFBundleDisplayName.
APP="$BUILD/Nonya.app"
rm -rf "$BUILD/NonyaPet.app" "$BUILD/노냐.app"   # drop old-named bundles

# 1) standalone Python core — ALWAYS rebuild so the bundle never ships a stale core
# (a cached dist/nonya silently shipped old CLI args and broke "watch all" — exit 2).
bash "$ROOT/packaging/build-core.sh" >/dev/null
# 2) Swift shell (release, universal arm64+x86_64 so it runs on Intel + Apple Silicon)
( cd "$ROOT/macos" && swift build -c release --arch arm64 --arch x86_64 >/dev/null )
RELEASE_DIR="$ROOT/macos/.build/apple/Products/Release"
SHELL_BIN="$RELEASE_DIR/NonyaPet"

# 3) assemble bundle
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/core"
cp "$SHELL_BIN" "$APP/Contents/MacOS/NonyaPet"
cp "$ROOT/macos/NonyaPet-Info.plist" "$APP/Contents/Info.plist"
cp "$BUILD/dist/nonya" "$APP/Contents/Resources/core/nonya"
# NOTE: the eyes are native Core Graphics (no WebView) — the old web/ + *.bundle
# 3D pet assets (~40MB) are NOT referenced at runtime, so they are not bundled.
chmod +x "$APP/Contents/MacOS/NonyaPet" "$APP/Contents/Resources/core/nonya"

# 3b) app icon — rendered fresh from the Swift eyes brand into a proper .icns
ICONSET="$BUILD/AppIcon.iconset"
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
if "$SHELL_BIN" --render-icon "$BUILD/icon-1024.png" >/dev/null 2>&1 && [ -s "$BUILD/icon-1024.png" ]; then
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s" "$BUILD/icon-1024.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null 2>&1
    d=$((s * 2)); sips -z "$d" "$d" "$BUILD/icon-1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1
  done
  cp "$BUILD/icon-1024.png" "$ICONSET/icon_512x512@2x.png"
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns" 2>/dev/null \
    && echo "icon:         AppIcon.icns ($(du -h "$APP/Contents/Resources/AppIcon.icns" | cut -f1))"
fi

# Ad-hoc sign so the assembled bundle is internally consistent + passes
# `codesign --verify` and runs locally without a "damaged" prompt. This is NOT
# Developer-ID/notarization (Gatekeeper/spctl still needs that — run
# packaging/sign-notarize.sh with your cert to distribute).
codesign --force --sign - "$APP/Contents/Resources/core/nonya" 2>/dev/null || true
codesign --force --deep --sign - "$APP" 2>/dev/null || true
codesign --verify --deep --strict "$APP" 2>/dev/null \
  && echo "signed:       ad-hoc (codesign --verify OK; not notarized)" \
  || echo "signed:       (ad-hoc signing unavailable)"

echo "assembled: $APP"
echo "shell archs:  $(lipo -archs "$APP/Contents/MacOS/NonyaPet")"
echo "core archs:   $(lipo -archs "$APP/Contents/Resources/core/nonya")"
"$APP/Contents/Resources/core/nonya" --version
