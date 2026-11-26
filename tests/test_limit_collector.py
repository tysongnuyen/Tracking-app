import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from datetime import datetime, timedelta, timezone
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from collector import Collector
from tracking_core import Store
from windows_api import Sample


class LimitCollectorTests(unittest.TestCase):
    def run_collector(self, pause_after_first=False):
        with tempfile.TemporaryDirectory(prefix="tracking-collector-limit-") as directory:
            store = Store(Path(directory) / "test.sqlite3")
            try:
                collector = Collector(store, NS(current=lambda: {}))
                collector.set_paused(False)
                samples = []
                def sample():
                    samples.append(True)
                    return Sample({"child.exe"}, "child.exe", 55, 90002, 0, True)
                collector.sampler = NS(sample=sample, close=lambda: None,
                                       events=NS(title_revision=0))
                collector.probe = NS(poll_many=lambda *args: {}, status="", process=None,
                                     close=lambda: None)
                calls = []
                collector.limits = NS(tick=lambda start, end, pid, count_usage: calls.append((pid, count_usage, (end-start).total_seconds())))
                class Stop:
                    loops = 0
                    def is_set(self):return self.loops >= 3
                    def wait(self, seconds):
                        self.loops += 1
                        if pause_after_first and self.loops == 1:collector.set_paused(True)
                collector.stop_event = Stop()
                now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
                with patch("collector.time.monotonic", side_effect=[0, 1, 2, 3]), \
                     patch("collector.datetime") as clock:
                    clock.now.side_effect = [now + timedelta(seconds=n) for n in range(4)]
                    collector._run()
                return calls, len(samples)
            finally:
                store.close()

    def test_whole_app_limiter_receives_previous_foreground_child_once_per_interval(self):
        calls, samples = self.run_collector()
        self.assertEqual(calls, [(None, True, 1), (90002, True, 1), (90002, True, 1)])
        self.assertEqual(samples, 3)

    def test_pause_stops_accounting_and_sampling_but_keeps_enforcement_ticks(self):
        calls, samples = self.run_collector(pause_after_first=True)
        self.assertEqual(calls, [(None, True, 1), (90002, False, 1), (None, False, 1)])
        self.assertEqual(samples, 1)


if __name__ == "__main__":
    unittest.main()
