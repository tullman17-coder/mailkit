# Plugins

Three extension points. None of them require changing core code.

| Type | `plugin_type` | Job |
|---|---|---|
| Provider connector | `provider` | Talk to a mail system |
| Auth strategy | `auth` | Password, XOAUTH2, or a custom SASL/API login |
| Watcher | `watcher` | IDLE, Gmail push, Graph notify, poll |
| Hook | `hook` | Classify, tag, route, downstream action |

Load order:

1. Built-ins (`imap`, `gmail`, `graph`, `yahoo`, `password`, `oauth2`, `idle`, `poll`, `gmail_push`, `graph_push`)
2. Packaging entry points: `mailkit.providers`, `mailkit.auth`, `mailkit.hooks`, `mailkit.watchers`
3. `~/.mailkit/plugins/*.py` exposing `register(registry)` or `PLUGIN`

## Provider

```python
class AcmePlugin:
    plugin_type = "provider"
    id = "acme"
    label = "Acme Mail"

    def supports(self, account):
        return account.provider == "acme"

    def create(self, account, secrets, *, store=None):
        return AcmeProvider(account, secrets, store=store)
```

The object returned by `create` must implement `connect`, `list_mailboxes`, `list_messages`, `get_message`, `search`, `move`, `set_flags`, `send`, `close`, and `capabilities()`.

## Auth

Implement `prepare_imap`, `prepare_smtp`, and `refresh`. `oauth2` already covers Gmail and Microsoft XOAUTH2.

## Hook

```python
class InvoiceHook:
    plugin_type = "hook"
    id = "invoices"
    priority = 50  # higher runs first; rules engine is 1000

    def process(self, ctx):
        if "invoice" in (ctx.message.subject or "").lower():
            return HookAction(tag=["invoice"])
        return None
```

Hooks must not delete or forward mail unless you add that action yourself. Built-in rules strip `delete` / `purge` / `drop`.

See `examples/plugins/empire_today.py`.
