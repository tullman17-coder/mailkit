"""Frozen entry point for the Mailkit .app bundle."""

from mailkit.desktop import run_desktop

if __name__ == "__main__":
    raise SystemExit(run_desktop())
