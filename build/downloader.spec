# PyInstaller spec — run via `python build/build.py` (onedir, windowed).
# ruff: noqa
from pathlib import Path

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src" / "downloader"

datas = [
    # Alembic loads migration scripts from disk at runtime.
    (str(SRC / "db" / "migrations"), "downloader/db/migrations"),
    (str(SRC / "ui" / "assets"), "downloader/ui/assets"),
    (str(ROOT / "browser-extension"), "browser-extension"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
]

a = Analysis(
    [str(ROOT / "build" / "entry.py")],
    pathex=[str(ROOT / "src")],
    datas=datas,
    hiddenimports=[
        "sqlalchemy.dialects.sqlite",
        "logging.config",
    ],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf",
              "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Downloader",
    icon=str(ROOT / "build" / "out" / "app.ico"),
    console=False,
    version=str(ROOT / "build" / "out" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Downloader")
