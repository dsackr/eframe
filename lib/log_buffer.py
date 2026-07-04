"""In-memory ring buffer of recent log lines, for the /portal/logs page.

eframe already logs plenty of useful detail via the standard `logging`
module (WiFi, display, uploads, EPD errors...). This just taps the root
logger with an additional handler that keeps the last N records in
memory, in the same spirit as the real Fraimic frame's on-device log
viewer, without needing a separate log file or rotation policy.
"""
import logging
from collections import deque
from datetime import datetime
from threading import Lock
from typing import Dict, List

_MAX_ENTRIES = 500
_lock = Lock()
_buffer: deque = deque(maxlen=_MAX_ENTRIES)


class RingBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        with _lock:
            _buffer.append({
                "time": datetime.fromtimestamp(record.created),
                "level": record.levelname,
                "logger": record.name,
                "message": message,
            })


def install(root_logger: logging.Logger, level: int = logging.INFO) -> RingBufferHandler:
    handler = RingBufferHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)
    return handler


def get_entries(limit: int = _MAX_ENTRIES) -> List[Dict]:
    with _lock:
        entries = list(_buffer)
    return entries[-limit:]


def clear() -> None:
    with _lock:
        _buffer.clear()
