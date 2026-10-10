"""Regression: Epic launches must never expose STORE=egs to ProtonFixes.

ProtonFixes' EGS-store defaults are actively harmful, not just redundant:
they re-run vcrun2022 (core-dumps inside pressure-vessel) and — the one
that actually breaks launches — add a HKCR\\com.epicgames.launcher registry
key that makes the EOS SDK switch to launcher-IPC auth mode, causing an
instant exit/hang for non-Ubisoft Epic titles that use EOS (the retired
bash launcher forced STORE=none for exactly this reason).

Field case: Kingdom Hearts Re:Chain of Memories never launched, on any
Proton version, even after deleting and recreating the prefix — because
compat/vcruntime.py's regedit step (and prefix_init.py's createprefix, and
winetricks.py) all build their env as ``dict(plan.env)``, which carried
STORE=egs generically. That poisoned the prefix's registry on the very
first setup step, before the actual game ever ran — so a fresh prefix hit
the exact same corruption immediately. build_legendary_env() already forced
STORE=none for the final legendary invocation, but that was too late: the
damage was already done by the earlier setup steps sharing the same env.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from unifideck.core import arch
from unifideck.launcher.proton.infrastructure import core
from unifideck.launcher.types.context import LaunchContext, RuntimeState


def _prepare(
    tmp_path, monkeypatch, store, umu_id=None, game_id="game1", exe_name="null",
):
    ctx = LaunchContext(
        store=store,
        game_id=game_id,
        exe_path=Path("/dev") / exe_name,
        work_dir=tmp_path,
        plugin_dir=tmp_path,
    )
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    monkeypatch.setattr(core, "_resolve_prefix", lambda c: prefix)
    monkeypatch.setattr(core, "_lookup_umu_id", lambda c, s, p: umu_id)
    monkeypatch.setattr(
        core, "_locate_umu_wrapper", lambda p, d: tmp_path / "umu-run",
    )
    return core.proton_prepare(
        ctx, RuntimeState(),
        python_bin=Path("/usr/bin/python3"),
        proton_path=tmp_path / "proton",
        proton_tool_id="GE-Proton10-34",
    )


def test_epic_launch_forces_store_none(tmp_path, monkeypatch):
    plan = _prepare(tmp_path, monkeypatch, "epic")
    assert plan.env["STORE"] == "none"
    # Diagnostics still record the real store code.
    assert plan.state.umu_store_code == "egs"


def test_gog_launch_keeps_real_store(tmp_path, monkeypatch):
    plan = _prepare(tmp_path, monkeypatch, "gog")
    assert plan.env["STORE"] == "gog"


def test_ubisoft_launch_keeps_real_store(tmp_path, monkeypatch):
    plan = _prepare(tmp_path, monkeypatch, "ubisoft")
    assert plan.env["STORE"] == "ubisoft"


# ── Rockstar-on-Epic (RDR2/GTA5) — the one Epic case wanting STORE=egs ──

def test_ordinary_epic_game_unchanged(tmp_path, monkeypatch):
    """A non-Rockstar Epic game must NOT trigger any Rockstar handling.

    Regression guard: STORE stays "none" and no WINEDLLOVERRIDES is added.
    """
    plan = _prepare(tmp_path, monkeypatch, "epic", game_id="Fortnite")
    assert plan.env["STORE"] == "none"
    assert "WINEDLLOVERRIDES" not in plan.env


def test_rockstar_rdr2_gets_egs_with_umu_id_none(tmp_path, monkeypatch):
    """The REAL Decky-build case: game_id='Heather', umu_id=None (no
    umu_lookup.py). This is the exact runtime the tester hit — the gate
    MUST fire on the Epic app name, not the (always-None) umu id.
    """
    plan = _prepare(tmp_path, monkeypatch, "epic", umu_id=None, game_id="Heather")
    assert plan.env["STORE"] == "egs"
    assert plan.env["WINEDLLOVERRIDES"] == "vulkan-1=n,b"


def test_rockstar_gta5_gets_egs_by_app_name(tmp_path, monkeypatch):
    plan = _prepare(
        tmp_path, monkeypatch, "epic", umu_id=None,
        game_id="9d2d0eb64d5c44529cece33fe2a46482",
    )
    assert plan.env["STORE"] == "egs"
    assert plan.env["WINEDLLOVERRIDES"] == "vulkan-1=n,b"


def test_rockstar_gta5_enhanced_edition_gets_egs_by_app_name(tmp_path, monkeypatch):
    """UD report: GTA V's "Enhanced Edition" is a separate Epic catalog id
    from the legacy "Grand Theft Auto V" tested above — it was missing from
    the allowlist entirely, so this title got none of STORE=egs,
    WINEDLLOVERRIDES, the fake launcher, or the epic_cleanup skip, and the
    Rockstar Games Launcher couldn't re-verify the install after first boot.
    """
    plan = _prepare(
        tmp_path, monkeypatch, "epic", umu_id=None,
        game_id="8769e24080ea413b8ebca3f1b8c50951",
    )
    assert plan.env["STORE"] == "egs"
    assert plan.env["WINEDLLOVERRIDES"] == "vulkan-1=n,b"


def test_rockstar_matches_by_exe_name_with_unknown_app_id(tmp_path, monkeypatch):
    """The durable fallback: even an Epic app id the allowlist has never
    seen still gets the Rockstar flow, purely from the Play-launcher exe
    name — this is what makes the fix resilient to the NEXT Rockstar/Epic
    catalog reshuffle instead of needing another hardcoded id.
    """
    plan = _prepare(
        tmp_path, monkeypatch, "epic", umu_id=None,
        game_id="some-future-epic-catalog-id", exe_name="PlayGTAV.exe",
    )
    assert plan.env["STORE"] == "egs"
    assert plan.env["WINEDLLOVERRIDES"] == "vulkan-1=n,b"


def test_rockstar_umu_id_secondary_still_matches(tmp_path, monkeypatch):
    """If umu_lookup.py IS present and returns a Rockstar umu id, the
    secondary match still triggers the flow (belt-and-suspenders).
    """
    plan = _prepare(
        tmp_path, monkeypatch, "epic", umu_id="umu-1174180", game_id="game1",
    )
    assert plan.env["STORE"] == "egs"
    assert plan.env["WINEDLLOVERRIDES"] == "vulkan-1=n,b"


def test_rockstar_app_name_on_non_epic_store_is_not_special(tmp_path, monkeypatch):
    """The gate is store==epic AND rockstar identity — a GOG game named
    'Heather' (hypothetically) must keep its own store profile.
    """
    plan = _prepare(tmp_path, monkeypatch, "gog", game_id="Heather")
    assert plan.env["STORE"] == "gog"
    assert "WINEDLLOVERRIDES" not in plan.env


# ── Titles that bundle their own ICU (Cyberpunk 2077) ──────────────────
#
# Field case (2026-08-12 bundle): every Cyberpunk launch that reached the
# game aborted on ``icuuc.dll.u_setMemoryFunctions_65`` — Wine's builtin
# icuuc.dll stub, new in GE-Proton11-3, shadowing the ICU 65 the game ships.
# The window opens and stays blank. Proton's own bundled ICU is 68, so only
# loading the game's own copy fixes it.
#
# GE-Proton11-3 shipped builtin stubs for ``icuuc.dll`` AND ``icuin.dll`` in
# the same build, and Cyberpunk bundles ICU 65 as both. Overriding icuuc
# alone only moved the abort one library along — a later bundle (2026-08-23)
# shows the game dying on ``icuin.dll.?compile@RegexPattern@icu_65@@...``
# instead. Both must be overridden together.

def test_cyberpunk_gog_gets_native_icu_override(tmp_path, monkeypatch):
    plan = _prepare(
        tmp_path, monkeypatch, "gog",
        game_id="1423049311", exe_name="REDprelauncher.exe",
    )
    assert plan.env["WINEDLLOVERRIDES"] == "icuuc=n,b;icuin=n,b"
    assert plan.env["STORE"] == "gog"


def test_cyberpunk_matches_on_exe_name_alone(tmp_path, monkeypatch):
    """Primary tier: the exe name, for the direct-exe retry path and for
    any store/edition whose id we do not hold."""
    plan = _prepare(
        tmp_path, monkeypatch, "gog",
        game_id="some-unknown-id", exe_name="Cyberpunk2077.exe",
    )
    assert plan.env["WINEDLLOVERRIDES"] == "icuuc=n,b;icuin=n,b"


def test_cyberpunk_epic_codename_matches(tmp_path, monkeypatch):
    """Same title on Epic ships the same bundled ICU, so it needs the same
    override — the gate is deliberately store-independent."""
    plan = _prepare(
        tmp_path, monkeypatch, "epic", game_id="Ginger", exe_name="null",
    )
    assert plan.env["WINEDLLOVERRIDES"] == "icuuc=n,b;icuin=n,b"


def test_ordinary_gog_game_gets_no_icu_override(tmp_path, monkeypatch):
    """Regression guard: a non-matching launch is left completely alone."""
    plan = _prepare(
        tmp_path, monkeypatch, "gog",
        game_id="1207658883", exe_name="AoW.exe",
    )
    assert "WINEDLLOVERRIDES" not in plan.env


def test_icu_override_merges_with_existing(tmp_path, monkeypatch):
    """Must not clobber overrides another step already set (Proton appends
    its own long default list, and battlenet sets locationapi=d)."""
    monkeypatch.setenv("WINEDLLOVERRIDES", "locationapi=d")
    plan = _prepare(
        tmp_path, monkeypatch, "gog",
        game_id="1423049311", exe_name="REDprelauncher.exe",
    )
    assert plan.env["WINEDLLOVERRIDES"] == "locationapi=d;icuuc=n,b;icuin=n,b"


def test_icu_override_fills_in_only_the_missing_dll(tmp_path, monkeypatch):
    """The skip guard is per DLL, not "did we already mention any ICU".

    A single ``"icuuc" in existing`` test used to short-circuit the whole
    block, so an environment that already named icuuc got NO icuin — the
    half-applied state Cyberpunk aborts in.
    """
    monkeypatch.setenv("WINEDLLOVERRIDES", "icuuc=n,b")
    plan = _prepare(
        tmp_path, monkeypatch, "gog",
        game_id="1423049311", exe_name="REDprelauncher.exe",
    )
    assert plan.env["WINEDLLOVERRIDES"] == "icuuc=n,b;icuin=n,b"


def test_icu_override_is_a_noop_when_both_already_present(tmp_path, monkeypatch):
    monkeypatch.setenv("WINEDLLOVERRIDES", "icuin=b;icuuc=b")
    plan = _prepare(
        tmp_path, monkeypatch, "gog",
        game_id="1423049311", exe_name="REDprelauncher.exe",
    )
    assert plan.env["WINEDLLOVERRIDES"] == "icuin=b;icuuc=b"


def test_user_env_override_still_wins(tmp_path, monkeypatch):
    """ctx.env_overrides is applied last, so an explicit user value wins."""
    ctx = LaunchContext(
        store="gog",
        game_id="1423049311",
        exe_path=Path("/dev/REDprelauncher.exe"),
        work_dir=tmp_path,
        plugin_dir=tmp_path,
        env_overrides={"WINEDLLOVERRIDES": "icuuc=b"},
    )
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    monkeypatch.setattr(core, "_resolve_prefix", lambda c: prefix)
    monkeypatch.setattr(core, "_lookup_umu_id", lambda c, s, p: None)
    monkeypatch.setattr(
        core, "_locate_umu_wrapper", lambda p, d: tmp_path / "umu-run",
    )
    plan = core.proton_prepare(
        ctx, RuntimeState(),
        python_bin=Path("/usr/bin/python3"),
        proton_path=tmp_path / "proton",
        proton_tool_id="GE-Proton11-3",
    )
    assert plan.env["WINEDLLOVERRIDES"] == "icuuc=b"


# ── ARM64 direct Proton runner: only where it can actually work ─────────
#
# 0.7.9 added a "direct Valve Proton runner" that bypasses umu entirely on
# ARM64, driving ``SteamLinuxRuntime_4-arm64/_v2-entry-point`` instead. It is
# the right idea and it is gated far too broadly: the condition is
# ``is_arm() and ("arm64" in tool_id or "arm64" in path or
# "proton" in tool_id)``, and the last clause matches **every** Proton-family
# tool id — including GE-Proton. Field effect (SteamOS ARM64, 0.7.9, gog
# titles Mafia III / Hitman Absolution): every launch took the direct-runner
# branch and died in ~1 s with exit code 1, with nothing in game.log, while
# the very same titles launched fine under umu+GE-Proton on 0.7.8.
#
# The runner is only legitimate for a native ARM64 Valve Proton that has NO
# protonfixes/ — the one case umu cannot drive. GE-Proton bundles
# protonfixes/, so umu handles it and must be left to do so.

def _prepare_arm(
    tmp_path, monkeypatch, *, tool_id, proton_path, slr_entry,
):
    ctx = LaunchContext(
        store="gog", game_id="armgame", exe_path=Path("/dev/game.exe"),
        work_dir=tmp_path, plugin_dir=tmp_path,
    )
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    monkeypatch.setattr(core, "_resolve_prefix", lambda c: prefix)
    monkeypatch.setattr(core, "_lookup_umu_id", lambda c, s, p: None)
    monkeypatch.setattr(core, "_locate_umu_wrapper", lambda p, d: tmp_path / "umu-run")
    monkeypatch.setattr(arch, "is_arm", lambda: True)
    if slr_entry is None:
        monkeypatch.setattr(core, "_find_arm64_slr_entry_point", lambda: None)
    else:
        monkeypatch.setattr(
            core, "_find_arm64_slr_entry_point", lambda: Path(slr_entry),
        )
    # A gamescope session would start a window-tagger thread; keep it out.
    monkeypatch.delenv("GAMESCOPE_WAYLAND_DISPLAY", raising=False)
    return core.proton_prepare(
        ctx, RuntimeState(),
        python_bin=Path("/usr/bin/python3"),
        proton_path=Path(proton_path),
        proton_tool_id=tool_id,
    )


def test_ge_proton_never_gets_the_direct_arm_runner(tmp_path, monkeypatch):
    """GE-Proton ships protonfixes/, so umu drives it — no direct runner.

    The regression: ``"proton" in tool_id`` matched ``GE-Proton11-7``, so
    every GE launch on an ARM64 host was rerouted into the direct runner and
    failed, even though umu had just set the prefix up successfully.
    """
    ge_dir = tmp_path / "compatibilitytools.d" / "GE-Proton11-7"
    (ge_dir / "protonfixes").mkdir(parents=True)
    (ge_dir / "proton").write_text("#!/bin/sh\n")
    plan = _prepare_arm(
        tmp_path, monkeypatch,
        tool_id="GE-Proton11-7",
        proton_path=ge_dir / "proton",
        slr_entry="/var/…/SteamLinuxRuntime_4-arm64/_v2-entry-point",
    )
    assert plan.runner_prefix_argv is None
    # The umu path is therefore what build_argv must produce.
    argv = plan.build_argv("/dev/game.exe")
    assert argv[0] == "/usr/bin/python3"
    assert argv[1].endswith("umu-run")
    assert not any("_v2-entry-point" in a for a in argv)


def test_valve_proton_with_protonfixes_keeps_umu(tmp_path, monkeypatch):
    """A Valve Proton that DOES have protonfixes/ is umu-drivable too."""
    proton_dir = tmp_path / "steamapps" / "common" / "Proton 11.0 (ARM64)"
    (proton_dir / "protonfixes").mkdir(parents=True)
    (proton_dir / "proton").write_text("#!/bin/sh\n")
    plan = _prepare_arm(
        tmp_path, monkeypatch,
        tool_id="proton_11",
        proton_path=proton_dir / "proton",
        slr_entry="/var/…/SteamLinuxRuntime_4-arm64/_v2-entry-point",
    )
    assert plan.runner_prefix_argv is None


def test_arm64_valve_proton_without_protonfixes_uses_direct_runner(
    tmp_path, monkeypatch,
):
    """The one legitimate case: native ARM64 Valve Proton, no protonfixes/.

    umu cannot drive it (its ``run_command`` does
    ``cwd=f"{PROTONPATH}/protonfixes"``), so the SLR direct runner is the
    only way this Proton starts a Windows process at all.
    """
    proton_dir = tmp_path / "steamapps" / "common" / "Proton 11.0 (ARM64)"
    proton_dir.mkdir(parents=True)
    (proton_dir / "proton").write_text("#!/bin/sh\n")
    # NOTE: no protonfixes/ — the whole point of this case.
    slr = "/var/…/SteamLinuxRuntime_4-arm64/_v2-entry-point"
    plan = _prepare_arm(
        tmp_path, monkeypatch,
        tool_id="proton_11",
        proton_path=proton_dir / "proton",
        slr_entry=slr,
    )
    assert plan.runner_prefix_argv == [
        slr, "--verb=run", "--", str(proton_dir / "proton"), "run",
    ]
    assert plan.build_argv("/dev/game.exe") == [
        *plan.runner_prefix_argv, "/dev/game.exe",
    ]


def test_no_slr_available_means_no_direct_runner(tmp_path, monkeypatch):
    """Without a SteamLinuxRuntime entry point there is no direct runner."""
    proton_dir = tmp_path / "steamapps" / "common" / "Proton 11.0 (ARM64)"
    proton_dir.mkdir(parents=True)
    (proton_dir / "proton").write_text("#!/bin/sh\n")
    plan = _prepare_arm(
        tmp_path, monkeypatch,
        tool_id="proton_11",
        proton_path=proton_dir / "proton",
        slr_entry=None,
    )
    assert plan.runner_prefix_argv is None


def test_no_slr_and_no_protonfixes_logs_that_the_launch_will_fail(
    tmp_path, monkeypatch, caplog,
):
    """A doomed launch must say so, at ERROR, before the 1-second mystery exit.

    A native ARM64 Proton with no ``protonfixes/`` and no available
    SteamLinuxRuntime cannot start a Windows process by either route. The
    launch still proceeds (so the failure surface stays umu's own, and a
    caller passing an unresolvable placeholder is not hard-rejected), but the
    log has to state that this WILL fail and name both remedies — that is the
    difference between a diagnosable failure and the field's bare ``exit code
    1 after 1.0s`` with nothing in game.log.
    """
    proton_dir = tmp_path / "steamapps" / "common" / "Proton 11.0 (ARM64)"
    proton_dir.mkdir(parents=True)
    (proton_dir / "proton").write_text("#!/bin/sh\n")
    log_name = "unifideck.launcher.proton.infrastructure.core"
    caplog.set_level(logging.ERROR, logger=log_name)

    _prepare_arm(
        tmp_path, monkeypatch,
        tool_id="proton_11",
        proton_path=proton_dir / "proton",
        slr_entry=None,
    )

    errors = [
        r.getMessage() for r in caplog.records
        if r.levelno == logging.ERROR and "protonfixes" in r.getMessage()
    ]
    assert errors, "expected an ERROR explaining the launch cannot work"
    msg = errors[-1]
    assert "this launch will fail" in msg
    # Both remedies, so a field log is actionable without reading the source.
    assert "SteamLinuxRuntime_4-arm64" in msg
    assert "Compatibility" in msg


def test_x86_64_never_uses_the_direct_runner(tmp_path, monkeypatch):
    """The runner exists for ARM64 hosts only; x86_64 always goes via umu."""
    proton_dir = tmp_path / "steamapps" / "common" / "Proton 11.0"
    proton_dir.mkdir(parents=True)
    (proton_dir / "proton").write_text("#!/bin/sh\n")
    ctx = LaunchContext(
        store="gog", game_id="x86game", exe_path=Path("/dev/game.exe"),
        work_dir=tmp_path, plugin_dir=tmp_path,
    )
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    monkeypatch.setattr(core, "_resolve_prefix", lambda c: prefix)
    monkeypatch.setattr(core, "_lookup_umu_id", lambda c, s, p: None)
    monkeypatch.setattr(core, "_locate_umu_wrapper", lambda p, d: tmp_path / "umu-run")
    monkeypatch.setattr(arch, "is_arm", lambda: False)
    monkeypatch.setattr(
        core, "_find_arm64_slr_entry_point",
        lambda: Path("/var/…/SteamLinuxRuntime_4-arm64/_v2-entry-point"),
    )
    monkeypatch.delenv("GAMESCOPE_WAYLAND_DISPLAY", raising=False)
    plan = core.proton_prepare(
        ctx, RuntimeState(),
        python_bin=Path("/usr/bin/python3"),
        proton_path=proton_dir / "proton",
        proton_tool_id="proton_11",
    )
    assert plan.runner_prefix_argv is None


# ── One source of truth for which Proton the launch really uses ──────────
#
# ``proton_tool_id`` is only a selector label. ``setup_prefix`` legitimately
# borrows a DIFFERENT Proton to run umu's winetricks verb, so a launch can log
# ``proton=proton_11`` in its plan line while the process it actually spawns
# reads ``PROTONPATH=…/GE-Proton11-7`` — which is exactly the pair of lines
# that made the 0.7.9 ARM64 failures read as two unrelated problems. Both log
# lines and every consumer below must key off ``PROTONPATH``, the value umu
# itself reads.

def test_plan_env_protonpath_is_the_proton_the_launch_uses(
    tmp_path, monkeypatch, caplog,
):
    """``PROTONPATH`` must point at the proton_path handed to proton_prepare.

    A ``proton_tool_id`` that names a different build is the caller's problem
    to solve; what the plan may never do is let the env disagree with the path
    it was given.
    """
    proton_dir = tmp_path / "compatibilitytools.d" / "GE-Proton11-7"
    (proton_dir / "protonfixes").mkdir(parents=True)
    (proton_dir / "proton").write_text("#!/bin/sh\n")
    plan = _prepare_arm(
        tmp_path, monkeypatch,
        tool_id="proton_11",  # a label that disagrees with the path below
        proton_path=proton_dir / "proton",
        slr_entry=None,
    )
    assert plan.env["PROTONPATH"] == str(proton_dir)
    assert plan.state.proton_path == proton_dir / "proton"


def test_plan_ready_logs_the_proton_it_will_use(tmp_path, monkeypatch, caplog):
    """The ``plan ready`` line must name PROTONPATH, not the selector label.

    Field (0.7.9, SteamOS ARM64): the plan line read ``proton=proton_11``
    while every umu process the same launch spawned read
    ``PROTONPATH=…/GE-Proton11-7``. The two lines described two different
    Protons, so the ARM64 failures looked like two unrelated bugs. One value,
    the one umu reads.
    """
    proton_dir = tmp_path / "compatibilitytools.d" / "GE-Proton11-7"
    (proton_dir / "protonfixes").mkdir(parents=True)
    (proton_dir / "proton").write_text("#!/bin/sh\n")
    tool_id = "proton_11"  # the selector label, deliberately a different id
    logger = logging.getLogger("unifideck.launcher.proton.infrastructure.core")
    caplog.set_level(logging.INFO, logger=logger.name)
    _prepare_arm(
        tmp_path, monkeypatch,
        tool_id="proton_11",
        proton_path=proton_dir / "proton",
        slr_entry=None,
    )
    plan_lines = [r for r in caplog.records if "plan ready" in r.getMessage()]
    assert plan_lines, "expected a 'plan ready' log line"
    line = plan_lines[-1].getMessage()
    assert f"proton={proton_dir}" in line
    # Anchor on the field delimiter so a future id that merely CONTAINS the
    # old token (``proton=proton_11_extra``) cannot slip past this guard.
    assert f" proton={tool_id} " not in f"{line} "
    assert not re.search(rf"\bproton={re.escape(tool_id)}(\S)", line)



def test_state_tool_id_is_still_the_selector_label(tmp_path, monkeypatch):
    """Diagnostics keep the selector label; it is just not the launch truth.

    ``state.proton_tool_id`` is recorded for the same reason
    ``helpers.py`` logs PROTONPATH instead: the id is what the selector chose,
    and dropping it would lose the information needed to see that the borrow
    happened at all.
    """
    proton_dir = tmp_path / "compatibilitytools.d" / "GE-Proton11-7"
    (proton_dir / "protonfixes").mkdir(parents=True)
    (proton_dir / "proton").write_text("#!/bin/sh\n")
    plan = _prepare_arm(
        tmp_path, monkeypatch,
        tool_id="proton_11",
        proton_path=proton_dir / "proton",
        slr_entry=None,
    )
    assert plan.state.proton_tool_id == "proton_11"
    assert plan.env["PROTONPATH"] == str(proton_dir)


