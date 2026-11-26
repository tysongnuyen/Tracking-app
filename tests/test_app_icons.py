import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ctypes as c
from ctypes import wintypes as w
import os
import unittest
from app_icons import extract_icon, resolve_executable


class WindowsIconTests(unittest.TestCase):
    def test_real_icon_is_usable_and_releases_gdi_objects(self):
        path = Path(os.environ["WINDIR"]) / "explorer.exe"
        icon = extract_icon(path)
        self.assertEqual(icon.mode, "RGBA")
        self.assertEqual(icon.size, (32, 32))
        self.assertIsNotNone(icon.getbbox())
        user, kernel = c.WinDLL("user32"), c.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = w.HANDLE
        user.GetGuiResources.argtypes = [w.HANDLE, w.DWORD]
        user.GetGuiResources.restype = w.DWORD
        process = kernel.GetCurrentProcess()
        before = user.GetGuiResources(process, 0)
        for _ in range(4):
            self.assertIsNotNone(extract_icon(path))
        self.assertLessEqual(user.GetGuiResources(process, 0), before+1)

    def test_missing_executable_has_no_fake_logo(self):
        self.assertIsNone(extract_icon(r"C:\missing-tracking-icon-test\missing.exe"))
        self.assertIsNone(resolve_executable("missing-tracking-icon-test.exe"))
        self.assertIsNone(resolve_executable("../explorer.exe"))


if __name__ == "__main__":
    unittest.main()
