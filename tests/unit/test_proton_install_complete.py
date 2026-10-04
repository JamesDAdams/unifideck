"""Unit tests for Proton install-completeness validation + selector skip.

Regression: the selector handed Steam's global-default compat tool to
umu with no check that the install was actually complete. A truncated /
half-extracted Proton (observed: an official Proton whose ``files/`` was
empty; and separately a broken auto-updated Proton-Experimental) makes
every ``umu-run`` operation hang, wedging the serial install queue.

``is_proton_install_complete`` gates the structural case (missing
payload); ``_resolve_logged`` uses it to skip a broken tier and fall
through to the plugin-managed GE-Proton. (A build that is structurally
complete but hangs at *runtime* is caught separately by the compat-step
timeout + warmup GE-retry.)
"""
from __future__ import annotations

import os
import stat

from unifideck.launcher.proton.infrastructure import ge_installer, selector

_VALID_MANIFEST = '"manifest"\n{\n  "commandline" "/proton run"\n}\n'


def _make_proton(
    root, *, exe=True, files=True, wine=True, wine64=False,
    wine_bin_arm64=False, compat_vdf=None,
    version="1.0", manifest=_VALID_MANIFEST,
    protonfixes=False,
):
    """Build a Proton tool dir; return the ``proton`` script path.

    ``manifest`` writes ``toolmanifest.vdf``; pass ``None`` to omit the file
    or ``""`` to model the truncated-download case.

    ``wine_bin_arm64`` models the real on-device layout of native ARM64
    Protons (Proton Experimental (ARM64), proton-cachyos-11.0-arm64):
    confirmed on 2026-10-03 to ship their Wine loader at
    ``files/bin-arm64/wine`` — NOT ``files/bin/wine`` or
    ``files/bin/wine64`` — with no ``files/bin/`` directory at all.
    """
    root.mkdir(parents=True, exist_ok=True)
    proton = root / "proton"
    proton.write_text("#!/usr/bin/env python3\n")
    if exe:
        proton.chmod(proton.stat().st_mode | stat.S_IXUSR)
    else:
        proton.chmod(proton.stat().st_mode & ~stat.S_IXUSR)
    if files:
        files_dir = root / "files"
        bindir = files_dir / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        if wine:
            (bindir / "wine").write_text("")
        if wine64:
            (bindir / "wine64").write_text("")
        if wine_bin_arm64:
            bindir_arm64 = files_dir / "bin-arm64"
            bindir_arm64.mkdir(parents=True, exist_ok=True)
            (bindir_arm64 / "wine").write_text("")
    if compat_vdf is not None:
        (root / "compatibilitytool.vdf").write_text(compat_vdf)
    if version is not None:
        (root / "version").write_text(version)
    # Every real Proton ships a toolmanifest.vdf; umu parses it on every
    # launch, so a build without a usable one is not a usable build.
    if manifest is not None:
        (root / "toolmanifest.vdf").write_text(manifest)
    # Every official Steam Proton ships a zero-byte dist.lock — assert it
    # does NOT trip the check (it is a normal per-tool lock, not corruption).
    (root / "dist.lock").write_text("")
    # ``protonfixes/`` is what umu's ``run_command`` chdirs into
    # (``cwd=PROTONPATH/protonfixes``); GE-Proton / UMU-Proton / community
    # builds bundle it, official Valve Protons do not. Opt-in so tests can
    # model either shape.
    if protonfixes:
        (root / "protonfixes").mkdir(exist_ok=True)
    return proton


def test_complete_install_passes(tmp_path):
    proton = _make_proton(tmp_path / "Proton - Experimental")
    assert ge_installer.is_proton_install_complete(proton) is True


def test_zero_byte_dist_lock_does_not_fail_a_complete_install(tmp_path):
    # Guards the real-world false-positive: all official Protons have a
    # 0-byte dist.lock; treating it as "mid-install" would reject them all.
    proton = _make_proton(tmp_path / "Proton 10.0")
    assert (proton.parent / "dist.lock").stat().st_size == 0
    assert ge_installer.is_proton_install_complete(proton) is True


def test_zero_byte_toolmanifest_fails(tmp_path):
    """The umu-launcher#706 crash shape.

    umu guards only ``toolmanifest.vdf.is_file()``, so a 0-byte file (what a
    truncated download or interrupted extract leaves) sails through, then
    ``vdf.load`` returns ``{}`` and ``["manifest"]`` raises an unhandled
    ``KeyError: 'manifest'`` — a bare traceback instead of a launch. Failing
    the completeness gate instead makes the selector fall through to a
    known-good Proton.
    """
    proton = _make_proton(tmp_path / "GE-Proton11-3", manifest="")
    assert (proton.parent / "toolmanifest.vdf").stat().st_size == 0
    assert ge_installer.is_proton_install_complete(proton) is False


def test_missing_toolmanifest_fails(tmp_path):
    proton = _make_proton(tmp_path / "GE-Proton11-3", manifest=None)
    assert ge_installer.is_proton_install_complete(proton) is False


def test_garbage_toolmanifest_fails(tmp_path):
    """Present and non-empty, but with no ``manifest`` block to read."""
    proton = _make_proton(tmp_path / "GE-Proton11-3", manifest="<html>404</html>")
    assert ge_installer.is_proton_install_complete(proton) is False


def test_non_executable_proton_script_fails(tmp_path):
    proton = _make_proton(tmp_path / "GE-Proton", exe=False)
    assert ge_installer.is_proton_install_complete(proton) is False


def test_missing_proton_script_fails(tmp_path):
    proton = _make_proton(tmp_path / "P")
    proton.unlink()
    assert ge_installer.is_proton_install_complete(proton) is False


def test_empty_files_dir_fails(tmp_path):
    # The observed "Proton 8.0" case: dir present but no payload.
    root = tmp_path / "Proton 8.0"
    root.mkdir()
    (root / "proton").write_text("x")
    os.chmod(root / "proton", 0o755)
    (root / "files").mkdir()  # empty
    (root / "version").write_text("1.0")
    assert ge_installer.is_proton_install_complete(root / "proton") is False


def test_missing_wine_loader_fails(tmp_path):
    proton = _make_proton(tmp_path / "Proton", wine=False)
    assert ge_installer.is_proton_install_complete(proton) is False


def test_arm64_wine64_only_passes(tmp_path):
    """ARM64 Proton builds (e.g. Proton Experimental ARM64) ship wine64, not wine."""
    proton = _make_proton(
        tmp_path / "Proton Experimental (ARM64)",
        wine=False,
        wine64=True,
    )
    assert ge_installer.is_proton_install_complete(proton) is True


def test_arm64_bin_arm64_wine_passes(tmp_path):
    """Real on-device layout (confirmed 2026-10-03): native ARM64 Protons

    ("Proton Experimental (ARM64)", "proton-cachyos-11.0-arm64") ship
    their Wine loader at ``files/bin-arm64/wine`` — NOT
    ``files/bin/wine`` or ``files/bin/wine64`` — and have no
    ``files/bin/`` directory at all, no ``compatibilitytool.vdf``
    either (for the official Proton Experimental ARM64 build). Without
    this check both native ARM64 Protons were rejected as "incomplete"
    and every launch silently fell back to an incompatible x86_64
    GE-Proton, breaking 32-bit DXVK/Vulkan for titles like
    Hitman: Absolution.
    """
    proton = _make_proton(
        tmp_path / "Proton Experimental (ARM64)",
        wine=False,
        wine64=False,
        wine_bin_arm64=True,
    )
    assert ge_installer.is_proton_install_complete(proton) is True


def test_arm64_bin_arm64_wine_passes_distro_package(tmp_path):
    """proton-cachyos-11.0-arm64 also ships files/bin-arm64/wine, confirmed on-device."""
    proton = _make_proton(
        tmp_path / "proton-cachyos-11.0-arm64",
        wine=False,
        wine64=False,
        wine_bin_arm64=True,
    )
    assert ge_installer.is_proton_install_complete(proton) is True


def test_distro_package_with_compatibilitytool_vdf_and_no_files_passes(tmp_path):
    """Distro packages (e.g. proton-cachyos) without files/ pass via compatibilitytool.vdf."""
    proton = _make_proton(
        tmp_path / "proton-cachyos-11.0-arm64",
        files=False,
        compat_vdf='"compatibilitytools" {}',
    )
    assert ge_installer.is_proton_install_complete(proton) is True


def test_distro_package_with_compatibilitytool_vdf_and_files_passes(tmp_path):
    """Distro packages with compatibilitytool.vdf and files/ pass even without files/bin/wine."""
    proton = _make_proton(
        tmp_path / "proton-cachyos-11.0-arm64",
        files=True,
        wine=False,
        wine64=False,
        compat_vdf='"compatibilitytools" {}',
    )
    assert ge_installer.is_proton_install_complete(proton) is True


def test_empty_version_fails(tmp_path):
    proton = _make_proton(tmp_path / "Proton", version="")
    assert ge_installer.is_proton_install_complete(proton) is False


# ── selector: skip an incomplete tier, keep a complete one ──────────


def test_resolve_logged_skips_incomplete_install(tmp_path, monkeypatch):
    bad = _make_proton(tmp_path / "Broken", files=False)
    monkeypatch.setattr(selector, "resolve_proton_path", lambda tool: bad)

    tried: list[str] = []
    result = selector._resolve_logged("global-default", "proton_experimental", tried)

    assert result is None  # skipped → caller falls through to managed GE
    assert tried == ["global-default:proton_experimental"]


def test_resolve_logged_returns_complete_install(tmp_path, monkeypatch):
    good = _make_proton(tmp_path / "Proton - Experimental", protonfixes=True)
    monkeypatch.setattr(selector, "resolve_proton_path", lambda tool: good)

    tried: list[str] = []
    result = selector._resolve_logged("global-default", "proton_experimental", tried)

    assert result == good


def test_resolve_logged_accepts_valve_proton_on_arm(
    tmp_path, monkeypatch,
):
    """On ARM64, official Valve Proton is accepted even without protonfixes/."""
    from unifideck.core import arch
    monkeypatch.setattr(arch, "is_arm", lambda: True)
    bare_valve = _make_proton(
        tmp_path / "Proton Experimental (ARM64)",
        wine=False, wine64=False, wine_bin_arm64=True,
        protonfixes=False,
    )
    monkeypatch.setattr(selector, "resolve_proton_path", lambda tool: bare_valve)

    tried: list[str] = []
    result = selector._resolve_logged("steam", "proton_experimental", tried)

    assert result == bare_valve


def test_resolve_logged_accepts_arm64_proton_experimental(tmp_path, monkeypatch):
    arm64_exp = _make_proton(
        tmp_path / "Proton Experimental (ARM64)",
        wine=False,
        wine64=True,
        protonfixes=True,
    )
    monkeypatch.setattr(selector, "resolve_proton_path", lambda tool: arm64_exp)

    tried: list[str] = []
    result = selector._resolve_logged("saved", "proton_experimental", tried)

    assert result == arm64_exp


def test_resolve_logged_accepts_real_arm64_proton_experimental_layout(
    tmp_path, monkeypatch,
):
    """Regression test for the real on-device layout (bin-arm64/wine),

    distinct from the hypothetical wine64 layout above — a field bundle
    showed ``files/bin/`` does not even exist on native ARM64 Proton,
    so a fix that only recognised ``wine64`` still rejected the real
    install and fell back to GE-Proton11-7.
    """
    arm64_exp = _make_proton(
        tmp_path / "Proton Experimental (ARM64)",
        wine=False,
        wine64=False,
        wine_bin_arm64=True,
        protonfixes=True,
    )
    monkeypatch.setattr(selector, "resolve_proton_path", lambda tool: arm64_exp)

    tried: list[str] = []
    result = selector._resolve_logged("saved", "proton_experimental", tried)

    assert result == arm64_exp


def test_resolve_logged_accepts_distro_cachyos_proton(tmp_path, monkeypatch):
    # ``proton-cachyos`` is a community build that bundles protonfixes/, so
    # the Valve-only protonfixes guard must not touch it.
    cachy = _make_proton(
        tmp_path / "proton-cachyos-11.0-arm64",
        files=False,
        protonfixes=True,
        compat_vdf='"compatibilitytools" {}',
    )
    monkeypatch.setattr(selector, "resolve_proton_path", lambda tool: cachy)

    tried: list[str] = []
    result = selector._resolve_logged("global-default", "proton-cachyos", tried)

    assert result == cachy


def test_resolve_logged_none_when_tool_unresolved(monkeypatch):
    monkeypatch.setattr(selector, "resolve_proton_path", lambda tool: None)
    tried: list[str] = []
    assert selector._resolve_logged("saved", "nope", tried) is None
