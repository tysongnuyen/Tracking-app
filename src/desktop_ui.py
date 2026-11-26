from __future__ import annotations
from datetime import date, timedelta
from collections import OrderedDict
import time
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk
from dashboard_model import (BROWSER_NAMES, display_name, duration, short_duration,
                             overview, browser_detail, chart_parts, rank_apps)
from ui_widgets import (BG, SIDEBAR, CARD, INK, MUTED, Surface, SoftButton,
                        RoundedScrollbar, SearchField, install_dark_frame)
from date_picker import DateButton, ChevronButton, CalendarPopup
from app_icons import AppIcons
from comparison_ui import ComparisonView
from weekly_report import trailing_week
from limits_ui import LimitsView
from overview_ui import UsageTable
from branding import app_icon, wordmark_image

def label(parent, text="", size=10, color=INK, bold=False, **kwargs):
    return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                    font=("Segoe UI", size, "bold" if bold else "normal"), **kwargs)

class Donut(tk.Canvas):
    ACTIONS = {"start": ("Bắt đầu", "#71D89B"), "pause": ("Tạm dừng", INK), "resume": ("Tiếp tục", "#71D89B")}

    def __init__(self, parent, on_action):
        super().__init__(parent, width=310, height=320, bg=CARD, highlightthickness=0,
                         takefocus=True)
        self.on_action = on_action
        self.pressed_action = None
        self.action = "start"
        self.hovered = False
        self.signature = None
        self.picture = None
        self.total_text = self.create_text(155, 166, text="0s", fill=INK, font=("Segoe UI", 32, "bold"))
        self.title_text = self.create_text(155, 127, text="Tổng thời gian", fill=MUTED, font=("Segoe UI", 10))
        self.image_id = self.create_image(155, 155)
        self.tag_lower(self.image_id)
        self.action_icons = {action: self.make_action_icon(action) for action in self.ACTIONS}
        self.action_image = self.create_image(155, 147, state="hidden")
        self.action_text = self.create_text(155, 190, font=("Segoe UI", 11, "bold"), state="hidden")
        self.bind("<Enter>", lambda event: self.set_hover(self.contains(event)))
        self.bind("<Motion>", lambda event: self.set_hover(self.contains(event)))
        self.bind("<Leave>", lambda _: self.set_hover(False))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<Return>", self.invoke)
        self.bind("<space>", self.invoke)

    @staticmethod
    def contains(event):
        return (event.x-155)**2+(event.y-155)**2 <= 147**2

    def press(self, event):
        self.pressed_action = self.action if self.contains(event) else None
        if self.pressed_action:
            self.focus_set()

    def release(self, event):
        action, self.pressed_action = self.pressed_action, None
        if action and self.contains(event):
            self.invoke(action=action)

    def invoke(self, event=None, action=None):
        self.on_action(action or self.action)
        return "break"

    def make_action_icon(self, action):
        scale = 3
        image = Image.new("RGBA", (54*scale, 54*scale))
        draw = ImageDraw.Draw(image)
        color = self.ACTIONS[action][1]
        if action == "pause":
            for left in (15, 31):
                draw.rounded_rectangle((left*scale, 11*scale, (left+8)*scale, 43*scale), radius=2*scale, fill=color)
        else:
            draw.polygon([(21*scale, 10*scale), (43*scale, 27*scale), (21*scale, 44*scale)], fill=color)
            if action == "resume":
                draw.rounded_rectangle((11*scale, 13*scale, 15*scale, 41*scale), radius=scale, fill=color)
        return ImageTk.PhotoImage(image.resize((54, 54), Image.Resampling.LANCZOS), master=self)

    def set_tracking_state(self, paused, has_started):
        action = "pause" if not paused else "resume" if has_started else "start"
        if action != self.action:
            self.action = action
            self.paint_control()

    def set_hover(self, hovered):
        if self.hovered != hovered:
            self.hovered = hovered
            self.configure(cursor="hand2" if hovered else "")
            self.paint_control()

    def paint_control(self):
        title, color = self.ACTIONS[self.action]
        self.itemconfigure(self.action_image, image=self.action_icons[self.action], state="normal" if self.hovered else "hidden")
        self.itemconfigure(self.action_text, text=title, fill=color, state="normal" if self.hovered else "hidden")
        for item in (self.title_text, self.total_text):
            self.itemconfigure(item, state="hidden" if self.hovered else "normal")

    def update_chart(self, rows, total):
        self.itemconfigure(self.total_text, text=short_duration(total))
        parts = chart_parts(rows)
        denominator = sum(value for value, _ in parts)
        signature = tuple((round(value / denominator * 720), color) for value, color in parts) if denominator else ()
        if signature == self.signature:
            return
        self.signature = signature
        scale, size, border = 3, 306, 29
        picture = Image.new("RGB", (size*scale, size*scale), CARD)
        draw = ImageDraw.Draw(picture)
        outer = (9*scale, 9*scale, (size-9)*scale, (size-9)*scale)
        draw.ellipse(outer, fill="#383838")
        start = -90.
        for value, color in parts:
            sweep = value / denominator * 360
            gap = min(1.5, sweep * .14) if len(parts) > 1 else 0
            draw.pieslice(outer, start+gap/2, start+sweep-gap/2, fill=color)
            start += sweep
        inset = (9+border)*scale
        draw.ellipse((inset, inset, size*scale-inset, size*scale-inset), fill=CARD)
        self.picture = ImageTk.PhotoImage(picture.resize((size, size), Image.Resampling.LANCZOS), master=self)
        self.itemconfigure(self.image_id, image=self.picture)

class Desktop:
    def __init__(self, root, store, collector, bridge, limits=None):
        self.root, self.store, self.collector, self.bridge = root, store, collector, bridge
        self.limits_service = limits
        self.rows, self.view_rows, self.ids = [], [], {}
        self.browser = None
        self.page = "overview"
        self.settings_window = None
        self.calendar_popup = None
        self.tree_signature = None
        self.total = 0.
        self.icons = AppIcons()
        self.icon_paths = {}
        self.icon_requested = set()
        self.icon_check_at = -100.
        self.limits_icon_revision = -1
        self.photos = OrderedDict()
        self.visible_photos = []
        root.title("Tracking")
        self.window_icon = ImageTk.PhotoImage(app_icon(64), master=root)
        root.iconphoto(True, self.window_icon)
        root.geometry("1280x780")
        root.minsize(1080, 640)
        root.configure(bg=BG)
        root.option_add("*Font", ("Segoe UI", 10))
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TEntry", padding=7, borderwidth=0, fieldbackground="#303030", foreground=INK,
                        bordercolor="#303030", lightcolor="#303030", darkcolor="#303030", insertcolor=INK)
        style.map("TEntry", fieldbackground=[("readonly", "#303030")], foreground=[("readonly", INK)])
        style.configure("TCombobox", padding=7, fieldbackground="#303030", background="#303030",
                        foreground=INK, arrowcolor=MUTED, borderwidth=0)
        style.map("TCombobox", fieldbackground=[("readonly", "#303030")],
                  foreground=[("readonly", INK)], selectbackground=[("readonly", "#303030")], selectforeground=[("readonly", INK)])
        root.option_add("*TCombobox*Listbox.background", "#303030")
        root.option_add("*TCombobox*Listbox.foreground", INK)
        root.option_add("*TCombobox*Listbox.selectBackground", "#464646")
        style.configure("Treeview", rowheight=50, background=CARD, fieldbackground=CARD, foreground=INK,
                        borderwidth=0, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=CARD, foreground=MUTED, padding=(0, 12),
                        borderwidth=0, relief="flat", font=("Segoe UI", 9))
        style.map("Treeview.Heading", background=[("active", CARD)])
        style.map("Treeview", background=[("selected", "#383838")], foreground=[("selected", INK)])

        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)
        self.sidebar = tk.Frame(root, bg=SIDEBAR, width=188, padx=14, pady=25)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.grid_propagate(False)
        self.brand_picture = ImageTk.PhotoImage(wordmark_image((150, 58)), master=root)
        self.brand_label = tk.Label(self.sidebar, image=self.brand_picture, bg=SIDEBAR)
        self.brand_label.pack(anchor="w", padx=5, pady=(0, 22))
        self.overview_button = SoftButton(self.sidebar, "◷   Monitoring", lambda: self.show_page("overview"), width=160, fill="#2B2B2B", align="left")
        self.overview_button.pack(fill="x")
        self.comparison_button = SoftButton(self.sidebar, "▥   Thống kê", lambda: self.show_page("comparison"), width=160, align="left")
        self.comparison_button.pack(fill="x", pady=(5, 0))
        self.limits_button = SoftButton(self.sidebar, "◴   Giới hạn app", lambda: self.show_page("limits"), width=160, align="left")
        if limits is not None:self.limits_button.pack(fill="x", pady=(5, 0))
        bottom = tk.Frame(self.sidebar, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x")
        self.app_status, self.notice = tk.StringVar(), tk.StringVar()
        label(bottom, textvariable=self.app_status, size=9, color=MUTED).pack(anchor="w", padx=12, pady=(0, 14))
        self.pause_button = SoftButton(bottom, "Ⅱ   Tạm dừng", self.toggle_pause, width=160, align="left")
        self.pause_button.pack(fill="x", pady=(0, 4))
        self.settings_button = SoftButton(bottom, "⚙   Cài đặt", self.settings, width=160, align="left")
        self.settings_button.pack(fill="x")
        outer = tk.Frame(root, bg=BG)
        outer.grid(row=0, column=1, sticky="nsew")
        body = tk.Frame(outer, bg=BG, padx=26, pady=28)
        body.place(relx=.5, rely=.5, anchor="center", width=1092, height=780)
        outer.bind("<Configure>", lambda event: body.place_configure(width=min(1160, event.width), height=min(860, event.height)))
        controls = tk.Frame(body, bg=BG)
        controls.pack(fill="x", pady=(0, 18))
        self.back_button = SoftButton(controls, text="‹  Ứng dụng", command=self.go_back, width=118, height=36, fill="#303030")
        self.heading = label(controls, "Monitoring", size=15, bold=True)
        self.heading.pack(side="left")
        date_controls = tk.Frame(controls, bg=BG)
        self.date_controls = date_controls
        date_controls.pack(side="right")
        ChevronButton(date_controls, command=lambda: self.change_day(-1)).pack(side="left", padx=(0, 6))
        self.day = tk.StringVar(value=date.today().isoformat())
        self.date_button = DateButton(date_controls, command=self.show_calendar)
        self.date_button.pack(side="left")
        self.next_button = ChevronButton(date_controls, command=lambda: self.change_day(1), direction="right")
        self.next_button.pack(side="left", padx=(6, 0))
        main = Surface(body)
        main.pack(fill="both", expand=True)
        self.overview_panel = tk.Frame(main.inner, bg=CARD)
        self.overview_panel.pack(fill="both", expand=True)
        self.overview_panel.columnconfigure(1, weight=1)
        self.overview_panel.rowconfigure(0, weight=1)
        chart_panel = tk.Frame(self.overview_panel, bg=CARD, width=310)
        chart_panel.grid(row=0, column=0, sticky="ns", padx=(0, 22))
        chart_panel.grid_propagate(False)
        chart_panel.rowconfigure(0, weight=1)
        chart_panel.rowconfigure(2, weight=1)
        chart_content = tk.Frame(chart_panel, bg=CARD)
        chart_content.grid(row=1, column=0)
        self.donut = Donut(chart_content, self.control_tracking)
        self.donut.pack()
        self.chart_caption = label(chart_content, "Tỷ lệ sử dụng ứng dụng", color=MUTED)
        self.chart_caption.pack(pady=(9, 0))
        right = tk.Frame(self.overview_panel, bg=CARD)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        list_header = tk.Frame(right, bg=CARD)
        list_header.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        self.list_title = label(list_header, "Ứng dụng", size=12, bold=True)
        self.list_title.pack(side="left", pady=4)
        self.search = tk.StringVar()
        self.search_field = SearchField(list_header, self.search, width=200)
        self.search_field.pack(side="right")
        self.search.trace_add("write", lambda *_: self.render_table())
        table = tk.Frame(right, bg=CARD)
        table.grid(row=1, column=0, sticky="nsew")
        self.tree = UsageTable(table)
        self.scroll = RoundedScrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y", padx=(5, 0))
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<ButtonRelease-1>", self.open_row)
        self.tree.bind("<Return>", self.open_row)
        self.list_hint = label(right, "Chọn trình duyệt để xem từng website  ›", color=MUTED, size=9, anchor="w")
        self.list_hint.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self.comparison = ComparisonView(main.inner, self.row_image)
        self.limits_view = LimitsView(main.inner, limits, self.row_image) if limits is not None else None
        self.notice_label = label(body, textvariable=self.notice, size=9, color="#EFB76C", anchor="w")
        self.notice.trace_add("write", lambda *_: self.update_notice())
        root.protocol("WM_DELETE_WINDOW", self.close)
        install_dark_frame(root)
        self.refresh()
        self.after_id = root.after(2000, self.periodic)

    def periodic(self):
        try:
            if self.root.state() == "iconic":
                self.collector.status()
            else:
                self.refresh()
        except Exception as exc:
            self.notice.set(str(exc))
        self.after_id = self.root.after(2000, self.periodic)

    def update_notice(self):
        if self.notice.get():
            self.notice_label.pack(fill="x", pady=(10, 0))
        else:
            self.notice_label.pack_forget()

    def selected_day(self):
        try:
            selected = date.fromisoformat(self.day.get())
            if selected > date.today():
                raise ValueError()
            return selected.isoformat()
        except ValueError:
            self.notice.set("Ngày không hợp lệ hoặc lớn hơn hôm nay.")
            return None

    def show_calendar(self):
        if self.calendar_popup and self.calendar_popup.winfo_exists():
            self.calendar_popup.dismiss()
            return
        selected = self.selected_day()
        if selected:
            self.calendar_popup = CalendarPopup(self.root, self.date_button, date.fromisoformat(selected), self.select_day)

    def select_day(self, value):
        self.day.set(value.isoformat())
        self.refresh()

    def change_day(self, delta):
        if self.page in {"comparison", "limits"}:
            return
        selected = self.selected_day()
        if selected:
            self.day.set(min(date.today(), date.fromisoformat(selected)+timedelta(days=delta)).isoformat())
            self.refresh()

    def refresh(self):
        state = self.collector.status()
        self.app_status.set("●  Đã tạm dừng" if state["paused"] else "●  Đang theo dõi")
        self.pause_button.configure(text="▶   Tiếp tục" if state["paused"] else "Ⅱ   Tạm dừng")
        if state["paused"] and not state.get("has_started", True):
            self.app_status.set("●  Chưa bắt đầu")
            self.pause_button.configure(text="▶   Bắt đầu")
        self.donut.set_tracking_state(state["paused"], state.get("has_started", True))
        self.notice.set(state["error"])
        for button, page in ((self.overview_button, "overview"), (self.comparison_button, "comparison"), (self.limits_button, "limits")):
            button.fill = "#2B2B2B" if self.page == page else SIDEBAR
            button.paint()
        if self.page == "limits":
            self.heading.configure(text="Giới hạn ứng dụng")
            self.date_controls.pack_forget()
            self.back_button.pack_forget()
            self.overview_panel.pack_forget()
            self.comparison.pack_forget()
            self.limits_view.pack(fill="both", expand=True)
            if self.limits_icon_revision != self.icons.revision:
                self.limits_view.signature = None
                self.limits_icon_revision = self.icons.revision
            self.limits_view.refresh()
            return
        if self.limits_view is not None:self.limits_view.pack_forget()
        selected = date.today().isoformat() if self.page == "comparison" else self.selected_day()
        if not selected:
            return
        self.date_button.configure(text=date.fromisoformat(selected).strftime("%d/%m/%Y"))
        self.next_button.configure(state="disabled" if selected == date.today().isoformat() else "normal")
        if self.page == "comparison":
            self.date_controls.pack_forget()
            start, selected = trailing_week(date.today())
            self.rows = self.store.report(start, selected)
        else:
            self.date_controls.pack(side="right")
            self.rows = self.store.report(selected)
        self.prepare_icons()
        if self.page == "comparison":
            self.heading.configure(text="Thống kê")
            self.back_button.pack_forget()
            self.overview_panel.pack_forget()
            self.comparison.pack(fill="both", expand=True)
            ranked, total = rank_apps(self.rows)
            self.comparison.set_data(ranked, total, self.icons.revision, period=(start, selected))
            return
        self.comparison.pack_forget()
        self.overview_panel.pack(fill="both", expand=True)
        if self.browser:
            app_row = next((r for r in self.rows if r["kind"] == "app" and r["name"] == self.browser), dict(name=self.browser, screen=0., kind="app", visible=0., covered=0., visibility_known=False))
            self.view_rows, self.total = browser_detail(app_row, self.store.browser_report(selected, self.browser))
            self.heading.configure(text=display_name(self.browser))
            self.list_title.configure(text="Website")
            self.chart_caption.configure(text="Thời gian bên trong trình duyệt")
            self.list_hint.configure(text="Tên miền được ghi trực tiếp, ví dụ facebook.com.")
        else:
            self.view_rows, self.total = overview(self.rows)
            self.heading.configure(text="Monitoring")
            self.list_title.configure(text="Ứng dụng")
            self.chart_caption.configure(text="Tỷ lệ sử dụng ứng dụng")
            self.list_hint.configure(text="Chọn trình duyệt để xem từng website  ›")
        if self.browser and not self.back_button.winfo_manager():
            self.back_button.pack(side="left", before=self.heading, padx=(0, 12))
        elif not self.browser:
            self.back_button.pack_forget()
        self.donut.update_chart(self.view_rows, self.total)
        self.render_table()

    def prepare_icons(self):
        apps = sorted((r for r in self.rows if r["kind"] == "app"), key=lambda r: (-r["screen"], r["name"]))[:128]
        names = {r["name"] for r in apps}
        now = time.monotonic()
        if names != self.icon_requested or now-self.icon_check_at >= 30:
            self.icon_paths = {k: v for k, v in self.icon_paths.items() if k in names}
            loader = getattr(self.collector, "app_paths", None)
            if loader:
                try:
                    self.icon_paths.update(loader(sorted(names)))
                except (RuntimeError, OSError):
                    pass
            self.icon_requested = names
            self.icon_check_at = now

    def row_image(self, row):
        color = row["color"]
        color_dot = row.get("color_dot", True)
        if row["kind"] == "app" and row.get("path"):
            picture = self.icons.get(row["name"], row["path"])
        else:
            picture = self.icons.get(row["name"], self.icon_paths.get(row["name"])) if row["kind"] == "app" and row["name"] in self.icon_requested else None
        key = ((row.get("path", row["name"]), id(picture), color, color_dot) if picture is not None
               else (row["kind"] == "app", color, color_dot))
        if key not in self.photos:
            image = Image.new("RGBA", (36*3, 24*3))
            draw = ImageDraw.Draw(image)
            if row["kind"] == "app":
                if picture is None:
                    draw.rounded_rectangle((3, 9, 57, 63), radius=10, outline="#B1B1B1", width=4)
                    draw.line((6, 24, 54, 24), fill="#B1B1B1", width=3)
                else:
                    image.alpha_composite(picture.resize((60, 60), Image.Resampling.LANCZOS), (0, 6))
                if color_dot:
                    draw.ellipse((87, 27, 105, 45), fill=color)
            else:
                draw.ellipse((36, 24, 60, 48), fill=color)
            self.photos[key] = ImageTk.PhotoImage(image.resize((36, 24), Image.Resampling.LANCZOS), master=self.root)
            while len(self.photos) > 160:
                self.photos.popitem(last=False)
        self.photos.move_to_end(key)
        return self.photos[key]

    def render_table(self):
        query = self.search.get().strip().lower()
        visible = [r for r in self.view_rows if query in r["name"].lower() or (r["kind"] == "app" and query in display_name(r["name"]).lower())]
        signature = tuple((r["kind"], r["name"], int(r["screen"]), r["color"]) for r in visible), round(self.total, 1), self.icons.revision
        if signature == self.tree_signature:
            return
        self.tree_signature = signature
        self.ids.clear()
        self.visible_photos.clear()
        for row in visible:
            key = f"{row['kind']}:{row['name']}"
            self.ids[key] = row
        self.tree.set_data(visible, self.total, self.row_image)
        if not visible:
            self.list_hint.configure(text="Không có kết quả." if query else "Chưa có dữ liệu trong ngày này.")
        else:
            self.list_hint.configure(text="Tên miền được ghi trực tiếp, ví dụ facebook.com." if self.browser else "Chọn trình duyệt để xem từng website  ›")

    def open_row(self, event):
        iid = self.tree.identify_row(event.y) if event.type != tk.EventType.KeyPress else next(iter(self.tree.selection()), "")
        row = self.ids.get(iid)
        if row and not self.browser and row["name"] in BROWSER_NAMES:
            self.browser = row["name"]
            self.search.set("")
            self.tree_signature = None
            self.refresh()

    def go_back(self):
        self.browser = None
        self.search.set("")
        self.tree_signature = None
        self.refresh()

    def show_page(self, page):
        if page not in {"overview", "comparison", "limits"} or (page == "limits" and self.limits_view is None):
            return
        self.page = page
        self.donut.set_hover(False)
        if self.calendar_popup and self.calendar_popup.winfo_exists():
            self.calendar_popup.dismiss()
        if page == "overview":
            self.browser = None
            self.search.set("")
            self.tree_signature = None
        self.refresh()

    def toggle_pause(self):
        try:
            self.collector.set_paused(not self.collector.status()["paused"])
            self.refresh()
        except Exception as exc:
            self.notice.set(str(exc))

    def control_tracking(self, action):
        try:
            if action not in Donut.ACTIONS:
                return
            paused = action == "pause"
            if self.collector.status()["paused"] != paused:
                self.collector.set_paused(paused)
            self.refresh()
        except Exception as exc:
            self.notice.set(str(exc))

    def settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        dialog = tk.Toplevel(self.root)
        self.settings_window = dialog
        dialog.title("Cài đặt")
        dialog.geometry("630x475")
        dialog.transient(self.root)
        dialog.configure(bg=BG)
        panel = tk.Frame(dialog, bg=BG, padx=26, pady=22)
        panel.pack(fill="both", expand=True)
        label(panel, "Cài đặt", size=20, bold=True).pack(anchor="w", pady=(0, 18))
        label(panel, "Nhận diện không thao tác", bold=True).pack(anchor="w")
        options, times = ("1 phút", "3 phút", "5 phút", "15 phút", "Tắt"), (60, 180, 300, 900, 0)
        choice = ttk.Combobox(panel, state="readonly", values=options)
        choice.current(times.index(self.collector.idle_threshold))
        choice.pack(fill="x", pady=(8, 0))
        label(panel, "Thời gian không thao tác vẫn nằm trong thời gian focus.", color=MUTED, size=9).pack(anchor="w", pady=(8, 20))
        label(panel, "Kết nối trình duyệt", bold=True).pack(anchor="w")
        label(panel, "Mã ghép nối cho tiện ích browser-extension đi kèm.", color=MUTED, size=9).pack(anchor="w", pady=(6, 8))
        token = ttk.Entry(panel)
        token.insert(0, self.bridge.token)
        token.configure(state="readonly")
        token.pack(fill="x")
        def copy():
            self.root.clipboard_clear()
            self.root.clipboard_append(self.bridge.token)
        SoftButton(panel, text="Sao chép mã", command=copy, width=135, fill="#333333").pack(anchor="w", pady=(8, 17))
        label(panel, "Dữ liệu: " + str(self.store.path), color=MUTED, size=8, wraplength=570, justify="left").pack(anchor="w")
        if self.bridge.error:
            label(panel, "Chưa mở được kết nối tiện ích. Bộ thu thập ứng dụng vẫn chạy.", color="#EFB76C", size=8).pack(anchor="w", pady=5)
        def save():
            try:
                self.collector.set_idle(times[choice.current()])
                dialog.destroy()
            except Exception as exc:
                self.notice.set(str(exc))
        SoftButton(panel, text="Xong", command=save, width=92, fill="#383838").pack(anchor="e", pady=(18, 0))
        install_dark_frame(dialog)

    def close(self):
        if self.limits_view is not None:self.limits_view.close_dialog()
        self.icons.close()
        self.root.after_cancel(self.after_id)
        self.root.destroy()
