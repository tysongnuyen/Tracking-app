import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from datetime import date, datetime, timedelta
import tempfile
import threading
import unittest
from types import SimpleNamespace as NS
import multiprocessing as mp
from unittest.mock import patch

from dashboard_model import rank_apps
from tracking_core import Observation, Store
from ui_host import DashboardHost, ui_worker


class WeeklyReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tracking-weekly-")
        self.store = Store(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, day, seconds, *observations, offset=0):
        start = datetime.combine(date.fromisoformat(day), datetime.min.time()).replace(hour=12).astimezone()
        start += timedelta(seconds=offset)
        self.store.record(start, start + timedelta(seconds=seconds), observations)

    def test_inclusive_week_crosses_month_with_one_database_query(self):
        for day, seconds in (("2026-01-28", 10), ("2026-01-29", 20),
                             ("2026-02-01", 30), ("2026-02-04", 40), ("2026-02-05", 50)):
            self.record(day, seconds, Observation("app", "chrome.exe", "screen"))
        self.store.flush(today=date(2026, 2, 5))
        queries = []
        self.store.db.set_trace_callback(queries.append)
        rows = self.store.report("2026-01-29", "2026-02-04")
        self.store.db.set_trace_callback(None)
        self.assertEqual([(row["name"], row["screen"]) for row in rows], [("chrome.exe", 90)])
        self.assertEqual(len([query for query in queries if query.lstrip().upper().startswith("SELECT")]), 1)

    def test_flushed_and_pending_counters_merge_once_by_source(self):
        self.record("2026-01-29", 10,
                    Observation("app", "chrome.exe", "screen", True, True),
                    Observation("web", "facebook.com", "screen", True, True, "chrome.exe"))
        self.store.flush(today=date(2026, 2, 4))
        self.record("2026-01-29", 5,
                    Observation("app", "chrome.exe", "idle", True, True),
                    Observation("web", "facebook.com", "idle", True, True, "chrome.exe"), offset=10)
        self.record("2026-02-04", 7,
                    Observation("app", "chrome.exe", "visible", True, True),
                    Observation("app", "code.exe", "screen"))
        self.record("2026-02-04", 11, Observation("app", "chrome.exe", "background", True, True), offset=7)
        self.record("2026-02-05", 13, Observation("app", "chrome.exe", "screen"))
        rows = self.store.report("2026-01-29", "2026-02-04")
        by_source = {(row["kind"], row["name"]): row for row in rows}
        chrome = by_source[("app", "chrome.exe")]
        self.assertEqual(chrome, dict(kind="app", name="chrome.exe", screen=15, background=18,
                                     idle=5, background_known=True, visible=22, covered=11, visibility_known=True))
        self.assertEqual(by_source[("web", "facebook.com")]["screen"], 15)
        self.assertFalse(by_source[("app", "code.exe")]["visibility_known"])
        ranked, total = rank_apps(rows)
        self.assertEqual(total, 22)
        self.assertEqual([row["name"] for row in ranked], ["chrome.exe", "code.exe"])
        self.assertAlmostEqual(ranked[0]["share"], 100 * 15 / 22)
        self.assertEqual(self.store.report("2026-01-29", "2026-02-04"), rows)
        self.store.flush(today=date(2026, 2, 5))
        self.assertEqual({(row["kind"], row["name"]): row
                          for row in self.store.report("2026-01-29", "2026-02-04")}, by_source)

    def test_day_only_matches_same_day_range_and_omitted_end(self):
        self.record("2026-02-01", 10, Observation("app", "chrome.exe", "screen", True, True))
        self.store.flush(today=date(2026, 2, 2))
        self.record("2026-02-01", 5, Observation("app", "chrome.exe", "screen"), offset=10)
        self.record("2026-02-02", 99, Observation("app", "code.exe", "screen"))
        daily = self.store.report("2026-02-01")
        self.assertEqual(daily, self.store.report("2026-02-01", "2026-02-01"))
        self.assertEqual(daily, self.store.report("2026-02-01", None))
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["screen"], 15)
        self.assertEqual(set(daily[0]), {"kind", "name", "screen", "background", "idle",
                                       "background_known", "visible", "covered", "visibility_known"})

    def test_year_boundary_and_empty_ranges(self):
        self.record("2025-12-31", 10, Observation("app", "chrome.exe", "screen"))
        self.store.flush(today=date(2026, 1, 1))
        self.record("2026-01-01", 20, Observation("app", "chrome.exe", "screen"))
        self.assertEqual(self.store.report("2025-12-29", "2026-01-04")[0]["screen"], 30)
        self.assertEqual(self.store.report("2025-12-29", "2025-12-30"), [])
        self.assertEqual(self.store.report("2026-01-02", "2026-01-08"), [])
        self.assertEqual(rank_apps(self.store.report("2026-01-02", "2026-01-08")), ([], 0))

    def test_invalid_or_reversed_dates_raise_value_error(self):
        for start, stop in (("2026-02-02", "2026-02-01"), ("bad", None),
                            ("2026-02-30", None), ("20260201", None),
                            ("2026-02-01", "bad"), ("2026-02-01", "20260202"),
                            (None, None), (date(2026, 2, 1), None)):
            with self.subTest(start=start, stop=stop), self.assertRaises(ValueError):
                self.store.report(start, stop)

    def test_dashboard_rpc_forwards_daily_and_range_arguments(self):
        calls = []
        class FakeStore:
            def report(self, *args):
                calls.append(args)
                return [dict(name="chrome.exe", screen=7 if len(args) == 2 else 1)]
        collector = NS(sampler=NS(excluded_pids={123}))
        host = DashboardHost(FakeStore(), collector, None)
        parent, child = mp.Pipe()
        worker = threading.Thread(target=host._serve, args=(parent, 123))
        worker.start()
        try:
            for args, expected in ((("2026-02-01",), 1), (("2026-01-29", "2026-02-04"), 7)):
                child.send(("report", args))
                self.assertTrue(child.poll(3))
                ok, rows = child.recv()
                self.assertTrue(ok)
                self.assertEqual(rows[0]["screen"], expected)
        finally:
            child.close()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(calls, [("2026-02-01",), ("2026-01-29", "2026-02-04")])
        self.assertNotIn(123, collector.sampler.excluded_pids)

    def test_gui_store_proxy_preserves_daily_call_shape_and_supports_range(self):
        sent = []
        connection = NS(send=sent.append, poll=lambda timeout: True,
                        recv=lambda: (True, []), close=lambda: None)
        root = NS(withdraw=lambda: None, protocol=lambda *args: None,
                  deiconify=lambda: None, mainloop=lambda: None)
        class FakeDesktop:
            def __init__(self, root, store, collector, bridge, limits=None):
                store.report("2026-02-01")
                store.report("2026-02-01", None)
                store.report("2026-01-29", "2026-02-04")
            def close(self):
                pass
        with patch.dict(sys.modules, {"tkinter": NS(Tk=lambda: root),
                                      "desktop_ui": NS(Desktop=FakeDesktop)}):
            ui_worker(connection, dict(path="test.sqlite3", token="", bridge_error=""))
        self.assertEqual(sent, [("report", ("2026-02-01",)),
                                ("report", ("2026-02-01",)),
                                ("report", ("2026-01-29", "2026-02-04"))])


if __name__ == "__main__":
    unittest.main()
