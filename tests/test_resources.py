import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import multiprocessing as mp
import subprocess
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from browser_reader import BrowserProbe
from ui_host import DashboardHost

class ResourceTests(unittest.TestCase):
    def test_background_import_does_not_load_gui_or_com(self):
        source=str(Path(__file__).resolve().parents[1]/"src")
        code="import sys;sys.path.insert(0,"+repr(source)+");import ui_host,collector,pystray;assert 'tkinter' not in sys.modules;assert 'comtypes' not in sys.modules"
        result=subprocess.run([sys.executable,"-c",code],capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
    def test_dashboard_rpc_and_detach(self):
        class Store:
            def report(self,day):return [dict(name="excel.exe",day=day)]
        class Collector:
            sampler=NS(excluded_pids={123}, app_paths=lambda names:{"excel.exe":"C:/example/excel.exe"})
            def status(self):return dict(paused=False,idle_threshold=180)
        dashboard=DashboardHost(Store(),Collector(),None)
        parent,child=mp.Pipe()
        thread=threading.Thread(target=dashboard._serve,args=(parent,123));thread.start()
        child.send(("report",("2026-10-15",)))
        self.assertTrue(child.poll(3));ok,rows=child.recv();self.assertTrue(ok);self.assertEqual(rows[0]["name"],"excel.exe")
        dashboard.show_requested=True;child.send(("status",()))
        self.assertTrue(child.poll(3));ok,state=child.recv();self.assertTrue(state["show_requested"])
        self.assertFalse(dashboard.show_requested)
        child.send(("app_paths",(["excel.exe"],)))
        self.assertTrue(child.poll(3));ok,paths=child.recv()
        self.assertTrue(ok);self.assertEqual(paths,{"excel.exe":"C:/example/excel.exe"})
        child.close();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertNotIn(123,Collector.sampler.excluded_pids)
    def test_uia_uses_cache_when_title_is_unchanged(self):
        clock=[100.];sent=[];replies=[]
        with patch("browser_reader.time.monotonic",side_effect=lambda:clock[0]):
            probe=BrowserProbe()
            probe.process=NS(is_alive=lambda:True)
            probe.connection=NS(poll=lambda:bool(replies),recv=lambda:replies.pop(0),send=lambda value:sent.append(value))
            self.assertEqual(probe.poll_many([(1,2)]),{})
            self.assertEqual(len(sent),1)
            replies.append((sent[0][0],1,2,"youtube.com","OK"));clock[0]=101
            self.assertEqual(probe.poll_many([(1,2)]),{1:"youtube.com"})
            for tick in (102,104,106,109):clock[0]=tick;probe.poll_many([(1,2)])
            self.assertEqual(len(sent),1)
            clock[0]=110;probe.poll_many([(1,2)]);self.assertEqual(len(sent),2)
    def test_uia_releases_worker_when_no_browser_visible(self):
        clock=[0.]
        with patch("browser_reader.time.monotonic",side_effect=lambda:clock[0]):
            probe=BrowserProbe();probe.process=object();clock[0]=16
            with patch.object(probe,"close") as close:
                probe.poll_many([]);close.assert_called_once()

if __name__=="__main__":unittest.main()
