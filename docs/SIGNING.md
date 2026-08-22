# macOS signing and notarization

Gatekeeper’s “Apple could not verify Mailkit is free of malware” dialog means
the disk image was **not signed with a Developer ID Application certificate
and notarized**. An *Apple Development* cert (the kind Xcode creates for
local builds) cannot fix that.

Mailkit’s release job **refuses to publish** an unsigned DMG. Future tags
fail CI until these secrets exist.

## 1. Developer ID Application certificate

1. Sign in at [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/certificates/list)
   with the **tom ullman** team (`J35QCDF5JA`).
2. Create **Developer ID Application** (not Apple Development, not Apple Distribution).
3. Download the cert, import it into Keychain Access, then export
   `Certificates.p12` with a password.

```bash
base64 -i Certificates.p12 | pbcopy
```

## 2. App Store Connect API key (notarization)

1. [App Store Connect → Integrations → Team Keys](https://appstoreconnect.apple.com/access/integrations/api)
2. Create a key with **Developer** access.
3. Download the `.p8` once. Note Key ID and Issuer ID.

```bash
# p8 file as the raw secret (including -----BEGIN PRIVATE KEY-----)
```

## 3. GitHub Actions secrets

On https://github.com/tullman17-coder/mailkit/settings/secrets/actions

| Secret | Value |
|---|---|
| `MACOS_CERTIFICATE` | base64 of the `.p12` |
| `MACOS_CERTIFICATE_PWD` | p12 password |
| `APPLE_TEAM_ID` | `J35QCDF5JA` |
| `APPLE_NOTARY_KEY_ID` | App Store Connect key id |
| `APPLE_NOTARY_ISSUER_ID` | App Store Connect issuer UUID |
| `APPLE_NOTARY_KEY` | full contents of the `.p8` |

Do not commit the p12, p8, or passwords.

```bash
gh secret set MACOS_CERTIFICATE < Certificates.p12.b64
gh secret set MACOS_CERTIFICATE_PWD
gh secret set APPLE_TEAM_ID -b J35QCDF5JA
gh secret set APPLE_NOTARY_KEY_ID
gh secret set APPLE_NOTARY_ISSUER_ID
gh secret set APPLE_NOTARY_KEY < AuthKey_XXXXXXXX.p8
```

## 4. Cut a signed release

```bash
git tag v0.1.1
git push origin v0.1.1
```

The workflow imports the cert, signs with hardened runtime, notarizes, staples,
and attaches `Mailkit-0.1.1.dmg`. Gatekeeper then opens it without the malware
warning (first launch may still show “downloaded from the internet” — that’s
normal and is not the malware dialog).

## Local debug only

Unsigned local images are allowed only with:

```bash
ALLOW_UNSIGNED=1 bash scripts/build-dmg.sh
```

That flag is ignored on GitHub Actions.
