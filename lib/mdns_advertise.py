"""Advertise a stable, unique mDNS hostname for this device.

Fraimic guide issue #1 (shared fraimic.local hostname is ambiguous with
multiple frames - https://github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide/issues/1)
proposes deriving a per-device hostname from each frame's device_key so
multiple units on one network don't fight over a single shared `.local`
name. This module does the eframe-side equivalent: it publishes
`eframe-<devicekey8>.local` via zeroconf, alongside whatever shared name
mDNS/avahi already resolves for this host, so eframe is unambiguously
addressable even with several units on the same LAN.

Degrades gracefully (logs and no-ops) if the `zeroconf` package isn't
installed or registration fails for any reason - losing the extra
hostname shouldn't take the app down.
"""
import logging
import socket
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_zeroconf = None
_service_info = None
_lock = threading.Lock()


def start(device_key: str, ip: str, port: int = 80, hostname_prefix: str = "eframe") -> Optional[str]:
    """Register `<hostname_prefix>-<devicekey8>.local` for this device.

    Returns the hostname that was registered, or None if mDNS advertising
    isn't available right now.
    """
    global _zeroconf, _service_info

    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        logger.warning("mDNS: 'zeroconf' package not installed - skipping unique hostname advertisement")
        return None

    with _lock:
        if _zeroconf is not None:
            return None  # already started

        try:
            addr_bytes = socket.inet_aton(ip)
        except OSError:
            logger.warning(f"mDNS: could not parse IP {ip!r} - skipping advertisement")
            return None

        short_key = device_key[:8]
        unique_host = f"{hostname_prefix}-{short_key}.local."
        service_name = f"{hostname_prefix}-{short_key}._http._tcp.local."

        info = ServiceInfo(
            "_http._tcp.local.",
            service_name,
            addresses=[addr_bytes],
            port=port,
            server=unique_host,
            properties={"device_key": device_key},
        )

        zc = Zeroconf()
        try:
            zc.register_service(info)
        except Exception as exc:
            logger.warning(f"mDNS: failed to register {unique_host}: {exc}")
            zc.close()
            return None

        _zeroconf = zc
        _service_info = info
        logger.info(f"mDNS: advertising unique hostname {unique_host} ({ip}:{port})")
        return unique_host.rstrip(".")


def stop() -> None:
    global _zeroconf, _service_info
    with _lock:
        if _zeroconf is None:
            return
        try:
            if _service_info is not None:
                _zeroconf.unregister_service(_service_info)
        except Exception as exc:
            logger.warning(f"mDNS: error unregistering service: {exc}")
        finally:
            _zeroconf.close()
            _zeroconf = None
            _service_info = None
