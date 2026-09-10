# Architecture

Mailkit is a **native desktop and mobile email app** with a local engine and a CLI so AI agents can use it. It is not a host, proxy, or provider. Every connection goes from this process to Gmail, Microsoft 365, Yahoo, or a custom IMAP/SMTP server.

The Hallmark UI is shared. Native shells (pywebview on desktop, WKWebView / Android WebView on phones) and the CLI are clients of the same API. Mail logic never lives in an app shell.

```
Desktop app · iOS · Android · CLI / agents
        │  HTTP / SSE / WebSocket / Unix event socket / NDJSON
        ▼
┌─────────────────────────────────────────────┐
│  Daemon (long-running, this machine)         │
│  REST /v1  ·  SSE  ·  WS  ·  webhooks       │
│  Event bus (durable SQLite log + live fan)  │
│  Rules + hook plugins                       │
│  Supervisor: one worker per account         │
└───────────────┬─────────────────────────────┘
                │ plugins
     ┌──────────┼──────────┬────────────┐
     ▼          ▼          ▼            ▼
   IMAP      Gmail API   Graph API   custom
   IDLE      history/    delta/      adapter
             Pub/Sub     notify
```

## Why not cron or full-inbox scan

Each account worker holds a persistent connection (IMAP IDLE) or a native push channel. On EXISTS/FETCH/EXPUNGE, Gmail history, or Graph delta, the worker fetches **only the new or changed UIDs**, normalizes an event, and publishes it. Incremental polling is the last resort and still uses a UID/history/delta cursor, never a full rescan.

## Process split

| Piece | Role |
|---|---|
| Daemon | Owns connections, tokens, event log, API |
| CLI | Native front end for humans and AI agents. Talks to the daemon |
| Desktop / iOS / Android | Native windows. HTTP clients of `/v1` only |
| Encrypted vault | Passwords and OAuth tokens, AES-256-GCM, local only |
| SQLite | Message index, events, subscriptions, rules, cursors |
| Plugins | Providers, auth strategies, watchers, message hooks |

## Watcher preference

1. Gmail: `users.watch` + Pub/Sub pull when configured, else Gmail history + IMAP IDLE
2. Microsoft 365: Graph change notifications when a notify URL exists, else Graph delta + IMAP IDLE
3. Yahoo / custom IMAP: IMAP IDLE
4. Anything else: incremental UID poll with stored cursor

Dropped connections reconnect with exponential backoff. OAuth refresh tokens are renewed before expiry. Graph subscription IDs are renewed on a timer. After downtime the worker backfills only the missing cursor interval.

## Navigation contract

Commands and API paths always name **account**, then **mailbox/section**, then **message**. Unified view is opt-in (`--unified`) and every row still carries `account_id`.
