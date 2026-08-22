from mailkit.cli.main import build_parser, main
from mailkit.cli.schemas import dump_schema, write_schema_files


def test_help_lists_required_commands():
    parser = build_parser()
    help_text = parser.format_help()
    for token in (
        "accounts",
        "messages",
        "send",
        "reply",
        "watch",
        "events",
        "webhooks",
        "service",
        "rules",
        "subscriptions",
        "doctor",
        "desktop",
    ):
        assert token in help_text


def test_schema_event_is_stable():
    schema = dump_schema("event")
    assert schema["$id"] == "mailkit.event.v1"
    required = set(schema["required"])
    assert {"id", "ts", "account_id", "provider_id", "type", "schema"} <= required


def test_json_schema_command(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILKIT_HOME", str(tmp_path))
    assert main(["-o", "json", "schema", "event"]) == 0
    assert main(["schema", "event", "-o", "json"]) == 0


def test_write_schema_files(tmp_path):
    write_schema_files(tmp_path)
    assert (tmp_path / "event.json").exists()
