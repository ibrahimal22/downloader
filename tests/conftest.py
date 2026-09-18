import os
import tempfile

# Must be set before any downloader module is imported so paths resolve to a sandbox.
os.environ.setdefault("DOWNLOADER_HOME", tempfile.mkdtemp(prefix="downloader-test-"))

import pytest  # noqa: E402

from downloader.db import session as db_session  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Each test gets its own app data dir (archives, thumbnails, settings)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DOWNLOADER_HOME", str(home))
    return home


@pytest.fixture
def db(tmp_path):
    engine = db_session.init_db(tmp_path / "test.db")
    yield engine
    engine.dispose()
