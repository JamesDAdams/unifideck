"""GOG ``setRegistry`` dual-WOW64-view write — gog_setup/scripts.py.

Regression: 32-bit GOG titles (Fallout: New Vegas, `gog:1454587428`) launched
into a launcher that said "Install". Cause: the ``goggame-*.script``
``setRegistry`` "Installed Path" key was written only to the native
``HKLM\\Software\\…`` view, but the game is PE32/i386 in a win64 prefix, so it
reads the ``Wow6432Node`` redirect — which was empty → "not installed".

``_apply_set_registry`` must now emit the key to BOTH views for
``HKLM\\Software`` keys, mirroring the Epic/Ubisoft registry fixes.
"""
from __future__ import annotations

import logging

from unifideck.launcher.proton.compat.gog_setup import scripts

_FNV_SUBKEY = "Software\\Bethesda Softworks\\FalloutNV"
_FNV_REDIRECT = "Software\\WOW6432Node\\Bethesda Softworks\\FalloutNV"


# ── _wow64_subkeys (pure) ──────────────────────────────────────────────


def test_wow64_subkeys_hklm_software_adds_redirect() -> None:
    assert scripts._wow64_subkeys("HKLM", _FNV_SUBKEY) == [_FNV_SUBKEY, _FNV_REDIRECT]


def test_wow64_subkeys_case_insensitive_software_prefix() -> None:
    out = scripts._wow64_subkeys("HKLM", "SOFTWARE\\Foo")
    assert out[0] == "SOFTWARE\\Foo"  # original preserved verbatim
    assert "Software\\WOW6432Node\\Foo" in out


def test_wow64_subkeys_skips_non_software() -> None:
    assert scripts._wow64_subkeys("HKLM", "System\\Foo") == ["System\\Foo"]


def test_wow64_subkeys_skips_hkcu() -> None:
    # Only HKLM\\Software is WOW64-redirected for these install-marker keys.
    assert scripts._wow64_subkeys("HKCU", "Software\\Foo") == ["Software\\Foo"]


def test_wow64_subkeys_already_redirected_not_doubled() -> None:
    assert scripts._wow64_subkeys("HKLM", _FNV_REDIRECT) == [_FNV_REDIRECT]


# ── _apply_set_registry (integration, run_wine mocked) ─────────────────


def _recorder(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    async def fake_run_wine(_plan, exe, args):
        calls.append((exe, list(args)))
        return True

    monkeypatch.setattr(scripts, "run_wine", fake_run_wine)
    return calls


async def test_apply_set_registry_writes_both_views(monkeypatch) -> None:
    calls = _recorder(monkeypatch)
    args = {
        "root": "HKEY_LOCAL_MACHINE",
        "subkey": _FNV_SUBKEY,
        "valueName": "Installed Path",
        "valueType": "string",
        "valueData": "{app}\\",
    }
    await scripts._apply_set_registry(None, args, "/games/Fallout New Vegas")

    assert len(calls) == 2
    keys = {a[1] for _exe, a in calls}  # the "<key>" positional after "add"
    assert f"HKLM\\{_FNV_SUBKEY}" in keys
    assert f"HKLM\\{_FNV_REDIRECT}" in keys
    for exe, a in calls:
        assert exe == "reg.exe"
        assert a[0] == "add" and "/f" in a
        assert a[a.index("/v") + 1] == "Installed Path"
        assert a[a.index("/t") + 1] == "REG_SZ"
        # {app} expanded to the Wine Z: path of the install dir.
        assert a[a.index("/d") + 1] == "Z:\\games\\Fallout New Vegas\\"


async def test_apply_set_registry_single_write_for_non_software(monkeypatch) -> None:
    calls = _recorder(monkeypatch)
    args = {
        "root": "HKLM",
        "subkey": "System\\CurrentControlSet\\Foo",
        "valueName": "X",
        "valueType": "dword",
        "valueData": "1",
    }
    await scripts._apply_set_registry(None, args, "/x")
    assert len(calls) == 1
    assert calls[0][1][1] == "HKLM\\System\\CurrentControlSet\\Foo"


async def test_apply_set_registry_noop_without_root_or_subkey(monkeypatch) -> None:
    calls = _recorder(monkeypatch)
    await scripts._apply_set_registry(None, {"subkey": "Software\\X"}, "/x")
    await scripts._apply_set_registry(None, {"root": "HKLM"}, "/x")
    assert calls == []


# ── _setup_args (InnoSetup argument formatting) ────────────────────────


def test_setup_args_formats_windows_paths_for_innosetup() -> None:
    """InnoSetup (scriptinterpreter.exe) runs under Wine and requires Windows Z: paths.

    Passing raw Linux paths (/DIR=/var/home/...) causes InnoSetup to fail
    with a 'Path not found' popup dialog.
    """
    manifest = {
        "buildId": "51721132362920919",
        "version_name": "1.1",
    }
    args = scripts._setup_args(
        manifest,
        product_id="1909524379",
        install_path="/var/home/armada/Games/Call of Juarez",
        lang="en-US",
    )
    assert "/VERYSILENT" in args
    dir_arg = next(a for a in args if a.startswith("/DIR="))
    assert dir_arg == r"/DIR=Z:\var\home\armada\Games\Call of Juarez"

    support_arg = next(a for a in args if a.startswith("/supportDir="))
    assert support_arg.startswith("/supportDir=Z:\\")
    assert "1909524379" in support_arg


# ── _format_reg_file (Batch .reg generation) ──────────────────────────


def test_format_reg_file_emits_valid_reg_syntax() -> None:
    actions = [
        (
            "goggame-1909524379.script",
            [
                {
                    "install": {
                        "action": "setRegistry",
                        "arguments": {
                            "root": "HKEY_LOCAL_MACHINE",
                            "subkey": "Software\\Techland\\CallOfJuarez",
                            "valueName": "Path",
                            "valueType": "string",
                            "valueData": "{app}",
                        },
                    },
                },
                {
                    "install": {
                        "action": "setRegistry",
                        "arguments": {
                            "root": "HKLM",
                            "subkey": "Software\\Techland\\CallOfJuarez",
                            "valueName": "Installed",
                            "valueType": "dword",
                            "valueData": "1",
                        },
                    },
                },
                {
                    "install": {
                        "action": "setRegistry",
                        "arguments": {
                            "root": "HKLM",
                            "subkey": "Software\\Techland\\CallOfJuarez\\Keys",
                            "valueName": "",
                            "valueType": "string",
                            "valueData": "",
                        },
                    },
                },
            ],
        ),
    ]
    content = scripts._format_reg_file(actions, "/var/home/armada/Games/Call of Juarez")
    assert content.startswith("Windows Registry Editor Version 5.00")
    assert "[HKEY_LOCAL_MACHINE\\Software\\Techland\\CallOfJuarez]" in content
    assert "[HKEY_LOCAL_MACHINE\\Software\\WOW6432Node\\Techland\\CallOfJuarez]" in content
    assert '"Path"="Z:\\\\var\\\\home\\\\armada\\\\Games\\\\Call of Juarez"' in content
    assert '"Installed"=dword:00000001' in content
    assert '@=""' in content


async def test_apply_script_registry_uses_batch_regedit(tmp_path, monkeypatch) -> None:
    calls = []

    async def fake_run_wine(_plan, exe, args):
        calls.append((exe, list(args)))
        return True

    monkeypatch.setattr(scripts, "run_wine", fake_run_wine)
    monkeypatch.setattr(
        scripts,
        "_load_script_actions",
        lambda _p, _g: [
            (
                "goggame-1909524379.script",
                [
                    {
                        "install": {
                            "action": "setRegistry",
                            "arguments": {
                                "root": "HKLM",
                                "subkey": "Software\\Techland\\CallOfJuarez",
                                "valueName": "Path",
                                "valueType": "string",
                                "valueData": "{app}",
                            },
                        },
                    },
                ],
            ),
        ],
    )

    from types import SimpleNamespace
    plan = SimpleNamespace(prefix_path=tmp_path)
    await scripts.apply_script_registry(plan, "1909524379", "/games/coj")

    assert len(calls) == 1
    # Bare ``regedit`` — the form proven to work elsewhere in this package
    # (``compat/vcruntime.py`` imports its bundled .reg the same way, and
    # ``redist.py`` drives ``msiexec``/``winetricks`` by bare name). Pinned
    # so the batch path cannot drift back to a per-key ``reg.exe`` loop.
    assert calls[0][0] == "regedit"
    assert calls[0][1][0] == "/S"
    assert "Z:\\" in calls[0][1][1]


async def test_apply_script_registry_warns_when_batch_fails(
    tmp_path, monkeypatch, caplog,
) -> None:
    """A failed batch import must WARN and still fall back to reg.exe.

    Regression: ``run_wine`` reports failure by returning ``False``, not by
    raising. The old code only logged inside the ``except`` branch, so a
    batch regedit that umu refused to resolve (``Executable not found``)
    fell through to the per-key loop with NO log line at all — the field
    log showed 30 ``reg.exe`` spawns with nothing explaining why the fast
    path had been abandoned.
    """
    calls: list[tuple[str, list[str]]] = []

    async def fake_run_wine(_plan, exe, args):
        calls.append((exe, list(args)))
        return exe != "regedit"

    monkeypatch.setattr(scripts, "run_wine", fake_run_wine)
    monkeypatch.setattr(
        scripts,
        "_load_script_actions",
        lambda _p, _g: [
            (
                "goggame-1909524379.script",
                [
                    {
                        "install": {
                            "action": "setRegistry",
                            "arguments": {
                                "root": "HKLM",
                                "subkey": "Software\\Techland\\CallOfJuarez",
                                "valueName": "Path",
                                "valueType": "string",
                                "valueData": "{app}",
                            },
                        },
                    },
                ],
            ),
        ],
    )

    from types import SimpleNamespace
    plan = SimpleNamespace(prefix_path=tmp_path)
    with caplog.at_level(logging.WARNING, logger=scripts.__name__):
        await scripts.apply_script_registry(plan, "1909524379", "/games/coj")

    assert any(
        "batch regedit failed" in r.getMessage() for r in caplog.records
    ), "a failed batch import must be reported, not silently degraded"
    # Fallback still ran: one regedit attempt + reg.exe writes afterwards.
    assert calls[0][0] == "regedit"
    assert any(exe == "reg.exe" for exe, _a in calls)
