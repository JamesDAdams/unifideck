"""core/arch.py — Host machine CPU architecture detection and path resolution."""
from __future__ import annotations

import os
import platform
from pathlib import Path

_ARM_ARCHS = frozenset({"aarch64", "arm64", "armv7l", "armv8l"})


def is_arm() -> bool:
    """Return True if running on an ARM CPU architecture."""
    return platform.machine().lower() in _ARM_ARCHS


def get_arch_name() -> str:
    """Return normalized architecture name ('aarch64' or 'x86_64')."""
    return "aarch64" if is_arm() else "x86_64"


def resolve_bundled_binary_path(plugin_dir: Path | str, tool_name: str) -> str:
    """Resolve bundled binary path checking architecture-specific subfolder first."""
    base = Path(plugin_dir)
    arch = get_arch_name()
    arch_bin = base / "bin" / arch / tool_name
    if arch_bin.is_file() and os.access(arch_bin, os.X_OK):
        return str(arch_bin)
    bundled = base / "bin" / tool_name
    return str(bundled) if bundled.is_file() else tool_name
