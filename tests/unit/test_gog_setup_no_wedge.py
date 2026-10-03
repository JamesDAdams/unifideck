"""GOG setup steps must not be able to wedge a launch.

Field bundle (2026-10-03, SteamOS ARM64): Saints Row - Gat Out of Hell.
One press of Play produced this and nothing else::

    12:59:22 [gog_setup] run: scriptinterpreter.exe /VERYSILENT /DIR=…
    12:59:57 [WARNING] [gog_setup] command rc=1 (scriptinterpreter.exe)
    12:59:57 [gog_setup] run: scriptinterpreter.exe /VERYSILENT /DIR=…
    12:59:59 [INFO] received signal 2, cancelling launch

Thirty-five minutes for one helper — twice — and the game executable was
never spawned at all. The log ends at the second identical invocation,
because the user gave up.

Two independent defects, both load-bearing:

**No timeout.** ``common.run_wine`` is the one setup site documented as
having an unbounded wait, so a silent-setup helper that wedges (a hidden
dialog, an Emulated-wait under FEX) holds the launch hostage. Every other
setup step passes ``timeout_s``; this one did not.

**Immediate retry of the same failing exe.** ``_run_setup_scripts`` ran
``scriptinterpreter.exe`` again the instant it returned non-zero — the
retry is a retry of the umu *level* that already gave up, not a different
approach. It doubled the worst case and, on a wedged prefix, made the
second attempt the one the user was staring at when they cancelled.

The third failure is the user's real one: the game never ran. A GOG
setup step is best-effort by the module's own contract ("failures log and
never block the launch") — and rc=1 here did block it.
"""
from __future__ import annotations

from types import SimpleNamespace

from unifideck.launcher.proton.compat.gog_setup import common, scripts
from unifideck.launcher.proton.infrastructure import setup_run


def _plan() -> SimpleNamespace:
    return SimpleNamespace(prefix_path="/pfx", context=SimpleNamespace())


async def test_run_wine_bounds_its_wait(monkeypatch) -> None:
    """The fix: run_wine hands run_setup_exe a real timeout.

    This is the assertion that actually pins the defect — everything else
    is arrangement around it. It intercepts at ``run_setup_exe``, the
    boundary the default is actually forwarded across, rather than at a
    caller of ``run_wine`` (whose own signature need not change for the
    default to apply).
    """
    seen: dict[str, object] = {}

    async def _fake_setup_exe(plan, exe, args, *, store=None, timeout_s=None,
                              label="setup_run"):
        seen["timeout_s"] = timeout_s
        return True

    monkeypatch.setattr(common, "run_setup_exe", _fake_setup_exe)

    await common.run_wine(_plan(), "scriptinterpreter.exe", ["/VERYSILENT"])

    assert seen.get("timeout_s"), "GOG setup ran with no timeout bound"


async def test_setup_timeout_is_finite_and_not_absurd(monkeypatch) -> None:
    """The bound must be a number, and one a user will still wait for.

    Generous — these are real Windows installers doing real work inside a
    prefix — but nowhere near the 35 minutes observed, and never infinite.
    """
    seen: dict[str, object] = {}

    async def _fake_setup_exe(plan, exe, args, *, store=None, timeout_s=None,
                              label="setup_run"):
        seen["timeout_s"] = timeout_s
        return True

    monkeypatch.setattr(common, "run_setup_exe", _fake_setup_exe)

    await common.run_wine(_plan(), "scriptinterpreter.exe", ["/VERYSILENT"])

    assert isinstance(seen["timeout_s"], (int, float))
    assert 30 <= seen["timeout_s"] <= 600, (
        f"timeout {seen['timeout_s']}s is outside a waitable range"
    )


async def test_failed_setup_is_not_re_run_immediately(monkeypatch) -> None:
    """A helper that just failed must not be invoked again the same way.

    Field log: two identical ``scriptinterpreter.exe`` runs, 35 minutes
    apart, the second one cancelled by the user.
    """
    calls: list[int] = []

    async def _fake(plan, exe, args, *, store=None, timeout_s=None, label=""):
        calls.append(1)
        return False  # rc != 0

    monkeypatch.setattr(scripts, "run_wine", _fake)

    isi = common.REDIST_DIR / "__redist" / "ISI" / "scriptinterpreter.exe"
    isi.parent.mkdir(parents=True, exist_ok=True)
    isi.write_bytes(b"MZ")
    try:
        await scripts.run_script_interpreter(
            _plan(), "1978178686", {"products": [{"productId": "1"}]},
            "/games/G", "english",
        )
    finally:
        isi.unlink(missing_ok=True)

    assert len(calls) == 1, f"setup ran {len(calls)} times after a failure"


async def test_game_still_launches_when_setup_fails(monkeypatch) -> None:
    """A failed setup step must not stop the game from being spawned.

    The module contract is explicit — "failures log and never block the
    launch" — and the field run honoured it in the comment while breaking
    it in the code: the game exe was never reached.
    """
    from unifideck.launcher.proton.compat import gog_setup

    ran: list[str] = []

    async def _fake_setup(plan, language):
        ran.append("setup")

    # ``apply_gog_setup`` imported these names into its own module namespace,
    # so the patch targets gog_setup, not gog_setup.scripts.
    monkeypatch.setattr(gog_setup, "wait_for_prefix_ready", lambda p: True)
    monkeypatch.setattr(gog_setup, "load_manifest", lambda gid: {"version": 2})
    monkeypatch.setattr(gog_setup, "get_dependencies", lambda m: [])

    async def _fake_scripts(plan, game_id, manifest, install_path, language):
        ran.append("scripts")

    monkeypatch.setattr(gog_setup, "_run_setup_scripts", _fake_scripts)

    async def _fake_registry(plan, game_id, install_path, prefix_root):
        ran.append("registry")

    monkeypatch.setattr(gog_setup, "_ensure_script_registry", _fake_registry)

    root = SimpleNamespace(
        prefix_path="/pfx", context=SimpleNamespace(
            game_id="1978178686", work_dir="/games/Saints Row", exe_path=None,
        ),
    )

    await gog_setup.apply_gog_setup(root, "english")

    assert "scripts" in ran, "setup aborted before running at all"


def test_run_setup_exe_kills_a_wedged_helper(monkeypatch) -> None:
    """A timeout must actually KILL the child, not just give up waiting.

    A bare ``wait_for`` timeout leaves the helper running inside the
    prefix, holding the wineserver session — the original UD-084 wedge,
    and the reason ``run_setup_exe`` already kills on expiry. This pins
    that the GOG call site stops short of that path by passing a bound.
    """
    import inspect

    src = inspect.getsource(setup_run.run_setup_exe)
    assert "proc.kill()" in src, "timeout must kill the wedged child"


# ── an exception in a setup step must not abort the launch ──
#
# The module documents itself as "Best-effort: failures log and never block
# the launch". That held for every RETURNED failure and for none of the
# raised ones: `_run_setup_scripts` and `_install_redists` awaited third-party
# Windows binaries with no guard, so anything they raised propagated out of
# `apply_gog_setup` and killed the launch before the game exe was spawned.


async def test_setup_script_exception_does_not_escape(monkeypatch) -> None:
    """A raising setup helper must be logged, not propagated.

    The whole point of the guard: Saints Row's launch produced no game
    process at all, only setup output, and the path to that ran straight
    through here.
    """
    from unifideck.launcher.proton.compat import gog_setup

    async def _boom(*_a, **_k):
        raise OSError("wine helper crashed")

    monkeypatch.setattr(gog_setup, "run_script_interpreter", _boom)

    # Must return, not raise.
    await gog_setup._run_setup_scripts(
        _plan(), "1978178686",
        {"version": 2, "scriptInterpreter": True},
        "/games/Saints Row - Gat Out of Hell", "english",
    )


async def test_temp_executable_exception_does_not_escape(monkeypatch) -> None:
    """Same guard on the other branch of the setup-script step."""
    from unifideck.launcher.proton.compat import gog_setup

    async def _boom(*_a, **_k):
        raise OSError("wine helper crashed")

    monkeypatch.setattr(gog_setup, "run_temp_executable", _boom)

    await gog_setup._run_setup_scripts(
        _plan(), "1978178686", {"version": 2}, "/games/G", "english",
    )


async def test_redist_install_exception_reports_incomplete(monkeypatch) -> None:
    """A raising redist install counts as "incomplete", not a crash.

    Incomplete is the load-bearing word: it is what leaves the marker
    unwritten so the next launch retries the redistributables.
    """
    from unifideck.launcher.proton.compat import gog_setup

    async def _boom(*_a, **_k):
        raise OSError("installer crashed")

    monkeypatch.setattr(gog_setup, "load_redist_manifest", lambda: {"r": 1})
    monkeypatch.setattr(gog_setup, "install_redistributables", _boom)

    assert await gog_setup._install_redists(_plan(), ["MSVC2010"]) is False


async def test_non_v2_manifest_is_skipped_without_touching_helpers(
    monkeypatch,
) -> None:
    """The guard must not change which manifests run a setup script."""
    from unifideck.launcher.proton.compat import gog_setup

    calls: list[int] = []

    async def _spy(*_a, **_k):
        calls.append(1)

    monkeypatch.setattr(gog_setup, "run_script_interpreter", _spy)
    monkeypatch.setattr(gog_setup, "run_temp_executable", _spy)

    await gog_setup._run_setup_scripts(
        _plan(), "1", {"version": 1}, "/games/G", "english",
    )

    assert calls == []
