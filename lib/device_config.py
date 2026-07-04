"""Small JSON-backed store for settings that need to survive a restart but
aren't real hardware state: a stable per-device identifier (device_key) and
the user-facing display orientation.

Added to support two Fraimic REST API feature requests that eframe now
mimics ahead of the real frame:
  - https://github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide/issues/2
    (report device_key / dimensions in /api/info)
  - https://github.com/Fraimic/Fraimic_eink_canvas_home_assistant_restAPI_guide/issues/3
    (let orientation be set, and report it in /api/info)
"""
import json
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

DEFAULTS: Dict[str, Any] = {
    "device_key": None,   # generated on first run, then persisted forever
    "orientation": "portrait",  # "portrait" | "landscape"
}

_lock = Lock()
_path: Optional[Path] = None
_cache: Dict[str, Any] = {}


def _read(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def init(path: Path) -> Dict[str, Any]:
    """Load config from disk (creating it on first run), filling in any
    missing defaults - notably a freshly generated device_key."""
    global _path, _cache
    with _lock:
        _path = path
        data = {**DEFAULTS, **_read(path)}
        if not data.get("device_key"):
            data["device_key"] = uuid.uuid4().hex
        _write(path, data)
        _cache = data
        return dict(_cache)


def get() -> Dict[str, Any]:
    with _lock:
        return dict(_cache)


def update(**kwargs: Any) -> Dict[str, Any]:
    """Merge kwargs into the config and persist immediately."""
    global _cache
    with _lock:
        _cache = {**_cache, **kwargs}
        if _path is not None:
            _write(_path, _cache)
        return dict(_cache)
