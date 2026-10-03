"""Unit tests for ``handlers.exit_status`` — played game vs failed launch.

Regression (ARM64 + FEX-Emu host, field logs ``fbe72431`` / ``2621d2e8``
/ ``69edd921``): two Epic games ran — 30 s and 56 s, well past the point
where the runtime, the prefix and the game's own loader are all up — and
then exited with the game's own status (3, 127). Every one of those was
turned into ``GameFailedError``, which fires the "failed to launch" toast
AND records a launch failure against the circuit breaker. To the user,
a game they played and closed looked exactly like a broken launcher.

The distinction is not the exit code — it is whether the process ever got
going. These tests pin both halves of that:

  * past the bootstrap window, a non-zero code is the game's own and the
    launch succeeds (with the code preserved for the log);
  * inside the window, the same code is still a launch failure;
  * umu's own unrecoverable codes (2/74) always fail, since a process that
    exits that fast never started the game either way.

``GameRun`` is fed by ``run_umu_with_retry``'s ``on_exit``; the duration
is scripted here the way umu measures it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from unifideck.launcher.proton.infrastructure.exit_status import GameRun, finish_launch
from unifideck.launcher.proton.infrastructure.umu_runtime import (
    _BOOTSTRAP_FAILED_SECONDS,
)
from unifideck.launcher.types.errors import GameFailedError, UmuRuntimeError


def _make_plan(store: str = "epic", game_id: str = "Quail") -> SimpleNamespace:
    return SimpleNamespace(
        context=SimpleNamespace(store=store, game_id=game_id),
        state=SimpleNamespace(game_exit_code=None),
    )


def _run(rc: int, ran_for: float) -> GameRun:
    run = GameRun()
    run(rc, ran_for)
    return run


# ── the regression ──


@pytest.mark.parametrize("rc", [1, 3, 127])
def test_long_session_exit_is_not_a_launch_failure(rc: int) -> None:
    """The field case: ran past bootstrap, exited with the game's code."""
    plan = _make_plan()

    result = finish_launch(plan, rc, _run(rc, 56.0))

    assert result == 0, "a played game is a successful launch"
    assert plan.state.game_exit_code == rc, "real code kept for the log"


def test_long_session_exit_is_not_a_circuit_breaker_event() -> None:
    """No exception means no ``GameFailedError`` and no failure record.

    This is the part that used to walk a user into being blocked from
    launching a game they had simply finished playing.
    """
    plan = _make_plan()

    try:
        finish_launch(plan, 127, _run(127, 56.0))
    except (GameFailedError, UmuRuntimeError) as e:  # pragma: no cover
        pytest.fail(f"played game raised {type(e).__name__}: {e}")


def test_boundary_is_the_bootstrap_window() -> None:
    """One second either side of the window decides the two outcomes."""
    just_under = _make_plan()
    with pytest.raises(GameFailedError):
        finish_launch(just_under, 3, _run(3, _BOOTSTRAP_FAILED_SECONDS - 1))

    just_over = _make_plan()
    assert finish_launch(just_over, 3, _run(3, _BOOTSTRAP_FAILED_SECONDS)) == 0


# ── the guard must not disarm the real failure ──


def test_fast_failure_is_still_a_launch_failure() -> None:
    plan = _make_plan()

    with pytest.raises(GameFailedError) as exc:
        finish_launch(plan, 127, _run(127, 2.0))

    assert exc.value.subprocess_rc == 127


@pytest.mark.parametrize("rc", [2, 74])
def test_umu_bootstrap_codes_always_fail(rc: int) -> None:
    """2/74 mean umu itself never came up — never a played game."""
    plan = _make_plan()

    with pytest.raises(UmuRuntimeError):
        finish_launch(plan, rc, _run(rc, _BOOTSTRAP_FAILED_SECONDS + 60))


def test_clean_exit_is_always_success() -> None:
    plan = _make_plan()

    assert finish_launch(plan, 0, _run(0, 0.5)) == 0
    assert plan.state.game_exit_code == 0


def test_store_is_named_in_the_error() -> None:
    plan = _make_plan(store="gog", game_id="RimWorld")

    with pytest.raises(GameFailedError) as exc:
        finish_launch(plan, 5, _run(5, 1.0))

    assert "gog" in str(exc.value)


# ── GameRun itself ──


def test_game_run_records_the_last_attempt() -> None:
    """A launch runs several umu steps; the LAST one is the game."""
    run = GameRun()
    run(0, 12.0)  # e.g. a prefix-compat step that succeeded
    run(7, 3.0)  # the game, which died during bootstrap

    assert run.last_rc == 7
    assert run.ran_past_bootstrap() is False

    run(7, 90.0)
    assert run.ran_past_bootstrap() is True


def test_bootstrap_window_matches_the_retryable_window() -> None:
    """Both verdicts must agree about the same span of time."""
    from unifideck.launcher.proton.infrastructure import umu_runtime as ur

    assert _BOOTSTRAP_FAILED_SECONDS == ur._RECOVERABLE_MAX_RUNTIME_SECONDS


# ── every game-launch handler uses the same verdict ──


def test_ubisoft_launch_uses_the_shared_verdict() -> None:
    """Ubisoft routes through ``finish_launch`` like every other handler.

    ``_raise_for_umu_rc`` raised for ANY non-zero rc with no duration
    check, so closing UPC after playing a session toasted "failed to
    launch" and recorded a circuit-breaker failure — the exact field
    case this change fixes. The function is retained as documentation of
    the old shape, so this test pins the routing rather than asserting it
    was deleted.
    """
    import inspect

    from unifideck.launcher.proton.handlers import ubisoft

    source = inspect.getsource(ubisoft.ubisoft_launch)
    assert "finish_launch" in source
    assert "_raise_for_umu_rc" not in source

    assert hasattr(ubisoft, "_raise_for_umu_rc"), (
        "kept as a reference shape; remove once the auth/install paths "
        "are reworked"
    )


def test_battlenet_launch_reports_success_after_a_real_session() -> None:
    """Battle.net never conflated a client's exit with a launch failure.

    Worth pinning because a reviewer reasonably suspected it had the
    Epic bug: its ``_fail`` raises ``GameFailedError``, but only for
    diagnosed pre-launch conditions (not signed in, family code unknown,
    no game process observed) — none of which carries a game exit code.
    The session itself ends with an unconditional success.
    """
    import inspect

    from unifideck.launcher.proton.handlers import battlenet

    source = inspect.getsource(battlenet.battlenet_launch)
    tail = source.split("_issue_and_confirm")[-1]
    assert "game_exit_code = 0" in tail
    assert "GameFailedError" not in tail
