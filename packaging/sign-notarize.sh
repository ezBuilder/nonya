#!/usr/bin/env bash
# Production: sign (Developer ID + hardened runtime) -> notarize -> staple -> DMG.
#
# One-time prerequisites (your Apple account, can't be automated):
#   1. Create a "Developer ID Application" certificate (Xcode > Settings > Accounts
#      > Manage Certificates > +, or developer.apple.com). It uses your existing
#      $99/yr membership — no extra cost.
#   2. Store notarization creds once:
#        xcrun notarytool store-credentials nonya-notary \
#          --apple-id you@example.com --team-id TEAMID --password <app-specific-pw>
#
# Then run:
#   DEV_ID="Developer ID Application: Your Name (TEAMID)" \
#   NOTARY_PROFILE="nonya-notary" bash packaging/sign-notarize.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
APP="$BUILD/Nonya.app"
ENT="$ROOT/macos/NonyaPet.entitlements"
: "${DEV_ID:?set DEV_ID to your 'Developer ID Application: NAME (TEAMID)' identity}"
: "${NOTARY_PROFILE:?set NOTARY_PROFILE to your stored notarytool profile name}"

if ! xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1; then
  cat >&2 <<EOF
ERROR: notarytool profile '$NOTARY_PROFILE' is not available.

Create it once with:
  xcrun notarytool store-credentials "$NOTARY_PROFILE" \\
    --apple-id you@example.com \\
    --team-id TEAMID \\
    --password <app-specific-password>

Refusing to build a public DMG without notarization credentials.
EOF
  exit 69
fi

bash "$ROOT/packaging/build-app.sh"

# sign inside-out: embedded core first, then the app bundle, hardened runtime
codesign --force --options runtime --timestamp --entitlements "$ENT" \
  --sign "$DEV_ID" "$APP/Contents/Resources/core/nonya"
codesign --force --options runtime --timestamp --deep --entitlements "$ENT" \
  --sign "$DEV_ID" "$APP"
codesign --verify --strict --verbose=2 "$APP"

VER="$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$APP/Contents/Info.plist")"
DMG="$BUILD/nonya-$VER.dmg"
rm -f "$DMG"
hdiutil create -volname "nonya" -srcfolder "$APP" -ov -format UDZO "$DMG"
codesign --force --timestamp --sign "$DEV_ID" "$DMG"

# notarize the DMG, then staple so it runs offline without Gatekeeper prompts
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$DMG"
xcrun stapler staple "$APP"
spctl --assess --type open --context context:primary-signature -v "$DMG"

echo "READY TO DISTRIBUTE -> $DMG"
