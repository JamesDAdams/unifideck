"""services/game_log_forwarder.py — Forward game.log to plugin logger on stop.

The launcher subprocess writes Proton/Wine/game stdout+stderr into
``~/.local/share/unifideck/launches/{launch_id}.game.log``.  That
process may not be able to reach the dev server (network isolation
inside pressure-vessel), so the plugin process — which *can* reach
the dev server — reads the most recent game.log after game_stopped
and logs its tail at ERROR level.  DevLogShipper then ships it.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import auto_wire, subscribe

logger = logging.getLogger(__name__)

_LAUNCHES_DIR = Path("~/.local/share/unifideck/launches").expanduser()
_TAIL_LINES = 80
_MAX_AGE_SECONDS = 30.0


class GameLogForwarder:

    def __init__(self, bus: Any | None = None) -> None:
        self._bus = bus
        if bus is not None:
            auto_wire(self, bus)

    @subscribe(Events.GAME_STOPPED)
    async def _on_game_stopped(self, **kwargs: Any) -> None:
        store = kwargs.get("store", "?")
        game_id = kwargs.get("game_id", "?")
        try:
            self._forward_latest(f"{store}:{game_id}")
        except Exception:
            logger.debug("[GameLogForwarder] forward failed", exc_info=True)

    def _forward_latest(self, game_key: str) -> None:
        if not _LAUNCHES_DIR.is_dir():
            return
        candidates = sorted(
            _LAUNCHES_DIR.glob("*.game.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return
        newest = candidates[0]
        age = time.time() - newest.stat().st_mtime
        if age > _MAX_AGE_SECONDS:
            logger.debug(
                "[GameLogForwarder] newest game.log is %.0fs old, skipping", age,
            )
            return
        text = newest.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        logger.info(
            "[GameLogForwarder] %s — game.log %s (%d lines, %.0fs old)",
            game_key, newest.name, len(lines), age,
        )
        tail = lines[-_TAIL_LINES:]
        for line in tail:
            logger.error("[game.log] %s", line)
        launcher_log = newest.with_suffix("").with_suffix(".log")
        if launcher_log.is_file():
            lt = launcher_log.read_text(encoding="utf-8", errors="replace")
            ll = lt.splitlines()
            logger.info(
                "[GameLogForwarder] launcher log %s (%d lines)",
                launcher_log.name, len(ll),
            )
            for line in ll[-_TAIL_LINES:]:
                logger.error("[launcher.log] %s", line)
