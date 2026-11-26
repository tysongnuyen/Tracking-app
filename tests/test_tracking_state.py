import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from datetime import datetime, timedelta
import tempfile
import unittest
from types import SimpleNamespace
from collector import Collector
from tracking_core import Store, Observation


class TrackingStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.sqlite3")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def collector(self):
        return Collector(self.store, SimpleNamespace())

    def test_fresh_install_waits_for_start_then_remembers_pause(self):
        collector = self.collector()
        self.assertTrue(collector.paused)
        self.assertFalse(collector.has_started)
        collector.set_paused(False)
        self.assertFalse(collector.paused)
        self.assertTrue(collector.has_started)
        generation = collector.generation
        collector.set_paused(False)
        self.assertEqual(collector.generation, generation)
        collector.set_paused(True)
        reopened = self.collector()
        self.assertTrue(reopened.paused)
        self.assertTrue(reopened.has_started)
        self.assertTrue(reopened.status()["has_started"])

    def test_old_active_install_remains_running_without_activity_yet(self):
        self.store.set_setting("paused", 0)
        collector = self.collector()
        self.assertFalse(collector.paused)
        self.assertTrue(collector.has_started)
        collector.set_paused(True)
        self.assertTrue(self.collector().has_started)

    def test_legacy_history_is_recognized_and_totals_survive_actions(self):
        now = datetime.now().astimezone()
        self.store.record(now, now+timedelta(seconds=10), [Observation("app", "code.exe", "screen")])
        collector = self.collector()
        self.assertFalse(collector.paused)
        self.assertTrue(collector.has_started)
        self.store.flush()
        collector.set_paused(True)
        collector.set_paused(False)
        row, = self.store.report(now.date().isoformat())
        self.assertEqual(row["screen"], 10)
        self.store.set_setting("paused", 1)
        self.assertTrue(self.collector().has_started)


if __name__ == "__main__":
    unittest.main()
