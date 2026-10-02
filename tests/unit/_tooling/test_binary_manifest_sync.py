"""Guard test — bundled-binary versions must agree across all three sources.

A bundled store CLI is pinned in three places that nothing links together:

1. ``package.json`` ``remote_binary[]`` — URL + ``sha256hash``. Decky reads
   this at install time and verifies the download against the hash.
2. ``build-plugin.sh`` ``<TOOL>_URL`` — the same URLs, hardcoded a second
   time because ``prebuild_binaries()`` runs before any JSON parsing is
   available to it. Its own header comment says the two "must stay in sync"
   and points at this test.
3. ``core/binaries/binary_signatures.py`` ``_KNOWN_HASHES`` — the runtime
   check that the file on disk is the pinned version, whose docstring
   requires updating "IN THE SAME COMMIT as the binary update".

Three hand-maintained copies of the same fact drift the moment someone
bumps a version and updates two of them. The failure is quiet and nasty:
the build downloads one version while the manifest advertises another, so
Decky's install-time hash check rejects it, or the runtime signature check
reports a mismatch on a binary that is perfectly fine.

De-duplicating these properly is roadmap item #7. Until then this test is
the seam that makes a partial bump fail loudly, in CI, immediately.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

# Tools pinned in all three places. umu is deliberately absent: it is
# committed to the repo rather than downloaded, so it has no remote_binary
# entry and no URL in build-plugin.sh (its version lives in bin/umu/VERSION).
_TRIPLE_PINNED = (
    "legendary", "legendary_arm64",
    "gogdl", "gogdl_arm64",
    "nile", "nile_arm64",
    "comet", "comet_arm64",
)


def _repo_file(relative: str) -> Path | None:
    """Locate ``relative`` in the checkout, or ``None``."""
    from tests.unit._repo_root import find_repo_file

    return find_repo_file(relative)


@pytest.fixture(scope="module")
def manifest() -> dict[str, dict[str, str]]:
    """``remote_binary`` entries from package.json, keyed by tool name."""
    path = _repo_file("package.json")
    if path is None:
        pytest.skip(
            "package.json not found in any candidate location "
            "(set UNIFIDECK_REPO_ROOT to point at the checkout root)")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("remote_binary") or []
    return {e["name"]: e for e in entries if "name" in e}


@pytest.fixture(scope="module")
def shell_urls() -> dict[str, str]:
    """``<TOOL>_URL="..."`` assignments from build-plugin.sh."""
    path = _repo_file("build-plugin.sh")
    if path is None:
        pytest.skip(
            "build-plugin.sh not found in any candidate location "
            "(set UNIFIDECK_REPO_ROOT to point at the checkout root)")
    text = path.read_text(encoding="utf-8")
    found = re.findall(
        r'^([A-Z0-9_]+)_URL="([^"]+)"', text, flags=re.MULTILINE,
    )
    return {name.lower(): url for name, url in found}


def test_every_manifest_entry_has_a_shell_url(
    manifest: dict[str, dict[str, str]], shell_urls: dict[str, str],
) -> None:
    """Every remote_binary tool is also downloadable by build-plugin.sh."""
    missing = sorted(set(manifest) - set(shell_urls))
    assert not missing, (
        f"package.json pins {missing} but build-plugin.sh has no "
        f"<TOOL>_URL for them — prebuild_binaries() will not fetch them"
    )


def test_manifest_and_shell_urls_match(
    manifest: dict[str, dict[str, str]], shell_urls: dict[str, str],
) -> None:
    """The URL in package.json is byte-identical to build-plugin.sh's."""
    drifted = {
        name: (entry["url"], shell_urls[name])
        for name, entry in sorted(manifest.items())
        if name in shell_urls and entry["url"] != shell_urls[name]
    }
    assert not drifted, (
        "package.json and build-plugin.sh disagree on a binary URL — the "
        "build would download a different version than the manifest "
        "advertises, and Decky's install-time hash check would reject it:\n"
        + "\n".join(
            f"  {n}:\n    package.json:     {a}\n    build-plugin.sh:  {b}"
            for n, (a, b) in drifted.items()
        )
    )


def test_known_hashes_match_the_manifest(
    manifest: dict[str, dict[str, str]],
) -> None:
    """``_KNOWN_HASHES`` agrees with package.json for every pinned tool.

    Both describe the same artifact: Decky verifies the download against
    the manifest, and ``verify_bundled_binary`` verifies the file on disk
    against ``_KNOWN_HASHES``. If they disagree, one of them is wrong and
    a correct binary gets reported as tampered.
    """
    from unifideck.core.binaries.binary_signatures import _KNOWN_HASHES

    mismatched = {}
    for name in _TRIPLE_PINNED:
        if name in manifest and name in _KNOWN_HASHES:
            known = _KNOWN_HASHES[name]
            expected = manifest[name]["sha256hash"]
            if isinstance(known, tuple):
                if expected not in known:
                    mismatched[name] = (f"one of {known}", expected)
            elif known != expected:
                mismatched[name] = (known, expected)
    assert not mismatched, (
        "binary_signatures._KNOWN_HASHES disagrees with package.json "
        "remote_binary — bump both in the same commit:\n"
        + "\n".join(
            f"  {n}:\n    _KNOWN_HASHES: {a}\n    package.json:  {b}"
            for n, (a, b) in mismatched.items()
        )
    )


def test_bundled_umu_version_is_pinned() -> None:
    """``bin/umu/VERSION`` exists and names a plausible umu version.

    umu is the one bundled tool with no manifest entry, so this file is
    the only machine-readable record of which umu shipped — the support
    bundle reports it, and it is the first question on any launch failure
    (umu <=1.4.1 fetches the runtime from a URL that is now permanently
    403, so the version alone explains a whole class of reports).
    """
    path = _repo_file("bin/umu/VERSION")
    if path is None:
        pytest.skip(
            "bin/umu/VERSION not found in any candidate location "
            "(set UNIFIDECK_REPO_ROOT to point at the checkout root)")
    version = path.read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
        f"bin/umu/VERSION should hold a bare x.y.z version, got {version!r}"
    )


def test_manifest_hashes_match_the_actual_binary_on_disk(
    manifest: dict[str, dict[str, str]],
) -> None:
    """Each ``sha256hash`` matches the binary the build actually bundles.

    The other tests in this module only cross-check the three *declared*
    sources against each other. All three can agree with one another and
    still be wrong: the ARM entries added for multi-arch support shipped
    with hashes that matched no published asset, and ``prebuild_binaries()``
    validation had been made non-fatal, so nothing caught it. Decky verifies
    ``sha256hash`` at install time and aborts the whole plugin install on
    mismatch, so a drifted hash ships a zip that installs nothing.

    Only runs when the binaries are present in the checkout — they are
    downloaded by ``prebuild_binaries()`` and are not committed.
    """
    import hashlib

    bin_dir = _repo_file("bin")
    if bin_dir is None:
        pytest.skip("bin/ not found — run ./build-plugin.sh to populate binaries")

    # remote_binary names are flat filenames; map each to the file that
    # prebuild_binaries() actually writes for that architecture.
    layout = {
        "legendary": "legendary",
        "legendary_arm64": "legendary",
        "gogdl": "gogdl",
        "gogdl_arm64": "gogdl",
        "nile": "nile",
        "nile_arm64": "nile",
        "comet": "comet",
        "comet_arm64": "comet",
        "winetricks": "winetricks",
    }

    checked: list[str] = []
    skipped: list[str] = []
    drifted: dict[str, tuple[str, str]] = {}

    for name, entry in sorted(manifest.items()):
        tool = layout.get(name)
        if tool is None:
            skipped.append(f"{name} (no known on-disk layout)")
            continue
        subdir = "aarch64" if name.endswith("_arm64") else "x86_64"
        candidates = (
            [bin_dir / tool] if tool == "winetricks" else
            [bin_dir / subdir / tool, bin_dir / tool]
        )
        actual_file = next((c for c in candidates if c.is_file()), None)
        if actual_file is None:
            skipped.append(f"{name} (binary not downloaded)")
            continue
        digest = hashlib.sha256(actual_file.read_bytes()).hexdigest()
        if digest != entry["sha256hash"]:
            drifted[name] = (entry["sha256hash"], digest)
        else:
            checked.append(name)

    if not checked and not drifted:
        pytest.skip("no bundled binaries present to verify")

    assert not drifted, (
        "package.json remote_binary sha256hash does not match the binary the "
        "build bundles — Decky verifies this at install time and aborts the "
        "whole plugin install on mismatch:\n"
        + "\n".join(
            f"  {n}:\n    manifest: {a}\n    on disk:  {b}"
            for n, (a, b) in drifted.items()
        )
    )
