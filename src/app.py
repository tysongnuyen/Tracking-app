from __future__ import annotations
import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path
import sys

def main():
    parser = argparse.ArgumentParser(description="Tracking App desktop for Windows")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--background",action="store_true",help="Start in the system tray without loading Tk")
    parser.add_argument("--background-check",action="store_true")
    parser.add_argument("--runtime-check",action="store_true",help="20s live tray check; requires a separate --data-dir and --result")
    parser.add_argument("--check-seconds", type=int, default=20)
    parser.add_argument("--check-dashboard", action="store_true")
    args = parser.parse_args()
    if args.runtime_check and (not args.data_dir or not args.result):
        parser.error("--runtime-check requires --data-dir and --result")
    if not 5 <= args.check_seconds <= 300:
        parser.error("--check-seconds must be between 5 and 300")
    if args.self_check or args.background_check:
        if args.background_check:
            from background_check import run
        else:
            from self_check import run
        result=run()
        if args.result:
            args.result.parent.mkdir(parents=True, exist_ok=True)
            args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        elif sys.stdout:
            print(json.dumps(result, ensure_ascii=False))
        return
    from windows_api import single_instance,reopen_instance,user,kernel
    from tracking_core import Store
    from collector import Collector
    from app_limits import AppLimits
    from process_blocker import ProcessBlocker
    from bridge import Bridge
    from ui_host import run_tray
    mutex, owner = single_instance()
    if not mutex:
        user.MessageBoxW(None,"Không tạo được khóa phiên chạy.","Tracking App",0x10)
        return
    if not owner:
        if args.runtime_check:
            args.result.parent.mkdir(parents=True,exist_ok=True)
            args.result.write_text(json.dumps(dict(status="SKIPPED",reason="Another tracker instance is running")),encoding="utf-8")
            kernel.CloseHandle(mutex)
            return
        reopen_instance()
        kernel.CloseHandle(mutex)
        return
    data = args.data_dir or Path(os.environ["LOCALAPPDATA"]) / "TrackingAppPython"
    store=bridge=collector=meter=None
    succeeded=False
    try:
        store = Store(data / "tracking.sqlite3")
        bridge = Bridge(store)
        bridge.start()
        collector = Collector(store, bridge)
        collector.limits = AppLimits(store, ProcessBlocker(excluded_pids=collector.sampler.excluded_pids), collector.queue_limit_event)
        if args.runtime_check:
            from runtime_meter import RuntimeMeter
            meter=RuntimeMeter();meter.start()
            collector.set_paused(False)
        collector.start()
        background = (not args.check_dashboard) if args.runtime_check else args.background
        run_tray(store,collector,bridge,background,args.check_seconds if args.runtime_check else None)
        succeeded=True
    except Exception as exc:
        user.MessageBoxW(None,str(exc),"Tracking App",0x10)
    finally:
        if collector and collector.thread.is_alive():collector.stop()
        if bridge:bridge.close()
        if store:store.close()
        if meter:
            result=meter.finish()
            result["status"]="PASS" if succeeded else "FAILED"
            result["uia_worker_started"]=collector.probe.sequence>0
            result["dashboard_opened"]=args.check_dashboard
            args.result.parent.mkdir(parents=True,exist_ok=True)
            args.result.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        kernel.CloseHandle(mutex)

if __name__ == "__main__":
    mp.freeze_support()
    main()
