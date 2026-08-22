# Optional clients

Mailkit’s product is the background engine, CLI, local API, and event contracts.

A graphical desktop app is a replaceable client. It must:

1. Talk only to the local `/v1` API, WebSocket, or SSE stream.
2. Contain no IMAP, SMTP, OAuth, credential, or classification logic.
3. Treat account → mailbox → message as explicit navigation.
4. Never bundle or hide which account a message belongs to.

No macOS-native, Electron, or Swift core is required or implied.
