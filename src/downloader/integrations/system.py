"""OS integration: sleep/shutdown after the queue, start with the OS, URL protocol handler."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_KEY_NAME = "Downloader"
PROTOCOL = "downloader"


def launch_command(*extra: str) -> list[str]:
    """argv that starts this app (frozen exe or `python -m downloader`)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *extra]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw) if pythonw.exists() else sys.executable
    return [exe, "-m", "downloader", *extra]


def _quote(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv)


# ---------------------------------------------------------------- power


def sleep() -> None:
    if sys.platform == "win32":
        import ctypes

        # SetSuspendState(hibernate=False, force=False, disableWakeEvents=False)
        ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
    elif sys.platform == "darwin":
        subprocess.Popen(["pmset", "sleepnow"])
    else:
        subprocess.Popen(["systemctl", "suspend"])


def shutdown() -> None:
    if sys.platform == "win32":
        subprocess.Popen(["shutdown", "/s", "/t", "0"], creationflags=subprocess.CREATE_NO_WINDOW)
    elif sys.platform == "darwin":
        subprocess.Popen(["osascript", "-e", 'tell app "System Events" to shut down'])
    else:
        subprocess.Popen(["systemctl", "poweroff"])


# ---------------------------------------------------------------- autostart


def set_autostart(enabled: bool) -> None:
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_KEY_NAME, 0, winreg.REG_SZ,
                                  _quote(launch_command("--minimized")))
            else:
                try:
                    winreg.DeleteValue(key, APP_KEY_NAME)
                except FileNotFoundError:
                    pass
    elif sys.platform.startswith("linux"):
        desktop = Path.home() / ".config" / "autostart" / "downloader.desktop"
        if enabled:
            desktop.parent.mkdir(parents=True, exist_ok=True)
            desktop.write_text(
                "[Desktop Entry]\nType=Application\nName=Downloader\n"
                f"Exec={_quote(launch_command('--minimized'))}\n", encoding="utf-8")
        else:
            desktop.unlink(missing_ok=True)
    else:
        log.info("Autostart on this platform is configured by the installer")


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return (Path.home() / ".config" / "autostart" / "downloader.desktop").exists()
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_KEY_NAME)
            return True
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------- protocol handler


def register_protocol() -> None:
    """Register downloader:// for the current user (Windows). The installer does this too."""
    if sys.platform != "win32":
        return
    import winreg

    base = rf"Software\Classes\{PROTOCOL}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Downloader")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, _quote(launch_command()) + ' "%1"')


def url_from_protocol(arg: str) -> str | None:
    """downloader://https://example.com/v -> https://example.com/v (also ?url=...)."""
    from urllib.parse import parse_qs, unquote, urlparse

    if not arg.lower().startswith(f"{PROTOCOL}:"):
        return None
    rest = arg[len(PROTOCOL) + 1:].lstrip("/")
    if rest.startswith(("add?", "add/?")):
        query = parse_qs(urlparse("x://h/" + rest).query)
        return query.get("url", [None])[0]
    rest = unquote(rest)
    if rest.startswith(("http:/", "https:/")) and "://" not in rest:
        rest = rest.replace(":/", "://", 1)  # some browsers collapse the double slash
    return rest if rest.startswith(("http://", "https://")) else None


def register_protocol_safe() -> None:
    try:
        register_protocol()
    except OSError as exc:
        log.warning("Could not register %s:// handler: %s", PROTOCOL, exc)
