"""core/arch.py — Host machine CPU architecture detection and path resolution.

WHY THE ARCH CHECK IS KEYED TO THE CLI, NOT TO ``platform.machine()``
-------------------------------------------------------------------
The original check compared a zipapp's extracted native modules against
``platform.machine()``. That is only valid while exactly one architecture
ever runs on a device — and on an ARM64 host it does not.

SteamOS/Bazzite ARM64 runs the Decky backend itself under **FEX-Emu**, so
the long-lived backend process reports ``machine() == "x86_64"`` and loads
``bin/x86_64/legendary``, while the per-launch ``unifideck-launcher``
subprocess is a NATIVE aarch64 interpreter that loads
``bin/aarch64/legendary``. Both zipapps extract their native modules into
the SAME ``~/.cache/legendary/vendored``, and both then compared what they
found against *their own* ``platform.machine()`` — so each deleted the
other's cache. A single launch produced three back-to-back purges
(field log ``fbe72431.log``, 23:37-23:39) followed by::

    ModuleNotFoundError: No module named 'Cryptodome.Cipher'
    OSError: Cannot load native module 'Cryptodome.Util._cpuid_c'

and the game never started. The same thrash broke Epic downloads, which
is why a cache-purge fix looked like it "worked" and then regressed.

The correct question is not "does the cache match this process's
architecture" but **"does the cache match the architecture of the CLI that
is about to load it"** — and that is knowable exactly, from the ELF header
of the very binary ``resolve_bundled_binary_path`` selected. That binary
already matches the calling process by construction, so when the cache was
written by an earlier dual-arch run it is genuinely stale and purging it
is right, and when it was written by a peer process of the other
architecture nothing happens at all.

Never compare against ``platform.machine()`` for a decision that deletes
files. Used only to CHOOSE which bundled binary to run.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import struct
import time
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

_ARM_ARCHS = frozenset({"aarch64", "arm64", "armv7l", "armv8l"})
#: ELF ``e_machine`` values, for reading and for tests only. Nothing
#: compares a cache against a host-expected constant any more — the
#: expected value comes from the CLI's own header, which is the whole
#: point. Kept named so the test fixtures and any log line that prints a
#: machine value stay readable.
#: EM_AARCH64 == 0xB7, EM_X86_64 == 0x3E.

#: How long a purge of one tool's vendored cache is suppressed.
#: A wrong-arch cache is a one-time condition, not a per-invocation
#: state: once the CLI re-extracts (or a peer repopulates) the directory,
#: the next check reads a matching ELF and does nothing. The TTL exists
#: only to bound the damage when a purge cannot stick — a peer process
#: repopulating between our check and our ``rmtree``, or a read-only or
#: fully-loaded cache. Same shape and rationale as
#: ``umu_runtime._REPAIR_MARKER_TTL_SECONDS``: an on-disk marker, because
#: the backend (long-lived) and every launcher subprocess (fresh) share
#: one cache directory and must not each get their own in-memory budget.
_PURGE_MARKER_TTL_SECONDS = 120


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


def _elf_machine_from_bytes(data: bytes) -> int | None:
    """e_machine of an in-memory ELF image, or None."""
    if len(data) < 20 or data[:4] != b"\x7fELF":
        return None
    val: int = struct.unpack("<H", data[18:20])[0]
    return val


def plugin_root() -> Path | None:
    """The plugin's install root, or None when it holds no bundled binaries.

    Delegates to :func:`core.paths.resolve_plugin_dir` rather than walking
    up from ``__file__``: that resolver already honours
    ``UNIFIDECK_PLUGIN_DIR`` and ``DECKY_PLUGIN_DIR``, which a dev
    checkout or a relocated plugin sets — and a hand-rolled walk misses
    both, silently degrading every default here to "no check, no cache
    scoping" with nothing logged.

    Still verifies ``bin/`` exists. ``resolve_plugin_dir`` always returns
    something (its last step is a default that may not exist), so the
    probe is what actually distinguishes "found the plugin" from "fell
    back", and that distinction is logged instead of hidden.
    """
    from unifideck.core.paths import resolve_plugin_dir

    root = resolve_plugin_dir()
    if (root / "bin").is_dir():
        return root
    logger.debug(
        "[arch] no bin/ under resolved plugin root %s — "
        "skipping default vendored-cache checks", root,
    )
    return None


def get_cli_elf_machine(cli_path: Path | str | None) -> int | None:
    """Architecture of the native modules a bundled CLI will load.

    Accepts either shape the bundled tools ship as:

    * a **zipapp** (legendary, gogdl — a zip whose first bytes are a
      ``#!/usr/bin/env python3`` shebang). These are what actually need
      the answer: they extract ``*.so`` next to themselves under
      ``XDG_CACHE_HOME`` and load those. Read one of its bundled ``.so``
      members, preferring the one the tool's own ``__main__`` extracts
      when a specific name is given.
    * a plain **ELF executable** (nile, comet) — its own header is the
      answer.

    Returns ``None`` when the architecture cannot be established, which
    every caller must treat as "do not touch the cache".
    """
    if not cli_path:
        return None
    path = Path(cli_path)
    if not path.is_file():
        return None
    try:
        if not zipfile.is_zipfile(path):
            return get_elf_machine(path)
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.endswith(".so"):
                    # ``zf.read`` decompresses the WHOLE member before the
                    # caller slices it — on the real x86_64 legendary that
                    # is a 433 KB decompress to inspect 20 bytes of header,
                    # measured at ~2 ms, on a path now hit by every
                    # library refresh. ``zf.open`` streams, so this reads
                    # only the header it needs.
                    with zf.open(name) as member:
                        machine = _elf_machine_from_bytes(member.read(20))
                    if machine is not None:
                        return machine
    except (OSError, zipfile.BadZipFile):
        return None
    return None


def arch_scoped_cache_home_for_tool(tool: str) -> Path | None:
    """``arch_scoped_cache_home`` for a tool this process would run itself.

    For the many call sites that build an environment once and hand it to
    a long pipeline of CLI invocations (the GOG install path builds one
    env for gogdl and reuses it everywhere) — they know the tool by name
    but not the plugin root, and threading both through every one of them
    would be noise. Returns None when no bundled binary can be resolved
    or its architecture is unknown, leaving the caller's own value alone.
    """
    root = plugin_root()
    if root is None:
        return None
    return arch_scoped_cache_home(resolve_bundled_binary_path(root, tool))


def _cache_home() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME")
    return Path(cache_home).expanduser() if cache_home else Path("~/.cache").expanduser()


def arch_scoped_cache_home(cli_path: Path | str | None) -> Path | None:
    """A cache root private to one CLI's architecture, or None if unknown.

    This is the fix, not the purge. Deleting a shared directory works
    only while every writer wants the same contents — and on this host
    the FEX-emulated backend and the native launcher do NOT: they each
    extract their own natives into ``~/.cache/legendary/vendored`` and
    each needs the other's spot free. No TTL makes that safe; it just
    makes the destruction intermittent.

    Pointing ``XDG_CACHE_HOME`` at an architecture-tagged subdirectory
    means the two extract into sibling directories and neither can ever
    observe, disturb, or delete the other's files. The CLI's own
    ``__main__`` resolves everything under ``XDG_CACHE_HOME``, so this is
    the single variable that governs its cache — no patching of the
    zipapp required.

    Returns None when the architecture is unknown, so a caller keeps the
    caller's own value rather than guessing a scope.
    """
    machine = get_cli_elf_machine(cli_path)
    if machine is None:
        return None
    return _cache_home() / f"unifideck-arch-{machine:02x}"


def _purge_marker(vendored_dir: Path) -> Path:
    return vendored_dir.parent / ".unifideck-arch-purge"


def _purge_recently_attempted(vendored_dir: Path) -> bool:
    try:
        age = time.time() - _purge_marker(vendored_dir).stat().st_mtime
    except OSError:
        return False
    return age < _PURGE_MARKER_TTL_SECONDS


def _sample_native(vendored_dir: Path, preferred: str) -> Path | None:
    """A ``.so`` inside ``vendored_dir`` to inspect, preferring ``preferred``."""
    direct = vendored_dir / preferred
    if direct.is_file():
        return direct
    found = sorted(vendored_dir.glob("**/*.so"))
    return found[0] if found else None


def _purge_mismatched_vendored_cache(
    tool: str, preferred_so: str, cli_path: Path | str | None,
) -> bool:
    """Purge one tool's vendored cache when it cannot match ``cli_path``.

    Returns True only when a directory was actually removed.
    """
    vendored_dir = _cache_home() / tool / "vendored"
    if not vendored_dir.is_dir():
        return False

    expected = get_cli_elf_machine(cli_path)
    if expected is None:
        # Never delete on an unproven mismatch. An unreadable or
        # non-zipapp binary used to mean "purge it and hope"; now it means
        # "leave it alone" — the cost of being wrong is a game that will
        # not start, which is exactly the failure being fixed.
        return False

    sample = _sample_native(vendored_dir, preferred_so)
    if sample is None:
        return False

    machine = get_elf_machine(sample)
    if machine == expected:
        return False

    if _purge_recently_attempted(vendored_dir):
        # A peer process already purged for this exact condition seconds
        # ago and the directory is back: it is actively repopulating, so
        # deleting again only races it.
        logger.debug(
            "[arch] %s vendored cache still mismatched after a recent "
            "purge (found 0x%x, expected 0x%x) — leaving it alone",
            tool, machine or 0, expected,
        )
        return False

    logger.warning(
        "[arch] %s vendored native cache arch mismatch (found elf machine "
        "0x%x, expected 0x%x for %s). Purging %s",
        tool, machine or 0, expected, cli_path, vendored_dir,
    )
    try:
        shutil.rmtree(vendored_dir, ignore_errors=True)
    except OSError:
        logger.exception(
            "[arch] Failed to purge mismatched %s vendored dir %s",
            tool, vendored_dir,
        )
        return False
    try:
        _purge_marker(vendored_dir).touch()
    except OSError:
        logger.debug("[arch] purge marker not writable for %s", vendored_dir)
    return not vendored_dir.exists()


def clean_mismatched_legendary_vendored_cache(
    cli_path: Path | str | None = None,
) -> bool:
    """Purge ``~/.cache/legendary/vendored`` if it cannot serve ``cli_path``.

    ``cli_path`` defaults to the bundled legendary selected for THIS
    process — the right default, since the caller is about to run exactly
    that binary. Pass it explicitly when a caller already resolved a
    different path (an env override, or a bundled copy it prefers).
    """
    if cli_path is None:
        root = plugin_root()
        if root is None:
            return False
        cli_path = resolve_bundled_binary_path(root, "legendary")
    return _purge_mismatched_vendored_cache(
        "legendary", "Cryptodome/Util/_cpuid_c.abi3.so", cli_path,
    )


def clean_mismatched_gogdl_vendored_cache(
    cli_path: Path | str | None = None,
) -> bool:
    """Purge ``~/.cache/heroic_gogdl/vendored`` if it cannot serve ``cli_path``.

    See :func:`clean_mismatched_legendary_vendored_cache` for why the
    expected architecture comes from the CLI and never from
    ``platform.machine()``.
    """
    if cli_path is None:
        root = plugin_root()
        if root is None:
            return False
        cli_path = resolve_bundled_binary_path(root, "gogdl")
    return _purge_mismatched_vendored_cache(
        "heroic_gogdl", "gogdl_xdelta3.abi3.so", cli_path,
    )


def clean_all_mismatched_cli_vendored_caches(
    cli_paths: dict[str, str | None] | None = None,
) -> None:
    """Purge every known zipapp CLI vendored cache that cannot serve its CLI.

    ``cli_paths`` maps a tool name to the binary that will actually be
    executed for it. It is optional — omitting it falls back to the
    bundled binary each process would select for itself — but a caller
    that has ALREADY resolved a path should pass it, so the check and the
    execution can never disagree.
    """
    paths = cli_paths or {}

    def _path(tool: str) -> str | None:
        return paths.get(tool)

    clean_mismatched_legendary_vendored_cache(_path("legendary"))
    clean_mismatched_gogdl_vendored_cache(_path("gogdl"))
