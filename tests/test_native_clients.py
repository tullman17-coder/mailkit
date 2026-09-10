"""Native desktop/mobile client contract."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_shared_ui_is_phone_aware():
    html = (ROOT / "desktop/index.html").read_text()
    js = (ROOT / "desktop/js/app.js").read_text()
    css = (ROOT / "desktop/css/app.css").read_text()
    assert 'id="app"' in html
    assert 'id="nav-back"' in html
    assert 'id="pair-btn"' in html
    assert "apple-mobile-web-app-capable" in html
    assert "viewport-fit=cover" in html
    assert "data-shell" in js
    assert "setPane" in js
    assert 'html[data-shell="mobile"]' in css
    assert ".nav-back" in css
    assert 'URLQueryItem(name: "shell"' in (ROOT / "clients/ios/Mailkit/Pairing.swift").read_text()
    assert 'appendQueryParameter("shell"' in (ROOT / "clients/android/app/src/main/java/org/zermo/mailkit/Pairing.kt").read_text()


def test_ios_and_android_pair_on_mailkit_scheme():
    ios = (ROOT / "clients/ios/Mailkit/Pairing.swift").read_text()
    android = (ROOT / "clients/android/app/src/main/java/org/zermo/mailkit/Pairing.kt").read_text()
    plist = (ROOT / "clients/ios/Mailkit/Info.plist").read_text()
    manifest = (ROOT / "clients/android/app/src/main/AndroidManifest.xml").read_text()
    assert "mailkit" in ios and "connect" in ios
    assert 'scheme != "mailkit"' in android or 'uri.scheme != "mailkit"' in android
    assert "<string>mailkit</string>" in plist
    assert 'android:scheme="mailkit"' in manifest
    assert "usesCleartextTraffic" in manifest
