"""Packaged smoke check. Uses fixtures, not the user's browsing history."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import os
import tempfile
import tkinter as tk
from types import SimpleNamespace

def run():
    from tracking_core import Store, Observation
    from browser_reader import hostname, BrowserProbe, ui_automation
    from desktop_ui import Desktop
    from app_icons import extract_icon
    from app_limits import AppLimits
    checks = []
    sdk, ui = ui_automation()
    assert ui and sdk.IUIAutomationValuePattern
    checks.append("UI Automation COM bindings loaded")
    assert hostname("https://www.youtube.com/watch?v=private#section") == "youtube.com"
    assert hostname("not a url") is None
    checks.append("Hostname extraction, no path retention")
    icon = extract_icon(Path(os.environ["WINDIR"]) / "explorer.exe")
    assert icon is not None and icon.mode == "RGBA" and icon.size == (32, 32) and icon.getbbox()
    checks.append("Real Windows executable icon extracted with transparency")
    from app_catalog import _ShortcutReader, discover_apps
    with _ShortcutReader():
        pass
    assert discover_apps(running_provider=lambda: [], shortcut_provider=lambda: []) == []
    checks.append("Native Windows Start Menu shortcut reader and app catalogue load without extra dependencies")
    class FakeCollector:
        idle_threshold = 180
        paused = False
        has_started = True
        pause_writes = 0
        def status(self):
            return dict(paused=self.paused, has_started=self.has_started, app="excel.exe", host="youtube.com", web_status="Dữ liệu kiểm thử", error="")
        def set_paused(self, value):
            self.pause_writes += 1
            self.paused = value
            if not value:
                self.has_started = True
        def set_idle(self, value):
            self.idle_threshold = value
    class FakeBridge:
        token, error = "a" * 64, ""
    with tempfile.TemporaryDirectory(prefix="tracking-python-check-") as folder:
        store = Store(Path(folder) / "test.sqlite3")
        now = datetime.now(timezone.utc)
        store.record(now - timedelta(seconds=10), now, [Observation("app", "chrome.exe", "screen", True, True), Observation("web", "facebook.com", "screen", True, True, "chrome.exe")])
        store.flush()
        class FixtureBlocker:
            killed = []
            def validate_path(self, path):return str(Path(path).resolve())
            def scan(self, paths):return {path: dict(pids={90001, 90002}) for path in paths}
            def terminate(self, path):
                self.killed.append(path)
                return dict(killed=[90001, 90002], alive=[], denied=[], protected=[], errors=[])
        limit_clock = [datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)]
        limit_events = []
        limit_blocker = FixtureBlocker()
        limits = AppLimits(store, limit_blocker, limit_events.append, now=lambda: limit_clock[0])
        root = tk.Tk()
        root.withdraw()
        app = Desktop(root, store, FakeCollector(), FakeBridge(), limits=limits)
        root.update_idletasks()
        from branding import app_icon, asset_path, wordmark_image
        assert app_icon(64).size == (64, 64)
        assert wordmark_image((150, 58)).size == (150, 58)
        assert asset_path("tracking.ico").is_file()
        assert "Monitoring" in app.overview_button.cget("text")
        assert "Thống kê" in app.comparison_button.cget("text")
        assert app.brand_label.cget("image") and app.brand_picture.width() == 150
        checks.append("Bundled handwritten Tracking wordmark and T icon load; Monitoring/Statistics navigation labels and native window branding are applied")
        assert len(app.tree.get_children()) == 1
        assert app.sidebar.cget("bg") == "#181818"
        assert not hasattr(app, "detail") and not hasattr(app, "export")
        assert app.pause_button.winfo_parent() != app.tree.winfo_parent()
        app.pause_button.invoke()
        assert "Tiếp tục" in app.pause_button.cget("text")
        assert app.collector.status()["paused"]
        assert app.donut.action == "resume"
        writes = app.collector.pause_writes
        app.donut.set_hover(True)
        assert app.donut.itemcget(app.donut.total_text, "state") == "hidden"
        assert app.donut.itemcget(app.donut.action_text, "text") == "Tiếp tục"
        assert app.donut.itemcget(app.donut.action_image, "state") == "normal"
        app.donut.set_hover(False)
        assert app.donut.itemcget(app.donut.total_text, "state") == "normal"
        assert app.donut.itemcget(app.donut.action_image, "state") == "hidden"
        assert app.collector.pause_writes == writes
        app.donut.press(SimpleNamespace(x=0, y=0))
        app.donut.release(SimpleNamespace(x=0, y=0))
        assert app.collector.status()["paused"] and app.collector.pause_writes == writes
        app.donut.press(SimpleNamespace(x=155, y=155))
        app.donut.release(SimpleNamespace(x=155, y=155))
        assert not app.collector.status()["paused"]
        assert app.collector.pause_writes == writes+1
        assert app.donut.action == "pause"
        app.donut.set_hover(True)
        assert app.donut.itemcget(app.donut.action_text, "text") == "Tạm dừng"
        app.refresh()
        assert app.donut.itemcget(app.donut.total_text, "state") == "hidden"
        assert app.collector.pause_writes == writes+1
        app.donut.invoke()
        assert app.collector.status()["paused"] and app.collector.pause_writes == writes+2
        assert app.donut.action == "resume"
        app.collector.has_started = False
        app.refresh()
        assert app.donut.action == "start"
        assert app.donut.itemcget(app.donut.action_text, "text") == "Bắt đầu"
        assert app.donut.itemcget(app.donut.action_text, "fill") == "#71D89B"
        app.donut.invoke()
        assert not app.collector.status()["paused"] and app.collector.has_started
        assert app.donut.action == "pause"
        app.donut.set_hover(False)
        assert app.total == 10
        assert "Đang theo dõi" in app.app_status.get()
        checks.append("Hover shows Start/Pause/Resume icons over time; leaving restores time without changing tracking; clicks perform the shown action")
        assert app.donut.itemcget(app.donut.total_text, "text") == "10s"
        assert not any("focus" in app.donut.itemcget(item, "text").lower() for item in app.donut.find_all() if app.donut.type(item) == "text")
        assert app.search_field.itemcget(app.search_field.placeholder_window, "state") == "normal"
        app.search_field.entry.insert(0, "chrome")
        assert app.search.get() == "chrome" and len(app.tree.get_children()) == 1
        assert app.search_field.itemcget(app.search_field.placeholder_window, "state") == "hidden"
        app.search_field.entry.delete(0, "end")
        assert app.search.get() == ""
        assert app.search_field.itemcget(app.search_field.placeholder_window, "state") == "normal"
        checks.append("Search pill with internal placeholder, real query filtering, no focus caption in donut")
        app.browser = "chrome.exe"
        app.refresh()
        assert app.view_rows[0]["name"] == "facebook.com"
        assert app.tree.item(app.tree.get_children()[0], "values")[2] == "100.0%"
        app.search.set("facebook")
        assert len(app.tree.get_children()) == 1
        app.search.set("missing")
        assert not app.tree.get_children()
        assert app.total == 10
        app.go_back()
        assert app.browser is None and len(app.tree.get_children()) == 1
        app.scroll.set(0, .25)
        assert app.scroll.itemcget(app.scroll.thumb, "capstyle") == "round"
        assert app.scroll.itemcget(app.scroll.thumb, "state") == "normal"
        app.scroll.set(0, 1)
        assert app.scroll.itemcget(app.scroll.thumb, "state") == "hidden"
        checks.append("Dark native GUI, left sidebar controls, removed footer/CSV, rounded scrollbar, donut/browser detail/search/back")
        app.show_calendar()
        popup = app.calendar_popup
        assert popup.next_month.cget("state") == "disabled"
        popup.shift_month(1)
        assert (popup.year, popup.month) == (date.today().year, date.today().month)
        popup.year, popup.month = 2024, 2
        popup.render_month()
        leap_day = date(2024, 2, 29)
        assert leap_day in popup.day_buttons
        popup.day_buttons[leap_day].invoke()
        assert app.day.get() == "2024-02-29" and app.date_button.cget("text") == "29/02/2024"
        app.change_day(1)
        assert app.day.get() == "2024-03-01"
        app.show_calendar()
        popup = app.calendar_popup
        popup.year, popup.month = 2026, 1
        popup.shift_month(-1)
        assert (popup.year, popup.month) == (2025, 12)
        popup.today_button.invoke()
        assert app.day.get() == date.today().isoformat()
        assert app.next_button.cget("state") == "disabled"
        app.show_calendar()
        app.show_calendar()
        assert not app.calendar_popup.winfo_exists()
        root.update_idletasks()
        checks.append("Click calendar: leap day selection, year/month navigation, Today, future-day restriction and popup toggle")
        store.record(now - timedelta(seconds=30), now, [Observation("app", "code.exe", "screen", True, True)])
        week_start = date.today() - timedelta(days=6)
        first_day = datetime.combine(week_start, datetime.min.time()).replace(hour=12).astimezone()
        store.record(first_day, first_day + timedelta(seconds=60), [
            Observation("app", "chrome.exe", "screen", True, True),
            Observation("web", "facebook.com", "screen", True, True, "chrome.exe"),
        ])
        outside = first_day - timedelta(days=1)
        store.record(outside, outside + timedelta(seconds=90), [Observation("app", "notepad.exe", "screen", True, True)])
        writes = app.collector.pause_writes
        app.select_day(date.today() - timedelta(days=14))
        app.show_page("comparison")
        root.update_idletasks()
        assert app.comparison.winfo_manager() == "pack" and not app.overview_panel.winfo_manager()
        ranked = app.comparison.ranked_rows
        assert [(row["name"], row["rank"], row["share"]) for row in ranked] == [("chrome.exe", 1, 70), ("code.exe", 2, 30)]
        assert app.comparison.total == 100
        assert app.comparison.period == (week_start.isoformat(), date.today().isoformat())
        assert not app.date_controls.winfo_manager()
        assert app.comparison.summary.get().startswith(week_start.strftime("%d/%m/%Y"))
        app.comparison.query.set("code")
        assert len(app.comparison.visible_rows) == 1
        assert app.comparison.visible_rows[0]["rank"] == 2 and app.comparison.visible_rows[0]["share"] == 30
        assert app.comparison.total == 100
        app.comparison.pie_button.invoke()
        root.update_idletasks()
        assert app.comparison.mode == "pie"
        assert app.comparison.pie_panel.winfo_manager() == "pack"
        assert len(app.comparison.pie_slices) == 2
        assert len(app.comparison.pie_canvas.find_withtag("pie")) == 1
        assert len(app.comparison.visible_rows) == 1 and app.comparison.visible_rows[0]["share"] == 30
        assert len(app.comparison.legend_images) == 1
        app.comparison.ranking_button.invoke()
        root.update_idletasks()
        assert app.comparison.mode == "ranking" and app.comparison.table.winfo_manager() == "pack"
        app.comparison.query.set("")
        from dashboard_model import rank_apps
        pie_fixture, pie_total = rank_apps([dict(kind="app", name=name, screen=seconds)
            for name, seconds in (("chrome.exe", 92), ("code.exe", 5),
                                  ("notepad.exe", 2), ("brave.exe", 1))])
        app.comparison.set_data(pie_fixture, pie_total, period=(week_start.isoformat(), date.today().isoformat()))
        app.comparison.pie_button.invoke()
        root.update_idletasks()
        assert [row["name"] for row in app.comparison.pie_rows] == ["chrome.exe", "code.exe", "Khác"]
        assert app.comparison.pie_rows[-1]["share"] == 3
        assert [row["legend_kind"] for row in app.comparison.legend_rows] == ["app", "app", "other"]
        app.comparison.legend_clicked(SimpleNamespace(y=2*app.comparison.LEGEND_ROW_HEIGHT+20))
        root.update_idletasks()
        children = [row for row in app.comparison.legend_rows if row["legend_kind"] == "child"]
        assert [(row["name"], row["share"]) for row in children] == [("notepad.exe", 2), ("brave.exe", 1)]
        app.comparison.query.set("notepad")
        assert app.comparison.total == 100 and app.comparison.legend_rows[-1]["share"] == 2
        app.comparison.query.set("")
        app.comparison.ranking_button.invoke()
        assert len(app.comparison.visible_rows) == 4
        app.refresh()
        app.change_day(-1)
        assert app.day.get() == (date.today() - timedelta(days=14)).isoformat()
        assert app.comparison.total == 100
        app.show_page("overview")
        root.update_idletasks()
        assert app.overview_panel.winfo_manager() == "pack" and not app.comparison.winfo_manager()
        assert app.date_controls.winfo_manager() == "pack" and app.total == 0
        app.select_day(date.today())
        assert app.browser is None and app.total == 40 and len(app.tree.get_children()) == 2
        assert app.collector.pause_writes == writes
        checks.append("Comparison ranking/pie share 7-day totals; apps below 5% group into Other, exactly 5% stays separate, group click expands children with original percentages and search preserves totals; daily overview stays correct")
        from weekly_report import WeeklyReporter
        from ui_host import weekly_notification_message
        from notification_icon import NotificationIcon
        import pystray
        assert issubclass(NotificationIcon, pystray.Icon)
        monday = date.today() - timedelta(days=date.today().weekday())
        report_time = datetime.combine(monday, datetime.min.time()).replace(hour=8).astimezone()
        reporter = WeeklyReporter(store, 8)
        notifications = []
        assert reporter.poll(report_time - timedelta(seconds=1), notifications.append) is None
        report = reporter.poll(report_time, notifications.append)
        assert report and len(notifications) == 1
        assert len(weekly_notification_message(report).encode("utf-16-le")) <= 510
        assert WeeklyReporter(store, 8).poll(report_time + timedelta(minutes=1), notifications.append) is None
        assert len(notifications) == 1
        checks.append("Frozen notification adapter loads; Monday 08:00 fixture report triggers once, stays suppressed after restart and fits Windows text limits; no real notification sent")
        from ui_host import limit_notification_message
        from process_blocker import ProcessBlocker
        assert ProcessBlocker().scan([]) == {}
        app.show_page("limits")
        root.update_idletasks()
        assert app.limits_view.winfo_manager() == "pack" and not app.overview_panel.winfo_manager()
        assert not app.date_controls.winfo_manager() and app.limits_button.winfo_manager() == "pack"
        view = app.limits_view
        fixture_path = Path(folder) / "limit-fixture.exe"
        fixture_path.write_bytes(b"Fixture data, never executed")
        view.catalog_provider = lambda: [dict(path=str(fixture_path), name="Ứng dụng kiểm tra")]
        view.open_dialog()
        import time
        deadline = time.monotonic()+5
        while view.picker.loading and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        assert not view.picker.loading and len(view.picker.apps) == 1
        view.picker.query.set("kiểm tra")
        view.picker.select(view.picker.visible_apps[0])
        assert view.picker.photos and view.selected_app["name"] == "Ứng dụng kiểm tra"
        view.minutes_var.set("180")
        assert view.save_dialog()
        rule, = limits.rules()
        assert rule["name"] == "Ứng dụng kiểm tra"
        for index, minutes_left in enumerate((15, 10, 5, 0)):
            begin = limit_clock[0] + timedelta(seconds=index*2)
            with store.lock, store.db:
                store.db.execute("""INSERT INTO app_limit_daily(day,path,used) VALUES (?,?,?)
                    ON CONFLICT(day,path) DO UPDATE SET used=excluded.used""",
                    (begin.date().isoformat(), rule["path"], (180-minutes_left)*60-1))
            limits.tick(begin, begin+timedelta(seconds=1), 90002)
        assert [(e["type"], e["remaining_minutes"]) for e in limit_events] == [("warning", 15), ("warning", 10), ("warning", 5), ("blocked", 0)]
        assert limit_blocker.killed == [rule["path"]]
        assert "15 phút" in limit_notification_message(limit_events[0])
        app.refresh()
        assert view.rows[0]["blocked"]
        view.toggle(view.rows[0])
        assert not view.rows[0]["enabled"] and not view.rows[0]["blocked"]
        limits.tick(limit_clock[0], limit_clock[0]+timedelta(seconds=1), 90002)
        assert len(limit_blocker.killed) == 1
        view.toggle(view.rows[0])
        limits.tick(limit_clock[0], limit_clock[0]+timedelta(seconds=1), None, count_usage=False)
        assert len(limit_blocker.killed) == 2
        assert AppLimits(store, limit_blocker, now=lambda: limit_clock[0]).rules()[0]["blocked"]
        limit_clock[0] += timedelta(days=1)
        assert limits.rules()[0]["used"] == 0 and not limits.rules()[0]["blocked"]
        view.remove(view.rows[0])
        assert not view.rows
        app.show_page("overview")
        assert app.total == 40
        checks.append("App limits GUI selects a named app/logo without a file picker, saves the name, receives 15/10/5-minute fixture warnings, exhausts by a child PID, disables immediately, preserves quota after restart and resets at midnight; process termination is mocked")
        app.close()
        store.close()
    # Exercise the actual frozen multiprocessing worker and COM import path.
    probe = BrowserProbe()
    probe._start()
    probe.connection.send((1, 0, 0))
    assert probe.connection.poll(15), "Isolated UIA worker did not respond"
    response = probe.connection.recv()
    assert response[0] == 1 and response[3] is None
    probe.close()
    checks.append("Isolated UIA worker starts, responds and stops")
    return {"status": "PASS", "checks": checks, "note": "Fixture data only. This is not a real-browser compatibility test."}
