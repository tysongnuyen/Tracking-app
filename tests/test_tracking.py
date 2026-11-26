import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from datetime import datetime, timedelta, timezone, date
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch
from tracking_core import Store, Observation, export_csv
from browser_reader import hostname, read_address
from collector import observations_for

class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tracking-unit-")
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.store = Store(self.path)
        self.now = datetime.now(timezone.utc)
        self.day = self.now.astimezone().date().isoformat()
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def record(self, items, seconds=10):
        self.store.record(self.now, self.now + timedelta(seconds=seconds), items)
    def test_screen_idle_subset(self):
        self.record([Observation("app", "excel.exe", "idle", True)])
        row, = self.store.report(self.day)
        self.assertEqual((row["screen"],row["idle"],row["background"]), (10,10,0))
    def test_dedup_foreground_and_background(self):
        self.record([Observation("web","youtube.com","background",True),Observation("web","youtube.com","screen")])
        row, = self.store.report(self.day)
        self.assertEqual(row["screen"],10)
        self.assertEqual(row["background"],0)
        self.assertTrue(row["background_known"])
    def test_ten_minute_rollover(self):
        start=self.now.replace(minute=9,second=50,microsecond=0)
        self.store.record(start,start+timedelta(seconds=20),[Observation("app","code.exe","screen",True)])
        self.assertEqual(self.store.completed_windows,1)
        self.assertEqual(list(self.store.current_window.values()),[10])
        self.assertEqual(self.store.report(start.astimezone().date().isoformat())[0]["screen"],20)
    def test_local_midnight(self):
        midnight=datetime.combine(date.today(),datetime.min.time()).astimezone()
        self.store.record(midnight-timedelta(seconds=1),midnight+timedelta(seconds=1),[Observation("app","code.exe","screen")])
        self.assertEqual(self.store.report(midnight.date().isoformat())[0]["screen"],1)
        self.assertEqual(self.store.report((midnight.date()-timedelta(days=1)).isoformat())[0]["screen"],1)
    def test_durable_restart(self):
        self.record([Observation("app","code.exe","screen")])
        self.store.flush()
        other=Store(self.path)
        self.assertEqual(other.report(self.day)[0]["screen"],10)
        other.close()
    def test_retention(self):
        self.record([Observation("app","code.exe","screen")])
        self.store.flush(today=date.today()+timedelta(days=31))
        self.assertEqual(self.store.report(self.day),[])
    def test_cardinality_bound(self):
        self.record([Observation("web",f"site{i}.com","background",True) for i in range(1010)])
        self.assertLessEqual(len(self.store.report(self.day)),1002)
        self.assertLessEqual(len(self.store.current_window),1002)
    def test_reports_use_source_names_without_categories(self):
        self.record([Observation("web","youtube.com","screen")])
        self.assertNotIn("category", self.store.report(self.day)[0])
        self.store.set_category("web","youtube.com","Học tập")
        self.assertNotIn("category", self.store.report(self.day)[0])
        self.assertNotIn("Mục đích", export_csv(self.day, self.store.report(self.day)))
    def test_csv_unknown_and_formula(self):
        self.record([Observation("web","youtube.com","screen"),Observation("app","=unsafe.exe","screen",True)])
        result=export_csv(self.day,self.store.report(self.day))
        self.assertIn("chưa đo",result)
        self.assertIn("'=unsafe.exe",result)

class CollectorTests(unittest.TestCase):
    def sample(self, foreground="chrome.exe",unlocked=True,idle=0):
        return NS(apps={"chrome.exe","excel.exe"},foreground=foreground,unlocked=unlocked,idle_seconds=idle)
    def test_apps_work_without_browser(self):
        rows=observations_for(self.sample("excel.exe"),180,None,{})
        self.assertEqual({(r.name,r.mode) for r in rows},{("chrome.exe","background"),("excel.exe","screen")})
    def test_idle_and_locked(self):
        rows=observations_for(self.sample(idle=300),180,"youtube.com",{})
        self.assertTrue(all(r.mode=="idle" for r in rows if r.name in {"chrome.exe","youtube.com"}))
        self.assertEqual(observations_for(self.sample(unlocked=False),180,"youtube.com",{}),[])
    def test_uia_has_no_invented_background(self):
        rows=observations_for(self.sample(),180,"youtube.com",{})
        site,=[r for r in rows if r.kind=="web"]
        self.assertEqual(site.mode,"screen")
        self.assertFalse(site.background_known)
    def test_bridge_supersedes_uia(self):
        snap={"chrome.exe":dict(hosts={"youtube.com","facebook.com"},active="facebook.com",focused=True)}
        rows=observations_for(self.sample(),180,"outdated.com",snap)
        self.assertNotIn("outdated.com",{r.name for r in rows})
        self.assertIn(("youtube.com","unknown"),{(r.name,r.mode) for r in rows})
        self.assertIn(("facebook.com","screen"),{(r.name,r.mode) for r in rows})

class BrowserTests(unittest.TestCase):
    def test_hostnames(self):
        for value in ("https://www.youtube.com/watch?v=secret#frag","youtube.com/watch?v=secret"):
            self.assertEqual(hostname(value),"youtube.com")
        self.assertEqual(hostname("https://docs.google.com/spreadsheets/private"),"docs.google.com")
        for value in ("chrome://settings","about:blank","file:///private.txt","private search words","http://localhost/","https://user:secret@example.com/"):
            self.assertIsNone(hostname(value))
    def fake_api(self, focused=False, in_document=False, offscreen=False):
        sdk=NS(UIA_NamePropertyId=1,UIA_AutomationIdPropertyId=2,UIA_ControlTypePropertyId=3,UIA_EditControlTypeId=50004,UIA_DocumentControlTypeId=50030,TreeScope_Descendants=4,UIA_ValuePatternId=10002,IUIAutomationValuePattern=object)
        root=NS(CurrentControlType=50032)
        doc=NS(CurrentControlType=50030,parent=root)
        pattern=NS(QueryInterface=lambda interface:NS(CurrentValue="https://www.youtube.com/watch?v=secret"))
        edit=NS(CurrentControlType=50004,CurrentIsPassword=False,CurrentIsOffscreen=offscreen,CurrentHasKeyboardFocus=focused,parent=doc if in_document else root,GetCurrentPattern=lambda kind:pattern)
        root.FindAll=lambda *args:NS(Length=1,GetElement=lambda index:edit)
        ui=NS(ElementFromHandle=lambda hwnd:root,CreatePropertyCondition=lambda *args:args,CreateOrCondition=lambda *args:args,CreateAndCondition=lambda *args:args,CompareElements=lambda a,b:a is b,ControlViewWalker=NS(GetParentElement=lambda e:e.parent))
        return sdk,ui
    def test_reads_known_address_field(self):
        with patch("browser_reader.ui_automation",return_value=self.fake_api()):
            self.assertEqual(read_address(123)[0],"youtube.com")
    def test_does_not_read_page_inputs(self):
        with patch("browser_reader.ui_automation",return_value=self.fake_api(in_document=True)):
            self.assertIsNone(read_address(123)[0])
    def test_does_not_attribute_typed_or_hidden_address(self):
        for kwargs in ({"focused":True},{"offscreen":True}):
            with patch("browser_reader.ui_automation",return_value=self.fake_api(**kwargs)):
                self.assertIsNone(read_address(123)[0])

if __name__=="__main__":
    unittest.main()
