# Agents

Mailkit is a native desktop and mobile app. AI agents do not click the GUI. They use the same local engine through the **CLI** (preferred) or the versioned `/v1` HTTP API.

```bash
mailkit service start
mailkit -o json accounts list
mailkit -o json messages list --account work --mailbox inbox --unread
mailkit -o json messages get --account work --mailbox INBOX --body 12345
mailkit events stream --account work --subject "Invoice" -o ndjson
```

`-o json` wraps every successful payload in `mailkit.response.v1`. Streams are NDJSON of `mailkit.event.v1`. Exit codes are stable (`docs/API.md`).

## Do

- Name **account → mailbox → message** on every command.
- Resume with `mailkit events stream --cursor evt_…` and `mailkit events ack`.
- Read `mailkit schema event` / `mailkit schema message` before assuming fields.
- Pair a phone or another machine with `mailkit pair --lan` (token still required).

## Do not

- Scrape the desktop or mobile window.
- Store vault secrets in git or prompt logs.
- Scan the whole inbox on a timer. Subscribe, then act on events.

## Durable loop

```bash
mailkit subscriptions add --name invoices --account work --subject "Invoice"
mailkit events stream --cursor evt_last_seen -o ndjson
# after handling one event:
mailkit events ack sub_xxx evt_yyy
```
