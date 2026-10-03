"""core/binaries/cli_env.py — Clean environment for bundled CLI subprocesses.

WHY THIS EXISTS
---------------
The Decky backend (``PluginLoader``) is a PyInstaller-frozen binary. Frozen
processes rewrite the dynamic-loader environment for their OWN bundled libs
and leave it that way for every child they spawn, so ``os.environ`` inside the
plugin carries::

    LD_LIBRARY_PATH=/tmp/_MEIxxxx      # the loader's bundled libs
    LD_LIBRARY_PATH_ORIG=...           # PyInstaller's stash of the real value

Handing that to a child that is *not* the frozen app makes it link the wrong
libraries. The repo has hit this repeatedly and fixed it locally each time:
``curl`` picked up the Steam Runtime's old libssl (``stores/epic/playtime_api``
and ``stores/epic/achievements``), ``umu-run`` failed to start ``python3``
inside pressure-vessel with "libz.so.1" and exited 127
(``launcher/proton/infrastructure/umu_runtime``), and
``bin/unifideck-launcher`` pops both variables before it imports anything.

This module is that same fix, factored out for the bundled store CLIs, which
never had it.

The store CLIs made this urgent: legendary >=0.20.40 and gogdl >=1.2.2 ship a
Python **zipapp** rather than a PyInstaller ELF, so they run under the SYSTEM
``python3`` via a ``#!/usr/bin/env python3`` shebang. A frozen ELF carries its
own libraries and ignores both the loader variables and ``PYTHON*``; a
shebang-launched interpreter obeys all of them. The failure mode is not
hypothetical: with the plugin's ``py_modules`` on ``PYTHONPATH``, legendary's
``Cryptodome`` import resolved our vendored ``cffi`` and died with
``Exception: Version mismatch`` before it could parse a single argument.

Deliberately NOT scrubbed: ``PATH`` (the shebang needs it to find ``python3``)
and ``HOME``/``XDG_*`` (the zipapps extract native modules under the cache dir
those name, and pinning them here would strand the caches).
"""
from __future__ import annotations

import os
from pathlib import Path

# Loader variables, plus PyInstaller's ``*_ORIG`` stashes. The stashes are
# dropped rather than restored: the pre-freeze value is Steam's, which is no
# more welcome in a non-Steam child than the ``_MEI`` one — restoring it is
# exactly the bug that made every GOG/Amazon/Ubisoft launch exit 127.
_LOADER_VARS = (
    "LD_LIBRARY_PATH",
    "LD_LIBRARY_PATH_ORIG",
    "LD_PRELOAD",
    "LD_PRELOAD_ORIG",
)

# Interpreter controls. Harmless to a frozen ELF, decisive for a zipapp:
# ``PYTHONHOME`` sends it to the wrong stdlib entirely, and ``PYTHONPATH``
# lets our vendored packages shadow the ones the zipapp bundles.
_PYTHON_VARS = (
    "PYTHONHOME",
    "PYTHONPATH",
)

SCRUBBED_VARS: tuple[str, ...] = (*_LOADER_VARS, *_PYTHON_VARS)

#: Bundled CLIs that extract native modules into a shared, per-user cache
#: directory and then load them by path. Only these have a vendored-cache
#: architecture to verify (see ``core.arch``); the others ship their natives
#: inside their own binary or do not have any. Keyed by the tool's file
#: name, which is what ``clean_cli_env(for_cli=…)`` compares against.
_VENDORED_CACHE_TOOLS = frozenset({"legendary", "gogdl"})


def clean_cli_env(
    overrides: dict[str, str] | None = None,
    *,
    for_cli: str | None = None,
) -> dict[str, str]:
    """Return ``os.environ`` minus the vars that break a bundled CLI.

    Args:
      overrides: extra variables to set on top, applied AFTER scrubbing so
        a caller can still pass a deliberate ``PYTHONPATH`` if it ever
        needs one.
      for_cli: the bundled CLI about to be spawned under this env. Only
        consulted when it is one of the zipapp tools that extract native
        modules into a shared cache (``legendary``, ``gogdl``), and then
        used to verify that cache against the binary that will actually
        load it. Every other CLI — and any whose architecture cannot be
        read — skips the check entirely.

    Returns:
      A new dict — ``os.environ`` itself is never mutated, so this is safe
      to call from the long-lived backend process.
    """
    from unifideck.core.arch import (
        arch_scoped_cache_home,
        clean_all_mismatched_cli_vendored_caches,
    )

    tool = Path(for_cli).name if for_cli else ""
    if tool in _VENDORED_CACHE_TOOLS:
        clean_all_mismatched_cli_vendored_caches({tool: for_cli})
        # Give each architecture its own vendored-cache root (see
        # ``core.arch``). On a host that runs this backend and its
        # launcher subprocesses under DIFFERENT architectures — ARM64
        # with the backend under FEX-Emu — both extract into the same
        # ``~/.cache/legendary``, and neither can use what the other
        # wrote. Scoping the variable ends that entirely instead of
        # deleting each other's files when it is detected.
        scoped = arch_scoped_cache_home(for_cli)
        if scoped is not None:
            overrides = {**(overrides or {}), "XDG_CACHE_HOME": str(scoped)}
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_VARS}
    if overrides:
        env.update(overrides)
    return env


def scrub_cli_env(env: dict[str, str]) -> dict[str, str]:
    """Strip the same variables from an env dict the caller already built.

    For call sites that assemble their own environment (store credential
    builders, cloud-save strategies) instead of starting from
    :func:`clean_cli_env`. Mutates and returns ``env`` so it can be used
    inline at the ``subprocess`` call.
    """
    for var in SCRUBBED_VARS:
        env.pop(var, None)
    return env
