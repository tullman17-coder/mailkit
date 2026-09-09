"""mailkit command line. Humans, scripts, and agents share this surface."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from mailkit import SCHEMA_VERSION, __version__
from mailkit.cli.client import ApiClient
from mailkit.cli.output import Printer
from mailkit.config import load_config, save_config
from mailkit.discovery import discover
from mailkit.errors import ExitCode, MailkitError, UsageError
from mailkit.ids import new_id
from mailkit.paths import data_dir
from mailkit.runtime import open_runtime
from mailkit.service import is_running, read_pid, spawn_background, stop_daemon


EPILOG = """
examples:
  mailkit service start
  mailkit accounts add --address you@gmail.com --auth oauth2
  mailkit accounts add --address you@custom.com --imap-host imap.example.com --smtp-host smtp.example.com
  mailkit messages list --account work --mailbox inbox --unread
  mailkit messages get --account work --mailbox INBOX 12345
  mailkit events stream --account work --subject "Invoice"
  mailkit send --account work --to someone@example.com --subject Hello --body "Hi"

Navigation is always account → mailbox → message. Use --unified only when you
explicitly want every account in one list; each row still carries account_id.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mailkit",
        description="Local multi-account email engine, daemon, and CLI. Connects directly to your providers.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"mailkit {__version__} schema {SCHEMA_VERSION}")
    p.add_argument("-o", "--format", choices=["text", "json", "ndjson"], default=None, help="output format (json for agents)")
    p.add_argument("--home", help="data directory (default ~/.mailkit or $MAILKIT_HOME)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--account", help="account id for this command")
    p.add_argument("--unified", action="store_true", help="include all accounts; each row still shows account_id")

    sub = p.add_subparsers(dest="cmd", required=True)

    # service
    svc = sub.add_parser("service", help="control the background email engine")
    svc_sub = svc.add_subparsers(dest="svc_cmd", required=True)
    svc_sub.add_parser("start", help="start the daemon in the background")
    svc_sub.add_parser("stop", help="stop the daemon")
    svc_sub.add_parser("status", help="show daemon and account watcher status")
    runp = svc_sub.add_parser("run", help="run the daemon in the foreground")
    runp.add_argument("--background-child", action="store_true", help=argparse.SUPPRESS)
    inst = svc_sub.add_parser("install", help="print systemd user unit or launchd plist")
    inst.add_argument("--target", choices=["systemd", "launchd"], default=None)

    # accounts
    acc = sub.add_parser("accounts", help="add, list, and remove partitioned accounts")
    acc_sub = acc.add_subparsers(dest="acc_cmd", required=True)
    acc_sub.add_parser("list", help="list configured accounts")
    add = acc_sub.add_parser("add", help="add an account (discovery, then optional manual override)")
    add.add_argument("--address", required=True)
    add.add_argument("--id")
    add.add_argument("--name")
    add.add_argument("--provider", default="auto", help="auto, imap, gmail, graph, yahoo")
    add.add_argument("--auth", default="password", help="password, app_password, oauth2")
    add.add_argument("--imap-host")
    add.add_argument("--imap-port", type=int)
    add.add_argument("--imap-tls", action=argparse.BooleanOptionalAction, default=None)
    add.add_argument("--smtp-host")
    add.add_argument("--smtp-port", type=int)
    add.add_argument("--username")
    add.add_argument("--password")
    add.add_argument("--client-id")
    add.add_argument("--client-secret")
    add.add_argument("--no-discover", action="store_true")
    add.add_argument("--watch", default="auto")
    acc_sub.add_parser("remove", help="remove an account").add_argument("id")
    show = acc_sub.add_parser("show", help="show one account")
    show.add_argument("id")
    test = acc_sub.add_parser("test", help="test IMAP login and list folders")
    test.add_argument("id")
    disc = acc_sub.add_parser("discover", help="discover IMAP/SMTP endpoints for an address")
    disc.add_argument("address")

    # mailboxes
    mb = sub.add_parser("mailboxes", help="list mailboxes/sections for an account")
    mb.add_argument("--section", help="inbox, saved, sent, drafts, archives")

    # messages / mail alias
    for name, help_text in (("messages", "list, read, search, tag, and move messages"), ("mail", "alias for messages")):
        msg = sub.add_parser(name, help=help_text)
        msg_sub = msg.add_subparsers(dest="msg_cmd", required=True)
        lst = msg_sub.add_parser("list", help="list messages in one mailbox")
        _add_msg_filters(lst)
        rd = msg_sub.add_parser("get", aliases=["read"], help="read one message")
        rd.add_argument("id")
        rd.add_argument("--mailbox", default="INBOX")
        rd.add_argument("--body", action="store_true", help="include body text")
        srch = msg_sub.add_parser("search", help="search within an account mailbox")
        _add_msg_filters(srch)
        srch.add_argument("query", nargs="?")
        tag = msg_sub.add_parser("tag", help="add local tags")
        tag.add_argument("id")
        tag.add_argument("tags", nargs="+")
        mv = msg_sub.add_parser("move", help="move a message to another mailbox")
        mv.add_argument("id")
        mv.add_argument("mailbox")
        fl = msg_sub.add_parser("flag", help="star/flag a message")
        fl.add_argument("id")
        ufl = msg_sub.add_parser("unflag", help="remove star/flag")
        ufl.add_argument("id")
        msg_sub.add_parser("read-flag", help="mark read").add_argument("id")
        msg_sub.add_parser("unread", help="mark unread").add_argument("id")

    send = sub.add_parser("send", help="send a message via the account SMTP server")
    send.add_argument("--to", required=True, action="append")
    send.add_argument("--cc", action="append", default=[])
    send.add_argument("--subject", required=True)
    send.add_argument("--body", default="")
    send.add_argument("--html")

    reply = sub.add_parser("reply", help="reply to a cached message")
    reply.add_argument("id")
    reply.add_argument("--body", required=True)
    reply.add_argument("--html")

    watch = sub.add_parser("watch", help="stream live mailbox events as NDJSON")
    _add_event_filters(watch)

    ev = sub.add_parser("events", help="durable event log, stream, and acknowledgements")
    ev_sub = ev.add_subparsers(dest="ev_cmd", required=True)
    evl = ev_sub.add_parser("list", help="list events after a cursor")
    _add_event_filters(evl)
    evl.add_argument("--cursor")
    evl.add_argument("--limit", type=int, default=50)
    evs = ev_sub.add_parser("stream", help="replay then follow events (NDJSON)")
    _add_event_filters(evs)
    evs.add_argument("--cursor")
    ack = ev_sub.add_parser("ack", help="acknowledge an event for a durable subscription")
    ack.add_argument("subscription_id")
    ack.add_argument("event_id")

    subp = sub.add_parser("subscriptions", help="durable filtered subscriptions for agents")
    sub_sub = subp.add_subparsers(dest="sub_cmd", required=True)
    sub_sub.add_parser("list")
    sadd = sub_sub.add_parser("add")
    sadd.add_argument("--name")
    _add_event_filters(sadd)
    srm = sub_sub.add_parser("remove")
    srm.add_argument("id")

    wh = sub.add_parser("webhooks", help="local or remote webhook deliveries")
    wh_sub = wh.add_subparsers(dest="wh_cmd", required=True)
    wh_sub.add_parser("list")
    wadd = wh_sub.add_parser("add")
    wadd.add_argument("--url", required=True)
    wadd.add_argument("--name")
    wadd.add_argument("--secret")
    _add_event_filters(wadd)
    wrm = wh_sub.add_parser("remove")
    wrm.add_argument("id")

    ru = sub.add_parser("rules", help="classification and routing rules")
    ru_sub = ru.add_subparsers(dest="rule_cmd", required=True)
    ru_sub.add_parser("list")
    radd = ru_sub.add_parser("add")
    radd.add_argument("--name", required=True)
    radd.add_argument("--priority", type=int, default=100)
    radd.add_argument("--stop", action="store_true")
    radd.add_argument("--match", required=True, help="JSON object of match fields")
    radd.add_argument("--actions", required=True, help='JSON object, e.g. {"tag":["claim"]}')
    rrm = ru_sub.add_parser("remove")
    rrm.add_argument("id")
    ru_sub.add_parser("test").add_argument("--message", required=True, help="path to JSON message summary")

    pl = sub.add_parser("plugins", help="list loaded provider, auth, hook, and watcher plugins")
    pl.set_defaults(plug_cmd="list")

    schema = sub.add_parser("schema", help="print versioned JSON schemas")
    schema.add_argument("name", nargs="?", default="event", help="event|message|account|subscription|error")

    desk = sub.add_parser("desktop", help="open the local Mailkit window (starts the engine if needed)")
    desk.set_defaults(desk_cmd="open")

    doc = sub.add_parser("doctor", help="diagnose and safely repair the local engine")
    doc.add_argument("mode", nargs="?", choices=["run", "loop", "watchdog"], default="run")
    doc.add_argument("--repair", action="store_true", help="apply safe repairs")
    doc.add_argument("--start", action="store_true", help="start the daemon if it is down")
    doc.add_argument("--interval", type=float, default=60.0, help="loop/watchdog interval in seconds")

    return p


def _add_msg_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mailbox", default="inbox", help="mailbox or section: inbox, saved, sent, drafts, archives")
    p.add_argument("--unread", action="store_true")
    p.add_argument("--flagged", action="store_true")
    p.add_argument("--tagged")
    p.add_argument("--since", help="ISO date YYYY-MM-DD")
    p.add_argument("--before")
    p.add_argument("--from-addr", dest="from_")
    p.add_argument("--subject")
    p.add_argument("--limit", type=int, default=50)


def _add_event_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mailbox")
    p.add_argument("--sender")
    p.add_argument("--recipient")
    p.add_argument("--subject")
    p.add_argument("--label")
    p.add_argument("--tag")
    p.add_argument("--category")
    p.add_argument("--thread")
    p.add_argument("--attachment-type")
    p.add_argument("--event-type")
    p.add_argument("--rule")


_VALUE_FLAGS = {"-o", "--format", "--home", "--account"}
_BOOL_FLAGS = {"-v", "--verbose", "--unified"}


def _hoist_globals(argv: list[str]) -> list[str]:
    """Allow global flags after the subcommand, e.g. `mailkit schema event -o json`."""
    taken: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        key = arg.split("=", 1)[0]
        if key in _VALUE_FLAGS:
            if "=" in arg:
                taken.append(arg)
                i += 1
            elif i + 1 < len(argv):
                taken.extend([arg, argv[i + 1]])
                i += 2
            else:
                rest.append(arg)
                i += 1
            continue
        if arg in _BOOL_FLAGS:
            taken.append(arg)
            i += 1
            continue
        rest.append(arg)
        i += 1
    return taken + rest


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_hoist_globals(list(argv) if argv is not None else sys.argv[1:]))
    root = Path(args.home).expanduser() if args.home else data_dir()
    os.environ.setdefault("MAILKIT_HOME", str(root))
    fmt = args.format or os.environ.get("MAILKIT_FORMAT") or "text"
    if args.cmd in {"watch", "events"} and getattr(args, "ev_cmd", None) == "stream":
        fmt = args.format or "ndjson"
    if args.cmd == "watch" and not args.format:
        fmt = "ndjson"
    out = Printer(fmt)
    try:
        return _dispatch(args, root, out)
    except MailkitError as exc:
        return out.error(exc)
    except KeyboardInterrupt:
        return 130


def _dispatch(args, root: Path, out: Printer) -> int:
    cfg = load_config(root)
    if args.format is None and cfg.defaults.format in {"text", "json", "ndjson"} and args.cmd not in {"watch"}:
        if out.fmt == "text":
            out.fmt = cfg.defaults.format
    client = ApiClient(cfg, root)

    if args.cmd == "service":
        return _service(args, root, out, client)
    if args.cmd == "accounts":
        return _accounts(args, root, out, client)
    if args.cmd == "schema":
        from mailkit.cli.schemas import dump_schema

        out.data(dump_schema(args.name), text=json.dumps(dump_schema(args.name), indent=2))
        return 0
    if args.cmd == "desktop":
        from mailkit.desktop import run_desktop

        return run_desktop(root)
    if args.cmd == "doctor":
        return _doctor(args, root, out)
    if args.cmd == "plugins":
        client.require_daemon()
        out.data(_unwrap(client.request("GET", "/v1/plugins")))
        return 0

    client.require_daemon()
    account = args.account or cfg.default_account_id()

    if args.cmd == "mailboxes":
        data = _unwrap(client.request("GET", "/v1/mailboxes", query={"account": account}))
        out.data(data)
        return 0
    if args.cmd in {"messages", "mail"}:
        return _messages(args, client, out, account, unified=args.unified)
    if args.cmd == "send":
        body = {"account": account, "to": args.to, "cc": args.cc, "subject": args.subject, "body": args.body, "html": args.html}
        out.data(_unwrap(client.request("POST", "/v1/send", body=body)))
        return 0
    if args.cmd == "reply":
        body = {"account": account, "id": args.id, "body": args.body, "html": args.html}
        out.data(_unwrap(client.request("POST", "/v1/reply", body=body)))
        return 0
    if args.cmd == "watch":
        return _stream(args, client, out, account)
    if args.cmd == "events":
        return _events(args, client, out, account)
    if args.cmd == "subscriptions":
        return _subs(args, client, out, account)
    if args.cmd == "webhooks":
        return _webhooks(args, client, out, account)
    if args.cmd == "rules":
        return _rules(args, client, out)
    raise UsageError(f"unknown command {args.cmd}")


def _unwrap(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _service(args, root, out, client: ApiClient) -> int:
    if args.svc_cmd == "start":
        if is_running(root):
            out.data({"pid": read_pid(root), "status": "running"}, text=f"already running pid {read_pid(root)}")
            return 0
        pid = spawn_background(root)
        out.data({"pid": pid, "status": "started"}, text=f"started mailkit service pid {pid}")
        return 0
    if args.svc_cmd == "stop":
        stop_daemon(root)
        out.data({"status": "stopped"}, text="stopped")
        return 0
    if args.svc_cmd == "run":
        from mailkit.daemon import run

        return run(root, foreground=True)
    if args.svc_cmd == "status":
        running = is_running(root)
        payload = {"running": running, "pid": read_pid(root)}
        if running:
            try:
                payload.update(_unwrap(client.request("GET", "/v1/status")) or {})
            except MailkitError:
                pass
        text = f"{'running' if running else 'stopped'} pid={payload.get('pid')}"
        out.data(payload, text=text)
        return 0 if running else ExitCode.DAEMON
    if args.svc_cmd == "install":
        target = args.target
        if target is None:
            target = "launchd" if sys.platform == "darwin" else "systemd"
        from mailkit.cli.install import unit_text

        sys.stdout.write(unit_text(target, root))
        return 0
    return ExitCode.USAGE


def _accounts(args, root, out, client: ApiClient) -> int:
    runtime = open_runtime(root)
    if args.acc_cmd == "list":
        rows = [_acc_row(a) for a in runtime.config.accounts.values()]
        out.data(rows)
        return 0
    if args.acc_cmd == "discover":
        out.data(discover(args.address).to_dict())
        return 0
    if args.acc_cmd == "show":
        acc = runtime.config.accounts.get(args.id)
        if not acc:
            raise UsageError(f"unknown account {args.id}")
        out.data(_acc_row(acc))
        return 0
    if args.acc_cmd == "remove":
        runtime.config.accounts.pop(args.id, None)
        save_config(runtime.config, root)
        runtime.vault.delete_account(args.id)
        if is_running(root):
            try:
                client.request("DELETE", f"/v1/accounts/{args.id}")
            except MailkitError:
                pass
        out.data({"removed": args.id}, text=f"removed {args.id}")
        return 0
    if args.acc_cmd == "test":
        client.require_daemon()
        out.data(_unwrap(client.request("POST", f"/v1/accounts/{args.id}/test", body={})))
        return 0
    if args.acc_cmd == "add":
        body = {
            "address": args.address,
            "id": args.id,
            "name": args.name,
            "provider": args.provider,
            "auth": args.auth,
            "watch": args.watch,
            "discover": not args.no_discover,
            "username": args.username,
            "imap": {},
            "smtp": {},
            "oauth": {},
        }
        if args.imap_host:
            body["imap"]["host"] = args.imap_host
        if args.imap_port:
            body["imap"]["port"] = args.imap_port
        if args.imap_tls is not None:
            body["imap"]["tls"] = args.imap_tls
        if args.smtp_host:
            body["smtp"]["host"] = args.smtp_host
        if args.smtp_port:
            body["smtp"]["port"] = args.smtp_port
        if args.client_id:
            body["oauth"]["client_id"] = args.client_id
        password = args.password
        if args.auth in {"password", "app_password"} and not password:
            password = getpass.getpass("Password / app password: ")
        if password:
            body["password"] = password
        if args.client_secret:
            body["client_secret"] = args.client_secret
        if args.auth == "oauth2":
            from mailkit.api.routes import _account_from_body
            from mailkit.oauth_flow import run_local_oauth

            acc = _account_from_body(body)
            runtime.config.accounts[acc.id] = acc
            save_config(runtime.config, root)
            secrets = {}
            if args.client_secret:
                secrets["client_secret"] = args.client_secret
            tokens = run_local_oauth(acc, secrets)
            runtime.vault.put_account(acc.id, {**secrets, **tokens})
            if is_running(root):
                client.request("POST", "/v1/accounts", body={**body, **tokens})
            out.data(_acc_row(acc), text=f"added {acc.id} ({acc.address}) via oauth2")
            return 0
        if is_running(root):
            created = _unwrap(client.request("POST", "/v1/accounts", body=body))
            out.data(created)
            return 0
        from mailkit.api.routes import _account_from_body

        acc = _account_from_body(body)
        runtime.config.accounts[acc.id] = acc
        save_config(runtime.config, root)
        secrets = {k: body[k] for k in ("password", "username", "client_secret") if body.get(k)}
        if secrets:
            runtime.vault.put_account(acc.id, secrets)
        out.data(_acc_row(acc), text=f"added {acc.id} ({acc.address})")
        return 0
    return ExitCode.USAGE


def _acc_row(acc) -> dict:
    return {
        "schema": "mailkit.account.v1",
        "id": acc.id,
        "name": acc.display_name(),
        "address": acc.address,
        "provider": acc.provider,
        "auth": acc.auth,
        "enabled": acc.enabled,
        "watch": acc.watch,
        "imap_host": acc.imap.host,
        "smtp_host": acc.smtp.host,
    }


def _messages(args, client, out, account, *, unified: bool) -> int:
    cmd = args.msg_cmd
    if cmd in {"list", "search"}:
        query = {
            "account": None if unified else account,
            "unified": "true" if unified else None,
            "mailbox": args.mailbox,
            "unread": "true" if args.unread else None,
            "flagged": "true" if args.flagged else None,
            "tagged": args.tagged,
            "since": args.since,
            "before": args.before,
            "from": getattr(args, "from_", None),
            "subject": args.subject,
            "limit": args.limit,
        }
        if cmd == "search" and args.query:
            query["query"] = args.query
        path = "/v1/messages/search" if cmd == "search" else "/v1/messages"
        out.data(_unwrap(client.request("GET", path, query=query)))
        return 0
    if cmd in {"get", "read"}:
        query = {"account": account, "mailbox": args.mailbox}
        data = _unwrap(client.request("GET", f"/v1/messages/{args.id}", query=query))
        if not args.body and isinstance(data, dict):
            data = {k: v for k, v in data.items() if k not in {"body_html"}}
        out.data(data)
        return 0
    if cmd == "tag":
        out.data(_unwrap(client.request("POST", f"/v1/messages/{args.id}/tag", body={"tags": args.tags}, query={"account": account})))
        return 0
    if cmd == "move":
        out.data(_unwrap(client.request("POST", f"/v1/messages/{args.id}/move", body={"mailbox": args.mailbox}, query={"account": account})))
        return 0
    action = {"flag": "flag", "unflag": "unflag", "read-flag": "read", "unread": "unread"}[cmd]
    out.data(_unwrap(client.request("POST", f"/v1/messages/{args.id}/{action}", body={}, query={"account": account})))
    return 0


def _filter_query(args, account) -> dict:
    q = {
        "account": getattr(args, "account", None) or account,
        "mailbox": getattr(args, "mailbox", None),
        "sender": getattr(args, "sender", None),
        "recipient": getattr(args, "recipient", None),
        "subject": getattr(args, "subject", None),
        "label": getattr(args, "label", None),
        "tag": getattr(args, "tag", None),
        "category": getattr(args, "category", None),
        "thread": getattr(args, "thread", None),
        "attachment_type": getattr(args, "attachment_type", None),
        "event_type": getattr(args, "event_type", None),
        "rule": getattr(args, "rule", None),
    }
    return {k: v for k, v in q.items() if v}


def _stream(args, client, out, account) -> int:
    query = _filter_query(args, account)
    if getattr(args, "cursor", None):
        query["cursor"] = args.cursor
    for ev in client.stream_sse("/v1/events/stream", query):
        out.event(ev)
    return 0


def _events(args, client, out, account) -> int:
    if args.ev_cmd == "list":
        query = _filter_query(args, account)
        query["cursor"] = args.cursor
        query["limit"] = args.limit
        out.data(_unwrap(client.request("GET", "/v1/events", query=query)))
        return 0
    if args.ev_cmd == "stream":
        return _stream(args, client, out, account)
    if args.ev_cmd == "ack":
        out.data(_unwrap(client.request("POST", "/v1/events/ack", body={"subscription_id": args.subscription_id, "event_id": args.event_id})))
        return 0
    return ExitCode.USAGE


def _subs(args, client, out, account) -> int:
    if args.sub_cmd == "list":
        out.data(_unwrap(client.request("GET", "/v1/subscriptions")))
        return 0
    if args.sub_cmd == "add":
        body = {"name": args.name, "filter": _filter_query(args, account)}
        out.data(_unwrap(client.request("POST", "/v1/subscriptions", body=body)))
        return 0
    if args.sub_cmd == "remove":
        out.data(_unwrap(client.request("DELETE", f"/v1/subscriptions/{args.id}")))
        return 0
    return ExitCode.USAGE


def _webhooks(args, client, out, account) -> int:
    if args.wh_cmd == "list":
        out.data(_unwrap(client.request("GET", "/v1/webhooks")))
        return 0
    if args.wh_cmd == "add":
        body = {"url": args.url, "name": args.name, "secret": args.secret, "filter": _filter_query(args, account)}
        out.data(_unwrap(client.request("POST", "/v1/webhooks", body=body)))
        return 0
    if args.wh_cmd == "remove":
        out.data(_unwrap(client.request("DELETE", f"/v1/webhooks/{args.id}")))
        return 0
    return ExitCode.USAGE


def _rules(args, client, out) -> int:
    if args.rule_cmd == "list":
        out.data(_unwrap(client.request("GET", "/v1/rules")))
        return 0
    if args.rule_cmd == "add":
        body = {
            "name": args.name,
            "priority": args.priority,
            "stop": args.stop,
            "match": json.loads(args.match),
            "actions": json.loads(args.actions),
        }
        out.data(_unwrap(client.request("POST", "/v1/rules", body=body)))
        return 0
    if args.rule_cmd == "remove":
        out.data(_unwrap(client.request("DELETE", f"/v1/rules/{args.id}")))
        return 0
    if args.rule_cmd == "test":
        payload = json.loads(Path(args.message).read_text())
        out.data(_unwrap(client.request("POST", "/v1/rules/test", body=payload)))
        return 0
    return ExitCode.USAGE


def _doctor(args, root: Path, out: Printer) -> int:
    from mailkit.doctor import run_doctor, run_loop

    repair = bool(args.repair) or args.mode == "watchdog"
    start = bool(args.start) or args.mode == "watchdog"
    if args.mode in {"loop", "watchdog"}:
        return run_loop(root, interval=args.interval, repair=repair, start_daemon=start)
    report = run_doctor(root, repair=repair, start_daemon=start)
    out.data(report.to_dict(), text=report.render())
    return 0 if report.ok else ExitCode.ERROR
