"""Build the desktop app: icon -> PyInstaller bundle -> (Windows) Inno Setup installer.

Usage:  python build/build.py [--no-installer]
Output: dist/Downloader/ (app folder), dist/Downloader-<ver>-Setup.exe, dist/SHA256SUMS.txt
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "out"
DIST = ROOT / "dist"


def version() -> str:
    text = (ROOT / "src" / "downloader" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def make_icon() -> None:
    from PIL import Image

    OUT.mkdir(parents=True, exist_ok=True)
    src = ROOT / "src" / "downloader" / "ui" / "assets" / "app-256.png"
    Image.open(src).save(OUT / "app.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])


def make_version_info(ver: str) -> None:
    parts = (ver.split(".") + ["0", "0", "0"])[:4]
    nums = ", ".join(str(int(re.sub(r"\D", "", p) or 0)) for p in parts)
    (OUT / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({nums}), prodvers=({nums})),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Downloader'),
    StringStruct('FileDescription', 'Downloader'),
    StringStruct('FileVersion', '{ver}'),
    StringStruct('ProductName', 'Downloader'),
    StringStruct('ProductVersion', '{ver}'),
    StringStruct('OriginalFilename', 'Downloader.exe')])]),
  VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
""", encoding="utf-8")


def pyinstaller() -> None:
    shutil.rmtree(DIST / "Downloader", ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(DIST), "--workpath", str(OUT / "work"),
                    str(ROOT / "build" / "downloader.spec")], check=True)


def find_iscc() -> str | None:
    found = shutil.which("iscc")
    if found:
        return found
    bases = [Path(r"C:\Program Files (x86)\Inno Setup 6"), Path(r"C:\Program Files\Inno Setup 6")]
    local = os.environ.get("LOCALAPPDATA")
    if local:  # per-user install (e.g. `winget install --scope user`)
        bases.append(Path(local) / "Programs" / "Inno Setup 6")
    for base in bases:
        candidate = base / "ISCC.exe"
        if candidate.exists():
            return str(candidate)
    return None


def installer(ver: str) -> Path | None:
    iscc = find_iscc()
    if not iscc:
        print("Inno Setup (ISCC.exe) not found; skipping installer.")
        return None
    subprocess.run([iscc, f"/DAppVersion={ver}", f"/DSourceDir={DIST / 'Downloader'}",
                    f"/DOutputDir={DIST}", f"/DIconFile={OUT / 'app.ico'}",
                    str(ROOT / "installer" / "downloader.iss")], check=True)
    return DIST / f"Downloader-{ver}-Setup.exe"


def checksums(files: list[Path]) -> None:
    lines = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    (DIST / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print((DIST / "SHA256SUMS.txt").read_text())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-installer", action="store_true")
    args = parser.parse_args()
    ver = version()
    make_icon()
    make_version_info(ver)
    pyinstaller()
    artifacts: list[Path] = []
    if sys.platform == "win32" and not args.no_installer:
        setup = installer(ver)
        if setup:
            artifacts.append(setup)
    if artifacts:
        checksums(artifacts)
    print(f"Built Downloader {ver} -> {DIST}")


if __name__ == "__main__":
    main()
