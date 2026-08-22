#!/bin/sh
# Build Mailkit.app and a UDZO disk image for GitHub Releases.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="${MAILKIT_VERSION:-0.1.0}"
VERSION="${VERSION#v}"
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

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/Mailkit.app"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/README.txt" <<EOF
Mailkit $VERSION
A local email engine from Zermo Brands.

1. Drag Mailkit to Applications.
2. Open it. macOS may ask you to allow a local network client — that is the engine talking to Gmail / IMAP on this machine.
3. Credentials stay encrypted in ~/.mailkit and never leave the Mac.

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
echo "wrote $DMG"
ls -lh "$DMG"
