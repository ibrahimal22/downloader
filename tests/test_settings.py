import json

from downloader.config.settings import SettingsStore


def test_missing_fields_are_persisted_so_random_defaults_are_stable(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"first_run_done": True}), encoding="utf-8")
    token = SettingsStore(path).data.local_server_token
    assert json.loads(path.read_text())["local_server_token"] == token
    assert SettingsStore(path).data.local_server_token == token


def test_corrupt_file_is_backed_up(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    store = SettingsStore(path)
    assert store.data.max_concurrent == 3
    assert (tmp_path / "settings.corrupt.json").exists()


def test_update_notifies_listeners(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    seen = []
    store.subscribe(lambda s: seen.append(s.max_concurrent))
    store.update(max_concurrent=5)
    assert seen == [5]
    assert SettingsStore(tmp_path / "s.json").data.max_concurrent == 5
