import copy
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import tkinter as tk
import unittest

from comparison_ui import ComparisonView, comparison_color, raster_pie
from dashboard_model import OTHER, display_name, duration, rank_apps
from ui_widgets import CARD


def ranked(count=3):
    return rank_apps([dict(kind="app", name=f"App{index:03}.exe", screen=count-index,
                           background=0, idle=0, visible=0, covered=0)
                      for index in range(count)])


class PiePartsTests(unittest.TestCase):
    def test_normal_app_lists_have_individual_colors(self):
        colors = [comparison_color(index) for index in range(40)]
        self.assertEqual(len(set(colors)), len(colors))

    def test_raster_circle_has_smooth_edges_and_correct_slice_orientation(self):
        slices = [dict(start=-90, extent=180, color="#FF0000"),
                  dict(start=90, extent=180, color="#00FF00")]
        image = raster_pie(slices, 200)
        self.assertEqual(image.getpixel((150, 100)), (255, 0, 0))
        self.assertEqual(image.getpixel((50, 100)), (0, 255, 0))
        plain_colors = {(255, 0, 0), (0, 255, 0), tuple(bytes.fromhex(CARD[1:]))}
        self.assertTrue(any(color not in plain_colors for _, color in image.getcolors(40000)))


class ComparisonUiTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.attributes("-alpha", 0)
        self.root.geometry("900x600")
        self.icon = tk.PhotoImage(master=self.root, width=32, height=32)
        self.requested_legend_icons = []

        def image_provider(row):
            if row.get("color_dot") is False:
                self.requested_legend_icons.append(row["name"])
            return self.icon

        self.view = ComparisonView(self.root, image_provider)
        self.view.pack(fill="both", expand=True)
        # Realize the layout without showing a window: a withdrawn root leaves
        # newly packed canvases at 1px and cannot verify visible child rows.
        self.root.deiconify()
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def show_pie(self, count=3):
        rows, total = ranked(count)
        return self.show_rows(rows, total)

    def show_rows(self, rows, total):
        self.view.set_data(rows, total, period=("2026-10-12", "2026-10-18"))
        self.view.set_mode("pie")
        self.root.update_idletasks()
        return rows, total

    def grouped_fixture(self):
        rows, total = rank_apps([
            dict(kind="app", name=name, screen=seconds)
            for name, seconds in (("Main.exe", 90), ("Boundary.exe", 5),
                                  ("SmallOne.exe", 3), ("SmallTwo.exe", 2))])
        return self.show_rows(rows, total)

    def click_other(self):
        index = next(i for i, row in enumerate(self.view.legend_rows)
                     if row["legend_kind"] == "other")
        y = index*self.view.LEGEND_ROW_HEIGHT+23-self.view.legend_canvas.canvasy(0)
        self.view.legend_clicked(SimpleNamespace(y=y))
        self.root.update_idletasks()

    def legend_texts(self):
        return [self.view.legend_canvas.itemcget(item, "text")
                for item in self.view.legend_canvas.find_withtag("rows")
                if self.view.legend_canvas.type(item) == "text"]

    def test_both_modes_use_same_period_total_and_data(self):
        rows, total = self.show_pie(20)
        self.assertEqual(self.view.pie_panel.winfo_manager(), "pack")
        self.assertEqual(self.view.table.winfo_manager(), "")
        self.assertEqual(len(self.view.pie_slices), 11)
        self.assertEqual(self.view.pie_slices[-1]["name"], "Khác")
        self.assertEqual(sum(len(part["members"]) for part in self.view.pie_rows), 20)
        self.assertEqual(len(self.view.pie_canvas.find_withtag("pie")), 1)
        self.assertEqual(len({row["color"] for row in self.view.ranked_rows}), 20)
        self.assertEqual(self.view.period, ("2026-10-12", "2026-10-18"))
        self.view.set_mode("ranking")
        self.root.update_idletasks()
        self.assertEqual(self.view.table.winfo_manager(), "pack")
        self.assertEqual(self.view.pie_panel.winfo_manager(), "")
        self.assertEqual(self.view.total, total)
        self.assertEqual([row["share"] for row in self.view.ranked_rows], [row["share"] for row in rows])

    def test_other_starts_collapsed_and_click_reveals_then_hides_children(self):
        self.grouped_fixture()
        self.assertFalse(self.view.other_expanded)
        self.assertEqual([row["name"] for row in self.view.legend_rows],
                         ["Main.exe", "Boundary.exe", "Khác"])
        group = self.view.legend_rows[-1]
        self.assertEqual(group["share"], 5)
        self.assertFalse(group["expanded"])
        self.click_other()
        self.assertTrue(self.view.other_expanded)
        self.assertEqual([row["name"] for row in self.view.legend_rows],
                         ["Main.exe", "Boundary.exe", "Khác", "SmallOne.exe", "SmallTwo.exe"])
        children = [row for row in self.view.legend_rows if row["legend_kind"] == "child"]
        self.assertEqual([row["share"] for row in children], [3, 2])
        self.assertTrue(all(row["color"] == OTHER for row in children))
        # Draw the children in the viewport, then inspect rendered text and logos.
        self.view.legend_canvas.yview_moveto(1)
        self.root.update_idletasks()
        texts = self.legend_texts()
        for child in children:
            self.assertIn(display_name(child["name"]), texts)
            self.assertIn(duration(child["screen"]), texts)
            self.assertIn(f"{child['share']:.1f}%", texts)
            self.assertIn(child["name"], self.requested_legend_icons)
        self.assertEqual(sum(self.view.legend_canvas.type(item) == "image"
                             for item in self.view.legend_canvas.find_withtag("rows")),
                         len(self.view.legend_images))
        self.view.legend_canvas.yview_moveto(0)
        self.root.update_idletasks()
        self.click_other()
        self.assertFalse(self.view.other_expanded)
        self.assertEqual(len(self.view.legend_rows), 3)
        self.assertFalse(any(row["legend_kind"] == "child" for row in self.view.legend_rows))

    def test_search_for_grouped_app_expands_matching_child_without_rescaling(self):
        rows, total = self.grouped_fixture()
        slices = copy.deepcopy(self.view.pie_slices)
        self.view.query.set("smalltwo")
        self.root.update_idletasks()
        self.assertEqual([row["name"] for row in self.view.legend_rows], ["Khác", "SmallTwo.exe"])
        self.assertTrue(self.view.legend_rows[0]["expanded"])
        self.assertTrue(self.view.other_expanded)
        self.assertEqual(self.view.legend_rows[0]["share"], 5)
        self.assertEqual(self.view.legend_rows[1]["share"], 2)
        self.assertEqual(self.view.total, total)
        self.assertEqual([part["extent"] for part in self.view.pie_slices],
                         [part["extent"] for part in slices])
        self.assertEqual([part["color"] for part in self.view.pie_slices],
                         ["#414141", "#414141", OTHER])
        self.assertIn("1/4 ứng dụng", self.view.summary.get())
        self.view.query.set("")
        self.root.update_idletasks()
        self.assertEqual(len(self.view.legend_rows), 5)

    def test_search_autoexpansion_can_be_collapsed_and_survives_icon_refresh(self):
        rows, total = self.grouped_fixture()
        self.view.query.set("smalltwo")
        self.root.update_idletasks()
        self.assertTrue(self.view.other_expanded)
        self.click_other()
        self.assertFalse(self.view.other_expanded)
        self.assertEqual([row["name"] for row in self.view.legend_rows], ["Khác"])
        self.assertIn("1/4 ứng dụng", self.view.summary.get())
        self.view.set_data(rows, total, icon_revision=1, period=self.view.period)
        self.root.update_idletasks()
        self.assertFalse(self.view.other_expanded)
        self.assertEqual([row["name"] for row in self.view.legend_rows], ["Khác"])
        self.click_other()
        self.assertEqual([row["name"] for row in self.view.legend_rows], ["Khác", "SmallTwo.exe"])

    def test_search_for_other_label_lists_all_grouped_children(self):
        self.grouped_fixture()
        self.view.query.set("khác")
        self.root.update_idletasks()
        self.assertEqual([row["name"] for row in self.view.legend_rows],
                         ["Khác", "SmallOne.exe", "SmallTwo.exe"])
        self.assertIn("2/4 ứng dụng", self.view.summary.get())

    def test_grouping_uses_raw_share_and_keeps_exactly_five_percent_individual(self):
        rows, total = rank_apps([dict(kind="app", name=name, screen=seconds)
                               for name, seconds in (("Main.exe", 85), ("Above.exe", 5.001),
                                                     ("Exact.exe", 5), ("Below.exe", 4.999))])
        self.show_rows(rows, total)
        self.assertEqual([row["name"] for row in self.view.pie_rows],
                         ["Main.exe", "Above.exe", "Exact.exe", "Khác"])
        other = self.view.pie_rows[-1]
        self.assertEqual(other["members"], ("Below.exe",))
        self.assertAlmostEqual(other["share"], 4.999)
        self.assertAlmostEqual(self.view.pie_rows[2]["share"], 5)

    def test_fractional_total_update_moves_app_across_five_percent_boundary(self):
        rows, total = rank_apps([dict(kind="app", name="Main.exe", screen=95),
                                dict(kind="app", name="Boundary.exe", screen=5)])
        self.show_rows(rows, total)
        self.assertEqual([part["name"] for part in self.view.pie_rows], ["Main.exe", "Boundary.exe"])
        updated, new_total = rank_apps([dict(kind="app", name="Main.exe", screen=95.001),
                                       dict(kind="app", name="Boundary.exe", screen=5)])
        # Rounded shares and integer durations are unchanged, but membership is not.
        self.assertEqual([round(row["share"], 2) for row in rows],
                         [round(row["share"], 2) for row in updated])
        self.view.set_data(updated, new_total, period=self.view.period)
        self.root.update_idletasks()
        self.assertEqual([part["name"] for part in self.view.pie_rows], ["Main.exe", "Khác"])
        self.assertEqual(self.view.pie_rows[-1]["members"], ("Boundary.exe",))
        self.assertLess(self.view.pie_rows[-1]["share"], 5)

    def test_grouping_search_and_expansion_do_not_mutate_ranking_input(self):
        rows, total = self.grouped_fixture()
        original = copy.deepcopy(rows)
        ranked_before = copy.deepcopy(self.view.ranked_rows)
        self.click_other()
        self.view.query.set("small")
        self.root.update_idletasks()
        self.view.set_mode("ranking")
        self.root.update_idletasks()
        self.assertEqual(rows, original)
        self.assertEqual(self.view.ranked_rows, ranked_before)
        self.assertEqual([row["name"] for row in self.view.visible_rows],
                         ["SmallOne.exe", "SmallTwo.exe"])
        self.assertEqual([row["share"] for row in self.view.visible_rows], [3, 2])
        self.assertEqual(self.view.total, total)
        self.assertNotIn("Khác", [row["name"] for row in self.view.ranked_rows])

    def test_search_filters_legend_without_changing_pie_denominator(self):
        rows, total = self.show_pie(3)
        self.view.query.set("app001")
        self.root.update_idletasks()
        self.assertEqual(len(self.view.visible_rows), 1)
        self.assertEqual(self.view.visible_rows[0]["share"], rows[1]["share"])
        self.assertEqual(self.view.total, total)
        slices = self.view.pie_slices
        self.assertEqual(len(slices), 3)
        colors = [part["color"] for part in slices]
        self.assertEqual(colors.count("#414141"), 2)
        self.assertIn(self.view.visible_rows[0]["color"], colors)
        extents = [part["extent"] for part in slices]
        self.assertAlmostEqual(sum(extents), 360)
        self.view.query.set("missing app")
        self.root.update_idletasks()
        self.assertEqual(self.view.visible_rows, [])
        self.assertEqual(len(self.view.pie_slices), 3)

    def test_single_app_is_a_full_visible_circle_and_empty_state_is_clear(self):
        self.show_pie(1)
        slices = self.view.pie_slices
        self.assertEqual(len(slices), 1)
        self.assertEqual(slices[0]["extent"], 360)
        self.assertEqual(len(self.view.pie_canvas.find_withtag("pie")), 1)
        self.view.set_data([], 0)
        self.root.update_idletasks()
        self.assertEqual(self.view.pie_slices, [])
        self.assertEqual(self.view.pie_canvas.find_withtag("pie"), ())
        self.assertIn("Chưa có dữ liệu", self.view.pie_canvas.itemcget(self.view.pie_canvas.find_all()[0], "text"))

    def test_many_apps_keep_drawing_and_legend_icons_bounded_when_scrolling(self):
        self.show_pie(1000)
        self.assertEqual(len(self.view.pie_slices), 1)
        self.assertEqual(len(self.view.legend_rows), 1)
        self.assertEqual(self.view.legend_rows[0]["name"], "Khác")
        self.assertEqual(self.view.legend_rows[0]["share"], 100)
        self.assertEqual(len(self.view.legend_rows[0]["children"]), 1000)
        self.assertEqual(len(self.view.pie_canvas.find_withtag("pie")), 1)
        self.click_other()
        self.assertEqual(len(self.view.legend_rows), 1001)
        self.assertEqual(sum(row["legend_kind"] == "child" for row in self.view.legend_rows), 1000)
        self.assertLess(len(self.view.legend_images), 15)
        self.assertLess(len(self.view.legend_canvas.find_withtag("rows")), 100)
        self.requested_legend_icons.clear()
        self.view.legend_canvas.yview_moveto(1)
        self.root.update_idletasks()
        self.assertLess(len(self.view.legend_images), 15)
        self.assertLess(len(self.view.legend_canvas.find_withtag("rows")), 100)
        self.assertIn("App999.exe", self.requested_legend_icons)
        self.assertGreater(self.view.legend_canvas.canvasy(0), 0)
        self.assertEqual(len(self.view.visible_rows), 1000)
        self.assertEqual(len(self.view.pie_slices), 1)


if __name__ == "__main__":
    unittest.main()
