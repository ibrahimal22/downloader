import json

import pytest

from downloader.config.settings import SettingsStore
from downloader.integrations.clipboard import video_url
from downloader.integrations.local_server import LocalServer, parse_request
from downloader.integrations.system import url_from_protocol
from downloader.updater.app_updater import is_newer, parse_release, parse_version

EXT = "chrome-extension://abcdefghijklmnop"


@pytest.fixture
def server(tmp_path, qtbot):
    store = SettingsStore(tmp_path / "s.json")
    store.update(local_server_token="secret-token")
    return LocalServer(store)


def call(server, method, path, headers=None, body=b""):
    return server.handle(method, path, {k.lower(): v for k, v in (headers or {}).items()}, body)


def test_rejects_missing_or_wrong_token(server):
    assert call(server, "GET", "/ping")[0] == 401
    assert call(server, "GET", "/ping", {"X-Downloader-Token": "nope"})[0] == 401


def test_rejects_web_page_origins(server):
    status, _payload, origin = call(server, "POST", "/add",
                                    {"Origin": "https://evil.example",
                                     "X-Downloader-Token": "secret-token"})
    assert status == 403 and origin == ""


def test_preflight_allowed_for_extensions(server):
    status, payload, origin = call(server, "OPTIONS", "/add", {"Origin": EXT})
    assert status == 204 and payload is None and origin == EXT


def test_add_emits_signal(server, qtbot):
    body = json.dumps({"url": "https://youtu.be/x", "preset": "720", "quick": True}).encode()
    with qtbot.waitSignal(server.add_requested) as sig:
        status, payload, _ = call(server, "POST", "/add",
                                  {"Origin": EXT, "X-Downloader-Token": "secret-token"}, body)
    assert status == 200 and payload == {"ok": True}
    assert sig.args == ["https://youtu.be/x", "720", True]


def test_add_validates_input(server):
    headers = {"X-Downloader-Token": "secret-token"}
    assert call(server, "POST", "/add", headers, b"{bad")[0] == 400
    assert call(server, "POST", "/add", headers, b'{"url": "javascript:alert(1)"}')[0] == 400
    assert call(server, "GET", "/nope", headers)[0] == 404
    assert call(server, "GET", "/ping", headers)[1]["app"] == "Downloader"


def test_parse_request_waits_for_full_body():
    raw = b"POST /add HTTP/1.1\r\nContent-Length: 10\r\nX-A: b\r\n\r\n12345"
    assert parse_request(raw) is None
    method, path, headers, body = parse_request(raw + b"67890")
    assert (method, path, headers["x-a"], body) == ("POST", "/add", "b", b"1234567890")


def test_clipboard_video_url_detection():
    assert video_url("https://www.youtube.com/watch?v=abc") == "https://www.youtube.com/watch?v=abc"
    assert video_url("  https://youtu.be/abc  ") == "https://youtu.be/abc"
    assert video_url("https://m.vimeo.com/123") == "https://m.vimeo.com/123"
    assert video_url("https://example.com/page") is None
    assert video_url("see https://youtu.be/abc here") is None
    assert video_url("https://notyoutube.com/x") is None


def test_protocol_urls():
    assert url_from_protocol("downloader://https://youtu.be/abc") == "https://youtu.be/abc"
    assert url_from_protocol("downloader:https:/youtu.be/abc") == "https://youtu.be/abc"
    assert url_from_protocol("downloader://add?url=https%3A%2F%2Fyoutu.be%2Fx") == "https://youtu.be/x"
    assert url_from_protocol("downloader://file:///etc/passwd") is None
    assert url_from_protocol("https://youtu.be/abc") is None


def test_version_compare():
    assert parse_version("v1.2.10") == (1, 2, 10)
    assert is_newer("0.2.0", "0.1.9")
    assert is_newer("0.1.10", "0.1.9")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("0.1.0-beta", "0.1.0")


def test_parse_release_picks_installer_and_checksums():
    release = parse_release({
        "tag_name": "v1.0.0", "body": "notes", "html_url": "https://gh/r",
        "assets": [
            {"name": "Downloader-1.0.0-Setup.exe", "browser_download_url": "https://gh/setup.exe"},
            {"name": "SHA256SUMS.txt", "browser_download_url": "https://gh/sums"},
            {"name": "Downloader-1.0.0.AppImage", "browser_download_url": "https://gh/app"},
        ],
    })
    assert release.version == "1.0.0" and release.checksums_url == "https://gh/sums"
    assert release.installer_url is not None
