"""HTTP route table for the local v1 API."""

from __future__ import annotations

import json
import threading
from typing import Any
from urllib.parse import unquote

from mailkit.api.http import App, Response, json_response
from mailkit.compose import build_message, reply_subject
from dataclasses import fields

from mailkit.config import AccountConfig, FolderSettings, ImapSettings, SmtpSettings, OAuthSettings, save_config
from mailkit.discovery import discover
from mailkit.errors import NotFoundError, UsageError
from mailkit.ids import idempotency_key, new_id
from mailkit.models import (
    FLAG_MUTATION_EVENTS,
    Event,
    EventFilter,
    Mailbox,
    apply_flag_mutation,
    fail,
    ok,
    utcnow,
)
from mailkit.rules import Rule, RulesEngine, event_filter_match


def dispatch(app: App, method: str, path: str, qs: dict, body: dict, handler) -> Any:
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ok({"service": "mailkit", "api": "v1"})
    if parts[0] != "v1":
        raise UsageError("API is versioned under /v1/")
    rest = parts[1:]
    key = (method.upper(), rest[0] if rest else "")
    if method == "GET" and rest == ["health"]:
        return ok({"status": "ok", "ts": utcnow()})
    if method == "GET" and rest == ["status"]:
        return ok(_status(app))
    if rest[:1] == ["doctor"]:
        return _doctor(app, method, rest[1:], qs, body)
    if rest[:1] == ["accounts"]:
        return _accounts(app, method, rest[1:], qs, body)
    if rest[:1] == ["mailboxes"]:
        return _mailboxes(app, method, rest[1:], qs, body)
    if rest[:1] == ["messages"]:
        return _messages(app, method, rest[1:], qs, body)
    if rest == ["send"] and method == "POST":
        return _send(app, body)
    if rest == ["reply"] and method == "POST":
        return _reply(app, body)
    if rest[:1] == ["events"]:
        return _events(app, method, rest[1:], qs, body, handler)
    if rest[:1] == ["subscriptions"]:
        return _subscriptions(app, method, rest[1:], qs, body)
    if rest[:1] == ["webhooks"]:
        return _webhooks(app, method, rest[1:], qs, body)
    if rest[:1] == ["rules"]:
        return _rules(app, method, rest[1:], qs, body)
    if rest[:1] == ["plugins"] and method == "GET":
        return ok(_plugins(app))
    if rest[:1] == ["discover"] and method in {"GET", "POST"}:
        address = body.get("address") or (qs.get("address") or [""])[0]
        return ok(discover(address).to_dict())
    if rest == ["provider-hooks", "graph"] and method == "POST":
        # Graph validationToken handshake + notifications.
        token = (qs.get("validationToken") or [None])[0]
        if token:
            return Response(status=200, body=token.encode(), headers={"Content-Type": "text/plain"})
        for note in body.get("value") or []:
            app.bus.publish(
                __import__("mailkit.models", fromlist=["Event"]).Event(
                    id=new_id("evt"),
                    account_id=note.get("clientState") or "",
                    provider_id="graph",
                    type="message.updated",
                    data={"graph_notification": note},
                )
            )
        return ok({"accepted": True})
    raise NotFoundError(f"No route for {method} {path}")


def _status(app: App) -> dict:
    return {
        "schema": "mailkit.status.v1",
        "started_at": app.started_at,
        "accounts": app.supervisor.status(),
        "event_cursor": app.runtime.store.last_event_id(),
        "subscriptions": len(app.runtime.store.list_subscriptions()),
    }


def _account_view(acc: AccountConfig) -> dict:
    return {
        "schema": "mailkit.account.v1",
        "id": acc.id,
        "name": acc.display_name(),
        "address": acc.address,
        "provider": acc.provider,
        "auth": acc.auth,
        "enabled": acc.enabled,
        "watch": acc.watch,
        "imap": dict(acc.imap.__dict__),
        "smtp": dict(acc.smtp.__dict__),
        "folders": acc.folders.overrides(),
    }


def _accounts(app: App, method: str, rest: list[str], qs: dict, body: dict):
    if method == "GET" and not rest:
        return ok([_account_view(a) for a in app.runtime.config.accounts.values()])
    if method == "POST" and not rest:
        acc = _account_from_body(body)
        app.runtime.config.accounts[acc.id] = acc
        save_config(app.runtime.config, app.runtime.root)
        secrets = {k: body[k] for k in ("password", "username", "refresh_token", "access_token", "client_secret") if body.get(k)}
        if secrets:
            app.runtime.vault.put_account(acc.id, secrets)
        if acc.enabled:
            app.supervisor.start_account(acc.id)
        return ok(_account_view(acc))
    if not rest:
        raise UsageError("Missing account id")
    account_id = unquote(rest[0])
    acc = app.runtime.config.accounts.get(account_id)
    if not acc:
        raise NotFoundError(f"Unknown account: {account_id}")
    if method == "GET" and len(rest) == 1:
        return ok(_account_view(acc))
    if method == "DELETE" and len(rest) == 1:
        app.supervisor.stop_account(account_id)
        app.runtime.config.accounts.pop(account_id, None)
        save_config(app.runtime.config, app.runtime.root)
        app.runtime.vault.delete_account(account_id)
        return ok({"removed": account_id})
    if rest[1:] == ["test"] and method == "POST":
        provider, _, _ = app.runtime.provider_for(account_id)
        provider.connect()
        boxes = provider.list_mailboxes()
        provider.close()
        return ok({"ok": True, "mailboxes": [b.name for b in boxes]})
    if rest[1:] == ["mailboxes"] and method == "GET":
        provider, _, _ = app.runtime.provider_for(account_id)
        boxes = provider.list_mailboxes()
        return ok([b.__dict__ if hasattr(b, "__dict__") else b for b in boxes])
    raise NotFoundError("Unknown accounts route")


def _fill(cls, *dicts: dict):
    merged: dict = {}
    for data in dicts:
        if data:
            merged.update(data)
    allowed = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in merged.items() if k in allowed and v is not None}
    if "port" in kwargs:
        kwargs["port"] = int(kwargs["port"])
    return cls(**kwargs)


def _account_from_body(body: dict) -> AccountConfig:
    address = body.get("address") or ""
    if not address:
        raise UsageError("address is required")
    acc_id = body.get("id") or address.split("@")[0].replace(".", "-")
    discovered = None
    if body.get("discover", True) and not (body.get("imap") or {}).get("host"):
        discovered = discover(address)
    imap = _fill(ImapSettings, discovered.to_dict()["imap"] if discovered else {}, body.get("imap") or {})
    smtp = _fill(SmtpSettings, discovered.to_dict()["smtp"] if discovered else {}, body.get("smtp") or {})
    oauth_body = body.get("oauth") or {}
    oauth = OAuthSettings(
        client_id=oauth_body.get("client_id") or "",
        tenant=oauth_body.get("tenant") or "common",
        token_url=oauth_body.get("token_url") or (discovered.oauth_token_url if discovered else ""),
        auth_url=oauth_body.get("auth_url") or (discovered.oauth_auth_url if discovered else ""),
        scopes=oauth_body.get("scopes") or (discovered.oauth_scopes if discovered else []),
        pubsub_topic=oauth_body.get("pubsub_topic") or "",
        pubsub_subscription=oauth_body.get("pubsub_subscription") or "",
        graph_notify_url=oauth_body.get("graph_notify_url") or "",
    )
    folders = FolderSettings(**(body.get("folders") or {}))
    provider = body.get("provider") or (discovered.provider_id if discovered else "imap")
    auth = body.get("auth") or (discovered.auth_hint if discovered else "password")
    return AccountConfig(
        id=acc_id,
        name=body.get("name") or address,
        address=address,
        provider=provider,
        auth=auth if auth != "app_password" else "password",
        enabled=body.get("enabled", True),
        watch=body.get("watch") or "auto",
        poll_interval=int(body.get("poll_interval") or 45),
        imap=imap,
        smtp=smtp,
        oauth=oauth,
        folders=folders,
    )


def _mailboxes(app: App, method: str, rest: list[str], qs: dict, body: dict):
    account_id = (qs.get("account") or [None])[0]
    acc = app.runtime.config.require_account(account_id)
    provider, _, _ = app.runtime.provider_for(acc.id)
    boxes = provider.list_mailboxes()
    return ok([_box_dict(b, acc.id) for b in boxes])


def _box_dict(box, account_id: str) -> dict:
    if hasattr(box, "__dict__"):
        data = dict(box.__dict__)
        data["account_id"] = account_id
        data["schema"] = "mailkit.mailbox.v1"
        return data
    return box


def _qbool(qs, name):
    raw = (qs.get(name) or [None])[0]
    if raw is None:
        return None
    return raw.lower() in {"1", "true", "yes"}


def _messages(app: App, method: str, rest: list[str], qs: dict, body: dict):
    if rest and rest[0] == "search" and method in {"GET", "POST"}:
        params = {**{k: v[0] if v else "" for k, v in qs.items()}, **body}
        return _list_messages(app, params)
    if rest and method == "GET" and rest[0] not in {"search"}:
        msg = app.runtime.store.get_message(rest[0])
        if msg:
            return ok(msg)
        # live fetch
        account_id = (qs.get("account") or [None])[0]
        mailbox = (qs.get("mailbox") or ["INBOX"])[0]
        acc = app.runtime.config.require_account(account_id)
        provider, _, _ = app.runtime.provider_for(acc.id)
        fetched = provider.get_message(mailbox, rest[0], peek=True)
        return ok(fetched.to_dict())
    if rest and method == "POST" and len(rest) >= 2:
        msg_id = rest[0]
        action = rest[1]
        return _mutate_message(app, msg_id, action, body, qs)
    if method == "GET":
        params = {k: v[0] if v else "" for k, v in qs.items()}
        return _list_messages(app, params)
    raise UsageError("Unsupported messages route")


def _list_messages(app: App, params: dict):
    unified = str(params.get("unified") or "").lower() in {"1", "true", "yes"}
    account_id = params.get("account")
    mailbox = params.get("mailbox") or params.get("section") or "INBOX"
    unread = params.get("unread")
    flagged = params.get("flagged")
    tagged = params.get("tagged") or params.get("tag")
    limit = int(params.get("limit") or 50)
    if unified:
        rows = app.runtime.store.list_messages(
            unread=_truth(unread),
            flagged=_truth(flagged),
            tagged=tagged or None,
            since=params.get("since") or None,
            before=params.get("before") or None,
            query=params.get("query") or params.get("text") or None,
            limit=limit,
        )
        return ok(rows)
    acc = app.runtime.config.require_account(account_id)
    # Resolve section names like inbox/saved/sent
    provider, account, _ = app.runtime.provider_for(acc.id)
    boxes = provider.list_mailboxes()
    folder = mailbox
    if mailbox.lower() in {"inbox", "saved", "sent", "drafts", "archives", "trash", "junk"}:
        role = mailbox.lower()
        match = next((b for b in boxes if getattr(b, "role", "") == role), None)
        if role == "saved" and (not match or match.name.upper() == "INBOX"):
            folder = match.name if match else "INBOX"
            params["flagged"] = "true"
        elif match:
            folder = match.name
    live = str(params.get("live") or "true").lower() != "false"
    if live:
        try:
            messages = provider.list_messages(
                folder,
                unread=_truth(params.get("unread")),
                flagged=_truth(params.get("flagged")),
                since=params.get("since"),
                before=params.get("before"),
                from_=params.get("from") or params.get("sender"),
                subject=params.get("subject"),
                text=params.get("query") or params.get("text"),
                limit=limit,
            )
            rows = [m.summary() for m in messages]
            if tagged:
                rows = [r for r in rows if tagged in (r.get("tags") or [])]
            return ok(rows)
        except Exception:
            pass
    rows = app.runtime.store.list_messages(
        account_id=acc.id,
        mailbox=folder,
        unread=_truth(params.get("unread")),
        flagged=_truth(params.get("flagged")),
        tagged=tagged or None,
        since=params.get("since") or None,
        before=params.get("before") or None,
        query=params.get("query") or params.get("text") or None,
        limit=limit,
    )
    return ok(rows)


def _truth(value) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes"}


def _mutate_message(app: App, msg_id: str, action: str, body: dict, qs: dict):
    stored = app.runtime.store.get_message(msg_id)
    account_id = (stored or {}).get("account_id") or (qs.get("account") or [None])[0]
    mailbox = (stored or {}).get("mailbox") or (qs.get("mailbox") or ["INBOX"])[0]
    native = (stored or {}).get("native_id") or (stored or {}).get("uid") or body.get("native_id")
    acc = app.runtime.config.require_account(account_id)
    provider, _, _ = app.runtime.provider_for(acc.id)
    if action == "move":
        dest = body.get("mailbox") or body.get("dest")
        if not dest:
            raise UsageError("move requires mailbox")
        provider.move(mailbox, str(native), dest)
        if stored:
            stored["mailbox"] = dest
            app.runtime.store.conn.execute(
                "UPDATE messages SET mailbox=?, updated_at=? WHERE id=?",
                (dest, utcnow(), msg_id),
            )
            app.runtime.store.conn.commit()
        return ok({"moved": msg_id, "mailbox": dest, "account_id": acc.id})
    if action in {"flag", "unflag", "read", "unread"}:
        add, remove = [], []
        if action == "flag":
            add = ["Flagged"]
        elif action == "unflag":
            remove = ["Flagged"]
        elif action == "read":
            add = ["Seen"]
        else:
            remove = ["Seen"]
        provider.set_flags(mailbox, str(native), add=add, remove=remove)
        event_type = FLAG_MUTATION_EVENTS[action]
        snapshot = apply_flag_mutation(
            stored
            or {
                "id": msg_id,
                "account_id": acc.id,
                "provider_id": acc.provider,
                "mailbox": mailbox,
                "native_id": str(native) if native is not None else "",
                "flags": [],
            },
            action,
        )
        app.bus.publish(
            Event(
                id=new_id("evt"),
                ts=utcnow(),
                account_id=acc.id,
                provider_id=acc.provider,
                mailbox=mailbox,
                type=event_type,
                thread_id=snapshot.get("thread_id") or "",
                message=snapshot,
                data={"action": action},
                idempotency_key=idempotency_key(
                    acc.id,
                    mailbox,
                    event_type,
                    str(native or msg_id),
                ),
            )
        )
        return ok({"id": msg_id, "action": action})
    if action == "tag":
        tags = body.get("tags") or body.get("tag") or []
        if isinstance(tags, str):
            tags = [tags]
        current = (stored or {}).get("tags") or []
        updated = app.runtime.store.set_tags(msg_id, list(current) + list(tags))
        return ok(updated or {"id": msg_id, "tags": tags})
    if action == "untag":
        tags = body.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        current = [t for t in ((stored or {}).get("tags") or []) if t not in tags]
        updated = app.runtime.store.set_tags(msg_id, current)
        return ok(updated)
    raise UsageError(f"Unknown message action {action}")


def _send(app: App, body: dict):
    acc = app.runtime.config.require_account(body.get("account"))
    to = body.get("to") or []
    if isinstance(to, str):
        to = [to]
    if not to:
        raise UsageError("to is required")
    msg = build_message(
        from_addr=body.get("from") or acc.address,
        to=to,
        cc=body.get("cc") or [],
        bcc=body.get("bcc") or [],
        subject=body.get("subject") or "",
        body=body.get("body") or body.get("text") or "",
        html=body.get("html"),
    )
    provider, _, _ = app.runtime.provider_for(acc.id)
    mid = provider.send(acc.address, to + list(body.get("cc") or []) + list(body.get("bcc") or []), msg.as_bytes())
    return ok({"message_id": mid, "account_id": acc.id})


def _reply(app: App, body: dict):
    msg_id = body.get("id") or body.get("message_id")
    stored = app.runtime.store.get_message(msg_id) if msg_id else None
    if not stored:
        raise NotFoundError("Original message not found; pass id of a cached message")
    acc = app.runtime.config.require_account(body.get("account") or stored.get("account_id"))
    to = body.get("to")
    if not to:
        to = [a.get("address") for a in stored.get("from") or [] if a.get("address")]
    if isinstance(to, str):
        to = [to]
    refs = stored.get("references") or []
    if stored.get("message_id"):
        refs = list(refs) + [stored["message_id"]]
    msg = build_message(
        from_addr=acc.address,
        to=to,
        subject=body.get("subject") or reply_subject(stored.get("subject") or ""),
        body=body.get("body") or body.get("text") or "",
        html=body.get("html"),
        in_reply_to=stored.get("message_id"),
        references=refs,
    )
    provider, _, _ = app.runtime.provider_for(acc.id)
    mid = provider.send(acc.address, to, msg.as_bytes())
    native = stored.get("native_id") or stored.get("uid")
    if native:
        try:
            provider.set_flags(stored.get("mailbox") or "INBOX", str(native), add=["Answered"])
        except Exception:
            pass
    return ok({"message_id": mid, "in_reply_to": stored.get("message_id")})


def _events(app: App, method: str, rest: list[str], qs: dict, body: dict, handler):
    if rest == ["stream"] and method == "GET":
        filt = EventFilter.from_dict({k: v[0] if len(v) == 1 else v for k, v in qs.items() if k not in {"token", "cursor"}})
        cursor = (qs.get("cursor") or [None])[0]
        stop = threading.Event()

        def stream(wfile):
            for ev in app.bus.stream(filt, cursor, stop):
                chunk = f"id: {ev.get('id')}\nevent: {ev.get('type')}\ndata: {json.dumps(ev)}\n\n"
                wfile.write(chunk.encode("utf-8"))
                wfile.flush()

        return Response(stream=stream)
    if rest == ["ack"] and method == "POST":
        sub = body.get("subscription_id")
        event_id = body.get("event_id")
        if not sub or not event_id:
            raise UsageError("subscription_id and event_id required")
        app.runtime.store.ack(sub, event_id, "acked")
        return ok({"acked": event_id})
    if method == "GET" and not rest:
        filt = EventFilter.from_dict({k: v[0] if len(v) == 1 else v for k, v in qs.items() if k not in {"token", "cursor", "limit"}})
        cursor = (qs.get("cursor") or [None])[0]
        limit = int((qs.get("limit") or ["50"])[0])
        return ok(app.bus.replay(filt, cursor, limit=limit))
    raise NotFoundError("Unknown events route")


def _subscriptions(app: App, method: str, rest: list[str], qs: dict, body: dict):
    if method == "GET" and not rest:
        rows = []
        for s in app.runtime.store.list_subscriptions():
            item = dict(s)
            item["filter"] = json.loads(item.pop("filter_json") or "{}")
            item["schema"] = "mailkit.subscription.v1"
            rows.append(item)
        return ok(rows)
    if method == "POST" and not rest:
        sub_id = body.get("id") or new_id("sub")
        filt = EventFilter.from_dict(body.get("filter") or body)
        app.runtime.store.save_subscription(
            sub_id,
            body.get("name") or sub_id,
            filt,
            durable=body.get("durable", True),
            ack_required=body.get("ack_required", True),
            cursor=body.get("cursor") or app.runtime.store.last_event_id(),
        )
        return ok({"id": sub_id, "filter": filt.to_dict()})
    if rest and method == "DELETE":
        app.runtime.store.delete_subscription(rest[0])
        return ok({"removed": rest[0]})
    raise UsageError("Unknown subscriptions route")


def _webhooks(app: App, method: str, rest: list[str], qs: dict, body: dict):
    if method == "GET" and not rest:
        rows = []
        for h in app.runtime.store.list_webhooks():
            item = dict(h)
            item["filter"] = json.loads(item.pop("filter_json") or "{}")
            item["schema"] = "mailkit.webhook.v1"
            rows.append(item)
        return ok(rows)
    if method == "POST" and not rest:
        hook_id = body.get("id") or new_id("wh")
        url = body.get("url")
        if not url:
            raise UsageError("url is required")
        filt = EventFilter.from_dict(body.get("filter") or {})
        app.runtime.store.save_webhook(hook_id, body.get("name") or hook_id, url, filt, enabled=body.get("enabled", True))
        if body.get("secret"):
            app.runtime.vault.put_webhook_secret(hook_id, body["secret"])
        return ok({"id": hook_id, "url": url})
    if rest and method == "DELETE":
        app.runtime.store.delete_webhook(rest[0])
        return ok({"removed": rest[0]})
    raise UsageError("Unknown webhooks route")


def _rules(app: App, method: str, rest: list[str], qs: dict, body: dict):
    if method == "GET" and not rest:
        return ok(app.runtime.store.list_rules())
    if method == "POST" and rest == ["test"]:
        engine = RulesEngine(
            [
                Rule(
                    id=r["id"],
                    name=r.get("name") or "",
                    priority=int(r.get("priority") or 0),
                    enabled=bool(r.get("enabled")),
                    stop=bool(r.get("stop")),
                    match=r.get("match") or {},
                    actions=r.get("actions") or {},
                )
                for r in app.runtime.store.list_rules()
            ]
        )
        from mailkit.supervisor import _message_from_summary

        msg = _message_from_summary(body.get("message") or body)
        action = engine.evaluate(msg, account_id=body.get("account_id") or msg.account_id, mailbox=body.get("mailbox") or msg.mailbox)
        return ok(action.__dict__)
    if method == "POST" and not rest:
        rule_id = body.get("id") or new_id("rule")
        app.runtime.store.save_rule(
            rule_id,
            body.get("name") or rule_id,
            int(body.get("priority") or 0),
            bool(body.get("enabled", True)),
            bool(body.get("stop", False)),
            body.get("match") or {},
            body.get("actions") or {},
        )
        return ok({"id": rule_id})
    if rest and method == "DELETE":
        app.runtime.store.delete_rule(rest[0])
        return ok({"removed": rest[0]})
    raise UsageError("Unknown rules route")


def _doctor(app: App, method: str, rest: list[str], qs: dict, body: dict):
    from mailkit.doctor import run_doctor

    repair = method == "POST" or str((qs.get("repair") or ["false"])[0]).lower() in {"1", "true", "yes"}
    if rest and rest[0] == "repair":
        repair = True
    report = run_doctor(app.runtime.root, repair=repair, app=app, runtime=app.runtime)
    return ok(report.to_dict())


def _plugins(app: App) -> dict:
    return {
        "providers": sorted(app.runtime.plugins.providers),
        "auth": sorted(app.runtime.plugins.auth),
        "hooks": sorted(app.runtime.plugins.hooks),
        "watchers": sorted(app.runtime.plugins.watchers),
    }
