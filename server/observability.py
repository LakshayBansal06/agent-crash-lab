"""Structured logs, request ids and Prometheus metrics."""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections import defaultdict
from typing import Any

REDACT = {"authorization", "cookie", "x-api-key"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in getattr(record, "ctx", {}).items():
            out[k] = "[redacted]" if k.lower() in REDACT else v
        if record.exc_info:
            out["error"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class StdoutHandler(logging.StreamHandler):
    """Always writes to the *current* sys.stdout (12-factor: logs are an event stream)."""

    @property
    def stream(self):  # type: ignore[override]
        return sys.stdout

    @stream.setter
    def stream(self, _value) -> None:
        pass


def get_logger(name: str = "crashlab") -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = StdoutHandler()
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def log(event: str, level: int = logging.INFO, exc_info: bool = False, **ctx: Any) -> None:
    get_logger().log(level, event, extra={"ctx": ctx}, exc_info=exc_info)


class Metrics:
    """Minimal Prometheus text exposition, no dependency."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self.gauges: dict[str, float] = {}
        self.started = time.time()

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self.lock:
            self.counters[(name, tuple(sorted(labels.items())))] += value

    def set(self, name: str, value: float) -> None:
        with self.lock:
            self.gauges[name] = value

    def render(self, extra_gauges: dict[str, float] | None = None) -> str:
        lines = []
        with self.lock:
            for (name, labels), v in sorted(self.counters.items()):
                lab = ",".join(f'{k}="{val}"' for k, val in labels)
                lines.append(f"crashlab_{name}{{{lab}}} {v}" if lab else f"crashlab_{name} {v}")
            for name, v in sorted({**self.gauges, **(extra_gauges or {})}.items()):
                lines.append(f"crashlab_{name} {v}")
        lines.append(f"crashlab_uptime_seconds {time.time() - self.started:.0f}")
        return "\n".join(lines) + "\n"
