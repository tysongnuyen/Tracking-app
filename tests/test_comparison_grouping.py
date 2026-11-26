import copy
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from comparison_model import pie_parts
from dashboard_model import OTHER


def app(name, seconds, color="#82AFFF"):
    return dict(kind="app", name=name, screen=seconds, color=color,
                share=-1, rank=1, visible=seconds * 2)


class PieGroupingTests(unittest.TestCase):
    def test_strict_unrounded_boundary(self):
        rows = [app("below", 4.999), app("exact", 5), app("above", 5.001),
                app("main", 85)]
        parts = pie_parts(rows)
        self.assertEqual([p["name"] for p in parts], ["exact", "above", "main", "Khác"])
        self.assertEqual(parts[-1]["members"], ("below",))
        self.assertAlmostEqual(parts[0]["share"], 5)
        self.assertAlmostEqual(parts[1]["share"], 5.001)
        self.assertAlmostEqual(parts[-1]["share"], 4.999)

    def test_group_preserves_all_children_and_totals(self):
        rows = [app("main", 92), app("small one", 4, "#123456"),
                app("small two", 3), app("small three", 1)]
        parts = pie_parts(rows)
        other = parts[-1]
        self.assertEqual(other["name"], "Khác")
        self.assertEqual(other["kind"], "other")
        self.assertEqual(other["color"], OTHER)
        self.assertTrue(other["is_other"])
        self.assertEqual(other["screen"], 8)
        self.assertEqual(other["share"], 8)
        self.assertEqual(other["members"], ("small one", "small two", "small three"))
        self.assertEqual([row["share"] for row in other["children"]], [4, 3, 1])
        self.assertEqual(other["children"][0]["color"], "#123456")
        self.assertEqual(sum(part["screen"] for part in parts), 100)
        self.assertEqual(sum(part["share"] for part in parts), 100)
        self.assertFalse(parts[0]["is_other"])
        self.assertEqual(parts[0]["children"], ())
        self.assertEqual(parts[0]["members"], ("main",))
        self.assertEqual(parts[0]["visible"], 184)

    def test_all_apps_can_form_one_other_slice(self):
        parts = pie_parts([app(str(i), 1) for i in range(21)])
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["name"], "Khác")
        self.assertEqual(parts[0]["screen"], 21)
        self.assertAlmostEqual(parts[0]["share"], 100)
        self.assertEqual(len(parts[0]["children"]), 21)

    def test_empty_and_nonpositive_rows_are_excluded(self):
        self.assertEqual(pie_parts([]), [])
        self.assertEqual(pie_parts([app("zero", 0), app("negative", -1)]), [])
        parts = pie_parts([app("main", 1), app("zero", 0)])
        self.assertEqual([part["name"] for part in parts], ["main"])
        self.assertEqual(parts[0]["share"], 100)

    def test_single_app_without_other(self):
        result = pie_parts([app("main", 75)])
        self.assertEqual(len(result), 1)
        self.assertFalse(result[0]["is_other"])
        self.assertEqual(result[0]["share"], 100)

    def test_explicit_denominator_belongs_to_complete_period(self):
        parts = pie_parts([app("visible main", 10), app("visible tiny", 1)], total=100)
        self.assertEqual(parts[0]["share"], 10)
        self.assertEqual(parts[1]["name"], "Khác")
        self.assertEqual(parts[1]["share"], 1)
        self.assertEqual(parts[1]["children"][0]["share"], 1)
        self.assertEqual(sum(part["share"] for part in parts), 11)

    def test_zero_denominator_uses_actual_durations(self):
        self.assertEqual(pie_parts([app("main", 10)], total=0)[0]["share"], 100)

    def test_fractional_durations_do_not_disappear(self):
        rows = [app("main", 0.000099), app("tiny", 0.000001)]
        parts = pie_parts(rows)
        self.assertEqual(len(parts), 2)
        self.assertGreater(parts[-1]["screen"], 0)
        self.assertAlmostEqual(parts[-1]["share"], 1)
        self.assertAlmostEqual(math.fsum(p["screen"] for p in parts), 0.0001)

    def test_rows_and_nested_children_are_copies(self):
        rows = [app("main", 98), app("tiny", 2)]
        original = copy.deepcopy(rows)
        parts = pie_parts(rows)
        self.assertEqual(rows, original)
        parts[0]["name"] = "changed"
        parts[-1]["children"][0]["name"] = "also changed"
        self.assertEqual(rows, original)

    def test_thousand_apps_keep_all_members(self):
        rows = [app(f"app {i}", 1) for i in range(1000)]
        parts = pie_parts(rows)
        self.assertEqual(len(parts), 1)
        self.assertEqual(len(parts[0]["children"]), 1000)
        self.assertEqual(len(set(parts[0]["members"])), 1000)
        self.assertEqual(parts[0]["screen"], 1000)
        self.assertEqual(parts[0]["share"], 100)

    def test_exactly_twenty_five_percent_slices_remain_separate(self):
        parts = pie_parts([app(f"app {i}", 1) for i in range(20)])
        self.assertEqual(len(parts), 20)
        self.assertTrue(all(not p["is_other"] for p in parts))
        self.assertTrue(all(p["share"] == 5 for p in parts))


if __name__ == "__main__":
    unittest.main()
