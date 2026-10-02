"""auth.edge_browser.detection — Edge install detection utilities.

Pure-function helpers that probe the system to determine whether
Microsoft Edge is present. Used both by ``EdgeInstaller`` (to
decide if install is needed) and by ``EdgeBrowser.is_installed``
(for UI availability checks).

Extracted from ``installer.py`` on 2026-04-18 to separate the
read-only detection concern from the mutate-the-system install
concern. The two were conflated in a single class; keeping them
apart makes the detection side trivially testable without any
install side effects.

Functions here take ``clean_env_fn`` as first argument rather
than being methods of a class — detection has no instance state,
it's a series of subprocess probes.
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_ARM_ARCHS = frozenset({"aarch64", "arm64", "armv7l", "armv8l"})
_EDGE_FLATPAK_APP = "com.microsoft.Edge"
_CHROMIUM_FLATPAK_APP = "org.chromium.Chromium"
_FLATPAK_APPS = (_EDGE_FLATPAK_APP, _CHROMIUM_FLATPAK_APP)
_NATIVE_BINS = (
    "microsoft-edge",
    "microsoft-edge-stable",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
)


def is_arm() -> bool:
    return platform.machine().lower() in _ARM_ARCHS


def get_flatpak_apps() -> tuple[str, ...]:
    if is_arm():
        return (_CHROMIUM_FLATPAK_APP, _EDGE_FLATPAK_APP)
    return (_EDGE_FLATPAK_APP, _CHROMIUM_FLATPAK_APP)


def get_native_bins() -> tuple[str, ...]:
    if is_arm():
        return (
            "chromium",
            "chromium-browser",
            "google-chrome",
            "google-chrome-stable",
            "microsoft-edge",
            "microsoft-edge-stable",
        )
    return (
        "microsoft-edge",
        "microsoft-edge-stable",
        "chromium",
        "chromium-browser",
        "google-chrome",
        "google-chrome-stable",
    )


def get_target_flatpak_app() -> str:
    if is_arm():
        return _CHROMIUM_FLATPAK_APP
    return _EDGE_FLATPAK_APP


def flatpak_remote_names(
    clean_env_fn: Callable[[], dict[str, Any]], scope: str,
) -> set[str]:
    """Return configured flatpak remote names for the given scope.

    Empty set is a valid "no remotes" signal — caller shouldn't
    treat it as an error.
    """
    if scope not in ("--user", "--system"):
        return set()
    try:
        result = subprocess.run(
            ["flatpak", "remotes", scope, "--columns=name"],
            capture_output=True,
            text=True,
            timeout=5,
            env=clean_env_fn(),
            check=False,
        )
    except Exception:
        # Intentional: flatpak may be missing (non-Deck), or the
        # scope unsupported. An empty set is a "no remotes" signal
        # and is fine for callers.
        return set()
    if result.returncode != 0:
        return set()
    remotes: set[str] = set()
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.lower() == "name":
            continue
        remotes.add(line)
    return remotes


def find_edge_cmd(
    clean_env_fn: Callable[[], dict[str, Any]],
) -> list[str] | None:
    """Find an available Microsoft Edge browser command.

    Checks both ``--user`` and ``--system`` flatpak installations,
    then falls back to native Edge binaries.

    Returns:
      Command as a list (for subprocess), or ``None``.

    """
    if shutil.which("flatpak"):
        for app_id in get_flatpak_apps():
            cmd = _try_flatpak_app(app_id, clean_env_fn)
            if cmd is not None:
                return cmd
    for binary in get_native_bins():
        if shutil.which(binary):
            return [binary]
    return None


def _try_flatpak_app(
    app_id: str, clean_env_fn: Callable[[], dict[str, Any]],
) -> list[str] | None:
    """Probe ``flatpak info`` for ``app_id`` in user and system scopes.

    Returns the runnable command list if the app is installed
    in either scope, None if neither scope has it OR if the
    probe itself raised (timeout, missing flatpak binary after
    a race). The caller just moves to the next app_id / native
    fallback on None.
    """
    try:
        for flag in ("--user", "--system"):
            result = subprocess.run(
                ["flatpak", "info", flag, app_id],
                capture_output=True, timeout=5,
                env=clean_env_fn(),
                check=False,  # rc is read manually below
            )
            if result.returncode == 0:
                return ["flatpak", "run", app_id]
    except Exception as e:
        # Flatpak probe can raise many things (subprocess
        # timeout, OSError from missing binary after race).
        # Fall through to the next app_id / native fallback.
        logger.debug("[Edge] flatpak probe failed for %s: %s", app_id, e)
    return None


def is_edge_installed(clean_env_fn: Callable[[], dict[str, Any]]) -> bool:
    """Return True if Microsoft Edge is available (flatpak or native)."""
    return find_edge_cmd(clean_env_fn) is not None
