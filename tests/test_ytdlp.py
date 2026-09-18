from downloader.config.settings import Settings
from downloader.core import ytdlp
from downloader.core.fsutil import sanitize_component
from downloader.core.ytdlp import (
    DoneEvent,
    InfoEvent,
    JobSpec,
    MessageEvent,
    PostprocessEvent,
    ProgressEvent,
    build_args,
    is_retryable,
    parse_line,
    parse_probe,
    preview_template,
    render_template,
)


def test_render_template_leaves_unknown_fields_to_ytdlp():
    assert render_template("{uploader}/{title} [{id}].{ext}") == \
        "%(uploader)s/%(title)s [%(id)s].%(ext)s"


def test_render_template_substitutes_known_and_formats():
    out = render_template(
        "{uploader}/{playlist}/{playlist_index:03d} - {title}.{ext}",
        {"playlist": "My: List?", "playlist_index": 7},
    )
    assert out == "%(uploader)s/My_ List_/007 - %(title)s.%(ext)s"


def test_render_template_defaults_and_alternatives():
    assert render_template("{artist,uploader|Unknown}/{playlist|Singles}") == \
        "%(artist,uploader|Unknown)s/%(playlist|Singles)s"


def test_render_template_escapes_literal_percent():
    assert render_template("100% {title}") == "100%% %(title)s"
    assert render_template("{playlist}", {"playlist": "50% off"}) == "50%% off"


def test_preview_template():
    sample = {"uploader": "Chan", "title": "Hello/World", "id": "abc", "ext": "mp4"}
    assert preview_template("{uploader}/{title} [{id}].{ext}", sample) == \
        "Chan/Hello_World [abc].mp4"
    assert preview_template("{playlist|Singles}/{title}.{ext}", sample) == "Singles/Hello_World.mp4"


def test_sanitize_component_windows_rules():
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("a<b>c. ") == "a_b_c"
    assert sanitize_component("") == "_"
    assert len(sanitize_component("x" * 500)) == 120


def test_build_args_core_flags():
    s = Settings(download_dir="D:/Videos")
    args = build_args(JobSpec(url="https://y.tube/v", output_root="D:/Videos", preset="1080",
                              limit_kbps=500), s)
    assert args[-2:] == ["--", "https://y.tube/v"]
    assert "--no-playlist" in args
    assert args[args.index("-r") + 1] == "500K"
    f = args[args.index("-f") + 1]
    assert "height<=1080" in f
    assert "--embed-metadata" in args


def test_build_args_audio_uses_audio_template_and_skips_video_only_flags():
    s = Settings(subtitles_enabled=True)
    args = build_args(JobSpec(url="u", output_root="/m", preset="audio_mp3"), s)
    assert "-x" in args and "mp3" in args
    assert "--embed-chapters" not in args
    assert "--write-subs" not in args
    templates = [args[i + 1] for i, a in enumerate(args) if a == "-o"]
    assert templates[0].startswith("Music/")


def test_build_args_options():
    s = Settings(cookies_from_browser="firefox", proxy="socks5://127.0.0.1:9050",
                 sponsorblock="remove", subtitles_enabled=True, subtitles_auto=True)
    args = build_args(JobSpec(url="u", output_root="/v", archive_file="/a.txt"), s)
    assert args[args.index("--cookies-from-browser") + 1] == "firefox"
    assert args[args.index("--proxy") + 1] == "socks5://127.0.0.1:9050"
    assert "--sponsorblock-remove" in args
    assert "--write-auto-subs" in args and "--embed-subs" in args
    assert args[args.index("--download-archive") + 1] == "/a.txt"


def test_parse_progress_line():
    ev = parse_line('[dl] {"status": "downloading", "downloaded_bytes": 512, '
                    '"total_bytes": null, "total_bytes_estimate": 2048, "speed": 1024.5, '
                    '"eta": 3, "filename": "x.f137.mp4"}\r\n')
    assert isinstance(ev, ProgressEvent)
    assert ev.total == 2048 and ev.percent == 25.0 and ev.eta == 3


def test_parse_other_lines():
    assert isinstance(parse_line('[pp] {"status": "started", "postprocessor": "Merger"}'),
                      PostprocessEvent)
    assert isinstance(parse_line('[info] {"id": "x", "title": "T"}'), InfoEvent)
    done = parse_line('[done] {"filepath": "C:\\\\v\\\\a.mp4", "id": "x"}')
    assert isinstance(done, DoneEvent) and done.info["filepath"] == "C:\\v\\a.mp4"
    err = parse_line("ERROR: [youtube] abc: Private video")
    assert isinstance(err, MessageEvent) and err.level == "error"
    assert parse_line("") is None
    assert parse_line("[dl] not json") is None


def test_retryable_classification():
    assert is_retryable("Unable to download: HTTP Error 503")
    assert is_retryable("Connection reset by peer")
    assert not is_retryable("[youtube] x: Private video. Sign in if you've been granted access")
    assert not is_retryable("Unsupported URL: https://example.com")


def test_parse_probe_playlist_and_single():
    pl = parse_probe("u", {
        "_type": "playlist", "title": "PL", "uploader": "U", "extractor_key": "YoutubeTab",
        "entries": [
            {"url": "https://y/1", "id": "1", "title": "One", "duration": 10},
            None,
            {"id": "3", "title": "No url"},
            {"url": "https://y/2", "id": "2", "title": "Two", "playlist_index": 5},
        ],
    })
    assert pl.is_playlist and [e.id for e in pl.entries] == ["1", "2"]
    assert pl.entries[1].index == 5
    single = parse_probe("u", {"title": "V", "id": "v", "formats": [
        {"height": 720}, {"height": 1080}, {"height": None}, {"height": 720}]})
    assert not single.is_playlist and single.heights == [1080, 720]


def test_module_exports():
    assert callable(ytdlp.probe)
