"""services/dev_log_shipper.py — Temporary live log shipper to a Mac dev server."""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)


class _BufferHandler(logging.Handler):
    def __init__(self, shipper: "DevLogShipperService") -> None:
        super().__init__()
        self._shipper = shipper

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        try:
            self._shipper.enqueue(record)
        except Exception:
            pass


class DevLogShipperService:
    """Live log shipping service (temporary thread-based)."""

    def __init__(self, config: ConfigManager | None = None, bus: Any = None) -> None:
        self._config = config
        self._bus = bus
        self._queue: deque[dict[str, Any]] = deque(maxlen=100000)
        self._handler: _BufferHandler | None = None
        self._enabled = False
        self._url = ""
        self._token = ""
        self._batch_size = 50
        self._flush_interval = 1.0
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._load_config()
        if self._enabled and self._url:
            self._install()

    def _load_config(self) -> None:
        cfg = self._config
        if cfg is None:
            return
        try:
            self._enabled = bool(cfg.get("dev_logs.enabled", False))
        except Exception:
            self._enabled = False
        self._url = str(cfg.get("dev_logs.url", "") or "")
        self._token = str(cfg.get("dev_logs.token", "") or "")
        try:
            self._batch_size = int(cfg.get("dev_logs.batch_size", 50))
        except (TypeError, ValueError):
            self._batch_size = 50
        try:
            self._flush_interval = float(cfg.get("dev_logs.flush_interval_seconds", 1))
        except (TypeError, ValueError):
            self._flush_interval = 1.0

    def _install(self) -> None:
        if self._handler is not None:
            return
        self._handler = _BufferHandler(self)
        lg = logging.getLogger("unifideck")
        lg.addHandler(self._handler)
        lg.setLevel(logging.DEBUG)
        for h in lg.handlers:
            h.setLevel(logging.DEBUG)
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def _uninstall(self) -> None:
        if self._handler is not None:
            logging.getLogger("unifideck").removeHandler(self._handler)
            self._handler = None
        self._stop_event.set()
        if self._worker is not None:
            self._worker.join(timeout=3)
        self._worker = None

    def enqueue(self, record: logging.LogRecord) -> None:
        if not self._enabled:
            return
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        entry = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": msg,
        }
        self._queue.append(entry)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            self._flush_once()
            self._stop_event.wait(self._flush_interval)
        # final drain
        self._flush_once()

    def _flush_once(self) -> None:
        if not self._queue or not self._url:
            return
        batch: list[dict[str, Any]] = []
        while self._queue and len(batch) < self._batch_size:
            batch.append(self._queue.popleft())
        if not batch:
            return
        payload = {"source": "unifideck", "lines": [json.dumps(e) for e in batch]}
        try:
            self._post(payload)
        except Exception as e:
            logger.debug("[DevLogShipper] flush failed: %s", e)
            # requeue failed batch at front (best effort)
            for e2 in reversed(batch):
                self._queue.appendleft(e2)

    def _post(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._url.rstrip("/") + "/ingest", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if self._token:
            req.add_header("X-Dev-Token", self._token)
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()

    async def start(self) -> None:
        self._load_config()
        if self._enabled and self._url:
            if self._handler is None:
                self._install()

    async def stop(self) -> None:
        self._uninstall()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "url": self._url,
            "queued": len(self._queue),
            "installed": self._handler is not None,
        }

    def configure(self, enabled: bool, url: str, token: str = "") -> dict[str, Any]:
        if self._config is not None:
            try:
                self._config.set("dev_logs.enabled", enabled)
                self._config.set("dev_logs.url", url)
                self._config.set("dev_logs.token", token)
            except Exception as e:
                logger.debug("[DevLogShipper] persist failed: %s", e)
        self._enabled = enabled
        self._url = url
        self._token = token
        if enabled and url:
            if self._handler is None:
                self._install()
        else:
            self._uninstall()
        return self.status()

# entry-point: dev tooling
