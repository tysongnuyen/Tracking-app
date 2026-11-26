import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace as NS
from visibility import WindowGeometry as W,visible_windows
from tracking_core import Store,Observation
from collector import observations_for,match_window,uia_candidates

class GeometryTests(unittest.TestCase):
    def test_side_by_side(self):
        result=visible_windows([W(1,1,"a",(0,0,500,800)),W(2,2,"b",(500,0,1000,800))],[(0,0,1000,800)])
        self.assertEqual(result,{1:True,2:True})
    def test_complete_and_partial_cover(self):
        result=visible_windows([W(1,1,"a",(0,0,500,800)),W(2,2,"b",(0,0,500,800)),W(3,3,"c",(0,0,1000,800))],[(0,0,1000,800)])
        self.assertEqual(result,{1:True,2:False,3:True})
    def test_union_of_occluders(self):
        result=visible_windows([W(1,1,"a",(0,0,500,800)),W(2,2,"b",(500,0,1000,800)),W(3,3,"c",(0,0,1000,800))],[(0,0,1000,800)])
        self.assertFalse(result[3])
    def test_minimized_and_offscreen(self):
        result=visible_windows([W(1,1,"a",(0,0,500,800),True),W(2,2,"b",(5000,0,6000,800))],[(0,0,1000,800)])
        self.assertFalse(any(result.values()))
    def test_two_monitors(self):
        result=visible_windows([W(1,1,"a",(-1000,0,0,800)),W(2,2,"b",(0,0,1000,800))],[(-1000,0,0,800),(0,0,1000,800)])
        self.assertTrue(all(result.values()))
    def test_tiny_exposed_border(self):
        result=visible_windows([W(1,1,"a",(0,0,999,800)),W(2,2,"b",(0,0,1000,800))],[(0,0,1000,800)])
        self.assertFalse(result[2])
    def test_transparent_overlay_and_excluded_app(self):
        self.assertTrue(visible_windows([W(1,1,None,(0,0,1000,800),occludes=False),W(2,2,"b",(0,0,1000,800))],[(0,0,1000,800)])[2])
        self.assertFalse(visible_windows([W(1,1,None,(0,0,1000,800)),W(2,2,"b",(0,0,1000,800))],[(0,0,1000,800)])[2])

class StateTests(unittest.TestCase):
    def sample(self):
        return NS(apps={"chrome.exe","excel.exe"},foreground="chrome.exe",hwnd=1,pid=1,unlocked=True,idle_seconds=0,
            visible={"chrome.exe","excel.exe"},geometry_known=True,shown={1:True,2:True},
            windows=[W(1,1,"chrome.exe",(0,0,500,800)),W(2,2,"excel.exe",(500,0,1000,800))])
    def test_passive_visible_not_background(self):
        rows=observations_for(self.sample(),180,"youtube.com",{})
        self.assertIn(("excel.exe","visible"),{(r.name,r.mode) for r in rows})
    def test_internal_browser_split_both_visible(self):
        entry=dict(hosts={"youtube.com","github.com"},visible={"youtube.com","github.com"},focused=True,minimized=False,complete=True,bounds=[0,0,500,800])
        snap={"chrome.exe":dict(hosts=entry["hosts"],active="github.com",focused=True,windows=[entry])}
        rows=observations_for(self.sample(),180,None,snap)
        self.assertIn(("youtube.com","visible"),{(r.name,r.mode) for r in rows})
        self.assertIn(("github.com","screen"),{(r.name,r.mode) for r in rows})
    def test_hidden_tab_does_count_as_background(self):
        entry=dict(hosts={"youtube.com","github.com"},visible={"github.com"},focused=True,minimized=False,complete=True,bounds=None)
        snap={"chrome.exe":dict(hosts=entry["hosts"],active="github.com",focused=True,windows=[entry])}
        rows=observations_for(self.sample(),180,None,snap)
        self.assertIn(("youtube.com","background"),{(r.name,r.mode) for r in rows})
    def test_missing_browser_visibility_stays_unknown(self):
        entry=dict(hosts={"youtube.com","github.com"},visible={"github.com"},focused=True,minimized=False,complete=False,bounds=None)
        snap={"chrome.exe":dict(hosts=entry["hosts"],active="github.com",focused=True,windows=[entry])}
        rows=observations_for(self.sample(),180,None,snap)
        row,=[r for r in rows if r.name=="youtube.com"]
        self.assertEqual(row.mode,"unknown");self.assertFalse(row.visibility_known)
    def test_uia_two_browser_windows(self):
        sample=self.sample();sample.apps={"chrome.exe"};sample.windows=[W(1,1,"chrome.exe",(0,0,500,800)),W(2,1,"chrome.exe",(500,0,1000,800))]
        rows=observations_for(sample,180,"github.com",{},{1:"github.com",2:"youtube.com"})
        self.assertIn(("youtube.com","visible"),{(r.name,r.mode) for r in rows})
    def test_ambiguous_window_bounds(self):
        sample=self.sample();sample.foreground="excel.exe";sample.windows=[W(1,1,"chrome.exe",(0,0,500,800)),W(3,1,"chrome.exe",(0,0,500,800))]
        self.assertIsNone(match_window(sample,"chrome.exe",dict(focused=False,bounds=[0,0,500,800]),2))
    def test_complete_bridge_avoids_redundant_uia(self):
        sample=self.sample()
        snap={"chrome.exe":dict(windows=[dict(complete=True,focused=True,bounds=[0,0,500,800])])}
        self.assertEqual(uia_candidates(sample,snap),[])
        snap["chrome.exe"]["windows"][0]["complete"]=False
        self.assertEqual(uia_candidates(sample,snap),[(1,1)])

class SchemaTests(unittest.TestCase):
    def test_migration_keeps_old_unfocused_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"old.sqlite3";db=sqlite3.connect(path)
            db.execute("CREATE TABLE daily(day TEXT,kind TEXT,name TEXT,screen REAL,background REAL,idle REAL,background_known REAL,PRIMARY KEY(day,kind,name))")
            now=datetime.now(timezone.utc);day=now.astimezone().date().isoformat()
            db.execute("INSERT INTO daily VALUES (?,?,?,?,?,?,?)",(day,"app","excel.exe",20,40,5,60));db.commit();db.close()
            store=Store(path)
            row,=store.report(day)
            self.assertEqual(row["background"],40)
            self.assertEqual(row["covered"],0)
            self.assertFalse(row["visibility_known"])
            store.record(now,now+timedelta(seconds=10),[Observation("app","excel.exe","visible",True,True)])
            row,=store.report(day)
            self.assertEqual(row["visible"],10);self.assertEqual(row["screen"],20);self.assertEqual(row["background"],50);self.assertEqual(row["covered"],0)
            store.close()
    def test_focus_subset_and_simultaneous_visibility(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/"data.sqlite3");now=datetime.now(timezone.utc);day=now.astimezone().date().isoformat()
            store.record(now,now+timedelta(seconds=10),[Observation("app","code.exe","screen",True,True),Observation("app","excel.exe","visible",True,True)])
            rows=store.report(day)
            self.assertEqual(sum(r["screen"] for r in rows),10)
            self.assertEqual(sum(r["visible"] for r in rows),20)
            self.assertEqual(sum(r["covered"] for r in rows),0)
            store.close()

if __name__=="__main__":unittest.main()
