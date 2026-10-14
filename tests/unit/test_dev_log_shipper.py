from __future__ import annotations

from types import SimpleNamespace
from unifideck.services.dev_log_shipper import DevLogShipperService


def test_disabled_by_default() -> None:
    svc = DevLogShipperService(config=None)
    assert svc.status()["enabled"] is False
    assert svc.status()["installed"] is False


def test_configure_enables_and_installs_handler() -> None:
    svc = DevLogShipperService(config=None)
    st = svc.configure(True, "http://127.0.0.1:8765", "tok")
    assert st["enabled"] is True
    assert st["url"] == "http://127.0.0.1:8765"
    assert st["installed"] is True
    svc.configure(False, "", "")
    assert svc.status()["installed"] is False


def test_enqueue_does_nothing_when_disabled() -> None:
    svc = DevLogShipperService(config=None)
    import logging
    rec = logging.LogRecord("unifideck", logging.INFO, "x.py", 1, "msg", (), None)
    svc.enqueue(rec)
    assert svc.status()["queued"] == 0


def test_enqueue_queues_when_enabled() -> None:
    svc = DevLogShipperService(config=None)
    svc.configure(True, "http://127.0.0.1:8765")
    import logging
    rec = logging.LogRecord("unifideck", logging.INFO, "x.py", 1, "msg %s", ("v",), None)
    svc.enqueue(rec)
    assert svc.status()["queued"] == 1
