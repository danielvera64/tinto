"""WiFi control via NetworkManager (nmcli, Raspberry Pi OS Bookworm).

Provides scanning, connecting, and the Tinto-Setup hotspot used for
provisioning: when the device has no network, it can host its own
access point; the portal page (reader/portal.py) is then reachable at
http://10.42.0.1:8080 to pick a network and enter its password.
Joining a network from hotspot mode is handled by NetworkManager
itself (single radio: activating the client connection tears the
hotspot down).

Everything degrades gracefully where nmcli is missing (development
on a Mac): available() is False and callers show "unavailable".
"""

import logging
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

HOTSPOT_SSID = "Tinto-Setup"
HOTSPOT_PASSWORD = "tintosetup"
HOTSPOT_CONNECTION = "TintoHotspot"
HOTSPOT_URL = "http://10.42.0.1:8080"  # NetworkManager's default AP IP

_available = None


def _nmcli(*args, timeout=30):
    return subprocess.run(["nmcli", *args], capture_output=True,
                          text=True, timeout=timeout)


def available() -> bool:
    global _available
    if _available is None:
        try:
            _available = _nmcli("-v", timeout=10).returncode == 0
        except Exception:
            _available = False
    return _available


def _unescape(value: str) -> str:
    return value.replace("\\:", ":").replace("\\\\", "\\")


def scan():
    """Returns [{ssid, signal, secured}] sorted by signal, deduped."""
    r = _nmcli("-t", "-f", "SSID,SIGNAL,SECURITY",
               "device", "wifi", "list", "--rescan", "yes", timeout=45)
    nets = {}
    for line in r.stdout.splitlines():
        try:
            ssid_raw, signal, security = line.rsplit(":", 2)
        except ValueError:
            continue
        ssid = _unescape(ssid_raw)
        if not ssid or ssid == HOTSPOT_SSID:
            continue
        entry = {"ssid": ssid,
                 "signal": int(signal or 0),
                 "secured": security not in ("", "--")}
        if ssid not in nets or entry["signal"] > nets[ssid]["signal"]:
            nets[ssid] = entry
    return sorted(nets.values(), key=lambda n: -n["signal"])


def connect(ssid: str, password: str = ""):
    """Joins a network. Returns (ok, detail). Activating a client
    connection tears down the hotspot automatically (single radio)."""
    args = ["device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    try:
        r = _nmcli(*args, timeout=90)
    except Exception as exc:
        return False, str(exc)
    detail = (r.stdout + r.stderr).strip()
    logger.info("wifi connect %s: rc=%s %s", ssid, r.returncode, detail)
    return r.returncode == 0, detail


def hotspot_active() -> bool:
    r = _nmcli("-t", "-f", "NAME", "connection", "show", "--active")
    return HOTSPOT_CONNECTION in r.stdout.splitlines()


def hotspot_start():
    """Starts the setup access point. Returns (ok, detail)."""
    try:
        r = _nmcli("device", "wifi", "hotspot",
                   "con-name", HOTSPOT_CONNECTION,
                   "ssid", HOTSPOT_SSID,
                   "password", HOTSPOT_PASSWORD, timeout=30)
    except Exception as exc:
        return False, str(exc)
    logger.info("hotspot start: rc=%s", r.returncode)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def hotspot_stop():
    """Stops the setup AP; NetworkManager reconnects to saved WiFi."""
    try:
        _nmcli("connection", "down", HOTSPOT_CONNECTION)
    except Exception as exc:
        logger.warning("hotspot stop failed: %s", exc)


def current_ssid():
    r = _nmcli("-t", "-f", "ACTIVE,SSID", "device", "wifi")
    for line in r.stdout.splitlines():
        if line.startswith("yes:"):
            return _unescape(line[4:])
    return None


def is_online() -> bool:
    try:
        r = _nmcli("-t", "networking", "connectivity", "check",
                   timeout=20)
        return r.stdout.strip() == "full"
    except Exception:
        return False


def start_fallback_watchdog(grace=60, interval=90):
    """Background thread: if the device sits offline (and is not
    already hosting the setup AP), start the hotspot so the portal
    stays reachable for provisioning. No-op without nmcli."""
    if not available():
        return None

    def loop():
        time.sleep(grace)
        while True:
            try:
                if not is_online() and not hotspot_active():
                    logger.warning("offline: starting the setup hotspot")
                    hotspot_start()
            except Exception as exc:
                logger.warning("wifi watchdog: %s", exc)
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t
