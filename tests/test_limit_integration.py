import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import multiprocessing as mp
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from collector import Collector
from ui_host import DashboardHost, ui_worker, limit_notification_message


class LimitIntegrationTests(unittest.TestCase):
    def test_rules_rpc_round_trip_preserves_ids_path_and_switch(self):
        calls = []
        class FakeLimits:
            def rules(self):return [dict(id=7, path=r"C:\Apps\target.exe", minutes=180, enabled=True)]
            def save_rule(self, *args):calls.append(("save", args));return 7
            def set_enabled(self, *args):calls.append(("enabled", args))
            def delete_rule(self, *args):calls.append(("delete", args))
        collector = NS(limits=FakeLimits(), sampler=NS(excluded_pids={123}))
        host = DashboardHost(None, collector, None)
        parent, child = mp.Pipe()
        worker = threading.Thread(target=host._serve, args=(parent, 123))
        worker.start()
        try:
            for command, args in (("limits_rules", ()), ("limits_save", (r"C:\Apps\target.exe", 180, True)),
                                  ("limits_save", (r"C:\Apps\target.exe", 180, True, "Target App")),
                                  ("limits_enabled", (7, False)), ("limits_delete", (7,))):
                child.send((command, args))
                self.assertTrue(child.poll(3))
                ok, value = child.recv()
                self.assertTrue(ok, value)
                if command == "limits_rules":self.assertEqual(value[0]["id"], 7)
                if command == "limits_save":self.assertEqual(value, 7)
        finally:
            child.close();worker.join(3)
        self.assertEqual(calls, [("save", (r"C:\Apps\target.exe", 180, True)),
                                ("save", (r"C:\Apps\target.exe", 180, True, "Target App")),
                                ("enabled", (7, False)), ("delete", (7,))])
        self.assertFalse(worker.is_alive())

    def test_gui_limits_proxy_and_initial_page_use_same_service_contract(self):
        sent, pages = [], []
        connection = NS(send=sent.append, poll=lambda _: True, recv=lambda: (True, []), close=lambda: None)
        root = NS(withdraw=lambda: None, protocol=lambda *args: None, deiconify=lambda: None, mainloop=lambda: None)
        class FakeDesktop:
            def __init__(self, root, store, collector, bridge, limits=None):
                limits.rules();limits.save_rule(r"C:\Apps\a.exe", 180)
                limits.save_rule(r"C:\Apps\a.exe", 180, name="Friendly App")
                limits.set_enabled(5, False);limits.delete_rule(5)
            def show_page(self, page):pages.append(page)
            def close(self):pass
        with patch.dict(sys.modules, {"tkinter": NS(Tk=lambda: root), "desktop_ui": NS(Desktop=FakeDesktop)}):
            ui_worker(connection, dict(path="test.sqlite3", token="", bridge_error="", page="limits"))
        self.assertEqual(sent, [("limits_rules", ()), ("limits_save", (r"C:\Apps\a.exe", 180, True)),
                                ("limits_save", (r"C:\Apps\a.exe", 180, True, "Friendly App")),
                                ("limits_enabled", (5, False)), ("limits_delete", (5,))])
        self.assertEqual(pages, ["limits"])

    def test_limit_notifications_have_correct_threshold_and_preserve_whole_app_warning(self):
        for minutes in (15, 10, 5):
            message = limit_notification_message(dict(type="warning", name="code.exe", remaining_minutes=minutes))
            self.assertIn(f"còn {minutes} phút", message)
            self.assertIn("các tiến trình con", message)
        message = limit_notification_message(dict(type="error", name="example.exe", error="Access denied"))
        self.assertIn("Access denied", message)
        self.assertNotIn("đã đóng", message)
        message = limit_notification_message(dict(type="warning", name="🧭"*200+".exe", remaining_minutes=5))
        self.assertIn("còn 5 phút", message)
        self.assertLessEqual(len(message.encode("utf-16-le")), 510)

    def test_event_queue_is_bounded_and_fifo(self):
        collector = Collector.__new__(Collector)
        import queue
        collector.limit_events = queue.Queue(maxsize=128)
        for value in range(200):collector.queue_limit_event(dict(value=value))
        self.assertEqual(collector.limit_events.qsize(), 128)
        self.assertEqual(collector.pop_limit_event(), dict(value=72))
        for _ in range(127):collector.pop_limit_event()
        self.assertIsNone(collector.pop_limit_event())


if __name__ == "__main__":
    unittest.main()
