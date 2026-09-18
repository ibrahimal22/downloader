import pytest

from downloader.core.binaries import BinaryError, parse_checksum_file

H1 = "a" * 64
H2 = "B" * 64


def test_sha256sum_multi_asset():
    text = f"{H1}  yt-dlp\n{H2}  yt-dlp.exe\n{'c' * 64} *yt-dlp_linux\n"
    assert parse_checksum_file(text, "yt-dlp.exe") == H2.lower()
    assert parse_checksum_file(text, "yt-dlp_linux") == "c" * 64


def test_sha256sum_does_not_match_suffix_of_other_asset():
    text = f"{H1}  yt-dlp.exe\n{H2}  yt-dlp_x86.exe\n"
    assert parse_checksum_file(text, "yt-dlp.exe") == H1


def test_powershell_get_filehash_format():
    text = (
        "\r\nAlgorithm : SHA256\r\n"
        f"Hash      : {H2}\r\n"
        "Path      : C:\\a\\deno\\target\\release\\deno-x86_64-pc-windows-msvc.zip\r\n"
    )
    assert parse_checksum_file(text, "deno-x86_64-pc-windows-msvc.zip") == H2.lower()


def test_bare_hash():
    assert parse_checksum_file(f"{H1}\n", "anything.zip") == H1


def test_missing_asset_raises():
    with pytest.raises(BinaryError):
        parse_checksum_file(f"{H1}  a\n{H2}  b\n", "c")
