# Configuration

Home directory: `~/.mailkit` or `$MAILKIT_HOME`.

| File | Purpose |
|---|---|
| `config.toml` | Accounts, daemon bind, folder overrides. Mode `0600`. No secrets. |
| `vault.enc` | AES-256-GCM JSON: passwords, refresh tokens, webhook secrets |
| `master.key` | 32-byte vault key, mode `0600`. Override with `MAILKIT_MASTER_KEY` (hex or raw) |
| `mailkit.db` | SQLite index + durable event log |
| `daemon.token` | Bearer token for the local API |
| `plugins/*.py` | User plugins (`register(registry)` entry) |
| `logs/mailkit.log` | Rotating logs. Bodies are not logged. |

## config.toml

See `examples/config.toml`. Important keys:

```toml
[daemon]
host = "127.0.0.1"   # loopback by default
port = 8765
allow_remote = false   # true when phones on this Wi-Fi need mailkit pair --lan

[defaults]
account = "work"     # used when --account is omitted and only if set

[accounts.work]
address = "you@company.com"
provider = "auto"    # auto | imap | gmail | graph | yahoo
auth = "password"    # password | app_password | oauth2
watch = "auto"       # auto | idle | gmail_push | graph_push | poll

[accounts.work.imap]
host = "imap.company.com"
port = 993
tls = true

[accounts.work.smtp]
host = "smtp.company.com"
port = 587
starttls = true

[accounts.work.folders]
inbox = "INBOX"
sent = "Sent"
# saved/starred is a view over \Flagged unless a Starred folder exists
```

Manual IMAP/SMTP fields override discovery. Discovery uses well-known provider maps, MX, then SRV `_imaps._tcp` / `_submission._tcp`, then `imap.` / `smtp.` guesses.

OAuth `client_secret` belongs in the vault (`mailkit accounts add --client-secret ...`), not in config.
