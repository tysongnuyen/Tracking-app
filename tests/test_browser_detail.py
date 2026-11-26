import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from tracking_core import Store, Observation
from collector import observations_for
from dashboard_model import overview, browser_detail, chart_parts
from visibility import WindowGeometry as W

class BrowserDetailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.store = Store(self.path)
        self.now = datetime.now().astimezone()
        self.day = self.now.date().isoformat()
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def record(self, rows):
        self.store.record(self.now, self.now + timedelta(seconds=10), rows)
    def test_same_domain_in_two_browsers_remains_separate(self):
        self.record([Observation("app", "chrome.exe", "screen", True, True),
                     Observation("app", "msedge.exe", "visible", True, True),
                     Observation("web", "facebook.com", "screen", True, True, "chrome.exe"),
                     Observation("web", "facebook.com", "visible", True, True, "msedge.exe"),
                     Observation("web", "facebook.com", "background", True, True, "chrome.exe")])
        chrome, = self.store.browser_report(self.day, "chrome.exe")
        edge, = self.store.browser_report(self.day, "msedge.exe")
        self.assertEqual((chrome["screen"], chrome["visible"], chrome["background"]), (10, 10, 0))
        self.assertEqual((edge["screen"], edge["visible"], edge["background"]), (0, 10, 10))
        apps, total = overview(self.store.report(self.day))
        self.assertEqual(total, 10)
        self.assertEqual(sum(v for v, _ in chart_parts(apps)), 10)
        sites, subtotal = browser_detail(next(r for r in apps if r["name"] == "chrome.exe"), self.store.browser_report(self.day))
        self.assertEqual(subtotal, 10)
        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0]["name"], "facebook.com")
    def test_old_web_history_is_not_assigned_to_a_browser(self):
        self.record([Observation("app", "chrome.exe", "screen"), Observation("web", "facebook.com", "screen")])
        self.assertEqual(self.store.browser_report(self.day), [])
        apps, _ = overview(self.store.report(self.day))
        sites, total = browser_detail(apps[0], [])
        self.assertEqual(total, 10)
        self.assertEqual(sites[0]["kind"], "unknown")
    def test_browser_data_is_durable_and_retained_for_30_days(self):
        self.record([Observation("web", "facebook.com", "screen", browser="chrome.exe")])
        self.store.flush()
        other = Store(self.path)
        self.assertEqual(other.browser_report(self.day)[0]["screen"], 10)
        other.close()
        self.store.flush(today=self.now.date() + timedelta(days=31))
        self.assertEqual(self.store.browser_report(self.day), [])
    def test_browser_target_bound_and_midnight(self):
        self.record([Observation("web", f"site{i}.com", "background", browser="chrome.exe") for i in range(1010)])
        self.assertLessEqual(len(self.store.browser_report(self.day)), 1001)
        midnight = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.store.record(midnight-timedelta(seconds=1), midnight+timedelta(seconds=1),
                          [Observation("web", "example.com", "screen", browser="firefox.exe")])
        self.assertEqual(self.store.browser_report((midnight-timedelta(days=1)).date().isoformat(), "firefox.exe")[0]["screen"], 1)
    def test_uia_and_bridge_attach_the_actual_browser(self):
        sample = NS(apps={"chrome.exe", "msedge.exe"}, foreground="chrome.exe", hwnd=1,
                    unlocked=True, idle_seconds=0, visible={"chrome.exe", "msedge.exe"}, geometry_known=True,
                    shown={1:True,2:True}, windows=[W(1,1,"chrome.exe",(0,0,500,800)), W(2,2,"msedge.exe",(500,0,1000,800))])
        rows = observations_for(sample, 180, "facebook.com", {}, {1:"facebook.com",2:"youtube.com"})
        self.assertEqual({(r.browser, r.name) for r in rows if r.kind=="web"},
                         {("chrome.exe","facebook.com"),("msedge.exe","youtube.com")})
        snapshot = {"chrome.exe":dict(hosts={"github.com"},active="github.com",focused=True)}
        rows = observations_for(sample, 180, "outdated.com", snapshot)
        self.assertEqual([(r.browser,r.name) for r in rows if r.kind=="web"], [("chrome.exe","github.com")])
    def test_partial_domain_coverage_is_explicit(self):
        app = dict(kind="app",name="chrome.exe",screen=100)
        websites = [dict(kind="web",name="facebook.com",browser="chrome.exe",screen=60),
                    dict(kind="web",name="youtube.com",browser="msedge.exe",screen=20)]
        rows, total = browser_detail(app, websites)
        self.assertEqual(total, 100)
        self.assertEqual(sum(r["screen"] for r in rows), 100)
        self.assertEqual({r["name"] for r in rows}, {"facebook.com", "Chưa xác định website"})

if __name__ == "__main__":
    unittest.main()
