import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import multiprocessing as mp
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from ui_host import DashboardHost, ui_worker, weekly_notification_message


class ReportNavigationTests(unittest.TestCase):
    def test_notification_reopens_comparison_once_without_restarting_dashboard(self):
        collector = NS(sampler=NS(excluded_pids={123}), status=lambda: dict(paused=False))
        host = DashboardHost(None, collector, None)
        host.process = NS(is_alive=lambda: True)
        host.open("comparison")
        parent, child = mp.Pipe()
        worker = threading.Thread(target=host._serve, args=(parent, 123))
        worker.start()
        try:
            child.send(("status", ()))
            self.assertTrue(child.poll(3))
            ok, state = child.recv()
            self.assertTrue(ok and state["show_requested"])
            self.assertEqual(state["requested_page"], "comparison")
            child.send(("status", ()))
            self.assertTrue(child.poll(3))
            ok, state = child.recv()
            self.assertTrue(ok)
            self.assertFalse(state["show_requested"])
            self.assertIsNone(state["requested_page"])
        finally:
            child.close()
            worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_notification_page_is_applied_before_new_dashboard_is_shown(self):
        calls = []
        root = NS(withdraw=lambda: None, protocol=lambda *args: None,
                  deiconify=lambda: calls.append("show"), mainloop=lambda: None)
        connection = NS(close=lambda: None)
        class FakeDesktop:
            def __init__(self, *args, **kwargs):
                pass
            def show_page(self, page):
                calls.append(page)
            def close(self):
                pass
        with patch.dict(sys.modules, {"tkinter": NS(Tk=lambda: root),
                                      "desktop_ui": NS(Desktop=FakeDesktop)}):
            ui_worker(connection, dict(path="test.sqlite3", token="", bridge_error="", page="comparison"))
        self.assertEqual(calls, ["comparison", "show"])

    def test_summary_shows_interval_total_and_three_top_apps(self):
        payload = dict(start_day="2026-10-12", end_day="2026-10-18", total_seconds=100,
                       apps=[dict(rank=i, name=name, screen=seconds, share=seconds)
                             for i, (name, seconds) in enumerate([
                                 ("chrome.exe", 40), ("code.exe", 30),
                                 ("excel.exe", 20), ("ignored.exe", 10)], 1)])
        message = weekly_notification_message(payload)
        self.assertIn("12/10 – 18/10", message)
        self.assertIn("Tổng 1 phút 40 giây", message)
        self.assertIn("Google Chrome: 40 giây (40%)", message)
        self.assertIn("Microsoft Excel", message)
        self.assertNotIn("ignored", message)

    def test_windows_utf16_limit_and_empty_report(self):
        payload = dict(start_day="2026-10-12", end_day="2026-10-18", total_seconds=7200,
                       apps=[dict(rank=i, name="🧭" * 40 + ".exe", screen=2400, share=100/3)
                             for i in range(1, 4)])
        message = weekly_notification_message(payload)
        self.assertLessEqual(len(message.encode("utf-16-le")), 510)
        self.assertTrue(message.endswith("…"))
        payload["apps"] = []
        payload["total_seconds"] = 0
        self.assertIn("Chưa có dữ liệu", weekly_notification_message(payload))


if __name__ == "__main__":
    unittest.main()
