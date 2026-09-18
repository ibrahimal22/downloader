from xml.etree import ElementTree as ET

import pytest

from downloader.config.settings import SettingsStore
from downloader.db.models import DownloadItem
from downloader.library.organizer import write_nfo
from downloader.library.service import LibraryService, Query, fts_query


@pytest.fixture
def lib(db, tmp_path, qtbot):
    store = SettingsStore(tmp_path / "s.json")
    store.update(write_nfo=True)
    return LibraryService(store)


def make_file(tmp_path, name, content=b"x" * 1000):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def add(lib, tmp_path, name, **info):
    path = make_file(tmp_path, name, info.pop("content", name.encode() * 50))
    item = DownloadItem(id=1, url="https://y/x", output_root=str(tmp_path),
                        playlist_title=info.pop("playlist_title", None))
    return lib.add_from_download(item, {"filepath": str(path), **info})


def test_fts_query_is_safe():
    assert fts_query('rust "tut') == '"rust"* "tut"*'
    assert fts_query("  -- ") is None


def test_add_search_and_filters(lib, tmp_path):
    add(lib, tmp_path, "a.mp4", title="Learning Rust quickly", uploader="Ferris", id="r1",
        extractor_key="Youtube", tags=["programming", "systems"], vcodec="avc1")
    add(lib, tmp_path, "b.mp3", title="Lo-fi beats", uploader="Chill", id="m1", vcodec="none")
    add(lib, tmp_path, "c.mkv", title="Cooking pasta", uploader="Chef", id="c1",
        description="A rusty old pan", playlist_title="Kitchen")

    titles = {r.title for r in lib.search(Query(text="rus"))}
    assert titles == {"Learning Rust quickly", "Cooking pasta"}
    assert {r.title for r in lib.search(Query(text="systems"))} == {"Learning Rust quickly"}
    assert [r.title for r in lib.search(Query(kind="audio"))] == ["Lo-fi beats"]
    assert [r.title for r in lib.search(Query(playlist="Kitchen"))] == ["Cooking pasta"]
    assert [r.title for r in lib.search(Query(sort="title"))] == \
        ["Cooking pasta", "Learning Rust quickly", "Lo-fi beats"]

    assert lib.is_duplicate("r1", "youtube")
    assert lib.is_duplicate("r1")
    assert not lib.is_duplicate("r1", "vimeo")
    assert not lib.is_duplicate("nope")

    facets = lib.facets()
    assert ("Kitchen", 1) in facets["playlists"]
    assert lib.stats()["count"] == 3


def test_nfo_written(lib, tmp_path):
    add(lib, tmp_path, "v.mp4", title="T", uploader="U", id="i", upload_date="20240131",
        duration=125, tags=["a"], extractor_key="Youtube")
    root = ET.parse(tmp_path / "v.nfo").getroot()
    assert root.findtext("title") == "T"
    assert root.findtext("premiered") == "2024-01-31"
    assert root.findtext("runtime") == "2"


def test_missing_files_and_delete(lib, tmp_path):
    rec = add(lib, tmp_path, "gone.mp4", title="Gone", id="g")
    (tmp_path / "gone.mp4").unlink()
    assert lib.verify_files() == 1
    assert lib.search(Query()) == []
    assert len(lib.search(Query(show_missing=True))) == 1
    assert not lib.is_duplicate("g")
    lib.delete([rec.id])
    assert lib.search(Query(show_missing=True)) == []


def test_delete_with_files(lib, tmp_path):
    rec = add(lib, tmp_path, "d.mp4", title="D", id="d")
    lib.delete([rec.id], delete_files=True)
    assert not (tmp_path / "d.mp4").exists() and not (tmp_path / "d.nfo").exists()


def test_duplicate_groups_by_hash(lib, tmp_path):
    add(lib, tmp_path, "one/x.mp4", title="X1", content=b"same" * 500)
    add(lib, tmp_path, "two/x.mp4", title="X2", content=b"same" * 500)
    add(lib, tmp_path, "y.mp4", title="Y", content=b"diff" * 500)
    groups = lib.duplicate_groups()
    assert len(groups) == 1 and {r.title for r in groups[0]} == {"X1", "X2"}


def test_import_folder_uses_info_json(lib, tmp_path):
    media = make_file(tmp_path / "imp", "Some Video [abc].mkv")
    (tmp_path / "imp" / "Some Video [abc].info.json").write_text(
        '{"title": "Real Title", "id": "abc", "uploader": "Up", "extractor_key": "Youtube"}',
        encoding="utf-8")
    make_file(tmp_path / "imp", "partial.mp4.part")
    assert lib.import_folder(tmp_path / "imp") == 1
    (rec,) = lib.search(Query())
    assert rec.title == "Real Title" and rec.filepath == str(media)
    assert lib.import_folder(tmp_path / "imp") == 0  # idempotent


def test_write_nfo_escapes(tmp_path):
    path = write_nfo(tmp_path / "a.mp4", {"title": "<b>&</b>", "id": "1"})
    assert ET.parse(path).getroot().findtext("title") == "<b>&</b>"
