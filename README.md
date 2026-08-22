# Mailkit

<p align="center">
  <img src="desktop/brand/icon-1024.png" width="180" alt="Mailkit — two T-rexes holding a sealed envelope">
</p>

<p align="center">Local email engine · CLI · desktop · an entity of <a href="https://zermo.org">zermo.org</a></p>

<p align="center">
  <a href="https://github.com/tullman17-coder/mailkit/releases/latest"><strong>Download latest release</strong></a>
  ·
  <a href="https://github.com/tullman17-coder/mailkit/releases/tag/v0.1.0">v0.1.0</a>
  ·
  <a href="https://github.com/tullman17-coder/mailkit">source</a>
</p>

Mailkit talks **directly** to your existing Gmail, Outlook / Microsoft 365, Yahoo, and custom-domain IMAP/SMTP accounts. It does not host, proxy, or relay mail through a third party. Credentials stay in an encrypted vault on disk.

Mailkit talks **directly** to your existing Gmail, Outlook / Microsoft 365, Yahoo, and custom-domain IMAP/SMTP accounts. It does not host, proxy, or relay mail through a third party. Credentials stay in an encrypted vault on disk.

The product is:

1. A long-running background **daemon** that keeps a live connection to every account
2. A **CLI** with JSON/NDJSON output and stable exit codes
3. A versioned local **HTTP / SSE / WebSocket** API
4. A durable **event log** so agents can subscribe, ack, and resume
5. A **plugin** layer for providers, auth, watchers, and classification hooks
6. A **doctor** engine that diagnoses local damage and applies safe repairs in a loop
7. A **desktop window** that is only a client of that API (no mail logic in the UI)

A graphical desktop app is a replaceable client. The engine remains the product.

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

Stream one slice of mail (for example Empire Today claims) without reading every mailbox:

```bash
mailkit events stream --account work --subject "Empire Today" --tag claim --format ndjson
```

Durable subscriptions survive process restarts:

```bash
mailkit subscriptions add --name empire --account work --subject "Empire Today"
mailkit events stream --cursor evt_last_seen
mailkit events ack sub_xxx evt_yyy
```

Webhooks (local or remote) get the same JSON with HMAC signatures and retries:

```bash
mailkit webhooks add --url http://127.0.0.1:9000/mail --secret "$HOOK_SECRET" --subject "Empire Today"
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
| `mailkit desktop` | Open the local window (starts the engine if needed) |

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
mailkit rules add --name empire --priority 200 --stop \
  --match '{"from_domain":["empiretoday.com"],"subject_contains":["claim"]}' \
  --actions '{"tag":["empire-today","claim"],"flag":true}'
```

Custom classifiers are plugins (see `examples/plugins/empire_today.py`).

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

The engine is ordinary Python. It runs on macOS, Linux, or a server. There is no macOS-only core.

## Develop

```bash
python3 -m pip install -e ".[dev]"
make test
```

Tests cover IMAP folder mapping, RFC 822 parsing, classification, discovery, the durable event log, and CLI help/schema contracts.

## Desktop and macOS disk image

The window is a Hallmark workbench: account rail, mailbox, message list, reading pane. Navigation is always account → mailbox → message. The mark is two T-rexes holding a sealed envelope on burgundy.

```bash
pip install -e ".[desktop]"
mailkit desktop
```

Build a drag-to-Applications DMG on a Mac:

```bash
bash scripts/build-dmg.sh
# → dist-dmg/Mailkit-0.1.0.dmg
```

**macOS download:** [Mailkit 0.1.0](https://github.com/tullman17-coder/mailkit/releases/tag/v0.1.0) · always-current: [latest release](https://github.com/tullman17-coder/mailkit/releases/latest)

Push a new tag and the [release workflow](.github/workflows/release.yml) attaches the DMG:

```bash
git tag v0.1.1
git push origin v0.1.1
```

Unsigned builds need a right-click → Open the first time (Gatekeeper).

## GitHub

Source: [github.com/tullman17-coder/mailkit](https://github.com/tullman17-coder/mailkit)  
Release (DMG): [github.com/tullman17-coder/mailkit/releases/latest](https://github.com/tullman17-coder/mailkit/releases/latest)

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Configuration](docs/CONFIG.md)
- [API](docs/API.md)
- [Events](docs/EVENTS.md)
- [Plugins](docs/PLUGINS.md)
- [Doctor](docs/DOCTOR.md)
- [JSON schemas](schemas/v1/)
