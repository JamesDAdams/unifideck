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


def test_thread_based_shipper_queue_retry_status() -> None:
    import logging
    import threading

    from unittest import mock

    svc = DevLogShipperService(config=None)
    svc.configure(True, "http://127.0.0.1:8765", "tok")
    st = svc.status()
    assert st["installed"] is True
    assert st["queued"] == 0
    assert svc._worker is not None
    assert svc._worker.is_alive()
    assert svc._worker.daemon is True

    # Stop background worker to make flushing deterministic (before enqueue;
    # worker starts with an empty queue and won't touch posted item).
    svc._stop_event.set()
    if svc._worker is not None:
        svc._worker.join(timeout=2)
        svc._worker = None

    rec = logging.LogRecord("unifideck", logging.INFO, "x.py", 1, "msg", (), None)
    svc.enqueue(rec)
    assert svc.status()["queued"] == 1

    with mock.patch.object(svc, "_post", side_effect=RuntimeError("boom")):
        with mock.patch("unifideck.services.dev_log_shipper.logger.debug"):
            svc._flush_once()
    assert svc.status()["queued"] == 1

    with mock.patch.object(svc, "_post", return_value=None):
        svc._flush_once()
    assert svc.status()["queued"] == 0
