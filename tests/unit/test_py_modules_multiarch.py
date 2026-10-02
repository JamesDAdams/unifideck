"""tests/unit/test_py_modules_multiarch.py — Multi-architecture Python module loading tests."""
from __future__ import annotations

import runpy
from pathlib import Path
from unittest.mock import patch

from unifideck.core.arch import (
    clean_all_mismatched_cli_vendored_caches,
    clean_mismatched_gogdl_vendored_cache,
    clean_mismatched_legendary_vendored_cache,
    get_arch_name,
    get_elf_machine,
    is_arm,
    resolve_bundled_binary_path,
)


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


def test_clean_mismatched_legendary_vendored_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    vendored = cache_dir / "legendary" / "vendored"
    so_dir = vendored / "Cryptodome" / "Util"
    so_dir.mkdir(parents=True)
    sample_so = so_dir / "_cpuid_c.abi3.so"

    # 1. Non-existent cache dir returns False
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path / "empty")}):
        assert clean_mismatched_legendary_vendored_cache() is False

    # 2. On aarch64, if cache holds an x86_64 .so, it gets purged
    sample_so.write_bytes(b"\x7fELF" + b"\x00" * 14 + b"\x3e\x00")
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}), \
         patch("platform.machine", return_value="aarch64"):
        assert clean_mismatched_legendary_vendored_cache() is True
        assert not vendored.exists()

    # 3. On aarch64, if cache holds an aarch64 .so, it is retained
    so_dir.mkdir(parents=True)
    sample_so.write_bytes(b"\x7fELF" + b"\x00" * 14 + b"\xb7\x00")
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}), \
         patch("platform.machine", return_value="aarch64"):
        assert clean_mismatched_legendary_vendored_cache() is False
        assert vendored.exists()

    # 4. On x86_64, if cache holds an aarch64 .so, it gets purged
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}), \
         patch("platform.machine", return_value="x86_64"):
        assert clean_mismatched_legendary_vendored_cache() is True
        assert not vendored.exists()


def test_clean_mismatched_gogdl_vendored_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    vendored = cache_dir / "heroic_gogdl" / "vendored"
    vendored.mkdir(parents=True)
    sample_so = vendored / "gogdl_xdelta3.abi3.so"

    # 1. Non-existent cache dir returns False
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(tmp_path / "empty")}):
        assert clean_mismatched_gogdl_vendored_cache() is False

    # 2. On aarch64, if cache holds an x86_64 .so, it gets purged
    sample_so.write_bytes(b"\x7fELF" + b"\x00" * 14 + b"\x3e\x00")
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}), \
         patch("platform.machine", return_value="aarch64"):
        assert clean_mismatched_gogdl_vendored_cache() is True
        assert not vendored.exists()

    # 3. On aarch64, if cache holds an aarch64 .so, it is retained
    vendored.mkdir(parents=True)
    sample_so.write_bytes(b"\x7fELF" + b"\x00" * 14 + b"\xb7\x00")
    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}), \
         patch("platform.machine", return_value="aarch64"):
        assert clean_mismatched_gogdl_vendored_cache() is False
        assert vendored.exists()


def test_clean_all_mismatched_cli_vendored_caches(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    leg_vendored = cache_dir / "legendary" / "vendored" / "Cryptodome" / "Util"
    gog_vendored = cache_dir / "heroic_gogdl" / "vendored"
    leg_vendored.mkdir(parents=True)
    gog_vendored.mkdir(parents=True)

    (leg_vendored / "_cpuid_c.abi3.so").write_bytes(b"\x7fELF" + b"\x00" * 14 + b"\x3e\x00")
    (gog_vendored / "gogdl_xdelta3.abi3.so").write_bytes(b"\x7fELF" + b"\x00" * 14 + b"\x3e\x00")

    with patch.dict("os.environ", {"XDG_CACHE_HOME": str(cache_dir)}), \
         patch("platform.machine", return_value="aarch64"):
        clean_all_mismatched_cli_vendored_caches()
        assert not (cache_dir / "legendary" / "vendored").exists()
        assert not gog_vendored.exists()


