import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import tkinter as tk
import time
import unittest

from limits_ui import LimitsView, limit_minutes


class FakeLimits:
    def __init__(self):
        self.data = []
        self.saved = []

    def rules(self):
        return [dict(row) for row in self.data]

    def save_rule(self, path, minutes, enabled=True, name=None):
        self.saved.append((path, minutes, enabled))
        row = next((row for row in self.data if row["path"] == path), None)
        if row is None:
            row = dict(id=len(self.data)+1, path=path, name=path.rsplit("\\", 1)[-1],
                       used=0, blocked=False, error="")
            self.data.append(row)
        row.update(minutes=minutes, enabled=enabled,
                   remaining=max(0, minutes*60-row["used"]))
        if name is not None:
            row["name"] = name

    def set_enabled(self, identifier, enabled):
        row = next(row for row in self.data if row["id"] == identifier)
        row["enabled"] = enabled
        row["blocked"] = enabled and row["remaining"] == 0

    def delete_rule(self, identifier):
        self.data[:] = [row for row in self.data if row["id"] != identifier]


class MinutesTests(unittest.TestCase):
    def test_minutes_validation_does_not_silently_round_or_accept_empty(self):
        self.assertEqual(limit_minutes(" 180 "), 180)
        self.assertEqual(limit_minutes("1440"), 1440)
        for value in ("", "0", "-1", "1.5", "1e2", "1441", "١٢"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                limit_minutes(value)


class LimitsUiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.geometry("960x650")
        self.service = FakeLimits()
        self.view = LimitsView(self.root, self.service, catalog_provider=lambda: [])
        self.view.pack(fill="both", expand=True)
        self.root.update_idletasks()

    def tearDown(self):
        self.view.close_dialog()
        self.root.destroy()

    def test_editor_rejects_bad_input_and_preserves_disabled_state_on_edit(self):
        self.view.open_dialog()
        self.view.minutes_var.set("1.5")
        self.view.path_var.set(r"C:\Apps\Example.exe")
        self.assertFalse(self.view.save_dialog())
        self.assertFalse(self.service.saved)
        self.assertIn("1 đến 1440", self.view.dialog_status.get())
        self.view.minutes_var.set("180")
        self.view.path_var.set("Example.exe")
        self.assertFalse(self.view.save_dialog())
        self.assertFalse(self.service.saved)
        self.view.path_var.set(r"C:\Apps\Example.exe")
        self.assertTrue(self.view.save_dialog())
        self.assertEqual(self.service.saved[-1], (r"C:\Apps\Example.exe", 180, True))
        self.view.toggle(self.view.rows[0])
        self.view.open_dialog(self.view.rows[0])
        self.view.minutes_var.set("90")
        self.assertTrue(self.view.save_dialog())
        self.assertEqual(self.service.saved[-1], (r"C:\Apps\Example.exe", 90, False))
        self.assertFalse(self.view.rows[0]["enabled"])

    def test_unblocking_uses_service_and_removing_never_leaves_a_rule_displayed(self):
        self.service.save_rule(r"C:\Apps\Example.exe", 1)
        self.service.data[0].update(used=60, remaining=0, blocked=True)
        self.view.refresh()
        self.root.update_idletasks()
        self.assertTrue(self.view.rows[0]["blocked"])
        self.view.toggle(self.view.rows[0])
        self.assertFalse(self.view.rows[0]["blocked"])
        self.assertFalse(self.view.rows[0]["enabled"])
        self.view.remove(self.view.rows[0])
        self.assertEqual(self.view.rows, [])
        self.assertEqual(self.service.data, [])

    def test_named_app_is_selected_from_searchable_catalog_without_file_picker(self):
        self.view.catalog_provider = lambda: [
            dict(name="Google Chrome", path=r"C:\Apps\chrome.exe"),
            dict(name="Visual Studio Code", path=r"C:\Apps\Code.exe"),
        ]
        self.view.open_dialog()
        deadline = time.monotonic()+3
        while self.view.picker.loading and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        picker = self.view.picker
        self.assertFalse(picker.loading)
        self.assertEqual(len(picker.apps), 2)
        self.assertFalse(hasattr(self.view, "browse_button"))
        self.assertFalse(self.view.save_dialog())
        self.assertIn("Chọn một ứng dụng", self.view.dialog_status.get())
        picker.query.set("visual studio")
        self.assertEqual([r["name"] for r in picker.visible_apps], ["Visual Studio Code"])
        picker.move(1)
        self.view.minutes_var.set("90")
        self.assertTrue(self.view.save_dialog())
        self.assertEqual(self.service.saved[-1], (r"C:\Apps\Code.exe", 90, True))
        self.assertEqual(self.view.rows[0]["name"], "Visual Studio Code")

    def test_large_rule_list_keeps_only_viewport_rows_and_reuses_them(self):
        for index in range(200):
            self.service.save_rule(fr"C:\Apps\App{index}.exe", 180)
        self.view.refresh()
        self.root.update_idletasks()
        self.assertLess(len(self.view.pool), 12)
        original_widgets = {str(widget) for widget, _ in self.view.pool}
        for index in (50, 100, 150):
            self.view.canvas.yview_moveto(index/200)
            self.root.update_idletasks()
            self.service.data[index]["used"] += 1
            self.view.refresh()
            self.root.update_idletasks()
        self.assertLess(len(self.view.pool), 12)
        self.assertTrue(original_widgets.issubset({str(widget) for widget, _ in self.view.pool}))


if __name__ == "__main__":
    unittest.main()
