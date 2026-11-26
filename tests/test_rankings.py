import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import tempfile
import unittest

from dashboard_model import COLORS, OTHER, rank_apps
from tracking_core import Observation, Store


class RankingTests(unittest.TestCase):
    def test_report_uses_app_focus_without_counting_websites_again(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory(prefix="tracking-rankings-") as folder:
            store = Store(Path(folder) / "test.sqlite3")
            try:
                store.record(now, now + timedelta(seconds=60), [
                    Observation("app", "chrome.exe", "screen"),
                    Observation("web", "facebook.com", "screen", browser="chrome.exe"),
                    Observation("app", "excel.exe", "visible", True, True),
                ])
                store.record(now + timedelta(seconds=60), now + timedelta(seconds=90), [
                    Observation("app", "code.exe", "screen"),
                    Observation("app", "chrome.exe", "background", True, True),
                ])
                report = store.report(now.astimezone().date().isoformat())
                rows, total = rank_apps(report)
            finally:
                store.close()
        self.assertEqual(total, 90)
        self.assertEqual([row["name"] for row in rows], ["chrome.exe", "code.exe"])
        self.assertEqual([row["rank"] for row in rows], [1, 2])
        self.assertAlmostEqual(rows[0]["share"], 100 * 60 / 90)
        self.assertAlmostEqual(sum(row["share"] for row in rows), 100)
        self.assertEqual(rows[0]["background"], 30)

    def test_sorting_is_by_time_then_case_insensitive_executable_name(self):
        source = [dict(kind="app", name=name, screen=seconds) for name, seconds in [
            ("zebra.exe", 20), ("Bravo.exe", 20), ("alpha.exe", 20),
            ("leader.exe", 30), ("Alpha.exe", 20),
        ]]
        rows, total = rank_apps(source)
        self.assertEqual([row["name"] for row in rows],
                         ["leader.exe", "Alpha.exe", "alpha.exe", "Bravo.exe", "zebra.exe"])
        self.assertEqual([row["rank"] for row in rows], [1, 2, 3, 4, 5])
        self.assertEqual(rank_apps(list(reversed(source))), (rows, total))
        self.assertEqual([row["color"] for row in rows], list(COLORS[:5]))

    def test_empty_and_background_only_apps_produce_no_rankings(self):
        self.assertEqual(rank_apps([]), ([], 0))
        rows = [dict(kind="app", name="chrome.exe", screen=0, background=3600),
                dict(kind="web", name="youtube.com", screen=7200)]
        self.assertEqual(rank_apps(rows), ([], 0))

    def test_filtering_ranked_rows_preserves_daily_share_and_rank(self):
        rows, total = rank_apps([
            dict(kind="app", name="chrome.exe", screen=75),
            dict(kind="app", name="code.exe", screen=25),
        ])
        filtered = [row for row in rows if "code" in row["name"]]
        self.assertEqual(total, 100)
        self.assertEqual((filtered[0]["rank"], filtered[0]["share"]), (2, 25))

    def test_source_rows_are_unchanged_and_existing_fields_are_preserved(self):
        source = [dict(kind="app", name=f"app{index}.exe", screen=index + 1,
                       visible=80, idle=4, visibility_known=True)
                  for index in range(len(COLORS) + 2)]
        before = deepcopy(source)
        rows, _ = rank_apps(source)
        self.assertEqual(source, before)
        self.assertEqual(rows[-1]["color"], OTHER)
        self.assertEqual((rows[0]["visible"], rows[0]["idle"], rows[0]["visibility_known"]),
                         (80, 4, True))
        rows[0]["screen"] = 999
        self.assertEqual(source, before)


if __name__ == "__main__":
    unittest.main()
