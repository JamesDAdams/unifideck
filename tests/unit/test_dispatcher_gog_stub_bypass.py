import pytest
from pathlib import Path
from types import SimpleNamespace
from unifideck.launcher import dispatcher as d


class _StubGamesMapShortcutSvc:
    def __init__(self, entry):
        self._entry = entry

    async def get_entry_for_game_key(self, _store: str, _game_id: str):
        return self._entry


@pytest.mark.asyncio
async def test_dispatcher_bypasses_launcher_stub_for_gog(monkeypatch, tmp_path):
    entry = SimpleNamespace(
        exe=str(tmp_path / "Launcher.exe"),
        work_dir=str(tmp_path),
        app_id=123,
    )
    svc = _StubGamesMapShortcutSvc(entry)
    real_game = str(tmp_path / "System" / "witcher.exe")

    monkeypatch.setattr(
        "unifideck.launcher.proton.compat.gog.resolve_fallback_exe",
        lambda _wd: real_game,
    )

    exe, work_dir, has_entry, app_id = await d._resolve_game_exe(
        svc, "gog", "1207658924", "gog:1207658924",
    )

    assert exe == real_game
    assert work_dir == str(tmp_path)
    assert has_entry is True
    assert app_id == 123
