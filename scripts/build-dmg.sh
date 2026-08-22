#!/bin/sh
# Build Mailkit.app and a UDZO disk image for GitHub Releases.
# Signing + notarization is mandatory on CI. See docs/SIGNING.md.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="${MAILKIT_VERSION:-0.1.1}"
VERSION="${VERSION#v}"
export MAILKIT_VERSION="$VERSION"
DIST="$ROOT/dist"
OUT="$ROOT/dist-dmg"
ICON="$ROOT/desktop/brand/AppIcon.icns"

python3 -m pip install -q -e ".[desktop,packaging]"
python3 -m PyInstaller --noconfirm --clean "$ROOT/packaging/mailkit.spec"

APP="$DIST/Mailkit.app"
if [ ! -d "$APP" ]; then
  echo "Mailkit.app was not produced" >&2
  exit 1
fi
if [ -f "$ICON" ]; then
  cp "$ICON" "$APP/Contents/Resources/AppIcon.icns"
fi

/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$APP/Contents/Info.plist"

# Sign the app before it is copied into the disk image.
sh "$ROOT/scripts/macos-sign.sh" "$APP"

STAGE="$(mktemp -d)"
cleanup() {
  rm -rf "$STAGE"
}
trap cleanup EXIT
cp -R "$APP" "$STAGE/Mailkit.app"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/README.txt" <<EOF
Mailkit $VERSION
A local email engine from Zermo Brands.

1. Drag Mailkit to Applications.
2. Open it. The engine talks directly to Gmail / IMAP on this Mac.
3. Credentials stay encrypted in ~/.mailkit and never leave the machine.

https://zermobrands.com
EOF

mkdir -p "$OUT"
DMG="$OUT/Mailkit-$VERSION.dmg"
rm -f "$DMG"
hdiutil create \
  -volname "Mailkit $VERSION" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  "$DMG"

# Sign and notarize the disk image (re-seals the already-signed app, then the DMG).
sh "$ROOT/scripts/macos-sign.sh" "$APP" "$DMG"

echo "wrote $DMG"
ls -lh "$DMG"
