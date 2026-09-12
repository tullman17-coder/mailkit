from subprocess import CompletedProcess

import pytest

from mailkit.cli.install import LABEL, apply_os_service, unit_text
from mailkit.errors import DaemonError
from mailkit.service import daemon_spawn_cmd, frozen_dispatch_argv, spawn_background, spawn_generation


def test_empty_argv_opens_ui():
    assert frozen_dispatch_argv([]) is None


def test_python_style_spawn_becomes_daemon_cli():
    assert frozen_dispatch_argv(["-m", "mailkit", "service", "run", "--background-child"]) == [
        "service",
        "run",
        "--background-child",
    ]


def test_role_env_forces_daemon_even_with_empty_argv():
    assert frozen_dispatch_argv([], role="daemon") == ["service", "run", "--background-child"]


def test_spawned_child_never_opens_ui():
    daemon = ["service", "run", "--background-child"]
    assert frozen_dispatch_argv([], spawn_gen=1) == daemon
    assert frozen_dispatch_argv(["ignored-junk"], spawn_gen=1) == daemon
    assert frozen_dispatch_argv(["ignored-junk"], role="daemon") == daemon


def test_frozen_spawn_cmd_does_not_use_module_flag():
    cmd = daemon_spawn_cmd("/Applications/Mailkit.app/Contents/MacOS/Mailkit", frozen=True)
    assert cmd[0].endswith("Mailkit")
    assert "-m" not in cmd
    assert cmd[1:] == ["service", "run", "--background-child"]


def test_spawn_generation_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("MAILKIT_SPAWN_GEN", "1")
    monkeypatch.setenv("MAILKIT_HOME", str(tmp_path))
    assert spawn_generation() == 1
    with pytest.raises(DaemonError, match="nested engine spawn"):
        spawn_background(tmp_path)


def test_ensure_engine_skips_nested_spawn(tmp_path, monkeypatch):
    from mailkit.desktop import ensure_engine

    called = []
    monkeypatch.setenv("MAILKIT_SPAWN_GEN", "1")
    monkeypatch.setattr("mailkit.desktop.is_running", lambda root: False)
    monkeypatch.setattr("mailkit.desktop.data_dir", lambda root: tmp_path)
    monkeypatch.setattr("mailkit.desktop.spawn_background", lambda root: called.append(root))
    ensure_engine(tmp_path)
    assert called == []


def test_ensure_engine_waits_for_launchd_before_fallback_spawn(tmp_path, monkeypatch):
    from mailkit.desktop import ensure_engine

    calls = []
    monkeypatch.setattr("mailkit.desktop.ensure_os_service", lambda root: calls.append(("service", root)))
    monkeypatch.setattr("mailkit.desktop.wait_until_api", lambda root, timeout: True)
    monkeypatch.setattr("mailkit.desktop.is_running", lambda root: False)
    monkeypatch.setattr("mailkit.desktop.spawn_background", lambda root: calls.append(("spawn", root)))

    ensure_engine(tmp_path)

    assert calls == [("service", tmp_path)]


def test_install_units_mark_frozen_children_as_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.frozen", True, raising=False)
    plist = unit_text("launchd", tmp_path)
    assert "<string>-m</string>" not in plist
    assert "MAILKIT_ROLE" in plist
    unit = unit_text("systemd", tmp_path)
    assert "MAILKIT_ROLE=daemon" in unit
    assert "-m mailkit" not in unit


def test_install_reloads_changed_launch_agent(tmp_path, monkeypatch):
    calls = []
    home = tmp_path / "home"
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("old", encoding="utf-8")
    monkeypatch.setattr("mailkit.cli.install.os.getuid", lambda: 501, raising=False)

    def runner(args, **kwargs):
        calls.append(args)
        return CompletedProcess(args, 0)

    result = apply_os_service(tmp_path, target="launchd", home=home, runner=runner)

    assert result["changed"] is True
    assert result["loaded"] is True
    assert calls == [
        ["launchctl", "print", "gui/501/dev.mailkit.daemon"],
        ["launchctl", "bootout", "gui/501/dev.mailkit.daemon"],
        ["launchctl", "bootstrap", "gui/501", str(plist)],
    ]

    calls.clear()
    unchanged = apply_os_service(tmp_path, target="launchd", home=home, runner=runner)
    assert unchanged["changed"] is False
    assert calls == [["launchctl", "print", "gui/501/dev.mailkit.daemon"]]
