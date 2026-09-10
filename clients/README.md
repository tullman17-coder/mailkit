# Optional clients

Mailkit’s product is the background engine, CLI, local API, and event contracts.

A graphical desktop app is a replaceable client. It must:

1. Talk only to the local `/v1` API, WebSocket, or SSE stream.
2. Keep IMAP, SMTP, provider tokens, and classification logic in the engine.
3. Treat account → mailbox → message as explicit navigation.
4. Never bundle or hide which account a message belongs to.

`apple/MailKit.xcodeproj` provides a native SwiftUI shell for iPhone, iPad, and Mac.
It stores the engine token in Keychain and opens the system browser for native
OAuth callbacks; the engine exchanges and stores provider credentials.
See [Apple setup and TestFlight](../docs/APPLE.md).
