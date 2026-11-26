import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from datetime import date, datetime, timedelta, timezone
import tempfile
import unittest

from tracking_core import Observation, Store
from weekly_report import WeeklyReporter, due_week, trailing_week


class FakeStore:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.settings = {}
        self.setting_reads = 0
        self.report_calls = []
        self.write_calls = []
        self.fail_write = False

    def setting(self, key):
        self.setting_reads += 1
        return self.settings.get(key)

    def set_setting(self, key, value):
        self.write_calls.append((key, value))
        if self.fail_write:
            raise OSError("fixture metadata write failed")
        self.settings[key] = value

    def report(self, start_day, end_day):
        self.report_calls.append((start_day, end_day))
        return self.rows


class WeeklyScheduleTests(unittest.TestCase):
    def test_trailing_week_includes_current_day_across_month_and_year(self):
        self.assertEqual(trailing_week(date(2026, 10, 18)), ("2026-10-12", "2026-10-18"))
        self.assertEqual(trailing_week(date(2026, 3, 2)), ("2026-02-24", "2026-03-02"))
        self.assertEqual(trailing_week(date(2026, 1, 2)), ("2025-12-27", "2026-01-02"))

    def test_due_only_at_or_after_configured_hour_on_monday(self):
        self.assertIsNone(due_week(datetime(2026, 10, 19, 7, 59, 59), None, 8))
        self.assertEqual(due_week(datetime(2026, 10, 19, 8), None, 8), "2026-10-19")
        self.assertEqual(due_week(datetime(2026, 10, 19, 22, 30), None, 8), "2026-10-19")
        self.assertIsNone(due_week(datetime(2026, 10, 18, 22), None, 8))
        self.assertIsNone(due_week(datetime(2026, 10, 20, 8), None, 8))
        self.assertIsNone(due_week(datetime(2026, 10, 19, 9), "2026-10-19", 8))
        self.assertEqual(due_week(datetime(2026, 10, 26, 8), "2026-10-19", 8), "2026-10-26")

    def test_schedule_uses_supplied_local_clock_without_utc_conversion(self):
        local = datetime(2026, 10, 19, 0, tzinfo=timezone(timedelta(hours=7)))
        self.assertEqual(local.astimezone(timezone.utc).weekday(), 6)
        self.assertEqual(due_week(local, None, 0), "2026-10-19")

    def test_invalid_hours_fail_predictably(self):
        for hour in (-1, 24, 8.5, "8", None, True):
            with self.subTest(hour=hour), self.assertRaises(ValueError):
                due_week(datetime(2026, 10, 19, 9), None, hour)
            with self.subTest(constructor_hour=hour), self.assertRaises(ValueError):
                WeeklyReporter(FakeStore(), hour)

    def test_payload_ranks_apps_without_website_double_counting_and_sends_once(self):
        store = FakeStore([dict(kind="app", name="chrome.exe", screen=60),
                           dict(kind="app", name="code.exe", screen=40),
                           dict(kind="web", name="facebook.com", screen=60),
                           dict(kind="app", name="background.exe", screen=0, background=3600)])
        reporter = WeeklyReporter(store, 8)
        sent = []
        self.assertIsNone(reporter.poll(datetime(2026, 10, 19, 7), sent.append))
        self.assertFalse(store.report_calls)
        payload = reporter.poll(datetime(2026, 10, 19, 8), sent.append)
        self.assertEqual(sent, [payload])
        self.assertEqual(set(payload), {"week", "start_day", "end_day", "total_seconds", "apps"})
        self.assertEqual((payload["week"], payload["start_day"], payload["end_day"]),
                         ("2026-10-19", "2026-10-13", "2026-10-19"))
        self.assertEqual(payload["total_seconds"], 100)
        self.assertEqual([(app["name"], app["rank"], app["share"]) for app in payload["apps"]],
                         [("chrome.exe", 1, 60), ("code.exe", 2, 40)])
        for minute in range(60):
            self.assertIsNone(reporter.poll(datetime(2026, 10, 19, 9, minute), sent.append))
        self.assertEqual(store.report_calls, [("2026-10-13", "2026-10-19")])
        self.assertEqual(store.setting_reads, 1)
        self.assertEqual(len(store.write_calls), 1)

    def test_failed_notification_is_not_marked_and_can_retry(self):
        store = FakeStore()
        reporter = WeeklyReporter(store, 8)
        now = datetime(2026, 10, 19, 9)
        def fail(payload):
            raise RuntimeError("fixture notification unavailable")
        with self.assertRaises(RuntimeError):
            reporter.poll(now, fail)
        self.assertIsNone(reporter.last_sent_week)
        self.assertFalse(store.write_calls)
        sent = []
        result = reporter.poll(now, sent.append)
        self.assertEqual((result["apps"], result["total_seconds"]), ([], 0))
        self.assertEqual(len(sent), 1)
        self.assertEqual(store.settings[WeeklyReporter.LAST_SENT_KEY], "2026-10-19")

    def test_failed_metadata_write_is_retryable(self):
        store = FakeStore()
        store.fail_write = True
        reporter = WeeklyReporter(store, 8)
        sent = []
        with self.assertRaises(OSError):
            reporter.poll(datetime(2026, 10, 19, 9), sent.append)
        self.assertIsNone(reporter.last_sent_week)
        self.assertFalse(store.settings)
        store.fail_write = False
        reporter.poll(datetime(2026, 10, 19, 10), sent.append)
        self.assertEqual(len(sent), 2)
        self.assertEqual(reporter.last_sent_week, "2026-10-19")

    def test_persisted_delivery_survives_store_restart(self):
        with tempfile.TemporaryDirectory(prefix="tracking-weekly-schedule-") as folder:
            path = Path(folder) / "test.sqlite3"
            store = Store(path)
            sent = []
            try:
                start = datetime(2026, 10, 18, 12).astimezone()
                store.record(start, start + timedelta(seconds=10), [Observation("app", "chrome.exe", "screen")])
                reporter = WeeklyReporter(store, 8)
                payload = reporter.poll(datetime(2026, 10, 19, 18), sent.append)
                self.assertEqual(payload["total_seconds"], 10)
            finally:
                store.close()
            reopened = Store(path)
            try:
                reporter = WeeklyReporter(reopened, 8)
                self.assertIsNone(reporter.poll(datetime(2026, 10, 19, 19), sent.append))
                self.assertIsNone(reporter.poll(datetime(2026, 10, 20, 9), sent.append))
                self.assertIsNotNone(reporter.poll(datetime(2026, 10, 26, 8), sent.append))
                self.assertEqual(len(sent), 2)
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
