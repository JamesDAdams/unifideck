from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from unifideck.auth.edge_browser import detection, env as env_mod, installer


class TestEdgeArmSupport(unittest.TestCase):
    def test_is_arm_true_for_arm_architectures(self):
        for arch in ("aarch64", "arm64", "ARM64", "armv7l", "armv8l"):
            with patch("platform.machine", return_value=arch):
                self.assertTrue(detection.is_arm())

    def test_is_arm_false_for_x86_architectures(self):
        for arch in ("x86_64", "amd64", "i386", "i686", "AMD64"):
            with patch("platform.machine", return_value=arch):
                self.assertFalse(detection.is_arm())

    def test_get_flatpak_apps_prioritizes_by_arch(self):
        with patch("platform.machine", return_value="aarch64"):
            apps = detection.get_flatpak_apps()
            self.assertEqual(apps[0], "org.chromium.Chromium")
        with patch("platform.machine", return_value="x86_64"):
            apps = detection.get_flatpak_apps()
            self.assertEqual(apps[0], "com.microsoft.Edge")
            self.assertIn("org.chromium.Chromium", apps)
            self.assertIn("com.google.Chrome", apps)

    def test_get_native_bins_prioritizes_by_arch(self):
        with patch("platform.machine", return_value="arm64"):
            bins = detection.get_native_bins()
            self.assertEqual(bins[0], "chromium")
        with patch("platform.machine", return_value="x86_64"):
            bins = detection.get_native_bins()
            self.assertEqual(bins[0], "microsoft-edge")
            self.assertIn("chromium", bins)
            self.assertIn("google-chrome", bins)

    def test_get_target_flatpak_app_by_arch(self):
        with patch("platform.machine", return_value="aarch64"):
            self.assertEqual(
                detection.get_target_flatpak_app(), "org.chromium.Chromium",
            )
        with patch("platform.machine", return_value="x86_64"):
            self.assertEqual(
                detection.get_target_flatpak_app(), "com.microsoft.Edge",
            )

    def test_find_edge_cmd_finds_chromium_flatpak_filesystem(self):
        with patch.object(Path, "is_dir", autospec=True) as mock_isdir:
            def fake_isdir(path_obj):
                return "org.chromium.Chromium" in str(path_obj)
            mock_isdir.side_effect = fake_isdir

            cmd = detection.find_edge_cmd(lambda: {})
            self.assertEqual(cmd, ["flatpak", "run", "org.chromium.Chromium"])

    def test_find_edge_cmd_finds_chrome_flatpak_filesystem(self):
        with patch.object(Path, "is_dir", autospec=True) as mock_isdir:
            def fake_isdir(path_obj):
                return "com.google.Chrome" in str(path_obj)
            mock_isdir.side_effect = fake_isdir

            cmd = detection.find_edge_cmd(lambda: {})
            self.assertEqual(cmd, ["flatpak", "run", "com.google.Chrome"])

    def test_find_edge_cmd_on_arm_native_fallback(self):
        with patch.object(Path, "is_dir", return_value=False), \
             patch("shutil.which") as mock_which:
            mock_which.side_effect = lambda bin_name, path=None: (
                "/usr/bin/chromium" if bin_name == "chromium" else None
            )
            cmd = detection.find_edge_cmd(lambda: {})
            self.assertEqual(cmd, ["/usr/bin/chromium"])

    def test_clean_env_injects_flatpak_paths(self):
        cleaned = env_mod.clean_env()
        self.assertIn("XDG_DATA_DIRS", cleaned)
        self.assertIn("flatpak", cleaned["XDG_DATA_DIRS"])
        self.assertIn("PATH", cleaned)
        self.assertIn("/usr/bin", cleaned["PATH"])

    def test_installer_installs_chromium_on_arm(self):
        inst = installer.EdgeInstaller(clean_env_fn=lambda: {})
        with patch("platform.machine", return_value="aarch64"), \
             patch("shutil.which", return_value="/usr/bin/flatpak"), \
             patch("unifideck.auth.edge_browser.installer.is_edge_installed", return_value=False), \
             patch.object(inst, "_ensure_user_flathub_remote", new_callable=AsyncMock, return_value=True), \
             patch.object(inst, "_get_default_browser", return_value=None), \
             patch.object(inst, "_run_flatpak_install", new_callable=AsyncMock) as mock_run_install, \
             patch.object(inst, "_wait_for_edge_ready", new_callable=AsyncMock), \
             patch.object(inst, "ensure_controller_permissions") as mock_perms:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_run_install.return_value = mock_proc

            import asyncio
            result = asyncio.run(inst.install())

            self.assertTrue(result["success"])
            mock_run_install.assert_awaited_once_with("org.chromium.Chromium")
            mock_perms.assert_called_once_with("org.chromium.Chromium")

    def test_ensure_controller_permissions_uses_target_app(self):
        inst = installer.EdgeInstaller(clean_env_fn=lambda: {})
        with patch("platform.machine", return_value="aarch64"), \
             patch("shutil.which", return_value="/usr/bin/flatpak"), \
             patch("subprocess.run") as mock_sub:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_sub.return_value = mock_proc

            success = inst.ensure_controller_permissions()
            self.assertTrue(success)
            mock_sub.assert_called_once_with(
                [
                    "flatpak", "--user", "override",
                    "--filesystem=/run/udev:ro",
                    "org.chromium.Chromium",
                ],
                capture_output=True,
                timeout=30,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
