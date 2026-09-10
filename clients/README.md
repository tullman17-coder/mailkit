# Native clients

Mailkit ships as:

1. A **native desktop app** (`mailkit desktop`, pywebview, packaged as Mailkit.app / DMG)
2. A **native iOS app** (`clients/ios`) and **Android app** (`clients/android`)
3. A **CLI** so humans and AI agents share one contract (`-o json|ndjson`)

The Hallmark workbench in `desktop/` is the shared UI. Native shells load it. They contain no IMAP, SMTP, OAuth, vault, or classification logic.

```
Desktop window ─┐
iOS / Android  ─┼── HTTP / SSE / WS ──► local engine (this machine)
mailkit CLI    ─┘
```

## Desktop

```bash
pip install -e ".[desktop]"
mailkit desktop
```

The window injects the API token into the UI. Do not treat a browser tab as the product.

## Phone

The engine stays on the computer. The phone is a client.

```bash
mailkit pair --lan
# open the printed mailkit://connect?… link, or paste URL + token in the app
```

`--lan` binds `0.0.0.0` and sets `allow_remote = true`, then restarts the engine. The bearer token is still required. Use `--off` to return to loopback.

### iOS

`clients/ios` is a SwiftUI app: connect screen, then `WKWebView` with `?shell=mobile`. URL scheme: `mailkit://connect`.

```bash
cd clients/ios
# with XcodeGen: xcodegen && xcodebuild -scheme Mailkit
```

### Android

`clients/android` is a Kotlin app: connect screen, then `WebView` with `?shell=mobile`. Intent: `mailkit://connect`.

LAN HTTP is allowed (`usesCleartextTraffic`) because the engine is local, not a public host.

## Agents

See [docs/AGENTS.md](../docs/AGENTS.md). Agents call `mailkit`, not the GUI.
