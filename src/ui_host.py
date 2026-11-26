"""Dashboard lives in a disposable child; the tray collector never imports Tk."""
import multiprocessing as mp
from datetime import datetime
from pathlib import Path
import threading
import time

WEEKLY_REPORT_HOUR = 8

def ui_worker(connection,config):
    import tkinter as tk
    from desktop_ui import Desktop
    root=tk.Tk();root.withdraw()
    def request(command,*args):
        connection.send((command,args))
        if not connection.poll(5):raise RuntimeError("Bộ thu thập chưa phản hồi")
        ok,value=connection.recv()
        if not ok:raise RuntimeError(value)
        return value
    class StoreProxy:
        path=Path(config["path"])
        def report(self,day,end_day=None):
            return request("report",day) if end_day is None else request("report",day,end_day)
        def browser_report(self,day,browser=None):return request("browser_report",day,browser)
    class CollectorProxy:
        @property
        def idle_threshold(self):return self.status()["idle_threshold"]
        def status(self):
            value=request("status")
            page=value.pop("requested_page",None)
            if value.pop("show_requested",False):
                def show():
                    if page:app.show_page(page)
                    root.deiconify();root.lift()
                root.after_idle(show)
            return value
        def set_paused(self,value):request("paused",value)
        def set_idle(self,value):request("idle",value)
        def app_paths(self,names):return request("app_paths",names)
    class LimitsProxy:
        def rules(self):return request("limits_rules")
        def save_rule(self,path,minutes,enabled=True,name=None):
            if name is None:return request("limits_save",path,minutes,enabled)
            return request("limits_save",path,minutes,enabled,name)
        def set_enabled(self,rule_id,enabled):return request("limits_enabled",rule_id,enabled)
        def delete_rule(self,rule_id):return request("limits_delete",rule_id)
    class BridgeProxy:
        token=config["token"]
        error=config["bridge_error"]
    app=Desktop(root,StoreProxy(),CollectorProxy(),BridgeProxy(),limits=LimitsProxy())
    if config.get("page") in {"comparison","limits"}:app.show_page(config["page"])
    def close():
        app.close()
    root.protocol("WM_DELETE_WINDOW",close)
    root.deiconify()
    try:root.mainloop()
    finally:connection.close()

class DashboardHost:
    def __init__(self,store,collector,bridge):
        self.store,self.collector,self.bridge=store,collector,bridge
        self.process=None
        self.show_requested=False
        self.requested_page=None
        self.lock=threading.Lock()
    def open(self,page=None):
        if page not in {None,"overview","comparison","limits"}:raise ValueError("Unknown dashboard page")
        with self.lock:
            if self.process and self.process.is_alive():
                self.show_requested=True
                if page:self.requested_page=page
                return
            if self.process:self.process.close()
            parent,child=mp.get_context("spawn").Pipe()
            config=dict(path=str(self.store.path),token=self.bridge.token,bridge_error=self.bridge.error,page=page)
            self.show_requested=False;self.requested_page=None
            process=mp.get_context("spawn").Process(target=ui_worker,args=(child,config),daemon=True,name="TrackingDashboard")
            process.start();child.close()
            self.process=process
            self.collector.sampler.excluded_pids.add(process.pid)
            threading.Thread(target=self._serve,args=(parent,process.pid),daemon=True,name="DashboardRPC").start()
    def _serve(self,connection,pid):
        try:
            while True:
                command,args=connection.recv()
                try:
                    if command=="report":value=self.store.report(*args)
                    elif command=="browser_report":value=self.store.browser_report(*args)
                    elif command=="paused":value=self.collector.set_paused(*args)
                    elif command=="idle":value=self.collector.set_idle(*args)
                    elif command=="app_paths":value=self.collector.sampler.app_paths(*args)
                    elif command=="limits_rules":value=self.collector.limits.rules()
                    elif command=="limits_save":value=self.collector.limits.save_rule(*args)
                    elif command=="limits_enabled":value=self.collector.limits.set_enabled(*args)
                    elif command=="limits_delete":value=self.collector.limits.delete_rule(*args)
                    elif command=="status":
                        value=self.collector.status()
                        with self.lock:
                            value["show_requested"]=self.show_requested
                            value["requested_page"]=self.requested_page
                            self.show_requested=False;self.requested_page=None
                    else:raise ValueError("Unknown dashboard command")
                    connection.send((True,value))
                except Exception as exc:connection.send((False,str(exc)))
        except (EOFError,OSError):pass
        finally:
            connection.close()
            self.collector.sampler.excluded_pids.discard(pid)
    def close(self):
        with self.lock:
            if self.process:
                if self.process.is_alive():self.process.terminate()
                self.process.join(1);self.process.close();self.process=None

def run_tray(store,collector,bridge,background=False,stop_after=None):
    import pystray
    from notification_icon import NotificationIcon
    from weekly_report import WeeklyReporter
    from branding import app_icon
    from windows_api import show_signal
    dashboard=DashboardHost(store,collector,bridge)
    picture=app_icon(64)
    def pause(icon,item):
        collector.set_paused(not collector.status()["paused"])
        icon.update_menu()
    def pause_label(item):
        state=collector.status()
        return "Tạm dừng" if not state["paused"] else "Tiếp tục" if state["has_started"] else "Bắt đầu"
    icon=NotificationIcon("TrackingAppPython",picture,"Tracking App",on_notification_click=lambda:dashboard.open("comparison"),menu=pystray.Menu(
        pystray.MenuItem("Mở Monitoring",lambda icon,item:dashboard.open(),default=True),
        pystray.MenuItem(pause_label,pause),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Thoát hoàn toàn",lambda icon,item:icon.stop())))
    stop=threading.Event()
    signal=show_signal()
    reporter=WeeklyReporter(store,WEEKLY_REPORT_HOUR) if stop_after is None else None
    def notify_report(report):
        icon.on_notification_click=lambda:dashboard.open("comparison")
        icon.notify(weekly_notification_message(report),"Báo cáo tuần · Tracking")
    def watch():
        from windows_api import kernel
        import sqlite3
        next_report_check=0.
        next_limit_notice=0.
        while not stop.is_set():
            if kernel.WaitForSingleObject(signal,500)==0:dashboard.open()
            if stop_after is None and icon.visible and time.monotonic() >= next_limit_notice:
                event=collector.pop_limit_event()
                if event:
                    next_limit_notice=time.monotonic()+3
                    icon.on_notification_click=lambda:dashboard.open("limits")
                    try:icon.notify(limit_notification_message(event),"Giới hạn ứng dụng · Tracking")
                    except (OSError,RuntimeError):pass
            if reporter and icon.visible and time.monotonic() >= next_report_check:
                next_report_check=time.monotonic()+60
                try:reporter.poll(datetime.now().astimezone(),notify_report)
                except (OSError,RuntimeError,sqlite3.Error):pass
    watcher=threading.Thread(target=watch,daemon=True,name="ReopenSignal");watcher.start()
    timer=None
    if stop_after is not None:
        timer=threading.Timer(stop_after,icon.stop);timer.daemon=True;timer.start()
    try:
        if not background:dashboard.open()
        icon.run()
    finally:
        stop.set();watcher.join(1);dashboard.close()
        if timer:timer.cancel()
        from windows_api import kernel
        kernel.CloseHandle(signal)


def weekly_notification_message(report):
    """Keep a compact summary within the Windows notification text limit."""
    from dashboard_model import display_name,duration
    from datetime import date
    start=date.fromisoformat(report["start_day"]).strftime("%d/%m")
    end=date.fromisoformat(report["end_day"]).strftime("%d/%m")
    lines=[f"{start} – {end} · Tổng {duration(report['total_seconds'])}"]
    for row in report["apps"][:3]:
        name=display_name(row["name"])
        if len(name)>30:name=name[:29]+"…"
        lines.append(f"{row['rank']}. {name}: {duration(row['screen'])} ({row['share']:.0f}%)")
    if not report["apps"]:lines.append("Chưa có dữ liệu sử dụng ứng dụng.")
    message="\n".join(lines)
    # szInfo holds 256 WCHARs, including the null terminator.
    return notification_text(message)


def notification_text(message):
    encoded=message.encode("utf-16-le")
    if len(encoded)>508:
        message=encoded[:506].decode("utf-16-le",errors="ignore")+"…"
    return message


def limit_notification_message(event):
    from dashboard_model import display_name
    name=event.get("name") or Path(event.get("path", "Ứng dụng")).name
    name=display_name(name)
    if len(name)>40:name=name[:39]+"…"
    if event["type"] == "warning":
        text=f"{name} còn {event['remaining_minutes']} phút sử dụng hôm nay. Hết giờ sẽ đóng toàn bộ app và các tiến trình con. Hãy lưu công việc."
    elif event["type"] == "error":
        text=f"Chưa đóng hết {name}: {event.get('error', 'Thiếu quyền xử lý tiến trình')}. Mở Giới hạn để kiểm tra."
    else:
        text=f"{name} đã hết thời gian sử dụng hôm nay. Mở Giới hạn để xem trạng thái hoặc bỏ chặn."
    return notification_text(text)
