"""Message listing stays newest-first regardless of IMAP response order."""
from unittest.mock import Mock

from mailkit.config import AccountConfig
from mailkit.providers.imap_smtp import ImapSmtpProvider


def test_list_and_search_restore_uid_order_across_fetch_chunks():
    for operation in ("list_messages", "search"):
        provider = ImapSmtpProvider(AccountConfig(id="test"), {})
        provider._select = Mock(return_value=1)
        provider.rate.consume = Mock()
        fetches = []

        def uid(command, *args):
            if command == "SEARCH":
                assert args == (None, "UNSEEN", "FLAGGED", "TEXT", "invoice")
                # SEARCH is a set of matches, not a promise of display order.
                matches = list(range(2, 121, 2)) + list(range(1, 121, 2))
                return "OK", [" ".join(map(str, matches)).encode()]
            assert command == "FETCH"
            assert "BODY.PEEK[HEADER]" in args[1]
            requested = list(map(int, args[0].split(",")))
            fetches.append(requested)
            replies = sorted(requested)
            if len(fetches) == 2:
                replies = replies[::2] + replies[1::2]
            # Two messages vanished after SEARCH; an unrelated update also arrived.
            replies = [value for value in replies if value not in {97, 46}] + [999]
            data = []
            for value in replies:
                header = f"Subject: Invoice {value}\r\nDate: Thu, 10 Sep 2026 12:00:00 +0000\r\n\r\n".encode()
                meta = f'{value} (UID {value} FLAGS () INTERNALDATE "10-Sep-2026 12:00:00 +0000" RFC822.SIZE {len(header)})'.encode()
                data.extend([(meta, header), b")"])
            return "OK", data

        provider._client = Mock(return_value=Mock(uid=Mock(side_effect=uid)))
        messages = getattr(provider, operation)("INBOX", limit=100, unread=True, flagged=True, text="invoice")
        assert fetches == [list(range(120, 70, -1)), list(range(70, 20, -1))]
        assert [message.uid for message in messages] == [value for value in range(120, 20, -1) if value not in {97, 46}]
