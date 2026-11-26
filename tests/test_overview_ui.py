import copy
import sys
from pathlib import Path
from types import SimpleNamespace
import tkinter as tk
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from overview_ui import UsageTable
from dashboard_model import duration


class UsageTableTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.attributes("-alpha", 0)
        self.root.geometry("460x360")
        self.table = UsageTable(self.root)
        self.table.pack(fill="both", expand=True)
        self.icon = tk.PhotoImage(master=self.root, width=36, height=24)
        self.requested = []
        self.scroll_calls = []
        self.table.configure(yscrollcommand=lambda *values: self.scroll_calls.append(values))
        self.root.deiconify()
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def provider(self, row):
        self.requested.append(row["name"])
        return self.icon

    def show(self, count=1000):
        rows = [dict(kind="app", name=f"App{index:04}.exe", screen=1000-index,
                     color="#82AFFF") for index in range(count)]
        self.table.set_data(rows, 1000, self.provider)
        self.root.update_idletasks()
        return rows

    def test_large_list_only_requests_icons_and_draws_cards_for_viewport(self):
        self.show()
        visible_bound = int(self.table._viewport_height()/self.table.ROW_PITCH)+2
        self.assertEqual(len(self.table.get_children()), 1000)
        self.assertLessEqual(len(self.requested), visible_bound)
        self.assertLessEqual(len(self.table.row_images), visible_bound)
        self.assertEqual(len(self.table.find_withtag("rowchrome")), len(self.table.row_images))
        self.assertLess(len(self.table.find_all()), 60)
        self.requested.clear()
        self.table.yview_moveto(1)
        self.root.update_idletasks()
        self.assertLessEqual(len(self.requested), visible_bound)
        self.assertIn("App0999.exe", self.requested)
        self.assertNotIn("App0000.exe", self.requested)
        self.assertAlmostEqual(self.table.yview()[1], 1.)
        self.assertLessEqual(len(self.table._row_card_cache), 8)

    def test_refresh_preserves_selection_scroll_and_input_data(self):
        rows = self.show()
        original = copy.deepcopy(rows)
        self.table.selection_set("app:App0500.exe")
        self.table.yview_moveto(.5)
        previous = self.table.yview()
        self.table.set_data(rows, 1000, self.provider)
        self.root.update_idletasks()
        self.assertEqual(self.table.selection(), ("app:App0500.exe",))
        self.assertEqual(self.table.yview(), previous)
        self.assertEqual(rows, original)
        self.table.set_data([], 0, self.provider)
        self.root.update_idletasks()
        self.assertEqual(self.table.selection(), ())
        self.assertEqual(self.table.yview(), (0., 1.))
        self.assertEqual(self.table.row_images, [])
        self.assertEqual(self.table.get_children(), ())
        self.assertEqual(self.scroll_calls[-1], (0., 1.))

    def test_hit_testing_ignores_header_and_card_gap_after_scroll(self):
        self.show(20)
        self.assertEqual(self.table.identify_row(10), "")
        self.assertEqual(self.table.identify_row(self.table.HEADER_HEIGHT+20), "app:App0000.exe")
        self.assertEqual(self.table.identify_row(self.table.HEADER_HEIGHT+self.table.ROW_HEIGHT+2), "")
        self.table.yview_scroll(2, "units")
        self.root.update_idletasks()
        self.assertEqual(self.table.identify_row(self.table.HEADER_HEIGHT+20), "app:App0002.exe")
        self.assertTrue(all(self.table.coords(item)[1] < self.table.HEADER_HEIGHT
                            for item in self.table.find_withtag("header")
                            if self.table.type(item) == "text"))

    def test_keyboard_and_mouse_selection_keep_browser_drilldown_contract(self):
        self.show(20)
        self.table._key(SimpleNamespace(keysym="End"))
        self.root.update_idletasks()
        self.assertEqual(self.table.selection(), ("app:App0019.exe",))
        self.assertAlmostEqual(self.table.yview()[1], 1.)
        self.table._key(SimpleNamespace(keysym="Home"))
        self.table._key(SimpleNamespace(keysym="Down"))
        self.assertEqual(self.table.selection(), ("app:App0001.exe",))
        self.table.set_data([dict(kind="app", name="chrome.exe", screen=30,
                                 color="#82AFFF")], 30, self.provider)
        self.root.update_idletasks()
        self.table._pressed(SimpleNamespace(y=self.table.HEADER_HEIGHT+20))
        self.assertEqual(self.table.selection(), ("app:chrome.exe",))
        self.assertEqual(self.table.item("app:chrome.exe", "values"),
                         ("Google Chrome", duration(30), "100.0%", "›"))

    def test_long_domain_at_minimum_width_keeps_name_clear_of_numeric_columns(self):
        self.root.geometry("400x360")
        self.root.update()
        domain = "a-very-long-name-with-many-components.example.com"
        self.table.set_data([dict(kind="web", name=domain, screen=36000,
                                 color="#82AFFF")], 36000, self.provider)
        self.root.update_idletasks()
        texts = [item for item in self.table.find_withtag("rows")
                 if self.table.type(item) == "text"]
        name, elapsed, share = texts
        self.assertTrue(self.table.itemcget(name, "text").endswith("…"))
        self.assertLess(self.table.bbox(name)[2], self.table.bbox(elapsed)[0])
        self.assertLess(self.table.bbox(elapsed)[2], self.table.bbox(share)[0])
        self.assertIn("Website", [self.table.itemcget(item, "text")
                                 for item in self.table.find_withtag("header")
                                 if self.table.type(item) == "text"])

    def test_mixed_hour_and_minute_labels_remain_complete_and_aligned(self):
        rows = [dict(kind="app", name="chrome.exe", screen=5280, color="#82AFFF"),
                dict(kind="app", name="code.exe", screen=300, color="#B59AFF")]
        self.table.set_data(rows, 5580, self.provider)
        self.root.update_idletasks()
        text_items = [item for item in self.table.find_withtag("rows")
                      if self.table.type(item) == "text"]
        elapsed = [item for item in text_items if self.table.itemcget(item, "text")
                   in {"1 giờ 28 phút", "5 phút 00 giây"}]
        self.assertEqual(len(elapsed), 2)
        self.assertEqual(self.table.coords(elapsed[0])[0], self.table.coords(elapsed[1])[0])
        for item in elapsed:
            _, center_y = self.table.coords(item)
            row_items = [other for other in text_items if other != item
                         and abs(self.table.coords(other)[1]-center_y) < 2]
            name = next(other for other in row_items
                        if self.table.itemcget(other, "anchor") == "w")
            share = next(other for other in row_items
                         if self.table.itemcget(other, "text").endswith("%"))
            self.assertLess(self.table.bbox(name)[2], self.table.bbox(item)[0])
            self.assertLess(self.table.bbox(item)[2], self.table.bbox(share)[0])


if __name__ == "__main__":
    unittest.main()
