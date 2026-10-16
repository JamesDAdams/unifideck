import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from unifideck.core.exe_finder import WRAPPER_EXES, exe_finder
from unifideck.launcher import dispatcher as d
from unifideck.launcher.proton.handlers.epic import _resolve_exe_override
from unifideck.stores.epic.exe_resolver import EpicExeResolver


def test_uplaylaunch_in_wrapper_exes():
    assert "uplaylaunch.exe" in WRAPPER_EXES
    assert "uplaylauncher.exe" in WRAPPER_EXES
    assert "uplay.exe" in WRAPPER_EXES


def test_epic_exe_resolver_bypasses_uplaylaunch_stub(tmp_path):
    (tmp_path / "UplayLaunch.exe").write_bytes(b"stub" * 10)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_exe = bin_dir / "Watch_Dogs.exe"
    real_exe.write_bytes(b"game" * 1024 * 1024)

    resolver = EpicExeResolver(
        cli_path=None,
        find_exe=exe_finder.find,
        info_timeout_seconds=5.0,
    )
    info = {
        "manifest": {
            "launch_exe": "UplayLaunch.exe",
        },
        "game": {
            "title": "Watch Dogs",
        },
    }

    resolved = resolver._resolve_executable(str(tmp_path), info, "Jasper")
    assert resolved == str(real_exe)


def test_resolve_exe_override_bypasses_wrapper_in_installed_json(tmp_path, monkeypatch):
    install_dir = tmp_path / "WatchDogs"
    install_dir.mkdir()
    (install_dir / "UplayLaunch.exe").write_bytes(b"stub")
    bin_dir = install_dir / "bin"
    bin_dir.mkdir()
    real_exe = bin_dir / "Watch_Dogs.exe"
    real_exe.write_bytes(b"realgame" * 1024 * 1024)

    installed_json = tmp_path / "installed.json"
    installed_json.write_text(json.dumps({
        "Jasper": {
            "install_path": str(install_dir),
            "executable": "UplayLaunch.exe",
        }
    }))

    monkeypatch.setattr(
        "unifideck.launcher.proton.handlers.epic.Path.expanduser",
        lambda self: installed_json if "installed.json" in str(self) else self,
    )

    plan = SimpleNamespace(
        context=SimpleNamespace(
            game_id="Jasper",
            work_dir=str(install_dir),
            exe_path=Path(install_dir / "UplayLaunch.exe"),
        ),
        state=SimpleNamespace(umu_id=None),
    )

    override = _resolve_exe_override(plan)
    assert override == real_exe


@pytest.mark.asyncio
async def test_dispatcher_bypasses_wrapper_stub_for_epic(tmp_path):
    install_dir = tmp_path / "WatchDogs"
    install_dir.mkdir()
    (install_dir / "UplayLaunch.exe").write_bytes(b"stub")
    bin_dir = install_dir / "bin"
    bin_dir.mkdir()
    real_exe = bin_dir / "Watch_Dogs.exe"
    real_exe.write_bytes(b"realgame" * 1024 * 1024)

    class _StubSvc:
        async def get_entry_for_game_key(self, _store, _gid):
            return SimpleNamespace(
                exe=str(install_dir / "UplayLaunch.exe"),
                work_dir=str(install_dir),
                app_id=123,
            )

    exe, _work_dir, _has_entry, _app_id = await d._resolve_game_exe(
        _StubSvc(), "epic", "Jasper", "epic:Jasper",
    )
    assert exe == str(real_exe)


def test_exe_finder_skips_uplay_installer_directory_and_binary(tmp_path):
    uplay_dir = tmp_path / "Uplay"
    uplay_dir.mkdir()
    (uplay_dir / "UplayInstaller.exe").write_bytes(b"biginstaller" * 1024 * 1024)
    (tmp_path / "installer.exe").write_bytes(b"installer" * 1024 * 1024)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_exe = bin_dir / "Watch_Dogs.exe"
    real_exe.write_bytes(b"game" * 1024)

    found = exe_finder.find(str(tmp_path), ["watch_dogs.exe"])
    assert found == str(real_exe)


def test_exe_finder_does_not_filter_games_with_crash_in_title(tmp_path):
    crash_game = tmp_path / "CrashBandicoot.exe"
    crash_game.write_bytes(b"game" * 1024)
    found = exe_finder.find(str(tmp_path))
    assert found == str(crash_game)


def test_exe_finder_filters_crash_reporters(tmp_path):
    (tmp_path / "crashreporter.exe").write_bytes(b"crash" * 1024 * 1024)
    (tmp_path / "crashreportclient.exe").write_bytes(b"crash" * 1024 * 1024)
    (tmp_path / "crashhandler.exe").write_bytes(b"crash" * 1024 * 1024)
    (tmp_path / "crashpad_handler.exe").write_bytes(b"crash" * 1024 * 1024)
    game_exe = tmp_path / "Game.exe"
    game_exe.write_bytes(b"game" * 1024)
    found = exe_finder.find(str(tmp_path))
    assert found == str(game_exe)


def test_exe_finder_matching_hint_bypasses_skip_substring(tmp_path):
    installer_game = tmp_path / "Installation04.exe"
    installer_game.write_bytes(b"game" * 1024)
    assert exe_finder.find(str(tmp_path)) is None
    found = exe_finder.find(str(tmp_path), hints=["Installation04.exe"])
    assert found == str(installer_game)


