"""tests/unit/test_py_modules_multiarch.py — Multi-architecture Python module loading tests."""
from __future__ import annotations

import runpy
from pathlib import Path
from unittest.mock import patch

from unifideck.core.arch import get_arch_name, is_arm, resolve_bundled_binary_path


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
