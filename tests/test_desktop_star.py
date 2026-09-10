import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "test_desktop_star.js"


def test_desktop_star_toggles_unflag_and_refreshes():
    result = subprocess.run(
        ["node", str(HARNESS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
