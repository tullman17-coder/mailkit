from mailkit.imaputf7 import decode, encode


def test_ascii_passthrough():
    assert encode("INBOX") == "INBOX"
    assert decode("INBOX") == "INBOX"


def test_ampersand():
    assert encode("A&B") == "A&-B"
    assert decode("A&-B") == "A&B"


def test_unicode_mailbox():
    name = "Résumé"
    assert decode(encode(name)) == name
