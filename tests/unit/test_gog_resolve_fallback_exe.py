import json
from pathlib import Path
from unifideck.launcher.proton.compat.gog import (
    is_launcher_stub_target,
    resolve_fallback_exe,
)


def test_resolve_fallback_exe_case_insensitive(tmp_path):
    info = {
        "gameId": "1207658924",
        "playTasks": [
            {
                "isPrimary": True,
                "type": "FileTask",
                "path": "Launcher.exe",
                "category": "launcher",
            },
            {
                "isPrimary": False,
                "type": "FileTask",
                "path": "System\\witcher.exe",
                "category": "game",
            },
        ],
    }
    (tmp_path / "goggame-1207658924.info").write_text(json.dumps(info))
    (tmp_path / "Launcher.exe").write_bytes(b"stub")
    (tmp_path / "System").mkdir()
    (tmp_path / "System" / "witcher.exe").write_bytes(b"game")

    res = resolve_fallback_exe(str(tmp_path))
    assert res == str(tmp_path / "System" / "witcher.exe")


def test_resolve_fallback_exe_implicit_category(tmp_path):
    info = {
        "gameId": "1207658924",
        "playTasks": [
            {
                "isPrimary": True,
                "type": "FileTask",
                "path": "Launcher.exe",
            },
            {
                "isPrimary": False,
                "type": "FileTask",
                "path": "System\\witcher.exe",
            },
        ],
    }
    (tmp_path / "goggame-1207658924.info").write_text(json.dumps(info))
    (tmp_path / "Launcher.exe").write_bytes(b"stub")
    (tmp_path / "System").mkdir()
    (tmp_path / "System" / "witcher.exe").write_bytes(b"game")

    res = resolve_fallback_exe(str(tmp_path))
    assert res == str(tmp_path / "System" / "witcher.exe")


def test_resolve_fallback_exe_in_game_subdir(tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    info = {
        "gameId": "1207658924",
        "playTasks": [
            {
                "isPrimary": True,
                "type": "FileTask",
                "path": "autorun.exe",
                "category": "launcher",
            },
            {
                "isPrimary": False,
                "type": "FileTask",
                "path": "bin\\game.exe",
                "category": "game",
            },
        ],
    }
    (game_dir / "goggame-1207658924.info").write_text(json.dumps(info))
    (game_dir / "autorun.exe").write_bytes(b"stub")
    (game_dir / "bin").mkdir()
    (game_dir / "bin" / "game.exe").write_bytes(b"game")

    res = resolve_fallback_exe(str(tmp_path))
    assert res == str(game_dir / "bin" / "game.exe")


def test_resolve_fallback_exe_via_exe_finder(tmp_path):
    info = {
        "gameId": "12345",
        "playTasks": [
            {
                "isPrimary": True,
                "type": "FileTask",
                "path": "launcher.exe",
                "category": "launcher",
            },
        ],
    }
    (tmp_path / "goggame-12345.info").write_text(json.dumps(info))
    (tmp_path / "launcher.exe").write_bytes(b"stub")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "real_game.exe").write_bytes(b"x" * 1024 * 1024)

    res = resolve_fallback_exe(str(tmp_path))
    assert res == str(tmp_path / "bin" / "real_game.exe")


def test_is_launcher_stub_target(tmp_path):
    assert is_launcher_stub_target(tmp_path, Path("Launcher.exe"))
    assert is_launcher_stub_target(tmp_path, Path("launch.exe"))
    assert is_launcher_stub_target(tmp_path, Path("AUTORUN.EXE"))
    assert not is_launcher_stub_target(tmp_path, Path("witcher.exe"))
