import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app_catalog import (_ShortcutReader, _com_method, _executable_name,
                         _shortcut_files, _shortcut_target, discover_apps)
from process_blocker import canonical_path


class AppCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def exe(self, name):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"MZ")
        return str(target)

    def test_merges_exact_paths_prefers_friendly_installed_name_and_sorts(self):
        chrome = self.exe("chrome.exe")
        editor = self.exe("editor.exe")
        apps = discover_apps(
            running_provider=lambda: [{"path": chrome, "name": "chrome"},
                                       {"path": editor, "name": "Editor"}],
            shortcut_provider=lambda: [{"path": chrome, "name": "Google Chrome"}])
        self.assertEqual([row["name"] for row in apps], ["Editor", "Google Chrome"])
        self.assertEqual(apps[1]["path"], canonical_path(chrome))

    def test_shared_name_does_not_merge_different_executable_paths(self):
        first, second = self.exe("one/app.exe"), self.exe("two/app.exe")
        apps = discover_apps(running_provider=lambda: [], shortcut_provider=lambda: [
            {"path": first, "name": "App"}, {"path": second, "name": "App"}])
        self.assertEqual(len(apps), 2)
        self.assertNotEqual(apps[0]["path"], apps[1]["path"])

    def test_invalid_missing_and_protected_entries_are_omitted(self):
        system = self.exe("svchost.exe")
        nonexe = self.exe("document.txt")
        good = self.exe("good.exe")
        entries = [{"path": system}, {"path": nonexe}, {"path": sys.executable},
                   {"path": str(self.root / "missing.exe")}, {"path": good}, {},
                   {"path": r"\\server\share\app.exe", "name": "Remote"}]
        apps = discover_apps(running_provider=lambda: entries, shortcut_provider=lambda: [])
        self.assertEqual(apps, [{"path": canonical_path(good), "name": "good"}])

    def test_source_failure_preserves_other_source(self):
        good = self.exe("good.exe")

        def broken():
            raise OSError("A disappearing process")

        apps = discover_apps(running_provider=broken,
                             shortcut_provider=lambda: [{"path": good, "name": "Good"}])
        self.assertEqual([row["name"] for row in apps], ["Good"])

    def test_cleans_blank_and_control_names(self):
        first, second = self.exe("first.exe"), self.exe("second.exe")
        apps = discover_apps(running_provider=lambda: [], shortcut_provider=lambda: [
            {"path": first, "name": "  Browser\0  \n App "},
            {"path": second, "name": None}])
        self.assertEqual([row["name"] for row in apps], ["Browser App", "second"])

    def test_shortcut_walk_only_scans_start_menu_links(self):
        nested = self.root / "Apps"
        nested.mkdir()
        (self.root / "ignore.txt").write_text("")
        link = nested / "An app.lnk"
        link.write_bytes(b"")
        self.assertEqual(list(_shortcut_files([self.root])), [link])

    def test_does_not_treat_shared_launchers_as_named_apps(self):
        for host in ("cmd.exe", "python.exe", "explorer.exe", "Update.exe"):
            with self.subTest(host=host):
                self.assertIsNone(_shortcut_target(self.exe(host), "", ""))
        self.assertIsNone(_shortcut_target(r"\\server\share\App.exe", "", ""))

    def test_squirrel_shortcut_resolves_real_executable_only_from_matching_icon(self):
        launcher = self.exe("Update.exe")
        app = self.exe("app-1.0/Discord.exe")
        self.assertEqual(_shortcut_target(launcher, '--processStart "Discord.exe"', app),
                         Path(app))
        self.assertIsNone(_shortcut_target(launcher, "--processStart Slack.exe", app))

    def test_uninstall_shortcut_is_not_an_app_choice(self):
        app = self.exe("app.exe")
        self.assertIsNone(_shortcut_target(app, "--uninstall", ""))
        self.assertIsNone(_shortcut_target(self.exe("unins000.exe"), "", ""))
        self.assertIsNone(_shortcut_target(self.exe("uninstall.exe"), "", ""))
        self.assertEqual(_shortcut_target(app, "--profile-directory=Default", ""), Path(app))

    def test_falls_back_to_executable_stem_without_version_resource(self):
        self.assertEqual(_executable_name(self.exe("simple.exe")), "simple")

    @unittest.skipUnless(os.name == "nt", "Native Windows ShellLink")
    def test_native_unicode_shell_link_load_reads_target_without_launching(self):
        target = self.exe("Ứng dụng/app.exe")
        shortcut = self.root / "Ứng dụng.lnk"
        with _ShortcutReader() as writer:
            result = _com_method(writer.link, 20, c.c_long, w.LPCWSTR)(writer.link, target)
            self.assertGreaterEqual(result, 0)
            result = _com_method(writer.link, 11, c.c_long, w.LPCWSTR)(
                writer.link, "--profile-directory=Default")
            self.assertGreaterEqual(result, 0)
            result = _com_method(writer.persist, 6, c.c_long, w.LPCWSTR, w.BOOL)(
                writer.persist, str(shortcut), True)
            self.assertGreaterEqual(result, 0)
        with _ShortcutReader() as reader:
            value = reader.read(shortcut)
            self.assertEqual(canonical_path(value[0]), canonical_path(target))
            self.assertEqual(value[1], "--profile-directory=Default")
            broken = self.root / "broken.lnk"
            broken.write_bytes(b"invalid")
            self.assertIsNone(reader.read(broken))


if __name__ == "__main__":
    unittest.main()
