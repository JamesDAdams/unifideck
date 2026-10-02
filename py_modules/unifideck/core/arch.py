"""core/arch.py — Host machine CPU architecture detection and path resolution."""
from __future__ import annotations

import logging
import os
import platform
import shutil
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

_ARM_ARCHS = frozenset({"aarch64", "arm64", "armv7l", "armv8l"})
_ELF_MACHINE_AARCH64 = 0xB7
_ELF_MACHINE_X86_64 = 0x3E


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


def get_elf_machine(path: Path | str) -> int | None:
    """Read the e_machine ELF header field from a binary, or None if invalid."""
    p = Path(path)
    try:
        with p.open("rb") as f:
            magic = f.read(4)
            if magic != b"\x7fELF":
                return None
            f.seek(18)
            e_machine_bytes = f.read(2)
            if len(e_machine_bytes) < 2:
                return None
            val: int = struct.unpack("<H", e_machine_bytes)[0]
            return val
    except OSError:
        return None


def clean_mismatched_legendary_vendored_cache() -> bool:
    """Purge ~/.cache/legendary/vendored if compiled modules do not match current architecture."""
    cache_home = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache_home).expanduser() if cache_home else Path("~/.cache").expanduser()
    vendored_dir = base / "legendary" / "vendored"

    if not vendored_dir.is_dir():
        return False

    sample_so = vendored_dir / "Cryptodome" / "Util" / "_cpuid_c.abi3.so"
    if not sample_so.is_file():
        # Fallback check for any .so in Cryptodome
        so_files = list(vendored_dir.glob("Cryptodome/**/*.so"))
        if not so_files:
            return False
        sample_so = so_files[0]

    machine = get_elf_machine(sample_so)
    expected_machine = _ELF_MACHINE_AARCH64 if is_arm() else _ELF_MACHINE_X86_64

    if machine != expected_machine:
        logger.warning(
            "[arch] Legendary vendored native cache arch mismatch "
            "(found elf machine 0x%x, expected 0x%x). Purging %s",
            machine or 0,
            expected_machine,
            vendored_dir,
        )
        try:
            shutil.rmtree(vendored_dir, ignore_errors=True)
            return True
        except Exception:
            logger.exception("[arch] Failed to purge mismatched legendary vendored dir %s", vendored_dir)
            return False

    return False

