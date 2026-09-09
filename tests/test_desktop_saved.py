from pathlib import Path


def test_desktop_saved_section_requests_flagged_without_changing_star_button():
    src = Path("desktop/js/app.js").read_text()
    assert 'state.flagged || state.mailbox === "saved"' in src
    assert 'POST", `/v1/messages/${state.current.id}/flag`' in src
    assert "unflag" not in src
