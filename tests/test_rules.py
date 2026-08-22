from mailkit.models import Address, Message
from mailkit.rules import Rule, RulesEngine, event_filter_match, rule_matches
from mailkit.models import EventFilter


def _msg(**kwargs) -> Message:
    base = dict(
        id="msg_1",
        account_id="work",
        provider_id="imap",
        mailbox="INBOX",
        subject="Invoice INV-1042 attached",
        from_=[Address("billing@vendor.example", "Billing")],
        to=[Address("ops@example.com")],
        snippet="Please review the attached invoice",
        attachment_types=["application/pdf"],
        has_attachments=True,
        thread_id="thr_inv",
        tags=[],
        labels=[],
    )
    base.update(kwargs)
    return Message(**base)


def test_priority_and_stop():
    engine = RulesEngine(
        [
            Rule(
                id="low",
                priority=1,
                match={"subject_contains": ["Invoice"]},
                actions={"tag": ["low"]},
            ),
            Rule(
                id="high",
                priority=50,
                stop=True,
                match={"from_domain": ["vendor.example"], "attachment_type": ["pdf"]},
                actions={"tag": ["invoice"], "flag": True},
            ),
        ]
    )
    result = engine.evaluate(_msg(), account_id="work", mailbox="INBOX")
    assert result.tag == ["invoice"]
    assert result.flag is True
    assert result.stop is True
    assert result.extra["matched_rules"] == ["high"]


def test_safe_defaults_ignore_delete():
    engine = RulesEngine(
        [
            Rule(
                id="bad",
                priority=10,
                match={"query": "invoice"},
                actions={"delete": True, "tag": ["kept"]},
            )
        ]
    )
    result = engine.evaluate(_msg(), account_id="work", mailbox="INBOX")
    assert result.tag == ["kept"]
    assert "delete" not in result.extra


def test_all_vs_any_mode():
    rule_all = Rule(
        id="all",
        match={"mode": "all", "subject_contains": ["Invoice"], "from_domain": ["nope.test"]},
        actions={"tag": ["x"]},
    )
    rule_any = Rule(
        id="any",
        match={"mode": "any", "subject_contains": ["Invoice"], "from_domain": ["nope.test"]},
        actions={"tag": ["y"]},
    )
    msg = _msg()
    assert rule_matches(rule_all, msg, account_id="work", mailbox="INBOX") is False
    assert rule_matches(rule_any, msg, account_id="work", mailbox="INBOX") is True


def test_event_filter_for_agent_subscription():
    event = {
        "type": "message.created",
        "account_id": "work",
        "mailbox": "INBOX",
        "thread_id": "thr_inv",
        "message": {
            "subject": "Invoice INV-1042 attached",
            "from": [{"address": "billing@vendor.example"}],
            "to": [{"address": "ops@example.com"}],
            "tags": ["invoice"],
            "attachment_types": ["application/pdf"],
            "snippet": "attached invoice",
        },
    }
    filt = EventFilter(subject=["Invoice"], sender=["vendor.example"], attachment_type=["pdf"])
    assert event_filter_match(filt, event) is True
    assert event_filter_match(EventFilter(subject=["unrelated"]), event) is False
    assert event_filter_match(EventFilter(), event) is True
