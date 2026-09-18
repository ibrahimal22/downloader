"""Format presets mapped to yt-dlp arguments."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    args: tuple[str, ...]
    audio_only: bool = False
    tags: tuple[str, ...] = field(default=())


def _video(height: int) -> tuple[str, ...]:
    return (
        "-f", f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b",
        "--merge-output-format", "mp4/mkv",
    )


def _audio(fmt: str, quality: str = "0") -> tuple[str, ...]:
    return ("-f", "ba/b", "-x", "--audio-format", fmt, "--audio-quality", quality)


PRESETS: dict[str, Preset] = {
    p.id: p
    for p in [
        Preset("best", "Best quality (auto)", ("-f", "bv*+ba/b", "--merge-output-format", "mp4/mkv")),
        Preset(
            "compat",
            "Best compatible MP4 (H.264/AAC)",
            ("-S", "vcodec:h264,res,acodec:m4a", "--merge-output-format", "mp4"),
        ),
        Preset("2160", "4K (2160p)", _video(2160)),
        Preset("1440", "1440p", _video(1440)),
        Preset("1080", "1080p", _video(1080)),
        Preset("720", "720p", _video(720)),
        Preset("480", "480p", _video(480)),
        Preset("360", "360p (small)", _video(360)),
        Preset("audio_best", "Audio – best original", ("-f", "ba/b", "-x"), audio_only=True),
        Preset("audio_mp3", "Audio – MP3", _audio("mp3"), audio_only=True),
        Preset("audio_m4a", "Audio – M4A/AAC", _audio("m4a"), audio_only=True),
        Preset("audio_opus", "Audio – Opus", _audio("opus"), audio_only=True),
        Preset("audio_flac", "Audio – FLAC", _audio("flac"), audio_only=True),
    ]
}


def get(preset_id: str) -> Preset:
    """Resolve a preset. 'custom:<format spec>' passes a raw -f selector."""
    if preset_id.startswith("custom:"):
        spec = preset_id.split(":", 1)[1]
        return Preset(preset_id, f"Custom ({spec})", ("-f", spec))
    return PRESETS.get(preset_id, PRESETS["best"])
