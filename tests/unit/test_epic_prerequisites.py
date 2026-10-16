from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from unifideck.launcher.proton.fixes.epic_prerequisites import (
    _is_ubisoft_connect_installed,
    _is_uplay_prereq,
    apply_epic_prerequisites,
)


def test_is_uplay_prereq_matches_uplay_name_and_path():
    assert _is_uplay_prereq({"name": "Uplay", "path": "Support/setup.exe"}) is True
    assert _is_uplay_prereq({"name": "GamePrereq", "path": "Uplay/UplayInstaller.exe"}) is True
    assert _is_uplay_prereq({"name": "uplayinstaller", "path": "setup.exe"}) is True
    assert _is_uplay_prereq({"name": "DirectX", "path": "redist/dxsetup.exe"}) is False
    assert _is_uplay_prereq({"name": "VCRedist", "path": "vcredist_x64.exe"}) is False


@pytest.mark.asyncio
async def test_apply_epic_prerequisites_upgrades_uplay_prereq_to_ubisoft_connect(tmp_path: Path):
    prefix = tmp_path / "prefix"
    (prefix / "drive_c").mkdir(parents=True)
    install = tmp_path / "install"
    install.mkdir()
    (install / "Uplay").mkdir()
    (install / "Uplay" / "UplayInstaller.exe").write_bytes(b"MZ-legacy")

    fake_installer = tmp_path / "UbisoftConnectInstaller.exe"
    fake_installer.write_bytes(b"MZ-modern" * 200)

    config_dir = tmp_path / "legendary"
    config_dir.mkdir()
    installed_json = config_dir / "installed.json"
    installed_json.write_text(
        json.dumps({
            "Jasper": {
                "install_path": str(install),
                "prereq_info": {
                    "name": "Uplay",
                    "path": "Uplay/UplayInstaller.exe",
                    "args": "/S",
                },
            },
        }),
    )

    plan = SimpleNamespace(
        context=SimpleNamespace(
            game_id="Jasper",
            game_key="epic:Jasper",
        ),
        prefix_path=prefix,
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=Path("/bin/umu-run"),
        env={},
    )

    with patch(
        "unifideck.launcher.proton.fixes.epic_prerequisites._find_legendary_installed_json",
        return_value=installed_json,
    ), patch(
        "unifideck.launcher.proton.fixes.epic_prerequisites._ensure_ubisoft_connect_installer",
        new_callable=AsyncMock,
        return_value=fake_installer,
    ), patch(
        "unifideck.launcher.proton.fixes.epic_prerequisites._spawn_installer",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_spawn:
        ok = await apply_epic_prerequisites(plan)

    assert ok is True
    mock_spawn.assert_called_once()
    spawn_cmd = mock_spawn.call_args[0][0]
    assert str(fake_installer) in spawn_cmd
    assert "/S" in spawn_cmd
    assert "UplayInstaller.exe" not in " ".join(spawn_cmd)

    marker = list(prefix.glob(".unifideck_prereqs_Jasper_*.done"))
    assert len(marker) == 1
    assert "installed: Ubisoft Connect" in marker[0].read_text()


@pytest.mark.asyncio
async def test_apply_epic_prerequisites_skips_when_ubisoft_connect_already_installed(tmp_path: Path):
    prefix = tmp_path / "prefix"
    upc = prefix / "drive_c" / "Program Files (x86)" / "Ubisoft" / "Ubisoft Game Launcher" / "UbisoftConnect.exe"
    upc.parent.mkdir(parents=True)
    upc.write_bytes(b"MZ-upc")
    assert _is_ubisoft_connect_installed(prefix) is True

    install = tmp_path / "install"
    install.mkdir()
    config_dir = tmp_path / "legendary"
    config_dir.mkdir()
    installed_json = config_dir / "installed.json"
    installed_json.write_text(
        json.dumps({
            "Jasper": {
                "install_path": str(install),
                "prereq_info": {
                    "name": "Uplay",
                    "path": "Uplay/UplayInstaller.exe",
                    "args": "/S",
                },
            },
        }),
    )

    plan = SimpleNamespace(
        context=SimpleNamespace(
            game_id="Jasper",
            game_key="epic:Jasper",
        ),
        prefix_path=prefix,
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=Path("/bin/umu-run"),
        env={},
    )

    with patch(
        "unifideck.launcher.proton.fixes.epic_prerequisites._find_legendary_installed_json",
        return_value=installed_json,
    ), patch(
        "unifideck.launcher.proton.fixes.epic_prerequisites._spawn_installer",
        new_callable=AsyncMock,
    ) as mock_spawn:
        ok = await apply_epic_prerequisites(plan)

    assert ok is True
    mock_spawn.assert_not_called()
    marker = list(prefix.glob(".unifideck_prereqs_Jasper_*.done"))
    assert len(marker) == 1
    assert "ubisoft connect ready" in marker[0].read_text()


@pytest.mark.asyncio
async def test_apply_epic_prerequisites_purges_obsolete_uplay_without_ubisoft_connect(tmp_path: Path):
    prefix = tmp_path / "prefix"
    ubi_dir = prefix / "drive_c" / "Program Files (x86)" / "Ubisoft" / "Ubisoft Game Launcher"
    ubi_dir.mkdir(parents=True)
    (ubi_dir / "upc.exe").write_bytes(b"MZ-old-2014-uplay")
    (ubi_dir / "Uplay.exe").write_bytes(b"MZ-old-2014-uplay")
    assert _is_ubisoft_connect_installed(prefix) is False

    install = tmp_path / "install"
    install.mkdir()
    fake_installer = tmp_path / "UbisoftConnectInstaller.exe"
    fake_installer.write_bytes(b"MZ-modern" * 200)

    config_dir = tmp_path / "legendary"
    config_dir.mkdir()
    installed_json = config_dir / "installed.json"
    installed_json.write_text(
        json.dumps({
            "Jasper": {
                "install_path": str(install),
                "prereq_info": {
                    "name": "Uplay",
                    "path": "Uplay/UplayInstaller.exe",
                    "args": "/S",
                },
            },
        }),
    )

    plan = SimpleNamespace(
        context=SimpleNamespace(
            game_id="Jasper",
            game_key="epic:Jasper",
        ),
        prefix_path=prefix,
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=Path("/bin/umu-run"),
        env={},
    )

    from unifideck.launcher.proton.fixes.epic_prerequisites import _get_marker_paths
    new_marker, _ = _get_marker_paths("Jasper", prefix)
    new_marker.write_text("installed: Uplay", encoding="utf-8")

    with patch(
        "unifideck.launcher.proton.fixes.epic_prerequisites._find_legendary_installed_json",
        return_value=installed_json,
    ), patch(
        "unifideck.launcher.proton.fixes.epic_prerequisites._ensure_ubisoft_connect_installer",
        new_callable=AsyncMock,
        return_value=fake_installer,
    ), patch(
        "unifideck.launcher.proton.fixes.epic_prerequisites._spawn_installer",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_spawn:
        ok = await apply_epic_prerequisites(plan)

    assert ok is True
    mock_spawn.assert_called_once()
    assert not (ubi_dir / "Uplay.exe").exists()
    marker = list(prefix.glob(".unifideck_prereqs_Jasper_*.done"))
    assert len(marker) == 1
    assert "installed: Ubisoft Connect" in marker[0].read_text()
