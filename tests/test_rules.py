from mailkit.models import Address, Message
from mailkit.rules import Rule, RulesEngine, event_filter_match, rule_matches
from mailkit.models import EventFilter


def _msg(**kwargs) -> Message:
    base = dict(
        id="msg_1",
        account_id="work",
        provider_id="imap",
        mailbox="INBOX",
        subject="Empire Today claim #4421",
        from_=[Address("claims@empiretoday.com", "Claims")],
        to=[Address("ops@example.com")],
        snippet="Please review the attached claim packet",
        attachment_types=["application/pdf"],
        has_attachments=True,
        thread_id="thr_emp",
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
                match={"subject_contains": ["Empire Today"]},
                actions={"tag": ["low"]},
            ),
            Rule(
                id="high",
                priority=50,
                stop=True,
                match={"from_domain": ["empiretoday.com"], "attachment_type": ["pdf"]},
                actions={"tag": ["empire-today", "claim"], "flag": True},
            ),
        ]
    )
    result = engine.evaluate(_msg(), account_id="work", mailbox="INBOX")
    assert result.tag == ["empire-today", "claim"]
    assert result.flag is True
    assert result.stop is True
    assert result.extra["matched_rules"] == ["high"]


def test_safe_defaults_ignore_delete():
    engine = RulesEngine(
        [
            Rule(
                id="bad",
                priority=10,
                match={"query": "claim"},
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
        match={"mode": "all", "subject_contains": ["Empire"], "from_domain": ["nope.test"]},
        actions={"tag": ["x"]},
    )
    rule_any = Rule(
        id="any",
        match={"mode": "any", "subject_contains": ["Empire"], "from_domain": ["nope.test"]},
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
        "thread_id": "thr_emp",
        "message": {
            "subject": "Empire Today claim #4421",
            "from": [{"address": "claims@empiretoday.com"}],
            "to": [{"address": "ops@example.com"}],
            "tags": ["claim"],
            "attachment_types": ["application/pdf"],
            "snippet": "claim packet",
        },
    }
    filt = EventFilter(subject=["Empire Today"], sender=["empiretoday.com"], attachment_type=["pdf"])
    assert event_filter_match(filt, event) is True
    assert event_filter_match(EventFilter(subject=["unrelated"]), event) is False
    assert event_filter_match(EventFilter(), event) is True
