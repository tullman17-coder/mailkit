#!/bin/sh
# Sign Mailkit.app + DMG with Developer ID, notarize, and staple.
# Unsigned output is refused unless ALLOW_UNSIGNED=1 (local debug only).
set -eu

usage() {
  echo "usage: macos-sign.sh <Mailkit.app> [Mailkit.dmg]" >&2
  exit 2
}

APP="${1:-}"
DMG="${2:-}"
[ -n "$APP" ] && [ -d "$APP" ] || usage

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
ENTITLEMENTS="${ENTITLEMENTS:-$ROOT/packaging/entitlements.plist}"
REQUIRE_SIGN="${REQUIRE_SIGN:-1}"
if [ -n "${GITHUB_ACTIONS:-}" ]; then
  REQUIRE_SIGN=1
fi

identity="${CODESIGN_IDENTITY:-}"
if [ -z "$identity" ]; then
  identity="$(security find-identity -v -p codesigning 2>/dev/null | awk -F'"' '/Developer ID Application/ {print $2; exit}')"
fi

if [ -z "$identity" ]; then
  if [ "$REQUIRE_SIGN" = "0" ] || [ "${ALLOW_UNSIGNED:-}" = "1" ]; then
    echo "macos-sign: no Developer ID Application identity; ALLOW_UNSIGNED=1, skipping" >&2
    exit 0
  fi
  cat >&2 <<'EOF'
macos-sign: no "Developer ID Application" certificate in the keychain.

Apple Development certificates cannot be notarized for GitHub downloads.
Create a Developer ID Application cert at https://developer.apple.com/account/resources/certificates/list
then export a .p12 and set the GitHub Actions secrets listed in docs/SIGNING.md.

Refusing to ship an unsigned Mac build.
EOF
  exit 1
fi

echo "macos-sign: using $identity"

# Innermost first: nested Mach-O, then the bundle.
sign_file() {
  codesign --force --options runtime --timestamp --sign "$identity" --entitlements "$ENTITLEMENTS" "$1"
}

# Clear leftover signatures so --force always re-seals.
codesign --remove-signature "$APP" 2>/dev/null || true

find "$APP/Contents" \( -name '*.so' -o -name '*.dylib' -o -name '*.node' \) -type f | while IFS= read -r f; do
  sign_file "$f" || true
done

# PyInstaller helpers and the main executable
if [ -d "$APP/Contents/MacOS" ]; then
  find "$APP/Contents/MacOS" -type f | while IFS= read -r f; do
    file "$f" | grep -q 'Mach-O' || continue
    sign_file "$f"
  done
fi

codesign --force --options runtime --timestamp --sign "$identity" --entitlements "$ENTITLEMENTS" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

if [ -n "$DMG" ]; then
  [ -f "$DMG" ] || { echo "missing dmg $DMG" >&2; exit 1; }
  codesign --force --timestamp --sign "$identity" "$DMG"

  if [ -n "${APPLE_NOTARY_KEY:-}" ] && [ -n "${APPLE_NOTARY_KEY_ID:-}" ] && [ -n "${APPLE_NOTARY_ISSUER_ID:-}" ]; then
    keyfile="$(mktemp)"
    trap 'rm -f "$keyfile"' EXIT
    printf '%s' "$APPLE_NOTARY_KEY" >"$keyfile"
    xcrun notarytool submit "$DMG" \
      --key "$keyfile" \
      --key-id "$APPLE_NOTARY_KEY_ID" \
      --issuer "$APPLE_NOTARY_ISSUER_ID" \
      --wait
    xcrun stapler staple "$DMG"
    xcrun stapler staple "$APP" || true
  elif [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ]; then
    xcrun notarytool submit "$DMG" \
      --apple-id "$APPLE_ID" \
      --password "$APPLE_APP_SPECIFIC_PASSWORD" \
      --team-id "$APPLE_TEAM_ID" \
      --wait
    xcrun stapler staple "$DMG"
    xcrun stapler staple "$APP" || true
  else
    echo "macos-sign: signed, but notarization secrets are missing." >&2
    echo "Set APPLE_NOTARY_KEY + APPLE_NOTARY_KEY_ID + APPLE_NOTARY_ISSUER_ID (see docs/SIGNING.md)." >&2
    exit 1
  fi
  echo "macos-sign: notarized and stapled $DMG"
fi
