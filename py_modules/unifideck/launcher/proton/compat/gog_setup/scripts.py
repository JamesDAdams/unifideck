"""compat/gog_setup/scripts.py — GOG setup-script execution + registry.

Ports Heroic ``setup.ts`` script handling: run the v2 setup executable
(``scriptinterpreter.exe`` / per-product ``temp_executable``) and apply
``goggame-*.script`` ``setRegistry`` actions (critical for older Ubisoft
GOG titles). ``Execute`` actions are intentionally skipped — running
arbitrary installers headless in Gaming Mode risks hanging on a GUI
dialog and poisoning the prefix.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .common import REDIST_DIR, SUPPORT_DIR, language_name, run_wine

if TYPE_CHECKING:
    from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan

logger = logging.getLogger(__name__)

_ROOT_MAP = {
    "HKEY_LOCAL_MACHINE": "HKLM", "HKLM": "HKLM",
    "HKEY_CURRENT_USER": "HKCU", "HKCU": "HKCU",
    "HKEY_CLASSES_ROOT": "HKCR", "HKCR": "HKCR",
}
_TYPE_MAP = {
    "string": "REG_SZ", "dword": "REG_DWORD", "binary": "REG_BINARY",
    "expandstring": "REG_EXPAND_SZ", "multistring": "REG_MULTI_SZ",
}


def _win_path(install_path: str) -> str:
    """Map a Linux install path to its Wine ``Z:`` path for ``{app}``."""
    return "Z:" + install_path.replace("/", "\\")


def _setup_args(
    manifest: dict[str, Any], product_id: str, install_path: str, lang: str,
    *, is_temp_exe: bool = False,
) -> list[str]:
    """Build the GOG silent-setup arg list (Heroic setup.ts)."""
    name = language_name(lang)
    win_install = _win_path(install_path)
    game_id = str(manifest.get("gameId") or manifest.get("game_id") or product_id)
    args = [
        "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES",
        f"/DIR={win_install}",
        f"/Language={name}", f"/LANG={name}",
        f"/ProductId={product_id}", "/galaxyclient",
        f"/buildId={manifest.get('buildId', '0')}",
        f"/versionName={manifest.get('version_name', '1.0')}",
        f"/lang-code={lang}",
    ]
    if not is_temp_exe:
        support_dir = SUPPORT_DIR / game_id
        support_dir.mkdir(parents=True, exist_ok=True)
        win_support = _win_path(str(support_dir))
        args.append(f"/supportDir={win_support}")
    args.extend(["/nodesktopshorctut", "/nodesktopshortcut"])
    return args


async def run_script_interpreter(
    plan: ProtonLaunchPlan, game_id: str,
    manifest: dict[str, Any], install_path: str, lang: str,
) -> bool:
    """Run ``scriptinterpreter.exe`` (ISI) for v2 manifests."""
    isi = REDIST_DIR / "__redist" / "ISI" / "scriptinterpreter.exe"
    if not isi.is_file():
        logger.warning("[gog_setup] scriptinterpreter.exe missing")
        return False
    kwargs: dict[str, Any] = {}
    if install_path:
        sig = inspect.signature(run_wine)
        if "cwd" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            kwargs["cwd"] = install_path
    if "timeout_s" in inspect.signature(run_wine).parameters:
        kwargs["timeout_s"] = 15.0
    success = True
    products = [p for p in (manifest.get("products", []) or []) if isinstance(p, dict) and p.get("productId")]
    for product in products:
        pid = product.get("productId")
        if not pid:
            continue
        pid_str = str(pid)
        if len(products) > 1 and pid_str != str(game_id) and not any(Path(install_path).glob(f"goggame-{pid_str}.*")):
            continue
        ok = await run_wine(
            plan, str(isi), _setup_args(manifest, pid_str, install_path, lang),
            **kwargs,
        )
        if not ok:
            success = False
    return success


async def run_temp_executable(
    plan: ProtonLaunchPlan, game_id: str,
    manifest: dict[str, Any], install_path: str, lang: str,
) -> bool:
    """Run a per-product ``temp_executable`` setup (e.g. The Witcher)."""
    kwargs: dict[str, Any] = {}
    if install_path:
        sig = inspect.signature(run_wine)
        if "cwd" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            kwargs["cwd"] = install_path
    if "timeout_s" in inspect.signature(run_wine).parameters:
        kwargs["timeout_s"] = 15.0
    success = True
    ran_any = False
    products = [p for p in (manifest.get("products", []) or []) if isinstance(p, dict) and p.get("temp_executable")]
    for product in products:
        temp_exe = product.get("temp_executable") or ""
        if not temp_exe:
            continue
        pid = str(product.get("productId", game_id))
        if len(products) > 1 and pid != str(game_id) and not any(Path(install_path).glob(f"goggame-{pid}.*")):
            continue
        exe = SUPPORT_DIR / game_id / pid / temp_exe
        if not exe.is_file():
            logger.warning("[gog_setup] temp_executable missing: %s", exe)
            continue
        ran_any = True
        ok = await run_wine(
            plan, str(exe), _setup_args(manifest, pid, install_path, lang, is_temp_exe=True),
            **kwargs,
        )
        if not ok:
            success = False
    return success if ran_any else True


def _wow64_subkeys(root: str, subkey: str) -> list[str]:
    """Return the subkey(s) to write so 32-bit *and* 64-bit games both see it.

    A win64 prefix redirects 32-bit reads of ``HKLM\\Software\\…`` to
    ``HKLM\\Software\\Wow6432Node\\…``. GOG ``setRegistry`` "Installed Path"
    keys (older Bethesda / Ubisoft-on-GOG titles) are 32-bit, so a key written
    only to the native view is invisible to the game → it shows "Install".
    Mirror the Epic/Ubisoft fixes (``epic_registry.py``) and write the literal
    ``Wow6432Node`` path too (don't rely on ``reg.exe /reg:32`` — Wine support
    is version-dependent). Only HKLM\\Software keys are redirected; everything
    else is written once, unchanged.
    """
    subkeys = [subkey]
    prefix = "Software\\"
    if (
        root == "HKLM"
        and subkey.lower().startswith(prefix.lower())
        and "wow6432node" not in subkey.lower()
    ):
        remainder = subkey[len(prefix):]
        subkeys.append(f"Software\\WOW6432Node\\{remainder}")
    return subkeys


async def _apply_set_registry(
    plan: ProtonLaunchPlan, args: dict[str, Any], install_path: str,
) -> None:
    """Apply one ``setRegistry`` action via ``reg.exe add`` (both WOW64 views)."""
    root = _ROOT_MAP.get(args.get("root", ""), args.get("root", ""))
    subkey = args.get("subkey", "")
    if not root or not subkey:
        return
    value_data = args.get("valueData", "")
    if isinstance(value_data, str):
        value_data = value_data.replace("{app}", _win_path(install_path))
    value_name = args.get("valueName", "")
    value_args: list[str] = []
    if value_name:
        reg_type = _TYPE_MAP.get(str(args.get("valueType", "string")).lower(), "REG_SZ")
        value_args = ["/v", value_name, "/t", reg_type, "/d", str(value_data)]
    for target in _wow64_subkeys(root, subkey):
        await run_wine(plan, "reg.exe", ["add", f"{root}\\{target}", "/f", *value_args])


_FULL_ROOT_MAP = {
    "HKLM": "HKEY_LOCAL_MACHINE",
    "HKCU": "HKEY_CURRENT_USER",
    "HKCR": "HKEY_CLASSES_ROOT",
    "HKEY_LOCAL_MACHINE": "HKEY_LOCAL_MACHINE",
    "HKEY_CURRENT_USER": "HKEY_CURRENT_USER",
    "HKEY_CLASSES_ROOT": "HKEY_CLASSES_ROOT",
}


def _collect_implicit_gog_registry(install_path: str, game_id: str) -> list[tuple[str, list[Any]]]:
    """Derive standard registry entries from goggame-*.info if present."""
    base = Path(install_path)
    search_dirs = [base]
    if (base / "game").is_dir():
        search_dirs.insert(0, base / "game")
    actions: list[Any] = []
    for d in search_dirs:
        for info_file in (list(d.glob(f"goggame-{game_id}.info")) or list(d.glob("goggame-*.info"))):
            try:
                data = json.loads(info_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            gid = str(data.get("gameId") or game_id)
            title = str(data.get("gameName") or data.get("name") or "")
            root_folder = str(data.get("rootFolder") or "")

            actions.append({
                "install": {
                    "action": "setRegistry",
                    "arguments": {
                        "root": "HKLM",
                        "subkey": f"Software\\GOG.com\\Games\\{gid}",
                        "valueName": "PATH",
                        "valueType": "string",
                        "valueData": "{app}\\",
                    },
                },
            })
            actions.append({
                "install": {
                    "action": "setRegistry",
                    "arguments": {
                        "root": "HKLM",
                        "subkey": f"Software\\GOG.com\\Games\\{gid}",
                        "valueName": "gameID",
                        "valueType": "string",
                        "valueData": gid,
                    },
                },
            })
            if title:
                actions.append({
                    "install": {
                        "action": "setRegistry",
                        "arguments": {
                            "root": "HKLM",
                            "subkey": f"Software\\GOG.com\\Games\\{gid}",
                            "valueName": "GAMENAME",
                            "valueType": "string",
                            "valueData": title,
                        },
                    },
                })
            # For legacy CD Projekt Red titles (e.g. The Witcher), witcher.exe and
            # launcher.exe query HKLM\Software\CD Projekt RED\The Witcher\InstallFolder
            # and HKCU\Software\CD Projekt RED\Witcher\Settings\TextLanguage.
            data_dir = d / "Data"
            if (d / "System" / "witcher.exe").is_file() or (data_dir.is_dir() and any(data_dir.glob("dialog_*.tlk"))):
                actions.append({
                    "install": {
                        "action": "setRegistry",
                        "arguments": {
                            "root": "HKLM",
                            "subkey": "Software\\CD Projekt RED\\The Witcher",
                            "valueName": "InstallFolder",
                            "valueType": "string",
                            "valueData": "{app}\\",
                        },
                    },
                })
                tw_lang_map = {
                    "english": "3", "polish": "5", "german": "10", "french": "11",
                    "spanish": "12", "italian": "13", "russian": "14", "czech": "15",
                    "hungarian": "16", "chinese": "21",
                }
                lang_code = tw_lang_map.get(str(data.get("language") or "english").strip().lower(), "3")
                actions.append({
                    "install": {
                        "action": "setRegistry",
                        "arguments": {
                            "root": "HKCU",
                            "subkey": "Software\\CD Projekt RED\\Witcher\\Settings",
                            "valueName": "TextLanguage",
                            "valueType": "string",
                            "valueData": lang_code,
                        },
                    },
                })
                actions.append({
                    "install": {
                        "action": "setRegistry",
                        "arguments": {
                            "root": "HKCU",
                            "subkey": "Software\\CD Projekt RED\\Witcher\\Settings",
                            "valueName": "VoiceLanguage",
                            "valueType": "string",
                            "valueData": lang_code,
                        },
                    },
                })
            break
        if actions:
            break
    return [("implicit_gog_info", actions)] if actions else []


def _format_reg_file(
    actions_by_script: list[tuple[str, list[Any]]], install_path: str,
) -> str:
    """Format all setRegistry actions into a single .reg file content."""
    keys_map: dict[str, list[tuple[str, str, Any]]] = {}
    for _name, actions in actions_by_script:
        for action in actions:
            install = action.get("install", {}) if isinstance(action, dict) else {}
            if install.get("action") != "setRegistry":
                continue
            args = install.get("arguments", {}) or {}
            raw_root = args.get("root", "")
            root_short = _ROOT_MAP.get(raw_root, raw_root)
            subkey = args.get("subkey", "")
            if not root_short or not subkey:
                continue
            full_root = _FULL_ROOT_MAP.get(root_short, root_short)
            value_data = args.get("valueData", "")
            if isinstance(value_data, str):
                value_data = value_data.replace("{app}", _win_path(install_path))
            value_name = args.get("valueName", "")
            val_type = str(args.get("valueType", "string")).lower()

            for target in _wow64_subkeys(root_short, subkey):
                full_key = f"{full_root}\\{target}"
                if full_key not in keys_map:
                    keys_map[full_key] = []
                keys_map[full_key].append((value_name, val_type, value_data))

    if not keys_map:
        return ""

    lines = ["Windows Registry Editor Version 5.00", ""]
    for key, values in keys_map.items():
        lines.append(f"[{key}]")
        for vname, vtype, vdata in values:
            name_part = f'"{vname}"' if vname else "@"
            if vtype == "dword":
                try:
                    int_val = int(vdata)
                    lines.append(f"{name_part}=dword:{int_val:08x}")
                except ValueError:
                    lines.append(f"{name_part}=dword:00000000")
            else:
                escaped = str(vdata).replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{name_part}="{escaped}"')
        lines.append("")
    return "\n".join(lines)


def _load_script_actions(
    install_path: str, game_id: str,
) -> list[tuple[str, list[Any]]]:
    """Find and parse ``goggame-*.script`` files (blocking I/O).

    Returns ``[(script_name, actions), ...]``. Synchronous so the
    async caller can do all the filesystem work in a single
    ``asyncio.to_thread`` hop rather than on the event loop.
    """
    base = Path(install_path)
    search_dirs = [base]
    if (base / "game").is_dir():
        search_dirs.insert(0, base / "game")
    scripts: list[Path] = []
    for d in search_dirs:
        found = list(d.glob(f"goggame-{game_id}.script")) or list(d.glob("goggame-*.script"))
        if found:
            scripts = found
            break
    parsed: list[tuple[str, list[Any]]] = []
    for script_file in scripts:
        try:
            data = json.loads(script_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.warning(
                "[gog_setup] script parse failed %s: %s", script_file, e,
            )
            continue
        actions = data.get("actions", []) if isinstance(data, dict) else []
        parsed.append((script_file.name, actions))
    return parsed


async def apply_script_registry(
    plan: ProtonLaunchPlan, game_id: str, install_path: str,
) -> None:
    """Apply ``goggame-*.script`` setRegistry actions to the prefix.

    Reads the script's setRegistry actions, generates a .reg file, applies it
    with ``regedit /S``, then deletes the .reg. Handles ``{app}`` expansion
    and writes to both ``HKLM\\Software\\…`` and
    ``HKLM\\Software\\Wow6432Node\\…`` views. NEVER logs the serial number.
    """
    parsed = await asyncio.to_thread(
        _load_script_actions, install_path, game_id,
    )
    implicit = await asyncio.to_thread(
        _collect_implicit_gog_registry, install_path, game_id,
    )
    if implicit:
        parsed.extend(implicit)
    if not parsed:
        return

    reg_content = _format_reg_file(parsed, install_path)
    if reg_content and plan is not None:
        reg_file = Path(plan.prefix_path) / f".unifideck_gog_script_{game_id}.reg"
        try:
            reg_file.write_text(reg_content, encoding="utf-8")
            ok = await run_wine(
                plan, "regedit", ["/S", _win_path(str(reg_file))],
            )
            if ok:
                logger.info(
                    "[gog_setup] batch script registry imported for %s", game_id,
                )
                return
            logger.warning(
                "[gog_setup] batch regedit failed for %s — falling back to reg.exe",
                game_id,
            )
        except Exception:
            logger.warning(
                "[gog_setup] batch regedit failed for %s — falling back to reg.exe",
                game_id, exc_info=True,
            )
        finally:
            with contextlib.suppress(OSError):
                reg_file.unlink(missing_ok=True)

    for script_name, actions in parsed:
        logger.info(
            "[gog_setup] %s: %d script action(s)", script_name, len(actions),
        )
        for action in actions:
            install = action.get("install", {}) if isinstance(action, dict) else {}
            if install.get("action") == "setRegistry":
                await _apply_set_registry(
                    plan, install.get("arguments", {}) or {}, install_path,
                )
