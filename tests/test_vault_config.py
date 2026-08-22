from pathlib import Path

from mailkit.config import AccountConfig, AppConfig, ImapSettings, load_config, save_config
from mailkit.vault import Vault


def test_roundtrip_config_and_vault(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MAILKIT_HOME", str(tmp_path))
    cfg = AppConfig()
    cfg.accounts["work"] = AccountConfig(
        id="work",
        name="Work",
        address="me@example.com",
        provider="imap",
        imap=ImapSettings(host="imap.example.com", port=993, tls=True),
    )
    save_config(cfg, tmp_path)
    loaded = load_config(tmp_path)
    assert loaded.accounts["work"].imap.host == "imap.example.com"
    vault = Vault(tmp_path)
    vault.put_account("work", {"password": "s3cret"})
    again = Vault(tmp_path)
    assert again.get_account("work")["password"] == "s3cret"
    raw = (tmp_path / "vault.enc").read_bytes()
    assert b"s3cret" not in raw
