"""tests/unit/test_py_modules_multiarch.py — Multi-architecture Python module loading tests."""
from __future__ import annotations

import os
import runpy
import time
from pathlib import Path
from unittest.mock import patch

from unifideck.core.arch import (
    arch_scoped_cache_home,
    clean_all_mismatched_cli_vendored_caches,
    clean_mismatched_gogdl_vendored_cache,
    clean_mismatched_legendary_vendored_cache,
    get_arch_name,
    get_cli_elf_machine,
    get_elf_machine,
    is_arm,
    resolve_bundled_binary_path,
)

_ELF_X86_64 = b"\x7fELF" + b"\x00" * 14 + b"\x3e\x00"
_ELF_AARCH64 = b"\x7fELF" + b"\x00" * 14 + b"\xb7\x00"


def _make_zipapp(path: Path, so_member: str, so_bytes: bytes) -> Path:
    """Build a fake zipapp CLI carrying one native module.

    Mirrors the real legendary/gogdl layout: a shebang line, then a zip
    whose native members are what get extracted to the vendored cache —
    so the ELF inside is what decides the expected architecture.
    """
    import zipfile

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("__main__.py", "# cli\n")
        zf.writestr(so_member, so_bytes)
    return path


def test_arch_helpers() -> None:
    with patch("platform.machine", return_value="aarch64"):
        assert is_arm() is True
        assert get_arch_name() == "aarch64"

    with patch("platform.machine", return_value="arm64"):
        assert is_arm() is True
        assert get_arch_name() == "aarch64"

    with patch("platform.machine", return_value="x86_64"):
        assert is_arm() is False
        assert get_arch_name() == "x86_64"


def test_resolve_bundled_binary_path(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    arm_bin = bin_dir / "aarch64" / "test_tool"
    x86_bin = bin_dir / "x86_64" / "test_tool"
    root_bin = bin_dir / "test_tool"

    arm_bin.parent.mkdir(parents=True)
    arm_bin.write_bytes(b"")
    arm_bin.chmod(0o755)

    x86_bin.parent.mkdir(parents=True)
    x86_bin.write_bytes(b"")
    x86_bin.chmod(0o755)

    with patch("platform.machine", return_value="aarch64"):
        assert resolve_bundled_binary_path(tmp_path, "test_tool") == str(arm_bin)

    with patch("platform.machine", return_value="x86_64"):
        assert resolve_bundled_binary_path(tmp_path, "test_tool") == str(x86_bin)

    # Fallback to root bin
    arm_bin.unlink()
    root_bin.write_bytes(b"")
    root_bin.chmod(0o755)
    with patch("platform.machine", return_value="aarch64"):
        assert resolve_bundled_binary_path(tmp_path, "test_tool") == str(root_bin)


def test_cffi_backends_exist_for_both_architectures() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    py_modules = repo_root / "py_modules"

    for ver in ("310", "311", "312", "313", "314"):
        x86_so = py_modules / f"_cffi_backend.cpython-{ver}-x86_64-linux-gnu.so"
        arm_so = py_modules / f"_cffi_backend.cpython-{ver}-aarch64-linux-gnu.so"

        assert x86_so.is_file(), f"Missing x86_64 cffi backend for Python {ver}"
        assert arm_so.is_file(), f"Missing aarch64 cffi backend for Python {ver}"


def test_cryptography_rust_bindings_exist_for_both_architectures() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    py_modules = repo_root / "py_modules"

    x86_rust = py_modules / "_arch" / "x86_64" / "cryptography" / "hazmat" / "bindings" / "_rust.abi3.so"
    arm_rust = py_modules / "_arch" / "aarch64" / "cryptography" / "hazmat" / "bindings" / "_rust.abi3.so"

    assert x86_rust.is_file(), "Missing x86_64 cryptography _rust.abi3.so"
    assert arm_rust.is_file(), "Missing aarch64 cryptography _rust.abi3.so"


def test_cryptography_bindings_path_resolution() -> None:
    from cryptography.hazmat import bindings

    init_file = Path(bindings.__file__).resolve()
    mock_path: list[str] = []

    with patch("platform.machine", return_value="aarch64"), \
         patch("sys.platform", "linux"):
        runpy.run_path(str(init_file), init_globals={"__file__": str(init_file), "__path__": mock_path})
        expected_arch_dir = str(init_file.parent.parent.parent.parent / "_arch" / "aarch64" / "cryptography" / "hazmat" / "bindings")
        assert expected_arch_dir in mock_path


def test_get_elf_machine(tmp_path: Path) -> None:
    invalid_file = tmp_path / "not_elf.so"
    invalid_file.write_bytes(b"not an elf file")
    assert get_elf_machine(invalid_file) is None

    nonexistent_file = tmp_path / "ghost.so"
    assert get_elf_machine(nonexistent_file) is None

    # Craft mock x86_64 and aarch64 ELF binaries
    x86_elf = tmp_path / "x86.so"
    # ELF magic (4 bytes) + 14 dummy bytes + 2 bytes e_machine (0x003e little endian)
    x86_elf.write_bytes(b"\x7fELF" + b"\x00" * 14 + b"\x3e\x00")
    assert get_elf_machine(x86_elf) == 0x3E

    arm_elf = tmp_path / "arm.so"
    # ELF magic (4 bytes) + 14 dummy bytes + 2 bytes e_machine (0x00b7 little endian)
    arm_elf.write_bytes(b"\x7fELF" + b"\x00" * 14 + b"\xb7\x00")
    assert get_elf_machine(arm_elf) == 0xB7


def test_get_cli_elf_machine(tmp_path: Path) -> None:
    """Architecture is read from the CLI itself, in both shipped shapes."""
    # A zipapp: the answer is the ELF of a native member it ships.
    arm_zip = _make_zipapp(
        tmp_path / "legendary", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_AARCH64,
    )
    assert get_cli_elf_machine(arm_zip) == 0xB7

    x86_zip = _make_zipapp(
        tmp_path / "legendary_x86", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_X86_64,
    )
    assert get_cli_elf_machine(x86_zip) == 0x3E

    # A plain ELF executable: its own header.
    elf_bin = tmp_path / "nile"
    elf_bin.write_bytes(_ELF_AARCH64)
    assert get_cli_elf_machine(elf_bin) == 0xB7

    # Unprovable inputs must return None, never a guess — a wrong guess
    # would delete a cache that is perfectly fine.
    assert get_cli_elf_machine(tmp_path / "does_not_exist") is None
    assert get_cli_elf_machine(None) is None
    empty = tmp_path / "empty_bin"
    empty.write_bytes(b"")
    assert get_cli_elf_machine(empty) is None

    # A zipapp with no native member cannot answer either.
    no_so = tmp_path / "pure_python"
    no_so.parent.mkdir(parents=True, exist_ok=True)
    import zipfile

    with zipfile.ZipFile(no_so, "w") as zf:
        zf.writestr("__main__.py", "# cli\n")
    assert get_cli_elf_machine(no_so) is None


def test_clean_mismatched_legendary_vendored_cache(tmp_path: Path) -> None:
    """Purge is keyed to the CLI's ELF — never to platform.machine()."""
    cache_dir = tmp_path / "cache"
    vendored = cache_dir / "legendary" / "vendored"
    so_dir = vendored / "Cryptodome" / "Util"
    so_dir.mkdir(parents=True)
    sample_so = so_dir / "_cpuid_c.abi3.so"

    arm_cli = _make_zipapp(
        tmp_path / "legendary", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_AARCH64,
    )
    x86_cli = _make_zipapp(
        tmp_path / "legendary_x86", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_X86_64,
    )

    # 1. Non-existent cache dir returns False
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path / "empty")}):
        assert clean_mismatched_legendary_vendored_cache(arm_cli) is False

    # 2. an aarch64 CLI faced with an x86_64 cache purges it
    sample_so.write_bytes(_ELF_X86_64)
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}):
        assert clean_mismatched_legendary_vendored_cache(arm_cli) is True
        assert not vendored.exists()

    # 3. a matching aarch64 cache is retained
    so_dir.mkdir(parents=True)
    sample_so.write_bytes(_ELF_AARCH64)
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}):
        assert clean_mismatched_legendary_vendored_cache(arm_cli) is False
        assert vendored.exists()

    # 4. THE REGRESSION: an x86_64 CLI (the FEX-emulated backend) faced
    #    with an aarch64 cache — written moments earlier by the native
    #    launcher subprocess sharing the same HOME — must NOT purge it.
    #    Comparing against platform.machine() deleted exactly this pair,
    #    back and forth, on every invocation.
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}), \
         patch("platform.machine", return_value="aarch64"):
        assert clean_mismatched_legendary_vendored_cache(x86_cli) is False
        assert vendored.exists()


def test_clean_mismatched_gogdl_vendored_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    vendored = cache_dir / "heroic_gogdl" / "vendored"
    vendored.mkdir(parents=True)
    sample_so = vendored / "gogdl_xdelta3.abi3.so"

    arm_cli = _make_zipapp(tmp_path / "gogdl", "gogdl_xdelta3.abi3.so", _ELF_AARCH64)

    # 1. Non-existent cache dir returns False
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path / "empty")}):
        assert clean_mismatched_gogdl_vendored_cache(arm_cli) is False

    # 2. a mismatched cache gets purged
    sample_so.write_bytes(_ELF_X86_64)
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}):
        assert clean_mismatched_gogdl_vendored_cache(arm_cli) is True
        assert not vendored.exists()

    # 3. a matching cache is retained
    vendored.mkdir(parents=True)
    sample_so.write_bytes(_ELF_AARCH64)
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}):
        assert clean_mismatched_gogdl_vendored_cache(arm_cli) is False
        assert vendored.exists()

    # 4. an unreadable/unprovable CLI must never trigger a purge
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}):
        assert clean_mismatched_gogdl_vendored_cache(tmp_path / "ghost") is False
        assert clean_mismatched_gogdl_vendored_cache(None) is False
        assert vendored.exists()


def test_clean_all_mismatched_cli_vendored_caches(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    leg_vendored = cache_dir / "legendary" / "vendored" / "Cryptodome" / "Util"
    gog_vendored = cache_dir / "heroic_gogdl" / "vendored"
    leg_vendored.mkdir(parents=True)
    gog_vendored.mkdir(parents=True)

    (leg_vendored / "_cpuid_c.abi3.so").write_bytes(_ELF_X86_64)
    (gog_vendored / "gogdl_xdelta3.abi3.so").write_bytes(_ELF_X86_64)

    arm_leg = _make_zipapp(
        tmp_path / "legendary", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_AARCH64,
    )
    arm_gog = _make_zipapp(tmp_path / "gogdl", "gogdl_xdelta3.abi3.so", _ELF_AARCH64)

    # A caller that already resolved its paths passes them in, so the
    # check and the execution can never disagree.
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}):
        clean_all_mismatched_cli_vendored_caches(
            {"legendary": str(arm_leg), "gogdl": str(arm_gog)},
        )
        assert not (cache_dir / "legendary" / "vendored").exists()
        assert not gog_vendored.exists()


def test_vendored_purge_is_suppressed_within_ttl(tmp_path: Path) -> None:
    """At most one purge per cache per TTL, even if it keeps mismatching.

    A peer process that repopulates between our check and our rmtree would
    otherwise have its cache deleted on every single invocation.
    """
    cache_dir = tmp_path / "cache"
    vendored = cache_dir / "legendary" / "vendored" / "Cryptodome" / "Util"
    vendored.mkdir(parents=True)
    sample_so = vendored / "_cpuid_c.abi3.so"

    arm_cli = _make_zipapp(
        tmp_path / "legendary", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_AARCH64,
    )

    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}):
        # First call purges and writes the marker.
        sample_so.write_bytes(_ELF_X86_64)
        assert clean_mismatched_legendary_vendored_cache(arm_cli) is True
        assert (cache_dir / "legendary" / ".unifideck-arch-purge").is_file()

        # A peer repopulates with its own (again wrong-for-us) build.
        vendored.mkdir(parents=True)
        sample_so.write_bytes(_ELF_X86_64)

        # Second call inside the TTL leaves it alone.
        assert clean_mismatched_legendary_vendored_cache(arm_cli) is False
        assert vendored.exists()

        # Past the TTL the check is allowed to act again.
        marker = cache_dir / "legendary" / ".unifideck-arch-purge"
        stale = time.time() - 10000
        os.utime(marker, (stale, stale))
        assert clean_mismatched_legendary_vendored_cache(arm_cli) is True
        assert not vendored.exists()


def test_purge_marker_is_per_tool(tmp_path: Path) -> None:
    """legendary's purge marker must not suppress gogdl's purge."""
    cache_dir = tmp_path / "cache"
    leg = cache_dir / "legendary" / "vendored" / "Cryptodome" / "Util"
    gog = cache_dir / "heroic_gogdl" / "vendored"
    leg.mkdir(parents=True)
    gog.mkdir(parents=True)
    (leg / "_cpuid_c.abi3.so").write_bytes(_ELF_X86_64)
    (gog / "gogdl_xdelta3.abi3.so").write_bytes(_ELF_X86_64)

    arm_leg = _make_zipapp(
        tmp_path / "legendary", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_AARCH64,
    )
    arm_gog = _make_zipapp(tmp_path / "gogdl", "gogdl_xdelta3.abi3.so", _ELF_AARCH64)

    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}):
        clean_all_mismatched_cli_vendored_caches(
            {"legendary": str(arm_leg), "gogdl": str(arm_gog)},
        )
        assert not (cache_dir / "legendary" / "vendored").exists()
        assert not gog.exists()


def test_unknown_arch_cli_never_purges(tmp_path: Path) -> None:
    """An unreadable CLI must leave the cache completely alone.

    The old behaviour treated "cannot tell" as "purge and hope". Being
    wrong that way stops a game from launching at all, which is the exact
    failure being fixed, so an unprovable mismatch is a no-op.
    """
    cache_dir = tmp_path / "cache"
    vendored = cache_dir / "legendary" / "vendored" / "Cryptodome" / "Util"
    vendored.mkdir(parents=True)
    (vendored / "_cpuid_c.abi3.so").write_bytes(_ELF_X86_64)

    unreadable = tmp_path / "not_a_binary"
    unreadable.write_bytes(b"not an ELF and not a zip")

    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}):
        assert clean_mismatched_legendary_vendored_cache(unreadable) is False
        assert clean_mismatched_legendary_vendored_cache(tmp_path / "ghost") is False
        assert vendored.exists()
        assert not (cache_dir / "legendary" / ".unifideck-arch-purge").exists()

    # ``None`` is not "unknown" — it means "use the bundled binary this
    # process would pick", which is a real, readable binary on an
    # installed plugin and may legitimately find a mismatch. Pin the
    # default to an unresolvable plugin root so this test's subject (the
    # never-guess rule) stays isolated from that fallback.
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}), \
         patch("unifideck.core.arch.plugin_root", return_value=None):
        assert clean_mismatched_legendary_vendored_cache(None) is False
        assert vendored.exists()


def test_arch_scoped_cache_home_separates_architectures(tmp_path: Path) -> None:
    """Each architecture gets its own cache root — the actual fix.

    A shared directory cannot serve two architectures at once: whoever
    extracts last wins, and the loser then deletes it. Scoping
    XDG_CACHE_HOME means neither process can see or disturb the other's
    files, which no purge policy can achieve.
    """
    arm_cli = _make_zipapp(
        tmp_path / "legendary", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_AARCH64,
    )
    x86_cli = _make_zipapp(
        tmp_path / "legendary_x86", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_X86_64,
    )

    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path)}):
        arm = arch_scoped_cache_home(arm_cli)
        x86 = arch_scoped_cache_home(x86_cli)

    assert arm != x86, "the two architectures must not share a cache root"
    assert arm is not None and arm.name.endswith("b7")
    assert x86 is not None and x86.name.endswith("3e")

    # An unknown CLI gets no scope — the caller keeps its own value.
    assert arch_scoped_cache_home(None) is None
    assert arch_scoped_cache_home(tmp_path / "ghost") is None


def test_clean_cli_env_scopes_the_cache_per_architecture(tmp_path: Path) -> None:
    """clean_cli_env hands each architecture its own XDG_CACHE_HOME."""
    from unifideck.core.binaries import clean_cli_env

    arm_cli = _make_zipapp(
        tmp_path / "legendary", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_AARCH64,
    )
    x86_cli = _make_zipapp(
        tmp_path / "legendary_x86", "Cryptodome/Util/_cpuid_c.abi3.so", _ELF_X86_64,
    )

    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path)}, clear=False):
        arm_env = clean_cli_env(for_cli=str(arm_cli))
        x86_env = clean_cli_env(for_cli=str(x86_cli))

    assert arm_env["XDG_CACHE_HOME"] != x86_env["XDG_CACHE_HOME"]

    # A CLI with no vendored cache keeps the caller's own value.
    other = tmp_path / "nile"
    other.write_bytes(_ELF_AARCH64)
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path)}, clear=False):
        assert clean_cli_env(for_cli=str(other))["XDG_CACHE_HOME"] == str(tmp_path)
        assert clean_cli_env()["XDG_CACHE_HOME"] == str(tmp_path)


