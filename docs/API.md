# Local API (`/v1`)

Bind: `http://127.0.0.1:8765` (configurable). Auth: `Authorization: Bearer <~/.mailkit/daemon.token>`.

JSON responses:

```json
{ "ok": true, "schema": "mailkit.response.v1", "data": {}, "error": null }
```

On failure `ok` is false and `error` matches `mailkit.error.v1`. Fields are additive; existing names keep their meaning.

## REST

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/pair` | pairing payload for the iOS/Android app (`mailkit.pair.v1`) |
| GET | `/v1/status` | watchers, last event cursor |
| GET/POST/DELETE | `/v1/accounts` `/v1/accounts/{id}` | accounts |
| POST | `/v1/accounts/{id}/test` | IMAP login + folder list |
| GET | `/v1/mailboxes?account=` | section-ordered mailboxes |
| GET | `/v1/messages?account=&mailbox=&unread=&flagged=&tagged=&since=&before=` | list |
| GET | `/v1/messages/{id}` | full message |
| GET | `/v1/messages/search` | search |
| POST | `/v1/messages/{id}/move` | `{ "mailbox": "Archive" }` |
| POST | `/v1/messages/{id}/tag` | `{ "tags": ["claim"] }` |
| POST | `/v1/messages/{id}/flag` `/unflag` `/read` `/unread` | flags |
| POST | `/v1/send` | SMTP send |
| POST | `/v1/reply` | reply to a cached message |
| GET | `/v1/events?cursor=` | page the durable log |
| GET | `/v1/events/stream` | SSE |
| GET | `/v1/events/ws` | WebSocket |
| POST | `/v1/events/ack` | durable subscription ack |
| GET/POST/DELETE | `/v1/subscriptions` | durable filters |
| GET/POST/DELETE | `/v1/webhooks` | HTTP callbacks |
| GET/POST/DELETE | `/v1/rules` | classification / routing |
| GET | `/v1/plugins` | loaded plugin ids |
| POST | `/v1/provider-hooks/graph` | Graph validation + notifications (no auth; Microsoft cannot send the daemon bearer) |

## Real-time

- **SSE** `GET /v1/events/stream?...filters`: `id`, `event`, `data` fields. Last-Event-ID is the event id cursor when the `cursor` query is omitted.
- **WebSocket** `GET /v1/events/ws?token=`: text frames of `mailkit.event.v1`. Client may send `{"op":"subscribe","filter":{...}}`, `{"op":"ack","subscription_id":"...","event_id":"..."}`, `{"op":"ping"}`.
- **Unix socket** `~/.mailkit/events.sock`: newline-delimited JSON events for local agents.
- **CLI** `mailkit events stream` / `mailkit watch`: NDJSON on stdout.
- **Webhooks**: POST of the event JSON, `X-Mailkit-Signature: sha256=...` when a secret is stored. Failed deliveries retry with backoff.

Filter query params (also subscription `filter` objects): `account`, `mailbox`, `sender`, `recipient`, `subject`, `label`, `tag`, `category`, `thread`, `attachment_type`, `event_type`, `rule`.

## Exit codes (CLI)

| Code | Meaning |
|---:|---|
| 0 | ok |
| 1 | error |
| 2 | usage |
| 3 | not found |
| 4 | auth |
| 5 | network |
| 6 | daemon not running |
| 7 | conflict |
| 8 | rate limit |

The CLI maps a live daemon's `error.code` onto these values (`not_found` → 3, `auth` → 4, `network` → 5, `daemon` → 6, `conflict` → 7, `rate_limit` → 8). When `error.code` is missing or generic, HTTP status is used: 401/403 → 4, 404 → 3, 409 → 7, 429 → 8, 5xx → 6. A local "service is not running" check still exits 6 without an HTTP round-trip.
