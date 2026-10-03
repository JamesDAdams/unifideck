"""Regression: the per-launch subprocess must get the scoped CLI cache.

The arch-scoped ``XDG_CACHE_HOME`` is the fix for two architectures
sharing one vendored-cache directory on an ARM64 host (the FEX-emulated
Decky backend and the native launcher subprocess). Setting it only in
``clean_cli_env`` — which the BACKEND calls — left the launch path
scoping itself out of the very fix: ``plan.env`` is built from a plain
``dict(os.environ)``, so ``legendary launch --json`` still extracted into
the shared root and ``resolve_legendary_bin`` still purged the other
architecture's natives. That is the original ``ModuleNotFoundError:
No module named 'Cryptodome.Cipher'`` / ``Cannot load native module
'Cryptodome.Util._cpuid_c'`` failure, so a fix that reached only the
backend would have been a partial fix for the reported symptom.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from unifideck.launcher.proton.compat import epic as epic_compat

_ELF_X86_64 = b"\x7fELF" + b"\x00" * 14 + b"\x3e\x00"
_ELF_AARCH64 = b"\x7fELF" + b"\x00" * 14 + b"\xb7\x00"


def _make_zipapp(path, so_member: str, so_bytes: bytes):
    import zipfile

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("__main__.py", "# cli\n")
        zf.writestr(so_member, so_bytes)
    return path


def _plan():
    """Plan stub whose ``env`` mirrors the real thing: ``dict(os.environ)``.

    ``infrastructure.core._build_umu_env`` builds ``plan.env`` that way,
    which is precisely why nothing in the launcher path scrubbed or
    scoped anything on its own.
    """
    import os

    return SimpleNamespace(
        context=SimpleNamespace(
            game_id="Quail",
            umu_id=None,
            exe_path=SimpleNamespace(name="20XX.exe"),
            plugin_dir="/plugin",
        ),
        state=SimpleNamespace(umu_id=None),
        env=dict(os.environ),
    )


def test_launch_env_scopes_the_cache_to_the_launchers_own_binary(
    tmp_path, monkeypatch,
) -> None:
    """The launch env must point at the arch scope of ITS binary."""
    arm_cli = _make_zipapp(
        tmp_path / "aarch64" / "legendary",
        "Cryptodome/Util/_cpuid_c.abi3.so",
        _ELF_AARCH64,
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    env = epic_compat.build_legendary_env(_plan(), "/cfg", str(arm_cli))

    scoped = env["XDG_CACHE_HOME"]
    assert scoped != str(tmp_path), "must not share the unscoped cache root"
    assert scoped.endswith("b7"), "scoped to the aarch64 binary's machine"
    assert scoped.startswith(str(tmp_path))


def test_backend_and_launcher_scopes_differ(tmp_path, monkeypatch) -> None:
    """The two architectures land in sibling directories.

    This is the property that actually stops the mutual eviction: not
    "each is scoped", but "the scopes are different".
    """
    from unifideck.core.arch import arch_scoped_cache_home

    arm_cli = _make_zipapp(
        tmp_path / "aarch64" / "legendary",
        "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_AARCH64,
    )
    x86_cli = _make_zipapp(
        tmp_path / "x86_64" / "legendary",
        "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_X86_64,
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    launch_env = epic_compat.build_legendary_env(_plan(), "/cfg", str(arm_cli))
    backend_scope = arch_scoped_cache_home(x86_cli)

    assert launch_env["XDG_CACHE_HOME"] != str(backend_scope)


def test_unresolvable_binary_keeps_the_inherited_value(
    tmp_path, monkeypatch, caplog,
) -> None:
    """An unreadable binary must not silently narrow the cache.

    Guessing a scope here would be worse than not scoping at all — it
    would send the CLI to an empty cache and re-trigger the very
    extraction storm this fixes. Leaving the inherited value keeps
    today's behaviour, and the warning says why.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    env = epic_compat.build_legendary_env(
        _plan(), "/cfg", str(tmp_path / "ghost"),
    )

    assert env["XDG_CACHE_HOME"] == str(tmp_path)
    assert any(
        "could not scope the cache root" in r.message for r in caplog.records
    ), "an unprovable scope must be visible in the log, not silent"


def test_omit_binary_keeps_env_untouched(monkeypatch, tmp_path) -> None:
    """No binary passed → no scoping, no warning, no crash."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    env = epic_compat.build_legendary_env(_plan(), "/cfg")

    assert env["XDG_CACHE_HOME"] == str(tmp_path)


@pytest.mark.parametrize("config_path", ["", "/cfg"])
def test_scoping_is_independent_of_the_config_path(
    tmp_path, monkeypatch, config_path: str,
) -> None:
    """A missing legendary config must not suppress the cache scope."""
    arm_cli = _make_zipapp(
        tmp_path / "legendary", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_AARCH64,
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    env = epic_compat.build_legendary_env(_plan(), config_path, str(arm_cli))

    assert env["XDG_CACHE_HOME"].endswith("b7")
