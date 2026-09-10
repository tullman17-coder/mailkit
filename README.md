# Mailkit

<p align="center">
  <img src="desktop/brand/icon-1024.png" width="180" alt="Mailkit — two T-rexes holding a sealed envelope">
</p>

<p align="center">Native desktop · iOS · Android · CLI for agents · an entity of <a href="https://zermo.org">zermo.org</a></p>

<p align="center">
  <a href="https://github.com/tullman17-coder/mailkit/releases/latest"><strong>Download latest release</strong></a>
  ·
  <a href="https://github.com/tullman17-coder/mailkit/releases/latest">v0.1.1</a>
  ·
  <a href="https://github.com/tullman17-coder/mailkit">source</a>
</p>

Mailkit talks **directly** to your existing Gmail, Outlook / Microsoft 365, Yahoo, and custom-domain IMAP/SMTP accounts. It does not host, proxy, or relay mail through a third party. Credentials stay in an encrypted vault on disk.

The product is a **native desktop app**, **native iOS and Android apps**, and a **CLI** so AI agents can use the same engine:

1. A long-running background **daemon** that keeps a live connection to every account
2. A **CLI** with JSON/NDJSON output and stable exit codes (the agent surface)
3. A versioned local **HTTP / SSE / WebSocket** API
4. A durable **event log** so agents can subscribe, ack, and resume
5. A **plugin** layer for providers, auth, watchers, and classification hooks
6. A **doctor** engine that diagnoses local damage and applies safe repairs in a loop
7. Native **desktop and mobile windows** that are only clients of that API (no mail logic in the UI)

The engine stays on your computer. Phones pair to it. Agents call `mailkit -o json`, not the GUI.

## Quick start

```bash
python3 -m pip install -e .
mailkit service start
mailkit accounts add --address you@gmail.com --auth oauth2 --client-id "$GOOGLE_OAUTH_CLIENT_ID"
mailkit accounts add --address you@company.com --imap-host imap.company.com --smtp-host smtp.company.com
mailkit mailboxes --account you
mailkit messages list --account you --mailbox inbox --unread
mailkit events stream --account you --format ndjson
mailkit doctor --repair
pip install -e ".[desktop]"
mailkit desktop
mailkit pair --lan
mailkit -o json messages list --mailbox inbox
```

App passwords work anywhere OAuth is unavailable:

```bash
mailkit accounts add --address you@ymail.com --auth app_password
```

If discovery guesses wrong, pass hosts explicitly (`--imap-host`, `--imap-port`, `--smtp-host`, `--no-discover`) plus folder overrides in `~/.mailkit/config.toml`.

## Always-on agents

The daemon detects new mail, replies, moves, deletes, and flag changes as they happen:

| Provider | Real-time path |
|---|---|
| Custom IMAP, Yahoo | IMAP IDLE |
| Gmail | IDLE, plus Gmail history / `users.watch` + Pub/Sub pull when configured |
| Microsoft 365 / Outlook | IMAP IDLE, plus Graph delta / change notifications when configured |
| Anything else | Incremental UID/history/delta poll with a stored cursor |

It does **not** cron-scan entire inboxes. After downtime it backfills only the missing cursor interval.

Stream one slice of mail without reading every mailbox:

```bash
mailkit events stream --account work --subject "Invoice" --tag invoice --format ndjson
```

Durable subscriptions survive process restarts:

```bash
mailkit subscriptions add --name invoices --account work --subject "Invoice"
mailkit events stream --cursor evt_last_seen
mailkit events ack sub_xxx evt_yyy
```

Webhooks POST to a URL you control. `--secret` is an HMAC key you generate locally — it is stored in the encrypted vault, never in git. `127.0.0.1` is this machine.

```bash
export WEBHOOK_HMAC="$(openssl rand -hex 24)"
mailkit webhooks add --url http://127.0.0.1:9000/mail --secret "$WEBHOOK_HMAC" --subject Invoice
```

## Command map

Navigation is explicit: **account → mailbox → message**. `--unified` is opt-in and still prints `account_id` on every row.

| Command | Purpose |
|---|---|
| `mailkit service start\|stop\|status\|run\|install` | Daemon |
| `mailkit accounts add\|list\|remove\|show\|test\|discover` | Accounts |
| `mailkit mailboxes` | Inbox / Saved / Sent / Drafts / Archives |
| `mailkit messages list\|get\|search\|tag\|move\|flag` | Mail (`mailkit mail …` is an alias) |
| `mailkit send` / `mailkit reply` | SMTP |
| `mailkit watch` / `mailkit events stream` | Live NDJSON |
| `mailkit subscriptions add` | Durable filters |
| `mailkit webhooks add` | HTTP callbacks |
| `mailkit rules add` | Classification / routing |
| `mailkit plugins` / `mailkit schema` | Introspection |
| `mailkit doctor` / `mailkit doctor watchdog` | Diagnose, repair, keep the daemon alive |
| `mailkit desktop` | Native desktop window (starts the engine if needed) |
| `mailkit pair [--lan\|--off]` | Pair the iOS/Android app (or an agent) to this engine |

Global flags: `-o json|ndjson|text`, `--account`, `--unified`, `--home`.

Machine output uses `mailkit.response.v1`. Events use `mailkit.event.v1`. See `docs/API.md` and `schemas/v1/`.

## Sections

Each account is a separate tab. Inside an account the default sections are:

- **Inbox**
- **Saved** (IMAP `\Flagged` / Gmail Starred)
- **Sent**
- **Drafts**
- **Archives**

Roles come from RFC 6154 SPECIAL-USE, then well-known names (`[Gmail]/Sent Mail`, `Sent Items`, …). Overrides live under `accounts.<id>.folders`.

Filters: `--unread`, `--flagged`, `--tagged`, `--since`, `--before`.

## Rules

Higher `priority` runs first. Matching stops when a rule sets `stop = true`. Built-in actions never delete mail.

```bash
mailkit rules add --name invoices --priority 200 --stop \
  --match '{"from_domain":["vendor.example"],"subject_contains":["Invoice"]}' \
  --actions '{"tag":["invoice"],"flag":true}'
```

Custom classifiers are plugins (see `examples/plugins/invoices.py`).

## Layout on disk

```
~/.mailkit/
  config.toml      # no secrets
  vault.enc        # AES-256-GCM credentials
  master.key       # or $MAILKIT_MASTER_KEY
  mailkit.db       # index + event log
  daemon.token     # local API bearer token
  plugins/         # drop-in .py plugins
  logs/
```

## Install as a service

```bash
mailkit service install --target systemd   # Linux user unit
mailkit service install --target launchd   # macOS
```

The engine is ordinary Python. It runs on macOS, Linux, or a server. Native iOS and Android apps in `clients/` talk to that engine over the LAN.

## Native apps

```bash
pip install -e ".[desktop]"
mailkit desktop                 # native window, not a browser tab
mailkit pair --lan              # phone: open the mailkit:// link or paste URL + token
```

iOS: `clients/ios`. Android: `clients/android`. Both load the same workbench as the desktop window with `?shell=mobile`. See [clients/README.md](clients/README.md) and [docs/AGENTS.md](docs/AGENTS.md).

## Develop

```bash
python3 -m pip install -e ".[dev]"
make test
```

Tests cover IMAP folder mapping, RFC 822 parsing, classification, discovery, the durable event log, and CLI help/schema contracts.

## Desktop, phone, and macOS disk image

The window is a Hallmark workbench: account rail, mailbox, message list, reading pane. Navigation is always account → mailbox → message. The mark is two T-rexes holding a sealed envelope on burgundy.

```bash
pip install -e ".[desktop]"
mailkit desktop
```

Build a drag-to-Applications DMG on a Mac:

```bash
bash scripts/build-dmg.sh
# → dist-dmg/Mailkit-0.1.1.dmg
```

**macOS download:** [Mailkit 0.1.1](https://github.com/tullman17-coder/mailkit/releases/latest) · always-current: [latest release](https://github.com/tullman17-coder/mailkit/releases/latest)

Do not run the v0.1.0 Mac app. Opening it spawned copies of itself until the machine locked up. 0.1.1+ treats a spawned child as the engine, never another window.

Push a new tag and the [release workflow](.github/workflows/release.yml) attaches the DMG:

```bash
git tag v0.1.1
git push origin v0.1.1
```

Releases are **Developer ID signed and notarized**. See [docs/SIGNING.md](docs/SIGNING.md).

## GitHub

Source: [github.com/tullman17-coder/mailkit](https://github.com/tullman17-coder/mailkit)  
Release (DMG): [github.com/tullman17-coder/mailkit/releases/latest](https://github.com/tullman17-coder/mailkit/releases/latest)

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Agents (CLI)](docs/AGENTS.md)
- [Native clients](clients/README.md)
- [Configuration](docs/CONFIG.md)
- [API](docs/API.md)
- [Events](docs/EVENTS.md)
- [Plugins](docs/PLUGINS.md)
- [Doctor](docs/DOCTOR.md)
- [JSON schemas](schemas/v1/)
