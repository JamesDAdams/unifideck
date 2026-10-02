"""tests/unit/test_binary_resolver_arm.py — Multi-architecture binary resolution tests."""
from __future__ import annotations

import stat
from pathlib import Path
from unittest.mock import patch

from unifideck.core.binaries.binary_resolver import BinaryResolver
from unifideck.core.binaries.binary_signatures import (
    _KNOWN_HASHES,
    compute_sha256,
    verify_bundled_binary,
)
from unifideck.core.types.domain import CLITool


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"#!/bin/sh\necho test\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_resolver_prioritizes_aarch64_when_on_arm(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    x86_bin = bin_dir / "x86_64" / "legendary"
    arm_bin = bin_dir / "aarch64" / "legendary"
    root_bin = bin_dir / "legendary"

    _make_executable(x86_bin)
    _make_executable(arm_bin)
    _make_executable(root_bin)

    tool = CLITool(name="legendary", search_paths=[str(root_bin)])

    with patch("platform.machine", return_value="aarch64"):
        resolved = BinaryResolver().resolve(tool)
        assert resolved == str(arm_bin)

    with patch("platform.machine", return_value="arm64"):
        resolved = BinaryResolver().resolve(tool)
        assert resolved == str(arm_bin)


def test_resolver_prioritizes_x86_64_when_on_x86(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    x86_bin = bin_dir / "x86_64" / "legendary"
    arm_bin = bin_dir / "aarch64" / "legendary"
    root_bin = bin_dir / "legendary"

    _make_executable(x86_bin)
    _make_executable(arm_bin)
    _make_executable(root_bin)

    tool = CLITool(name="legendary", search_paths=[str(root_bin)])

    with patch("platform.machine", return_value="x86_64"):
        resolved = BinaryResolver().resolve(tool)
        assert resolved == str(x86_bin)


def test_resolver_falls_back_to_root_bin_if_arch_bin_absent(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    root_bin = bin_dir / "legendary"
    _make_executable(root_bin)

    tool = CLITool(name="legendary", search_paths=[str(root_bin)])

    with patch("platform.machine", return_value="aarch64"):
        resolved = BinaryResolver().resolve(tool)
        assert resolved == str(root_bin)


def test_binary_signatures_verifies_multiarch(tmp_path: Path) -> None:
    sample_file = tmp_path / "test_bin"
    sample_file.write_bytes(b"content")
    digest = compute_sha256(str(sample_file))
    assert digest is not None

    with patch.dict(_KNOWN_HASHES, {"sample": (digest, "other_hash")}):
        assert verify_bundled_binary("sample", str(sample_file)) is True

    with patch.dict(_KNOWN_HASHES, {"sample": ("different_hash",)}):
        assert verify_bundled_binary("sample", str(sample_file)) is False
