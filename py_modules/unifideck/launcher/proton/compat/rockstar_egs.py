"""compat/rockstar_egs.py — Rockstar-on-Epic (RDR2 / GTA5) launch setup.

RDR2 and GTA5 bought on the Epic Games Store boot the **Rockstar Games
Launcher**, which needs the Epic Games Launcher present to link/verify the
Epic entitlement before it will treat the game as installed and playable.
To make that chain work under Proton/umu we automate what testers
otherwise do by hand:

  1. Drop a **fake** ``EpicGamesLauncher.exe`` (a tiny stub, NOT the real
     launcher — Heroic's ``USE_FAKE_EPIC_EXE``, bundled at
     ``bin/EpicGamesLauncher.exe``) into the game's install dir.
  2. Write a small launch shim ``.bat`` beside it that starts the game
     **through** that stub: ``start "" EpicGamesLauncher.exe
     PlayGTAV.exe %*``. ``handlers.epic`` then points legendary's
     ``--override-exe`` at the shim.
  3. Register the ``com.epicgames.launcher`` protocol handler in the
     prefix, so the protocol-based half of the handoff also resolves.

Step 2 is load-bearing and was the missing piece in the first cut of this
flow. That version assumed the Rockstar launcher performed the
``EpicGamesLauncher.exe PlayRDR2.exe`` handoff *itself*, so it pointed
``--override-exe`` straight at ``PlayGTAV.exe``. Launching the Play exe
directly is exactly the broken case a tester reported: the Rockstar
launcher finds the game on the very first boot but refuses to start it,
and after a restart no longer finds it at all. Going through the stub is
what makes it offer the Epic-account link and then work. (Reproduced and
fixed manually by the reporter with a hand-written ``fix.bat`` + "change
executable"; this module does both automatically.)

Every step is best-effort and — crucially — only ever runs for the
Rockstar titles (:func:`game_fixes.is_rockstar_egs`); ordinary Epic games
never reach here, so the standard Epic flow is byte-for-byte unchanged.
The complementary halves live in ``core.proton_prepare`` (STORE=egs +
WINEDLLOVERRIDES) and ``epic_cleanup`` (skips the stub/registry removal
for these games).

Online play never works on Linux (BattlEye has no Linux support) — this
enables story mode only.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.prefix_layout import (
    normalize_prefix_root,
    resolve_drive_c,
    resolve_registry_prefix,
)

logger = logging.getLogger(__name__)

_FAKE_LAUNCHER_NAME = "EpicGamesLauncher.exe"
# Name of the generated launch shim, written into the game's install dir and
# used as legendary's ``--override-exe``. Deliberately namespaced: testers
# following the manual workaround hand-write their own ``fix.bat`` in the same
# directory, and overwriting that would clobber their work.
LAUNCH_SHIM_NAME = "unifideck-rockstar-launch.bat"
# The shim body, verbatim from the reporter's proven ``fix.bat`` — do not
# "clean this up". ``start ""`` supplies the empty window-title argument cmd
# requires before the command (without it cmd treats the exe name AS the
# title and silently does nothing), and ``%*`` forwards any game args
# legendary appends. Single LF endings, matching the file that was verified
# working on-device.
_LAUNCH_SHIM_TEMPLATE = 'set SteamDeck=1\r\nstart "" {launcher} {play_exe} -nobattleye -onSteamDeck %*\r\n'
# Wine .reg block that registers the com.epicgames.launcher URL protocol
# so the Rockstar launcher's Epic handoff resolves. Mirrors Heroic's
# ``reg add HKEY_CLASSES_ROOT\com.epicgames.launcher /f``.
_EPIC_PROTOCOL_BASE = (
    "\n[Software\\\\Classes\\\\com.epicgames.launcher]\n"
    '@="URL:com.epicgames.launcher"\n'
    '"URL Protocol"=""\n'
)
_EPIC_PROTOCOL_COMMAND = (
    "\n[Software\\\\Classes\\\\com.epicgames.launcher\\\\shell\\\\open\\\\command]\n"
    '@="\\"C:\\\\windows\\\\command\\\\EpicGamesLauncher.exe\\" \\"%1\\""\n'
)
_EPIC_PROTOCOL_REG = _EPIC_PROTOCOL_BASE + _EPIC_PROTOCOL_COMMAND
_EPIC_PROTOCOL_MARKER = "[Software\\\\Classes\\\\com.epicgames.launcher]"
_EPIC_COMMAND_MARKER = "[Software\\\\Classes\\\\com.epicgames.launcher\\\\shell\\\\open\\\\command]"


def apply_rockstar_egs_setup(plan: ProtonLaunchPlan) -> None:
    """Best-effort Rockstar-on-Epic prefix + install-dir setup.

    No-op unless ``plan`` is a Rockstar-EGS game. Never raises — a
    failure here should degrade to the ordinary (likely-failing) launch,
    not abort it.
    """
    from unifideck.launcher.proton.fixes.game_fixes import is_rockstar_egs
    if not is_rockstar_egs(
        plan.context.game_id, plan.state.umu_id, plan.context.exe_path.name,
        _resolve_install_dir(plan),
    ):
        return
    logger.info(
        "[rockstar_egs] applying setup for %s (%s)",
        plan.context.game_key, plan.state.umu_id,
    )
    try:
        _clean_rockstar_crash_state(plan)
    except Exception:
        logger.exception("[rockstar_egs] crash state cleanup failed")
    try:
        _copy_fake_launcher(plan)
    except Exception:
        logger.exception("[rockstar_egs] fake-launcher copy failed")
    try:
        _write_launch_shim(plan)
    except Exception:
        logger.exception("[rockstar_egs] launch-shim write failed")
    try:
        _write_commandline_txt(plan)
    except Exception:
        logger.exception("[rockstar_egs] commandline.txt write failed")
    try:
        _register_epic_protocol(plan)
    except Exception:
        logger.exception("[rockstar_egs] protocol registration failed")
    try:
        _register_steamdeck_env(plan)
    except Exception:
        logger.exception("[rockstar_egs] SteamDeck env registration failed")


def _clean_rockstar_crash_state(plan: ProtonLaunchPlan) -> None:
    """Remove Rockstar Games Launcher crash state and logs to break 'exited unexpectedly' loop."""
    prefix_root = normalize_prefix_root(plan.prefix_path)
    drive_c_candidates: list[Path] = []
    for cand in (prefix_root / "drive_c", prefix_root / "pfx" / "drive_c", resolve_drive_c(plan.prefix_path)):
        if cand is not None and cand.is_dir() and cand not in drive_c_candidates:
            drive_c_candidates.append(cand)

    rs_dirs: list[Path] = []
    host_docs_rs = Path.home() / "Documents" / "Rockstar Games"
    if host_docs_rs.is_dir():
        rs_dirs.append(host_docs_rs)

    for drive_c in drive_c_candidates:
        pd_rs = drive_c / "ProgramData" / "Rockstar Games"
        if pd_rs.is_dir():
            rs_dirs.append(pd_rs)

        users_dir = drive_c / "users"
        if not users_dir.is_dir():
            continue
        try:
            user_dirs = [u for u in users_dir.iterdir() if u.is_dir() and u.name.lower() != "public"]
        except OSError:
            continue

        for user_dir in user_dirs:
            for doc_name in ("Documents", "documents", "My Documents"):
                rs_dir = user_dir / doc_name / "Rockstar Games"
                if rs_dir.is_dir() and rs_dir not in rs_dirs:
                    rs_dirs.append(rs_dir)
            for appdata_rel in (
                ("AppData", "Local", "Rockstar Games"),
                ("AppData", "Roaming", "Rockstar Games"),
                ("Local Settings", "Application Data", "Rockstar Games"),
            ):
                appdata_rs = user_dir.joinpath(*appdata_rel)
                if appdata_rs.is_dir() and appdata_rs not in rs_dirs:
                    rs_dirs.append(appdata_rs)

    for rs_dir in rs_dirs:
        for folder_name in ("Launcher", "Social Club"):
            target_dir = rs_dir / folder_name
            if target_dir.is_dir():
                shutil.rmtree(target_dir, ignore_errors=True)
                logger.info("[rockstar_egs] wiped crash state dir: %s", target_dir)

        # In game folders under Rockstar Games (e.g. GTAV Enhanced), delete crash/safe-mode settings
        try:
            for game_subdir in rs_dir.iterdir():
                if game_subdir.is_dir() and game_subdir.name not in ("Launcher", "Social Club"):
                    for f_name in ("settings_rr.xml", "crash.log", "settings.xml"):
                        f = game_subdir / f_name
                        if f.is_file():
                            try:
                                f.unlink()
                                logger.info("[rockstar_egs] removed game crash artifact: %s", f)
                            except OSError:
                                pass
        except OSError:
            pass


def _write_commandline_txt(plan: ProtonLaunchPlan) -> None:
    install_dir = _resolve_install_dir(plan)
    if install_dir is None:
        return
    cmd_file = install_dir / "commandline.txt"
    needed_args = ["-nobattleye", "-onSteamDeck", "-scofflineonly"]
    existing = ""
    if cmd_file.is_file():
        try:
            existing = cmd_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            existing = ""
    tokens = [t.strip() for t in existing.split() if t.strip()]
    modified = False
    for arg in needed_args:
        if not any(arg.lower() == t.lower() for t in tokens):
            tokens.append(arg)
            modified = True
    if modified or not cmd_file.is_file() or "\r\n" not in existing:
        try:
            cmd_file.write_text(" ".join(tokens) + "\r\n", encoding="utf-8")
            logger.info("[rockstar_egs] updated %s with %s", cmd_file, needed_args)
        except OSError:
            logger.warning("[rockstar_egs] failed to write %s", cmd_file, exc_info=True)


def _resolve_install_dir(plan: ProtonLaunchPlan) -> Path | None:
    """The game's install dir (where the stub + shim belong), or ``None``.

    The Rockstar bootstrap runs ``EpicGamesLauncher.exe`` from the game's own
    directory, so both artefacts must sit next to the game exe — not in the
    prefix.
    """
    work_dir = plan.context.work_dir or plan.context.exe_path.parent
    if not work_dir or not Path(work_dir).is_dir():
        return None
    return Path(work_dir)


def _write_launch_shim(plan: ProtonLaunchPlan) -> None:
    """Write the ``start "" EpicGamesLauncher.exe <PlayExe> %*`` shim.

    This is the piece that makes the Rockstar launcher verify the Epic
    entitlement instead of refusing to start / "losing" the install — see the
    module docstring. Rewrites whenever the content differs so a corrected
    template propagates to already-set-up installs, rather than being skipped
    forever because *a* file happened to exist.
    """
    from unifideck.launcher.proton.fixes.game_fixes import (
        resolve_rockstar_play_exe,
    )
    install_dir = _resolve_install_dir(plan)
    if install_dir is None:
        logger.warning("[rockstar_egs] install dir missing — skipping shim")
        return
    play_exe = resolve_rockstar_play_exe(
        plan.context.game_id, plan.state.umu_id, plan.context.exe_path.name,
        install_dir,
    )
    if not play_exe:
        logger.warning(
            "[rockstar_egs] no Play exe resolved for %s — skipping shim",
            plan.context.game_id,
        )
        return
    content = _LAUNCH_SHIM_TEMPLATE.format(
        launcher=_FAKE_LAUNCHER_NAME, play_exe=play_exe,
    )
    dest = install_dir / LAUNCH_SHIM_NAME
    if dest.is_file() and dest.read_text(encoding="utf-8") == content:
        logger.info("[rockstar_egs] launch shim already current: %s", dest)
        return
    dest.write_text(content, encoding="utf-8")
    logger.info("[rockstar_egs] wrote launch shim %s → %s", dest, play_exe)


def _copy_fake_launcher(plan: ProtonLaunchPlan) -> None:
    """Copy the bundled fake ``EpicGamesLauncher.exe`` beside the game exe.

    The generated launch shim runs ``EpicGamesLauncher.exe <PlayExe>`` from
    the game's own directory, so the stub must sit next to the game exe (the
    install/work dir), not in the prefix.
    """
    import shutil
    fake = plan.context.plugin_dir / "bin" / _FAKE_LAUNCHER_NAME
    if not fake.is_file():
        logger.warning(
            "[rockstar_egs] bundled %s missing at %s — skipping",
            _FAKE_LAUNCHER_NAME, fake,
        )
        return
    install_dir = _resolve_install_dir(plan)
    if install_dir is not None:
        dest = install_dir / _FAKE_LAUNCHER_NAME
        if dest.is_file():
            logger.info("[rockstar_egs] fake launcher already present: %s", dest)
        else:
            shutil.copy2(fake, dest)
            logger.info("[rockstar_egs] installed fake launcher: %s", dest)

    prefix_root = normalize_prefix_root(plan.prefix_path)
    drive_c_candidates: list[Path] = []
    c1 = prefix_root / "drive_c"
    if c1.is_dir():
        drive_c_candidates.append(c1)
    c2 = prefix_root / "pfx" / "drive_c"
    if c2.is_dir() and c2 not in drive_c_candidates:
        drive_c_candidates.append(c2)
    c3 = resolve_drive_c(plan.prefix_path)
    if c3 is not None and c3 not in drive_c_candidates:
        drive_c_candidates.append(c3)

    for drive_c in drive_c_candidates:
        prefix_targets = [
            drive_c / "windows" / "command" / _FAKE_LAUNCHER_NAME,
            (
                drive_c / "Program Files (x86)" / "Epic Games" / "Launcher"
                / "Portal" / "Binaries" / "Win32" / _FAKE_LAUNCHER_NAME
            ),
        ]
        for target in prefix_targets:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.is_file():
                    shutil.copy2(fake, target)
                    logger.info("[rockstar_egs] installed fake launcher into prefix: %s", target)
            except OSError as err:
                logger.warning("[rockstar_egs] failed to copy fake launcher to %s: %s", target, err)


def _register_epic_protocol(plan: ProtonLaunchPlan) -> None:
    """Append the com.epicgames.launcher protocol block to user.reg.

    Idempotent — skips if the block is already present. Writing the .reg
    directly (rather than a umu-run regedit) keeps this a cheap, offline
    file edit; Wine reads user.reg at prefix load.
    """
    registry_root = resolve_registry_prefix(plan.prefix_path)
    user_reg = registry_root / "user.reg"
    # drive_c must exist for the prefix to be real; if not, the prefix
    # hasn't been created yet and there's nothing to register into.
    if resolve_drive_c(plan.prefix_path) is None or not user_reg.is_file():
        logger.info(
            "[rockstar_egs] user.reg not ready (%s) — skipping protocol reg",
            user_reg,
        )
        return
    content = user_reg.read_text(encoding="utf-8", errors="replace")
    to_append = ""
    if _EPIC_PROTOCOL_MARKER not in content:
        to_append += _EPIC_PROTOCOL_BASE
    if _EPIC_COMMAND_MARKER not in content:
        to_append += _EPIC_PROTOCOL_COMMAND
    if not to_append:
        logger.info("[rockstar_egs] epic protocol already registered")
        return
    user_reg.write_text(content + to_append, encoding="utf-8")
    logger.info("[rockstar_egs] registered com.epicgames.launcher protocol")


def _inject_registry_value(reg_file: Path, section_name: str, key_name: str, key_value: str) -> None:
    """Ensure key_name=key_value is set in section_name of a Wine .reg file."""
    if not reg_file.is_file():
        return
    try:
        content = reg_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    sec_header = f"[{section_name}]"
    entry_line = f'"{key_name}"="{key_value}"'

    if sec_header in content:
        start = content.find(sec_header)
        next_sec = content.find("\n[", start + len(sec_header))
        sec_body = content[start:next_sec] if next_sec != -1 else content[start:]
        if f'"{key_name}"=' in sec_body:
            if entry_line in sec_body:
                return
            new_sec_body = re.sub(rf'"{re.escape(key_name)}"=.*', entry_line, sec_body)
            new_content = content[:start] + new_sec_body + (content[next_sec:] if next_sec != -1 else "")
            reg_file.write_text(new_content, encoding="utf-8")
        else:
            insert_pos = start + len(sec_header)
            new_content = content[:insert_pos] + "\n" + entry_line + content[insert_pos:]
            reg_file.write_text(new_content, encoding="utf-8")
    else:
        new_content = content.rstrip() + f"\n\n[{section_name}]\n{entry_line}\n"
        reg_file.write_text(new_content, encoding="utf-8")


def _register_steamdeck_env(plan: ProtonLaunchPlan) -> None:
    """Register SteamDeck=1 in both user.reg and system.reg so all Windows child processes inherit it."""
    prefix_root = normalize_prefix_root(plan.prefix_path)
    reg_roots = [prefix_root]
    r2 = resolve_registry_prefix(plan.prefix_path)
    if r2 not in reg_roots:
        reg_roots.append(r2)
    pfx_dir = prefix_root / "pfx"
    if pfx_dir.is_dir() and pfx_dir not in reg_roots:
        reg_roots.append(pfx_dir)

    for root in reg_roots:
        user_reg = root / "user.reg"
        if user_reg.is_file():
            _inject_registry_value(user_reg, "Environment", "SteamDeck", "1")
            logger.info("[rockstar_egs] injected SteamDeck=1 into %s", user_reg)

        system_reg = root / "system.reg"
        if system_reg.is_file():
            _inject_registry_value(
                system_reg,
                "System\\\\CurrentControlSet\\\\Control\\\\Session Manager\\\\Environment",
                "SteamDeck",
                "1",
            )
            logger.info("[rockstar_egs] injected SteamDeck=1 into %s", system_reg)

