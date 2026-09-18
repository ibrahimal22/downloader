# Third-party software

Downloader is MIT-licensed. It bundles or downloads the following components,
each under its own license.

## Bundled in the installer

| Component | License | Notes |
|---|---|---|
| Qt 6 / PySide6 | LGPL-3.0 | Dynamically linked (separate DLLs in the app folder); you may replace them. Source: https://code.qt.io |
| Python | PSF License | Embedded runtime |
| SQLAlchemy, Alembic | MIT | |
| pydantic | MIT | |
| httpx | BSD-3-Clause | |
| platformdirs | MIT | |
| psutil | BSD-3-Clause | |
| QtAwesome | MIT | Includes Material Design Icons (Apache-2.0) |

## Downloaded on first run (and kept updated)

These are fetched from their official GitHub releases into
`%LOCALAPPDATA%\Downloader\bin` and verified with SHA-256 before use.

| Component | License | Source |
|---|---|---|
| yt-dlp | Unlicense | https://github.com/yt-dlp/yt-dlp |
| FFmpeg (LGPL build) | LGPL-2.1+ | https://github.com/BtbN/FFmpeg-Builds |
| Deno | MIT | https://github.com/denoland/deno |
