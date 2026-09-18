"""System conditions that can pause the queue: battery power and metered networks."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time

import psutil

log = logging.getLogger(__name__)

_METERED_PS = (
    "$p=[Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,"
    "ContentType=WindowsRuntime]::GetInternetConnectionProfile();"
    "if($p){$p.GetConnectionCost().NetworkCostType}else{'None'}"
)


def on_battery() -> bool:
    try:
        battery = psutil.sensors_battery()
    except (AttributeError, NotImplementedError, OSError):
        return False
    return bool(battery and not battery.power_plugged)


class MeteredProbe:
    """Caches the (slow to query) metered-network state; refreshed off the UI thread."""

    TTL = 60.0

    def __init__(self) -> None:
        self._value = False
        self._checked = 0.0
        self._lock = threading.Lock()
        self._running = False

    def is_metered(self) -> bool:
        if sys.platform != "win32":
            return False
        if time.monotonic() - self._checked > self.TTL and not self._running:
            self._running = True
            threading.Thread(target=self._refresh, daemon=True).start()
        return self._value

    def _refresh(self) -> None:
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _METERED_PS],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout.strip()
            with self._lock:
                # Fixed/Variable cost types mean the user marked the connection as metered.
                self._value = out in ("Fixed", "Variable")
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.debug("Metered check failed: %s", exc)
        finally:
            self._checked = time.monotonic()
            self._running = False


metered = MeteredProbe()
