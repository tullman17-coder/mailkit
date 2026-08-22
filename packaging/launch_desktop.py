"""Frozen entry point for the Mailkit .app bundle."""

from __future__ import annotations

import os
import sys

from mailkit.service import frozen_dispatch_argv, spawn_generation


if __name__ == "__main__":
    rest = frozen_dispatch_argv(
        sys.argv[1:],
        os.environ.get("MAILKIT_ROLE"),
        spawn_generation(),
    )
    if rest is not None:
        from mailkit.cli.main import main

        raise SystemExit(main(rest))
    from mailkit.desktop import run_desktop

    raise SystemExit(run_desktop())
