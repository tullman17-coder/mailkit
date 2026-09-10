# Native Apple apps

Open `clients/apple/MailKit.xcodeproj` in Xcode and select the shared **MailKit** scheme.
One SwiftUI target supports iPhone/iPad on iOS 17+ and native Mac on macOS 14+.
Bundle ID: `org.zermo.mailkit`. Selected team: **ZERMO BRANDS LLC — FMYLGYWYXW**.
Version: **0.2.0 (1)**.

These are clients of the existing MailKit engine. Python, IMAP/SMTP connections,
mail credentials, indexing, and routing rules run on your Mac or server. iOS does
not embed the Python daemon and does not keep IMAP connections alive in the
background. No mail-provider password or refresh token is stored on the iPhone;
the engine bearer token is stored in device-only Keychain storage.

## Connect the engine

The native app accepts an HTTPS base URL and the engine's bearer token. Enter the
token directly into the app, never in a URL, source file, or GitHub issue.
The Mac app and iOS simulator also accept `http://127.0.0.1:8765` for a local
engine. On a physical iPhone, loopback means the phone itself.

Run the updated engine behind a TLS reverse proxy on a host you control. Keep its
HTTP listener on `127.0.0.1`. For example, a Caddy site with your actual DNS name
can use `reverse_proxy 127.0.0.1:8765`. Preserve the Authorization header and
disable caching and request-body logging. A private Tailscale HTTPS endpoint can
also work when every testing device belongs to that tailnet.

Existing configurations that deliberately bound HTTP to `0.0.0.0` with
`allow_remote=true` need to migrate to loopback plus HTTPS before upgrading. The
updated server rejects a non-loopback plaintext listener. The existing deployed
engine and its configuration were not changed during this work.

Add an account through Settings. Password accounts authenticate against both
IMAP and SMTP before the app saves them. Each account can use distinct outgoing
credentials. Google and Microsoft sign-in use the system authentication browser,
PKCE S256, a random expiring state, and a one-use callback. The engine uses fixed
provider endpoints for those sign-ins, validates both protocols, and persists
tokens in its encrypted vault only after success. Credentials never appear in
the account response.

See [provider requirements](PROVIDERS.md) for the ten presets and manual server
settings. Presets are configuration coverage, not a claim that every provider
has been tested with a live account. Plans, app passwords, IMAP enablement,
SMTP AUTH policy, and regional hosts still apply.

## Register OAuth clients

- Google: create an iOS OAuth client for `org.zermo.mailkit`. Use its client ID
  in account setup. The callback uses the reversed client ID followed by
  `:/oauthredirect`; for example `com.googleusercontent.apps.CLIENT:/oauthredirect`.
  Configure the consent screen, allowed test users, and the `https://mail.google.com/`
  scope. Public use of that restricted scope may need Google's verification.
- Microsoft: create a public-client app registration supporting the intended
  personal/work accounts. Register `org.zermo.mailkit://oauth/callback` as the
  native redirect. Grant delegated IMAP access, SMTP.Send, and offline_access.
  Native sign-in uses IMAP IDLE; its token is not treated as a Graph API token.
- Use platform-appropriate registrations for the Mac app; an iOS registration
  alone does not establish that the provider permits the macOS sign-in flow.

No OAuth client secret belongs in the app. A provider client ID is not a secret.
The authentication session receives the callback while active; the app also
declares the MailKit URL scheme in Info.plist.

## Native behavior and current limits

Navigation is account → mailbox → message. The app can list/search/read mail,
compose/reply, change flags/read state, move/archive messages, test/remove
accounts, and create account-scoped subject routing rules with tag/move actions.
HTML bodies disable scripts, remote resources, embedded navigation, and persistent
web storage. Link taps open externally. Plain text uses native selectable text.

The message list shows up to 100 matching recent messages; search operates on the
mail server. Refresh is explicit. There is no APNs/background sync, durable offline
mail cache, attachment downloading/uploading, or persisted local compose draft in
this first native version. Compose protects against accidental sheet dismissal;
SMTP delivery is never automatically retried. These limits are not hidden behind
fake data or simulated connection success.

## Validate and build

From the repository root with Python 3.11+ and Xcode installed:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
MAILKIT_HOME=/tmp/mailkit-tests .venv/bin/python -m pytest --basetemp=/tmp/mailkit-pytest
.venv/bin/python scripts/check-apple.py
xcodebuild -project clients/apple/MailKit.xcodeproj -scheme MailKit \
  -destination 'generic/platform=iOS Simulator' -derivedDataPath build/apple \
  CODE_SIGNING_ALLOWED=NO build
xcodebuild -project clients/apple/MailKit.xcodeproj -scheme MailKit \
  -destination 'platform=macOS' -derivedDataPath build/apple-macos build
```

The Swift check compiles with strict concurrency warnings as errors and tests
encrypted endpoint validation, provider/TLS configuration, JSON decoding, request
encoding, provider-versus-engine authentication errors, and refused redirects
against a local fixture. No real mailbox or email delivery is involved.

## Signed iOS archive and TestFlight

```sh
xcodebuild -project clients/apple/MailKit.xcodeproj -scheme MailKit \
  -configuration Release -destination 'generic/platform=iOS' \
  -derivedDataPath build/apple-device -archivePath build/MailKit.xcarchive \
  -allowProvisioningUpdates archive
xcodebuild -exportArchive -archivePath build/MailKit.xcarchive \
  -exportPath build/testflight -exportOptionsPlist clients/apple/ExportOptions.plist \
  -allowProvisioningUpdates
```

Export creates `build/testflight/MailKit.ipa`; it does **not upload** the build.
The export uses Apple Distribution, team `FMYLGYWYXW`, and an App Store profile
with `beta-reports-active=true` and `get-task-allow=false`.

Before the requested upload, verify a reachable HTTPS engine from a real iPhone,
registered Google/Microsoft sign-ins, provider credentials, and actual mail
read/flag/move/send/reply behavior. Test sending only with designated test
recipients and explicit authorization. Do not advertise unverified provider
connections as working.

App Store Connect needs an app record with this bundle ID, under the selected
team, and an authenticated user with upload access. In Xcode Organizer, choose
the archive → **Distribute App → App Store Connect → Upload**. After Apple
processes it, add the build to your internal TestFlight group and include your
own Apple account as a tester. External testers may require Beta App Review.
Increment the build number for each subsequent upload.

App privacy and export-compliance answers must reflect the actual deployed
engine and any later analytics/services. This client adds no analytics SDK;
its manifest declares no developer-collected data. Encryption uses Apple's
system frameworks; the current export declares no non-exempt encryption.

Apple: [create the app record](https://developer.apple.com/help/app-store-connect/create-an-app-record/add-a-new-app/),
[upload builds](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds),
[TestFlight workflow](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/).
