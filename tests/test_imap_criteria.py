from mailkit.providers.imap_smtp import _imap_criteria


def test_empty_query_is_all():
    assert _imap_criteria({}) == ["ALL"]


def test_none_unread_and_flagged_are_omitted():
    assert _imap_criteria({"unread": None, "flagged": None}) == ["ALL"]


def test_unread_true_emits_unseen():
    assert _imap_criteria({"unread": True}) == ["UNSEEN"]


def test_unread_false_emits_seen():
    assert _imap_criteria({"unread": False}) == ["SEEN"]


def test_flagged_true_emits_flagged():
    assert _imap_criteria({"flagged": True}) == ["FLAGGED"]


def test_flagged_false_emits_unflagged():
    assert _imap_criteria({"flagged": False}) == ["UNFLAGGED"]


def test_unread_and_flagged_false_together():
    assert _imap_criteria({"unread": False, "flagged": False}) == ["SEEN", "UNFLAGGED"]


def test_unread_and_flagged_true_together():
    assert _imap_criteria({"unread": True, "flagged": True}) == ["UNSEEN", "FLAGGED"]


def test_false_flags_combine_with_other_criteria():
    assert _imap_criteria({"unread": False, "subject": "invoice"}) == ["SEEN", "SUBJECT", "invoice"]
