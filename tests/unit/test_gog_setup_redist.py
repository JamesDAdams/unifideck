"""GOG manifest redistributables — auth path, verification, and no latching.

Three regressions, all from one log bundle where a GOG UE4 title showed a
"Visual C++ required" dialog on all 17 launch attempts:

1. ``common.AUTH_CONFIG`` pointed at ``~/.config/unifideck/gogdl/auth.json``, a
   subdir path that has never existed — the real file is the flat
   ``gogdl_auth.json`` every other gogdl call passes as ``--auth-config-path``.
   So ``ensure_redist_downloaded`` bailed with "cannot download redist
   (gogdl=True auth=False)" and NO GOG game ever received its manifest-declared
   redistributables. Identical to the bug already fixed for Comet in
   ``compat/gog.py``, which is why both now share one definition.
2. gogdl's exit code was treated as proof; it isn't. The download is verified
   against the tree it was supposed to fill.
3. ``apply_gog_setup`` wrote its "done" marker unconditionally, so a prefix that
   got nothing installed never retried.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from unifideck.launcher.proton.compat.gog_setup import common, redist


def _plan(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        context=SimpleNamespace(
            plugin_dir=tmp_path / "plugin", game_key="gog:1589319779",
        ),
    )


def test_auth_config_is_the_flat_file_the_plugin_actually_writes():
    # A subdir path here is the bug: nothing writes gogdl/auth.json.
    assert common.AUTH_CONFIG.name == "gogdl_auth.json"
    assert common.AUTH_CONFIG.parent.name == "unifideck"


def test_comet_and_redist_share_one_auth_definition():
    from unifideck.launcher.proton.compat import gog

    assert gog._GOGDL_AUTH_FILE == common.AUTH_CONFIG


async def test_missing_auth_reports_failure_rather_than_silently_passing(
    tmp_path, monkeypatch, _isolated_redist_paths,
):
    monkeypatch.setattr(redist, "REDIST_DIR", _isolated_redist_paths.parent)
    monkeypatch.setattr(redist, "AUTH_CONFIG", tmp_path / "absent.json")
    monkeypatch.setattr(redist, "launcher_toast", lambda *a, **k: None)

    ok = await redist.ensure_redist_downloaded(_plan(tmp_path), ["MSVC2019"])

    assert ok is False


async def test_download_is_verified_against_disk_not_the_exit_code(
    tmp_path, monkeypatch, _isolated_redist_paths,
):
    """A gogdl that exits 0 without writing anything must NOT count as success."""
    redist_dir = _isolated_redist_paths
    auth = tmp_path / "gogdl_auth.json"
    auth.write_text("{}", encoding="utf-8")
    gogdl = tmp_path / "plugin" / "bin" / "gogdl"
    gogdl.parent.mkdir(parents=True)
    gogdl.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(redist, "REDIST_DIR", redist_dir.parent)
    monkeypatch.setattr(redist, "AUTH_CONFIG", auth)
    monkeypatch.setattr(redist, "launcher_toast", lambda *a, **k: None)

    async def _noop_download(_gogdl, _missing):
        return  # exits "cleanly", writes nothing

    monkeypatch.setattr(redist, "_run_redist_download", _noop_download)

    ok = await redist.ensure_redist_downloaded(_plan(tmp_path), ["UE4REDIST"])

    assert ok is False


async def test_present_deps_need_no_download(tmp_path, monkeypatch):
    redist_dir = tmp_path / "redist"
    for dep in ("ISI", "UE4REDIST"):
        d = redist_dir / "__redist" / dep
        d.mkdir(parents=True)
        (d / "payload").write_text("x", encoding="utf-8")

    monkeypatch.setattr(redist, "REDIST_DIR", redist_dir)
    monkeypatch.setattr(redist, "launcher_toast", lambda *a, **k: None)

    called = False

    async def _fail(_gogdl, _missing):
        nonlocal called
        called = True

    monkeypatch.setattr(redist, "_run_redist_download", _fail)

    ok = await redist.ensure_redist_downloaded(_plan(tmp_path), ["UE4REDIST"])

    assert ok is True
    assert called is False


@pytest.mark.parametrize(
    ("redists_ok", "marker_expected"),
    [(True, True), (False, False)],
)
async def test_marker_written_only_when_redists_landed(
    tmp_path, monkeypatch, redists_ok, marker_expected,
):
    """A failed redist install must leave the marker off so the next launch retries."""
    from unifideck.launcher.proton.compat import gog_setup

    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (prefix / "system.reg").write_text("", encoding="utf-8")

    plan = SimpleNamespace(
        prefix_path=prefix,
        context=SimpleNamespace(
            game_id="1589319779", work_dir=tmp_path / "game",
            exe_path=tmp_path / "game" / "g.exe", game_key="gog:1589319779",
            plugin_dir=tmp_path / "plugin",
        ),
    )

    monkeypatch.setattr(gog_setup, "wait_for_prefix_ready", lambda *a, **k: True)
    monkeypatch.setattr(gog_setup, "_ensure_script_registry", _async_noop)
    monkeypatch.setattr(gog_setup, "_run_setup_scripts", _async_noop)
    monkeypatch.setattr(
        gog_setup, "load_manifest", lambda _gid: {"version": 2, "dependencies": ["MSVC2019"]},
    )
    monkeypatch.setattr(gog_setup, "get_dependencies", lambda _m: ["MSVC2019"])

    async def _download(_plan, _deps):
        return redists_ok

    async def _install(_plan, _deps):
        return True

    monkeypatch.setattr(gog_setup, "ensure_redist_downloaded", _download)
    monkeypatch.setattr(gog_setup, "_install_redists", _install)

    await gog_setup.apply_gog_setup(plan)

    marker = prefix / gog_setup._MARKER_NAME
    assert marker.is_file() is marker_expected


async def _async_noop(*_a, **_k):
    return None


@pytest.fixture
def _isolated_redist_paths(tmp_path, monkeypatch):
    """Point every redist path at the scratch tree.

    ``_redist_done_marker()`` derives the marker from ``REDIST_DIR`` at call
    time, so patching ``REDIST_DIR`` alone is now enough for both — no test
    can reach, or write, the live ``~/.config/unifideck/gogdl/redist``.
    """
    monkeypatch.setattr(redist, "REDIST_DIR", tmp_path / "redist")
    return tmp_path / "redist"


async def test_redist_toast_uses_valid_launcher_toast_kwargs(
    tmp_path, monkeypatch, _isolated_redist_paths,
):
    """A misspelled toast kwarg must not abort the whole redist install.

    Field (0.7.9, SteamOS ARM64, gog:1578751750 Mafia III): the toast call
    passed ``i8n_title_key`` instead of ``i18n_title_key``.
    ``launcher_toast()`` raised ``TypeError``, which propagated out of
    ``ensure_redist_downloaded`` and aborted ``apply_gog_setup`` before it
    downloaded or installed a single redistributable — every launch lost its
    dependencies to a typo in the progress toast. Field log::

        [compat.gog] gog_setup failed (non-fatal)
        TypeError: launcher_toast() got an unexpected keyword argument
        'i8n_title_key'. Did you mean 'i18n_title_key'?

    The REAL ``launcher_toast`` and the REAL ``record_event`` both run here —
    patching either is exactly the frame that hid this bug in the first place,
    since the TypeError was raised inside the real function. So the assertion
    is on the JSONL the launcher actually writes for the frontend to read,
    which ``tests/conftest.py`` already redirects away from the live file.
    """
    import json

    import unifideck.launcher.frontend_bridge as bridge

    auth = tmp_path / "gogdl_auth.json"
    auth.write_text("{}", encoding="utf-8")
    gogdl = tmp_path / "plugin" / "bin" / "gogdl"
    gogdl.parent.mkdir(parents=True)
    gogdl.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(redist, "AUTH_CONFIG", auth)
    monkeypatch.setattr(redist, "_gogdl_bin", lambda _plan: gogdl)

    async def _download(_gogdl, _missing):
        return  # writes nothing, so the call reports failure rather than lying

    monkeypatch.setattr(redist, "_run_redist_download", _download)
    # Toasts are suppressed during install-time warmup, never on a launch.
    monkeypatch.setattr(
        bridge, "_SUPPRESSED", bridge.contextvars.ContextVar("s", default=False),
    )

    # No TypeError: the toast is emitted with the correct kwarg, and the
    # function still completes and reports the truthful outcome.
    ok = await redist.ensure_redist_downloaded(_plan(tmp_path), ["MSVC2019"])

    assert ok is False  # verified against disk, per test_download_is_verified

    lines = [
        json.loads(line) for line in
        bridge.EVENTS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mine = [
        rec["kwargs"] for rec in lines
        if rec.get("event") == "launcher_stage"
        and rec["kwargs"]["i18n_key"] == "toasts.launcher.installingRedistMessage"
    ]
    assert mine == [{
        "i18n_key": "toasts.launcher.installingRedistMessage",
        "i18n_title_key": "toasts.launcher.installingRedist",
        "game_title": "gog:1589319779",
        "priority": "normal",
    }]



async def test_redist_toast_does_not_leak_into_the_real_config_dir(
    tmp_path, monkeypatch, _isolated_redist_paths,
):
    """The done-marker must land in the isolated tree, never the live one."""
    monkeypatch.setattr(redist, "AUTH_CONFIG", tmp_path / "absent.json")
    # No deps to download -> the "all present" branch writes the marker.
    redist_dir = _isolated_redist_paths
    dep = redist_dir / "__redist" / "ISI"
    dep.mkdir(parents=True)
    (dep / "payload").write_text("x", encoding="utf-8")

    ok = await redist.ensure_redist_downloaded(_plan(tmp_path), [])

    assert ok is True
    assert redist._redist_done_marker().is_file()
    assert str(redist.REDIST_DIR).startswith(str(tmp_path))
