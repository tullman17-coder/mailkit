# Doctor

Mailkit heals itself. The doctor is a diagnosis + safe-repair engine, not a
hosted service and not a mail proxy.

## What it checks

| Check | Auto-repair |
|---|---|
| Data directory exists and is `0700` | yes |
| Secret files are `0600` | yes |
| `config.toml` parses; default account exists | yes (never invents addresses) |
| Vault decrypts | create if missing; **never overwrite a locked vault** |
| Enabled accounts have passwords/tokens | no (needs you) |
| IMAP/SMTP hosts for well-known providers | yes |
| SQLite integrity + schema | checkpoint; will not wipe a corrupt DB |
| Stale pid / stolen pid | yes |
| Stale Unix sockets | yes |
| API token | yes |
| Built-in plugins load | no |
| Daemon process | only with `--start` or watchdog |
| Dead account watchers | yes, inside the daemon |
| Expired OAuth access tokens | yes, if a refresh token exists |
| Event log size | trim to retention |

Unsafe actions are refused: vault overwrite, mail delete, guessing custom-domain hosts, launching the daemon unless you asked.

## Commands

```bash
mailkit doctor                 # diagnose
mailkit doctor --repair        # apply safe repairs
mailkit doctor --repair --start
mailkit doctor loop --repair --interval 60
mailkit doctor watchdog        # loop + repair + keep the daemon alive
```

JSON: `mailkit doctor -o json`. HTTP: `GET /v1/doctor`, `POST /v1/doctor/repair`.

The daemon runs the same engine a few seconds after boot and then every
`daemon.doctor_interval` seconds (`doctor_repair = true` by default).

## Watchdog script

`scripts/mailkit-doctor` is a standalone process you can run under systemd or
launchd next to the engine:

```bash
python3 scripts/mailkit-doctor --interval 30
```

If the daemon dies, the watchdog starts it again. Watcher threads that exit
are restarted by the supervisor with backoff, and again by the in-process
doctor if they stay dead.

## Exit codes

- `0` — no remaining error/critical findings
- `1` — at least one unrepaired error
