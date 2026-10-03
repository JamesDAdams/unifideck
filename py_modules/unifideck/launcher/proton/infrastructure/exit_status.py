"""infrastructure/exit_status.py — Failed launch, or a game that simply ended?

``run_umu_with_retry`` hands back the exit code of the process it spawned.
Since UD-126 that process IS the game for every store (Epic used to report
legendary's, which is always 0), so a non-zero code no longer separates
"the launch broke" from "the game ended".

Two facts separate them, and only the first is about the code:

* a process that ran past :data:`_BOOTSTRAP_FAILED_SECONDS` demonstrably
  started — runtime, prefix, and the game's own loader — so whatever it
  returns afterwards is the game's own exit status;
* a process that died inside that window never got the game up, so the
  code describes the failure.

This lives in ``infrastructure/`` — below both ``handlers/`` and
``compat/`` — because all three need the identical answer, and neither
of the other two may import the other. It lives in ONE place because
getting it wrong is not cosmetic: raising ``GameFailedError`` fires the
"failed to launch" toast AND records a launch failure against the circuit
breaker, so a title that reliably exits non-zero (a launcher-style exe, a
game that delegates to a helper and reports its own status) would walk
itself into being blocked from launching. Field case — 20XX and Overcooked
2 on an ARM64 host each exiting 3 / 127 after 30 s and 56 s of running,
every one of them reported to the user as a failure to launch.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from unifideck.launcher.proton.infrastructure.umu_runtime import (
    _BOOTSTRAP_FAILED_SECONDS,
)
from unifideck.launcher.types.errors import GameFailedError, UmuRuntimeError

if TYPE_CHECKING:
    from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan

logger = logging.getLogger(__name__)


class GameRun:
    """Accumulates what a launch learned about how its process ended.

    Created once per launch and handed to every ``run_umu_with_retry``
    call as ``on_exit``, then read once by the handler's finish step.
    It is callable itself, so it IS the callback — a plain function
    could not carry the measurement back to whoever needs it afterwards.

    Reused across the short prefix-compat steps of one launch on
    purpose: they share the bootstrap window with the game run itself,
    so the LAST attempt to report — the game — is the one that decides.
    """

    def __init__(self) -> None:
        self.last_rc: int = 0
        self.last_ran_for: float = 0.0

    def __call__(self, rc: int, ran_for: float) -> None:
        """``on_exit`` callback: note one finished umu attempt."""
        self.last_rc = rc
        self.last_ran_for = ran_for

    def ran_past_bootstrap(self) -> bool:
        """Whether the last attempt outlived the bootstrap window."""
        return self.last_ran_for >= _BOOTSTRAP_FAILED_SECONDS


def finish_launch(plan: ProtonLaunchPlan, rc: int, run: GameRun) -> int:
    """Record the game's exit code and raise only on a real launch failure.

    Returns 0 for a game that ended on its own terms, whatever code it
    chose — the session ran, Steam can end it, playtime and cloud
    sync-up are already recorded, and toasting "failed to launch" over a
    game the user deliberately closed would be a lie. The real code stays
    on ``state.game_exit_code`` for the log and for playtime.

    Raises ``UmuRuntimeError`` when umu itself could not come up, and
    ``GameFailedError`` only when the game never started.
    """
    store = plan.context.store
    game_id = plan.context.game_id
    plan.state.game_exit_code = rc
    if rc == 0:
        return 0
    if rc in {2, 74}:
        raise UmuRuntimeError(
            f"umu-run failed with unrecoverable code {rc}",
            context={"subprocess_rc": rc, "store": store},
        )
    if run.ran_past_bootstrap():
        logger.info(
            "[launcher.proton.%s] game ran %.1fs then exited with code %d — "
            "treating as a normal session end, not a launch failure",
            store, run.last_ran_for, rc,
        )
        return 0
    raise GameFailedError(
        f"{store} game exited with code {rc}",
        subprocess_rc=rc,
        context={"store": store, "game_id": game_id},
    )
