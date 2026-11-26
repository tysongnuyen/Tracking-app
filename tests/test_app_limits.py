import ntpath
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from app_limits import AppLimits
from tracking_core import Observation, Store


APP = r"C:\Tools\Sample\sample.exe"


class FakeBlocker:
    def __init__(self):
        self.families = {ntpath.normcase(APP): {10, 11, 12}}
        self.scans = []
        self.terminations = []
        self.denied = set()
        self.scan_errors = []
        self.result = dict(killed=[10, 11, 12], alive=[], denied=[], protected=[], errors=[])
        self.scan_exception = None
        self.terminate_hook = None

    def validate_path(self, path):
        path = ntpath.normcase(str(path))
        if not path.endswith(".exe") or not ntpath.isabs(path):
            raise ValueError("Invalid executable")
        return path

    def scan(self, paths):
        self.scans.append(list(paths))
        if self.scan_exception:
            raise self.scan_exception
        return {path: dict(pids=self.families.get(path, set()), identities={},
                           denied=self.denied, errors=self.scan_errors) for path in paths}

    def terminate(self, path):
        self.terminations.append(path)
        if self.terminate_hook:
            self.terminate_hook()
        return self.result


class AppLimitTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="tracking-app-limits-")
        self.path = Path(self.folder.name) / "test.sqlite3"
        self.store = Store(self.path)
        self.blocker = FakeBlocker()
        self.events = []
        self.now = datetime(2026, 10, 18, 12)
        self.limits = AppLimits(self.store, self.blocker, self.events.append, now=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def add(self, minutes=20, enabled=True):
        return self.limits.save_rule(APP, minutes, enabled)

    def seed(self, seconds, day=None):
        with self.store.lock, self.store.db:
            self.store.db.execute("""INSERT INTO app_limit_daily(day,path,used)
                VALUES(?,?,?) ON CONFLICT(day,path) DO UPDATE SET used=excluded.used""",
                (day or self.now.date().isoformat(), ntpath.normcase(APP), seconds))

    def tick(self, seconds=1, pid=11, count=True):
        start = self.now
        self.now += timedelta(seconds=seconds)
        return self.limits.tick(start, self.now, pid, count_usage=count)

    def test_whole_family_focus_counts_once_and_background_does_not_count(self):
        self.add()
        self.tick(pid=12)
        self.tick(pid=10)
        self.tick(pid=99)
        self.tick(pid=None)
        self.assertEqual(self.limits.rules()[0]["used"], 2)
        self.assertEqual(len(self.blocker.scans), 4)
        self.assertFalse(self.blocker.terminations)

    def test_duplicate_and_overlapping_intervals_do_not_recharge_quota(self):
        self.add()
        start = self.now
        self.limits.tick(start, start + timedelta(seconds=2), 11)
        self.limits.tick(start, start + timedelta(seconds=2), 11)
        self.limits.tick(start + timedelta(seconds=1), start + timedelta(seconds=3), 11)
        self.assertEqual(self.limits.rules()[0]["used"], 3)

    def test_suspend_or_negative_intervals_are_ignored(self):
        self.add()
        self.tick(seconds=300)
        self.limits.tick(self.now, self.now - timedelta(seconds=1), 11)
        self.assertEqual(self.limits.rules()[0]["used"], 0)

    def test_pause_stops_counting_but_exhausted_quota_still_closes_reopened_app(self):
        self.add(minutes=1)
        self.seed(59)
        self.tick(count=False)
        self.assertFalse(self.blocker.terminations)
        self.tick()
        self.assertEqual(len(self.blocker.terminations), 1)
        self.tick(count=False)
        self.assertEqual(len(self.blocker.terminations), 2)
        self.assertEqual([event["type"] for event in self.events], ["blocked"])

    def test_disabling_and_deleting_stop_scans_and_termination(self):
        rid = self.add(minutes=1)
        self.seed(60)
        self.tick(count=False)
        scans, kills = len(self.blocker.scans), len(self.blocker.terminations)
        self.limits.set_enabled(rid, False)
        self.tick()
        self.assertFalse(self.limits.rules()[0]["blocked"])
        self.assertEqual((len(self.blocker.scans), len(self.blocker.terminations)), (scans, kills))
        self.limits.set_enabled(rid, True)
        self.tick(count=False)
        self.assertEqual(len(self.blocker.terminations), kills + 1)
        self.limits.delete_rule(rid)
        self.tick()
        self.assertEqual(self.limits.rules(), [])
        self.assertEqual(len(self.blocker.terminations), kills + 1)

    def test_rule_edits_and_readding_do_not_reset_daily_usage(self):
        rid = self.add()
        self.tick(seconds=2)
        self.assertEqual(self.limits.save_rule(APP.upper(), 30), rid)
        self.assertEqual(self.limits.rules()[0]["used"], 2)
        self.limits.delete_rule(rid)
        self.add()
        self.assertEqual(self.limits.rules()[0]["used"], 2)

    def test_catalog_display_name_is_preserved_on_limit_edits_and_in_notifications(self):
        rid = self.limits.save_rule(APP, 20, name="Visual Studio Code")
        self.assertEqual(self.limits.rules()[0]["name"], "Visual Studio Code")
        self.assertEqual(self.limits.save_rule(APP, 30, False), rid)
        self.assertEqual(self.limits.rules()[0]["name"], "Visual Studio Code")
        self.limits.save_rule(APP, 20, True)
        self.seed(299)
        self.tick()
        self.assertEqual(self.events[-1]["name"], "Visual Studio Code")

    def test_display_names_are_bounded_and_empty_names_fall_back_to_executable(self):
        self.limits.save_rule(APP, 20, name="  Sample\n App\x00  ")
        self.assertEqual(self.limits.rules()[0]["name"], "Sample App")
        self.limits.save_rule(APP, 20, name="Ứ" * 300)
        self.assertEqual(self.limits.rules()[0]["name"], "Ứ" * 160)
        self.limits.save_rule(APP, 20, name="\x00\n  ")
        self.assertEqual(self.limits.rules()[0]["name"], "sample.exe")
        with self.assertRaises(ValueError):
            self.limits.save_rule(APP, 20, name=123)

    def test_disabled_family_stays_discoverable_while_another_rule_is_active(self):
        rid = self.add(minutes=1)
        other = r"C:\Other\second.exe"
        other_path = ntpath.normcase(other)
        self.limits.save_rule(other, 30)
        self.seed(60)
        self.limits.set_enabled(rid, False)
        self.tick(pid=11)
        self.assertEqual(set(self.blocker.scans[-1]), {ntpath.normcase(APP), other_path})
        self.assertFalse(self.blocker.terminations)
        self.assertEqual(next(row for row in self.limits.rules() if row["id"] == rid)["used"], 60)
        self.limits.set_enabled(rid, True)
        self.tick(count=False)
        self.assertEqual(self.blocker.terminations, [ntpath.normcase(APP)])
        self.limits.delete_rule(rid)
        self.tick(count=False)
        self.assertEqual(self.blocker.scans[-1], [other_path])

    def test_exact_path_identity_avoids_same_basename_charge(self):
        self.add()
        other = r"C:\Other\sample.exe"
        self.limits.save_rule(other, 30)
        self.blocker.families[ntpath.normcase(other)] = {50}
        self.tick(pid=50)
        rows = {row["path"]: row for row in self.limits.rules()}
        self.assertEqual(rows[ntpath.normcase(APP)]["used"], 0)
        self.assertEqual(rows[ntpath.normcase(other)]["used"], 1)

    def test_no_unreliable_seed_from_old_basename_only_history(self):
        self.store.record(self.now - timedelta(seconds=50), self.now,
                          [Observation("app", "sample.exe", "screen")])
        self.add()
        self.assertEqual(self.limits.rules()[0]["used"], 0)

    def test_three_warning_thresholds_once_each_and_persist_across_restart(self):
        self.add(minutes=20)
        for used, expected in [(299, 15), (599, 10), (899, 5)]:
            self.seed(used)
            self.tick()
            self.tick(pid=None)
            self.limits = AppLimits(self.store, self.blocker, self.events.append, now=lambda: self.now)
            self.tick(pid=None)
            self.assertEqual(self.events[-1]["remaining_minutes"], expected)
        self.assertEqual([event["remaining_minutes"] for event in self.events], [15, 10, 5])

    def test_limit_at_warning_threshold_does_not_warn_immediately(self):
        self.add(minutes=15)
        self.tick()
        self.assertFalse(self.events)
        self.seed(299)
        self.tick()
        self.assertEqual(self.events[-1]["remaining_minutes"], 10)

    def test_limit_edit_into_multiple_warning_zones_sends_only_most_urgent(self):
        self.add(minutes=180)
        self.seed(176 * 60)
        self.tick(pid=None)
        self.tick(pid=None)
        self.assertEqual([event["remaining_minutes"] for event in self.events], [5])
        mask = self.store.db.execute("SELECT warning_mask FROM app_limit_daily").fetchone()[0]
        self.assertEqual(mask, 7)

    def test_midnight_splits_usage_and_resets_enforcement_and_notifications(self):
        self.now = datetime(2026, 10, 18, 23, 59, 59)
        self.add(minutes=20)
        self.seed(1200)
        self.tick(seconds=2)
        self.assertEqual(self.limits.rules()[0]["used"], 1)
        self.assertFalse(self.limits.rules()[0]["blocked"])
        self.assertFalse(self.blocker.terminations)
        yesterday_used = self.store.db.execute("SELECT used FROM app_limit_daily WHERE day='2026-10-18'").fetchone()[0]
        self.assertEqual(yesterday_used, 1201)
        self.seed(299)
        self.tick()
        self.assertEqual(self.events[-1]["remaining_minutes"], 15)

    def test_usage_is_committed_before_termination_and_survives_new_store(self):
        self.add(minutes=1)
        self.seed(59)

        def check_committed():
            db = sqlite3.connect(self.path)
            try:
                self.assertEqual(db.execute("SELECT used FROM app_limit_daily").fetchone()[0], 60)
            finally:
                db.close()

        self.blocker.terminate_hook = check_committed
        self.tick()
        self.store.close()
        self.store = Store(self.path)
        self.limits = AppLimits(self.store, self.blocker, self.events.append, now=lambda: self.now)
        self.tick(count=False)
        self.assertTrue(self.limits.rules()[0]["blocked"])
        self.assertEqual([event["type"] for event in self.events], ["blocked"])

    def test_errors_are_reported_once_and_clear_after_success(self):
        self.add(minutes=1)
        self.seed(60)
        self.blocker.result = dict(alive=[10], denied=[10], protected=[], killed=[], errors=[])
        self.tick(count=False)
        self.tick(count=False)
        self.assertTrue(self.limits.rules()[0]["error"])
        self.assertEqual([event["type"] for event in self.events].count("error"), 1)
        self.blocker.result = dict(alive=[], denied=[], protected=[], killed=[], errors=[])
        self.tick(count=False)
        self.assertEqual(self.limits.rules()[0]["error"], "")

    def test_discovery_errors_are_visible_and_not_spammed(self):
        self.add()
        self.blocker.scan_errors = ["Discovery failed"]
        self.tick()
        self.tick()
        self.assertEqual(self.limits.rules()[0]["error"], "Discovery failed")
        self.assertEqual(len(self.events), 1)
        self.blocker.scan_errors = []
        self.blocker.scan_exception = RuntimeError("Cannot enumerate")
        self.tick()
        self.tick()
        self.assertIn("Cannot enumerate", self.limits.rules()[0]["error"])
        self.assertEqual(len(self.events), 2)

    def test_termination_discovery_failure_is_not_reported_as_success(self):
        self.add(minutes=1)
        self.seed(60)
        self.blocker.result = dict(alive=[], denied=[], protected=[], errors=["Scan unavailable"])
        self.tick(count=False)
        self.assertEqual(self.limits.rules()[0]["error"], "Scan unavailable")

    def test_callback_failure_does_not_stop_enforcement(self):
        self.add(minutes=1)
        self.seed(60)
        self.limits.notify = lambda event: 1 / 0
        self.tick(count=False)
        self.tick(count=False)
        self.assertEqual(len(self.blocker.terminations), 2)

    def test_validation_rule_limit_and_missing_ids(self):
        for minutes in [0, -1, 1441, 1.5, True, "20", float("nan"), float("inf")]:
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                self.limits.save_rule(APP, minutes)
        with self.assertRaises(ValueError):
            self.limits.save_rule("bad.txt", 20)
        with self.assertRaises(ValueError):
            self.limits.set_enabled(1234, False)
        with self.assertRaises(ValueError):
            self.limits.delete_rule(1234)
        for index in range(100):
            self.limits.save_rule(rf"C:\Tools\app{index}.exe", 1)
        with self.assertRaises(ValueError):
            self.add()
        self.limits.save_rule(r"C:\Tools\app0.exe", 2)

    def test_old_daily_usage_is_bounded(self):
        self.add()
        self.seed(5, "2026-08-01")
        self.tick(pid=None)
        self.assertFalse(self.store.db.execute("SELECT 1 FROM app_limit_daily WHERE day='2026-08-01'").fetchone())


if __name__ == "__main__":
    unittest.main()
