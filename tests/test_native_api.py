"""Native onboarding/security regression check; no network calls or real email."""
import base64
import hashlib
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from mailkit.api.http import App, Handler, serve_forever
from mailkit.api.routes import _account_from_body, dispatch
from mailkit.auth.xoauth2 import XOAuth2Auth
from mailkit.auth.password import PasswordAuth
from mailkit.config import AppConfig, save_config, load_config
from mailkit.compose import build_message
from mailkit.errors import AuthError, ConfigError, ConflictError, UsageError, MailkitError
from mailkit.providers.imap_smtp import ImapSmtpProvider


class NativeAPICheck(unittest.TestCase):
    def test_send_reports_partial_acceptance_without_retry(self):
        import smtplib

        account = _account_from_body({"address": "me@gmail.com"})
        sender = Mock()
        runtime = SimpleNamespace(config=AppConfig(accounts={account.id: account}),
                                  provider_for=Mock(return_value=(sender, account, {})))
        app = App(runtime, Mock(), Mock(), Mock(), "token", "now")
        dispatch(app, "POST", "/v1/send", {}, {"account": account.id, "to": ["to@example.com"],
                 "cc": ["cc@example.com"], "bcc": ["hidden@example.com"], "body": "Hello"}, None)
        envelope, mime = sender.send.call_args.args[1:]
        self.assertEqual(envelope, ["to@example.com", "cc@example.com", "hidden@example.com"])
        self.assertNotIn(b"hidden@example.com", mime)
        self.assertIn(b"Cc: cc@example.com", mime)
        message = build_message(from_addr=account.address, to=["to@example.com"], subject="Hello", body="Hello",
                                extra_headers={"bCc": "hidden@example.com"})
        self.assertNotIn("Bcc", message)
        provider = ImapSmtpProvider(account, {})
        smtp = Mock()
        provider._smtp_client = Mock(return_value=smtp)
        raw = b"From: me@gmail.com\r\nMessage-ID: <original@example.com>\r\n\r\nHello"
        recipients = ["accepted@example.com", "refused@example.com"]
        smtp.sendmail.return_value = {recipients[1]: (550, b"mailbox unavailable")}
        smtp.quit.side_effect = smtplib.SMTPServerDisconnected("server closed after DATA")
        with self.assertRaises(MailkitError) as result:
            provider.send(account.address, recipients, raw)
        self.assertEqual(result.exception.details, {
            "message_id": "<original@example.com>", "accepted_recipients": recipients[:1],
            "refused_recipients": recipients[1:], "retry_safe": False,
        })
        self.assertIn("Resend only to refused recipients", str(result.exception))
        smtp.sendmail.assert_called_once_with(account.address, recipients, raw)
        smtp.close.assert_called_once()
        smtp.reset_mock()
        smtp.sendmail.return_value = {}
        self.assertEqual(provider.send(account.address, recipients, raw), "<original@example.com>")
        smtp.sendmail.assert_called_once()
        smtp.reset_mock()
        smtp.sendmail.side_effect = smtplib.SMTPServerDisconnected("ambiguous DATA result")
        with self.assertRaises(smtplib.SMTPServerDisconnected):
            provider.send(account.address, recipients, raw)
        smtp.sendmail.assert_called_once()
        smtp.close.assert_called_once()

    def test_onboarding_and_auth(self):
        body = {"address": "me@example.com", "discover": False, "password": "app-password",
                "imap": {"host": "imap.example.com"}, "smtp": {"host": "smtp.example.com"}}
        account = _account_from_body(body)
        self.assertNotEqual(account.id, _account_from_body({**body, "address": "me@other.com"}).id)
        self.assertTrue(_account_from_body({**body, "address": "o'reilly@example.com"}).id.startswith("acc_"))
        with TemporaryDirectory() as folder:
            dotted = _account_from_body({**body, "id": "me@example.com"})
            save_config(AppConfig(accounts={dotted.id: dotted}), Path(folder))
            self.assertEqual(load_config(Path(folder)).accounts[dotted.id].address, dotted.address)
        with self.assertRaises(ConflictError):
            _account_from_body(body, {account.id: account})
        for change in ({"address": "invalid"}, {"smtp": {"host": "smtp.example.com", "port": -1}},
                       {"imap": {"host": "imap.example.com", "tls": False}},
                       {"smtp": {"host": "smtp.example.com", "starttls": "false"}}):
            with self.assertRaises(UsageError):
                _account_from_body({**body, **change})

        auth = XOAuth2Auth()
        secrets = {"access_token": "access", "token_expiry": int(time.time()) + 3600}
        imap = Mock()
        def authenticate(mechanism, callback):
            # Reproduce imaplib's encoding, then inspect exactly what the server receives.
            wire = base64.b64encode(callback(b""))
            self.assertEqual(base64.b64decode(wire), b"user=me@example.com\x01auth=Bearer access\x01\x01")
            self.assertEqual(callback(b"error"), b"")
            return "OK", []
        imap.authenticate.side_effect = authenticate
        auth.prepare_imap(imap, account, secrets)
        smtp = Mock()
        smtp.docmd.return_value = (235, b"ok")
        auth.prepare_smtp(smtp, account, secrets)
        encoded = smtp.docmd.call_args.args[1].split(" ", 1)[1]
        self.assertEqual(base64.b64decode(encoded), b"user=me@example.com\x01auth=Bearer access\x01\x01")
        smtp.docmd.side_effect = [(334, b"challenge"), (535, b"denied")]
        with self.assertRaises(AuthError):
            auth.prepare_smtp(smtp, account, secrets)
        self.assertEqual(smtp.docmd.call_args.args, ("",))
        PasswordAuth().prepare_smtp(smtp, account, {"password": "incoming", "smtp_password": "outgoing", "smtp_username": "sender"})
        smtp.login.assert_called_once_with("sender", "outgoing")

        provider = ImapSmtpProvider(account, {"password": "app-password"})
        provider.list_mailboxes = Mock(return_value=[SimpleNamespace(name="INBOX")])
        provider.close = Mock()
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        provider._smtp_client = Mock(return_value=connection)
        self.assertEqual(provider.test_connection()["smtp"], True)
        connection.sendmail.assert_not_called()
        provider.close.assert_called()
        provider._smtp_client.side_effect = AuthError("SMTP disabled")
        with self.assertRaises(AuthError):
            provider.test_connection()
        self.assertEqual(provider.close.call_count, 2)
        account.imap.tls = False
        with self.assertRaises(ConfigError):
            provider.connect()

        # Read-only selection uses imaplib's real API; move never expunges other UIDs.
        import imaplib
        client = Mock(spec=imaplib.IMAP4)
        client.select.return_value = ("OK", [b"1"])
        client.status.return_value = ("OK", [b'INBOX (UIDVALIDITY 123)'])
        client.uid.return_value = ("OK", [])
        client.capabilities = ()
        provider._client = Mock(return_value=client)
        self.assertEqual(provider._select("INBOX"), 123)
        client.select.assert_called_with("INBOX", readonly=True)
        provider.move("INBOX", "42", "Archive")
        client.expunge.assert_not_called()
        self.assertEqual(provider._quote('A"B\\C'), '"A\\"B\\\\C"')
        with self.assertRaises(UsageError):
            provider._quote("INBOX\r\nLOGOUT")
        provider._imap = SimpleNamespace(utf8_enabled=True)
        self.assertEqual(provider._quote("收件箱"), '"收件箱"')
        self.assertEqual([call.args[0] for call in client.uid.call_args_list], ["COPY", "STORE"])
        client.uid.reset_mock()
        client.capabilities = (b"UIDPLUS",)
        provider.move("INBOX", "42", "Archive")
        client.uid.assert_called_with("EXPUNGE", "42")
        client.expunge.assert_not_called()

    def test_native_oauth_routes_and_lifecycle(self):
        runtime = SimpleNamespace(config=AppConfig(), root=None, vault=Mock(), plugins=Mock(), store=Mock())
        app = App(runtime, Mock(), Mock(), Mock(), "api-token", "now")
        provider = Mock()
        provider.test_connection.return_value = {"ok": True, "imap": True, "smtp": True, "mailboxes": ["INBOX"]}
        runtime.plugins.provider_for.return_value.create.return_value = provider
        begin_body = {"address": "me@gmail.com", "provider": "gmail", "client_id": "registered.apps.googleusercontent.com",
                      "redirect_uri": "com.googleusercontent.apps.registered:/oauthredirect",
                      "oauth": {"token_url": "https://attacker.example/token"}, "imap": {"host": "attacker.example"}}
        def begin():
            return dispatch(app, "POST", "/v1/oauth/begin", {}, begin_body, None)["data"]
        def complete(pending, suffix=""):
            callback = begin_body["redirect_uri"] + "?state=" + pending["state"] + "&code=one-use-code" + suffix
            return dispatch(app, "POST", "/v1/oauth/complete", {},
                            {"state": pending["state"], "callback_url": callback}, None)
        pending = begin()
        self.assertEqual(runtime.config.accounts, {})
        runtime.vault.put_account.assert_not_called()
        query = parse_qs(urlparse(pending["authorization_url"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        with patch("mailkit.oauth_flow.oauth_tokens", side_effect=AuthError("exchange failed")):
            with self.assertRaises(AuthError):
                complete(pending)
        self.assertEqual(runtime.config.accounts, {})
        runtime.vault.put_account.assert_not_called()
        pending = begin()
        with patch("mailkit.oauth_flow.oauth_tokens", return_value={"access_token": "secret-access", "refresh_token": "secret-refresh"}) as exchange:
            provider.test_connection.side_effect = AuthError("SMTP disabled")
            with self.assertRaises(AuthError):
                complete(pending)
            runtime.vault.put_account.assert_not_called()
            provider.close.assert_called()
            provider.test_connection.side_effect = None
            pending = begin()
            query = parse_qs(urlparse(pending["authorization_url"]).query)
            with patch("mailkit.api.routes.save_config"):
                response = complete(pending)
            self.assertEqual(exchange.call_args.args[0], "https://oauth2.googleapis.com/token")
            verifier = exchange.call_args.kwargs["extra"]["code_verifier"]
            self.assertEqual(query["code_challenge"][0], base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="))
            saved = runtime.config.accounts[response["data"]["id"]]
            self.assertEqual(saved.imap.host, "imap.gmail.com")
            self.assertEqual(saved.watch, "idle")
            self.assertNotIn("secret-access", json.dumps(response))
            self.assertNotIn("secret-refresh", repr(saved))
            self.assertEqual(runtime.vault.put_account.call_args.args[1]["refresh_token"], "secret-refresh")
            with self.assertRaises(AuthError):
                complete(pending)
        runtime.config.accounts.clear()
        for suffix in ("&state=duplicate", "#fragment", "&error=access_denied"):
            with patch("mailkit.oauth_flow.oauth_tokens") as exchange:
                with self.assertRaises(AuthError):
                    complete(begin(), suffix)
                exchange.assert_not_called()
        pending = begin()
        session = app.oauth_sessions.sessions[pending["state"]]
        app.oauth_sessions.sessions[pending["state"]] = (0, *session[1:])
        with self.assertRaises(AuthError):
            complete(pending)
        app.oauth_sessions.sessions.clear()
        for _ in range(32):
            begin()
        with self.assertRaises(UsageError):
            begin()
        with self.assertRaises(ConfigError):
            serve_forever(app, "0.0.0.0", 8765)
        handler = object.__new__(Handler)
        handler.app, handler.headers, handler.path = app, {}, "/v1/accounts"
        self.assertFalse(handler._check_auth())
        handler.headers = {"Authorization": "Bearer api-token"}
        self.assertTrue(handler._check_auth())

    def test_live_reads_fail_loudly_and_close(self):
        account = _account_from_body({"address": "me@gmail.com"})
        provider = Mock()
        runtime = SimpleNamespace(config=AppConfig(accounts={account.id: account}), store=Mock(), provider_for=Mock(return_value=(provider, account, {})))
        app = App(runtime, Mock(), Mock(), Mock(), "token", "now")
        provider.list_mailboxes.side_effect = AuthError("expired")
        with self.assertRaises(AuthError):
            dispatch(app, "GET", "/v1/messages", {"account": [account.id]}, {}, None)
        provider.close.assert_called_once()
        runtime.store.get_message.return_value = {"id": "cached-id", "native_id": "42", "account_id": account.id, "mailbox": "INBOX"}
        provider.get_message.return_value.to_dict.return_value = {"body": "full body"}
        response = dispatch(app, "GET", "/v1/messages/cached-id", {}, {}, None)
        self.assertEqual(response["data"]["body"], "full body")
        provider.get_message.assert_called_once_with("INBOX", "42", peek=True)
        self.assertEqual(provider.close.call_count, 2)

        other = _account_from_body({"address": "other@gmail.com"})
        runtime.config.accounts[other.id] = other
        runtime.store.get_message.return_value.update({"from": [{"address": "sender@example.com"}]})
        dispatch(app, "POST", "/v1/reply", {}, {"id": "cached-id", "account": other.id, "body": "Reply"}, None)
        provider.send.assert_called_once()
        provider.set_flags.assert_not_called()


if __name__ == "__main__":
    unittest.main()
