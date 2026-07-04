"""WiFi scan/connect helpers for the /portal WiFi setup page.

Uses the classic wireless-tools + wpa_supplicant stack (iwlist/wpa_cli),
matching the iwgetid/`/proc/net/wireless` calls already used elsewhere in
app.py, rather than NetworkManager/nmcli. All functions fail soft: if the
tools aren't present (e.g. running this app off the Pi, in a dev
environment) they log a warning and return an empty/false result instead
of raising, so the portal page still renders.
"""
import logging
import re
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)

WIFI_IFACE = "wlan0"


def scan_networks(iface: str = WIFI_IFACE) -> List[str]:
    """Return a de-duplicated, order-preserving list of visible SSIDs."""
    try:
        result = subprocess.run(
            ["iwlist", iface, "scan"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning(f"WiFi scan unavailable ({exc})")
        return []

    if result.returncode != 0:
        logger.warning(f"WiFi scan failed: {result.stderr.strip() or result.stdout.strip()}")
        return []

    seen = set()
    networks = []
    for ssid in re.findall(r'ESSID:"([^"]*)"', result.stdout):
        if ssid and ssid not in seen:
            seen.add(ssid)
            networks.append(ssid)
    return networks


def _wpa_cli(*args: str, iface: str = WIFI_IFACE) -> Optional[str]:
    try:
        result = subprocess.run(
            ["wpa_cli", "-i", iface, *args],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning(f"wpa_cli unavailable ({exc})")
        return None

    if result.returncode != 0 or "FAIL" in result.stdout:
        logger.warning(f"wpa_cli {' '.join(args)} failed: {result.stdout.strip() or result.stderr.strip()}")
        return None
    return result.stdout.strip()


def save_credentials(ssid: str, password: str, iface: str = WIFI_IFACE) -> bool:
    """Add a wpa_supplicant network for `ssid`/`password`, enable it, and
    persist it to wpa_supplicant.conf via wpa_cli.

    Requires the eframe process to have access to wpa_supplicant's control
    interface (typically root, or membership in the 'netdev' group -
    adjust eframe.service if credential saves fail with a permissions
    error in the logs).
    """
    net_id = _wpa_cli("add_network", iface=iface)
    if net_id is None or not net_id.isdigit():
        return False

    steps = [
        _wpa_cli("set_network", net_id, f'ssid "{ssid}"', iface=iface),
        _wpa_cli("set_network", net_id, f'psk "{password}"', iface=iface),
        _wpa_cli("enable_network", net_id, iface=iface),
        _wpa_cli("save_config", iface=iface),
    ]
    ok = all(step is not None for step in steps)
    if ok:
        logger.info(f"WiFi: saved credentials for SSID '{ssid}'")
    else:
        # Clean up the half-configured network rather than leaving it behind.
        _wpa_cli("remove_network", net_id, iface=iface)
    return ok
