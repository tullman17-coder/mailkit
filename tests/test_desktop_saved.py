from pathlib import Path


def test_desktop_saved_section_requests_flagged_without_changing_star_button():
    src = Path("desktop/js/app.js").read_text()
    assert 'state.flagged || state.mailbox === "saved"' in src
    assert "${action}" in src
    assert "unflag" in src
