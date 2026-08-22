from mailkit.service import daemon_spawn_cmd, frozen_dispatch_argv


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


def test_frozen_spawn_cmd_does_not_use_module_flag():
    cmd = daemon_spawn_cmd("/Applications/Mailkit.app/Contents/MacOS/Mailkit", frozen=True)
    assert cmd[0].endswith("Mailkit")
    assert "-m" not in cmd
    assert cmd[1:] == ["service", "run", "--background-child"]
