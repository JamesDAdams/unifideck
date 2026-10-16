import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from unifideck.core.exe_finder import WRAPPER_EXES, exe_finder
from unifideck.stores.epic.exe_resolver import EpicExeResolver
from unifideck.launcher.proton.handlers.epic import _resolve_exe_override
from unifideck.launcher import dispatcher as d


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
        find_exe=lambda p, hints: exe_finder.find(p, hints),
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

    exe, work_dir, has_entry, app_id = await d._resolve_game_exe(
        _StubSvc(), "epic", "Jasper", "epic:Jasper",
    )
    assert exe == str(real_exe)
