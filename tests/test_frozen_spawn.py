import plistlib
from subprocess import CompletedProcess

import pytest

from mailkit.cli.install import LABEL, apply_os_service, unit_text
from mailkit.errors import DaemonError
from mailkit.service import clear_pid, daemon_spawn_cmd, frozen_dispatch_argv, spawn_background, spawn_generation


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
    plist = plistlib.loads(unit_text("launchd", tmp_path).encode())
    assert "-m" not in plist["ProgramArguments"]
    assert plist["EnvironmentVariables"]["MAILKIT_ROLE"] == "daemon"
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    unit = unit_text("systemd", tmp_path)
    assert "MAILKIT_ROLE=daemon" in unit
    assert "-m mailkit" not in unit


def test_install_loads_launch_agent_once(tmp_path, monkeypatch):
    calls = []
    loaded = False
    home = tmp_path / "home"
    plist = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    monkeypatch.setattr("mailkit.cli.install.os.getuid", lambda: 501, raising=False)

    def runner(args, **kwargs):
        nonlocal loaded
        calls.append(args)
        if args[1] == "print":
            return CompletedProcess(args, 0 if loaded else 1)
        loaded = True
        return CompletedProcess(args, 0)

    result = apply_os_service(tmp_path, target="launchd", home=home, runner=runner)

    assert result["loaded"] is True
    assert calls == [
        ["launchctl", "print", "gui/501/dev.mailkit.daemon"],
        ["launchctl", "bootstrap", "gui/501", str(plist)],
    ]

    calls.clear()
    unchanged = apply_os_service(tmp_path, target="launchd", home=home, runner=runner)
    assert unchanged["loaded"] is True
    assert calls == [["launchctl", "print", "gui/501/dev.mailkit.daemon"]]


def test_daemon_cleanup_preserves_newer_pid(tmp_path):
    path = tmp_path / "mailkit.pid"
    path.write_text("202", encoding="utf-8")

    clear_pid(tmp_path, expected_pid=101)
    assert path.read_text(encoding="utf-8") == "202"

    clear_pid(tmp_path, expected_pid=202)
    assert not path.exists()
