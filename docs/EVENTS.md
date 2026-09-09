# Event contract (`mailkit.event.v1`)

Every event has a stable `id`, `ts`, `account_id`, `provider_id`, `mailbox`, `type`, `thread_id`, and enough metadata to fetch the full message via `GET /v1/messages/{id}` or `mailkit messages get`.

```json
{
  "schema": "mailkit.event.v1",
  "id": "evt_...",
  "ts": "2026-08-22T15:04:05Z",
  "account_id": "work",
  "provider_id": "imap",
  "mailbox": "INBOX",
  "type": "message.created",
  "thread_id": "thr_...",
  "idempotency_key": "hex",
  "message": { "schema": "mailkit.message.v1", "id": "msg_...", "subject": "..." },
  "data": {}
}
```

## Types

`message.created` `message.updated` `message.deleted` `message.moved` `message.flagged` `message.unflagged` `message.read` `message.unread` `message.tagged` `mailbox.created` `mailbox.deleted` `account.connected` `account.disconnected` `account.auth_error` `service.backfill.started` `service.backfill.completed` `service.heartbeat`

New types may be added. Clients must ignore unknown types.

## Durability

1. Events are appended to SQLite with a unique `idempotency_key`. Duplicates are dropped.
2. `mailkit subscriptions add` stores a filter + cursor (last event id).
3. On reconnect, replay `GET /v1/events?cursor=<last_acked>` using the cursor stored on the subscription.
4. `POST /v1/events/ack` records acknowledgement and advances `subscriptions.cursor` to the acked event id when that event is newer. Failed webhook deliveries retry (5 attempts, exponential backoff).
5. After process restart the daemon continues from stored IMAP UID / Gmail historyId / Graph deltaLink, so only the down interval is backfilled.

## Agent example

Subscribe to one invoice stream without scanning other inboxes:

```
mailkit subscriptions add --name invoices --account work --subject "Invoice" --tag invoice
mailkit events stream --account work --subject "Invoice" --format ndjson
```
