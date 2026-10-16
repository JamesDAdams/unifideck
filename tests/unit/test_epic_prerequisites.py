from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from unifideck.launcher.proton.fixes.epic_prerequisites import (
    _is_skipped_prereq,
    apply_epic_prerequisites,
)


def test_is_skipped_prereq_matches_uplay_name_and_path():
    assert _is_skipped_prereq({"name": "Uplay", "path": "Support/setup.exe"}) is True
    assert _is_skipped_prereq({"name": "GamePrereq", "path": "Uplay/UplayInstaller.exe"}) is True
    assert _is_skipped_prereq({"name": "uplayinstaller", "path": "setup.exe"}) is True
    assert _is_skipped_prereq({"name": "DirectX", "path": "redist/dxsetup.exe"}) is False
    assert _is_skipped_prereq({"name": "VCRedist", "path": "vcredist_x64.exe"}) is False


@pytest.mark.asyncio
async def test_apply_epic_prerequisites_skips_uplay_prereq(tmp_path: Path):
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    install = tmp_path / "install"
    install.mkdir()
    (install / "Uplay").mkdir()
    uplay_exe = install / "Uplay" / "UplayInstaller.exe"
    uplay_exe.write_bytes(b"MZ-installer")

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
    assert "skipped: Uplay" in marker[0].read_text()
