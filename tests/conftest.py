"""Test configuration.

QtWebEngine cannot create a web view in a headless CI session (no GPU / GL context): constructing a QWebEngineView
segfaults (exit code 139). The desktop page already has a "no QtWebEngine" mode that shows a message and offers the
browser instead, so the tests run in that mode. The embedded view itself is exercised by running the app.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_embedded_web_view(monkeypatch):
    try:
        from containerqt.gui.pages import desktop
    except Exception:  # noqa: BLE001
        return
    monkeypatch.setattr(desktop, "HAVE_WEB", False)
