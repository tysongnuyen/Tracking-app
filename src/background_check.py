"""Synthetic resource check: no tray shown, no real windows or browser inspected."""
import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime,timedelta,timezone

def run():
    import pystray
    from PIL import Image,ImageDraw
    import psutil
    from tracking_core import Store,Observation
    from collector import Collector
    from ui_host import DashboardHost
    from visibility import WindowGeometry,visible_windows
    assert "tkinter" not in sys.modules,"Tray imports must not load Tk"
    assert "comtypes" not in sys.modules,"Tray imports must not load COM/UIA"
    with tempfile.TemporaryDirectory(prefix="tracking-bg-check-") as folder:
        store=Store(Path(folder)/"test.sqlite3")
        class Bridge:
            token="a"*64;error=""
        collector=Collector(store,Bridge())
        dashboard=DashboardHost(store,collector,Bridge())
        assert dashboard.process is None and collector.probe.process is None
        process=psutil.Process()
        cpu_start=process.cpu_times();wall_start=time.perf_counter()
        peak=0
        windows=[WindowGeometry(i,i,f"app{i}.exe",(i*50,0,i*50+50,800)) for i in range(20)]
        for i in range(10):
            shown=visible_windows(windows,[(0,0,1000,800)])
            now=datetime.now(timezone.utc)
            store.record(now-timedelta(seconds=1),now,[Observation("app",w.app,"screen" if w.hwnd==0 else "visible",True,shown[w.hwnd]) for w in windows])
            peak=max(peak,process.memory_info().rss)
            time.sleep(1)
        store.flush();store.close()
        elapsed=time.perf_counter()-wall_start;cpu_end=process.cpu_times()
        cpu=cpu_end.user+cpu_end.system-cpu_start.user-cpu_start.system
    return dict(status="PASS",tk_loaded=False,uia_loaded=False,dashboard_started=False,uia_worker_started=False,
        duration_seconds=round(elapsed,2),rss_mib=round(peak/1048576,1),cpu_one_core_percent=round(cpu/elapsed*100,2),
        note="Synthetic 20-window aggregation and tray imports only. No real Windows hooks, visible tray, live UIA or browser workload. Not a guarantee of actual tracking resource usage.")
