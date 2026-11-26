"""Searchable local app selection, loaded only while the dashboard is open."""
from __future__ import annotations

import ntpath
import queue
import threading
import tkinter as tk
from tkinter import font

from ui_widgets import (CARD, INK, MUTED, ROW_FILL, ROW_HOVER, RoundedScrollbar,
                        SearchField, SoftButton, row_card)


class AppPicker(tk.Frame):
    ROW_HEIGHT = 59

    def __init__(self, parent, on_select, image_provider=None, catalog_provider=None):
        super().__init__(parent, bg=CARD)
        self.on_select, self.image_provider = on_select, image_provider
        self.catalog_provider = catalog_provider
        self.apps, self.visible_apps, self.photos = [], [], []
        self.selected_app = None
        self.hovered = None
        self.loading, self.closed = False, False
        self.result_queue = queue.Queue(maxsize=1)
        self.query = tk.StringVar(self)
        self.status = tk.StringVar(self, "Đang tìm ứng dụng trên máy…")
        self.name_font = font.Font(self, family="Segoe UI", size=11)
        header = tk.Frame(self, bg=CARD)
        header.pack(fill="x", pady=(0, 8))
        self.search = SearchField(header, self.query, width=370)
        self.search.pack(side="left", fill="x", expand=True)
        self.reload_button = SoftButton(header, "Làm mới", self.reload, width=88,
                                        height=38, fill="#353535")
        self.reload_button.pack(side="right", padx=(10, 0))
        table = tk.Frame(self, bg=CARD)
        table.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(table, height=260, bg=CARD, highlightthickness=0,
                                takefocus=True, yscrollincrement=20)
        self.scroll = RoundedScrollbar(table, self.canvas.yview)
        self.scroll.pack(side="right", fill="y", padx=(5, 0))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scrolled)
        tk.Label(self, textvariable=self.status, bg=CARD, fg=MUTED, anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x", pady=(8, 0))
        self.canvas.bind("<Configure>", lambda _: self.layout())
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Button-1>", self.clicked)
        self.canvas.bind("<Motion>", self.motion)
        self.canvas.bind("<Leave>", lambda _: self.motion())
        self.canvas.bind("<Up>", lambda _: self.move(-1))
        self.canvas.bind("<Down>", lambda _: self.move(1))
        self.search.entry.bind("<Down>", lambda _: self.move(1))
        self.trace_id = self.query.trace_add("write", self.filter_changed)
        self.bind("<Destroy>", self.destroyed, add="+")
        self.poll_id = self.after(100, self.poll)
        self.reload()

    def reload(self):
        if self.loading or self.closed:
            return
        self.loading = True
        self.status.set("Đang tìm ứng dụng trên máy…")
        self.reload_button.configure(state="disabled")
        # The worker never touches Tk and exits after this on-demand discovery.
        provider = self.catalog_provider
        def discover():
            try:
                if provider is None:
                    from app_catalog import discover_apps
                    rows = discover_apps()
                else:
                    rows = provider()
                result = (rows, "")
            except Exception:
                result = ([], "Chưa đọc được danh sách ứng dụng. Bấm Làm mới để thử lại.")
            self.result_queue.put_nowait(result)
        threading.Thread(target=discover, daemon=True, name="AppCatalogue").start()

    def poll(self):
        self.poll_id = None
        if self.closed:
            return
        try:
            rows, error = self.result_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            self.loading = False
            self.reload_button.configure(state="normal")
            self.apps = rows
            if self.selected_app:
                selected = next((r for r in rows if r["path"] == self.selected_app["path"]), None)
                self.selected_app = selected
                self.on_select(selected)
            self.filter_changed()
            if error:
                self.status.set(error)
        self.draw_rows()  # Refresh placeholders as the shared icon worker completes.
        self.poll_id = self.after(100 if self.loading else 500, self.poll)

    def filter_changed(self, *_):
        query = self.query.get().strip().casefold()
        self.visible_apps = [r for r in self.apps if query in r["name"].casefold()
                             or query in ntpath.basename(r["path"]).casefold()]
        if not self.loading:
            self.status.set(f"{len(self.visible_apps)} ứng dụng · Chọn app để đặt giới hạn."
                            if self.apps else "Chưa thấy app? Mở ứng dụng rồi bấm Làm mới.")
        self.canvas.yview_moveto(0)
        self.layout()

    def layout(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.configure(scrollregion=(0, 0, w, max(h, len(self.visible_apps)*self.ROW_HEIGHT)))
        self.draw_rows()

    def scrolled(self, first, last):
        self.scroll.set(first, last)
        self.draw_rows()

    def wheel(self, event):
        self.canvas.yview_scroll(-int(event.delta/120)*3, "units")
        return "break"

    def clicked(self, event):
        self.canvas.focus_set()
        index = int(self.canvas.canvasy(event.y)//self.ROW_HEIGHT)
        if 0 <= index < len(self.visible_apps):
            self.select(self.visible_apps[index])

    def motion(self, event=None):
        index = int(self.canvas.canvasy(event.y)//self.ROW_HEIGHT) if event else -1
        hovered = index if 0 <= index < len(self.visible_apps) else None
        self.canvas.configure(cursor="hand2" if hovered is not None else "")
        if hovered != self.hovered:
            self.hovered = hovered
            self.draw_rows()

    def select(self, app):
        self.selected_app = app
        self.on_select(app)
        self.draw_rows()

    def move(self, step):
        if not self.visible_apps:
            return "break"
        index = next((i for i, row in enumerate(self.visible_apps)
                      if self.selected_app and row["path"] == self.selected_app["path"]),
                     -1 if step > 0 else len(self.visible_apps))
        index = max(0, min(len(self.visible_apps)-1, index+step))
        self.select(self.visible_apps[index])
        top, height = self.canvas.canvasy(0), self.canvas.winfo_height()
        y = index*self.ROW_HEIGHT
        if y < top or y+self.ROW_HEIGHT > top+height:
            self.canvas.yview_moveto(max(0, y-height+self.ROW_HEIGHT if y > top else y)
                                     / max(height, len(self.visible_apps)*self.ROW_HEIGHT))
        self.canvas.focus_set()
        return "break"

    def draw_rows(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        top = self.canvas.canvasy(0)
        first = max(0, int(top//self.ROW_HEIGHT))
        last = min(len(self.visible_apps), int((top+h)//self.ROW_HEIGHT)+2)
        self.canvas.delete("rows")
        self.canvas.delete("rowchrome")
        self.photos.clear()
        if not self.visible_apps:
            self.canvas.create_text(w/2, max(40, h/2),
                text="Đang tải…" if self.loading else "Không tìm thấy ứng dụng." if self.query.get().strip()
                     else "Ứng dụng trên máy sẽ xuất hiện tại đây.",
                fill=MUTED, font=("Segoe UI", 10), tags="rows")
        for index in range(first, last):
            row, y = self.visible_apps[index], index*self.ROW_HEIGHT
            selected = self.selected_app and self.selected_app["path"] == row["path"]
            hovered = index == self.hovered
            row_card(self.canvas, 1, y+2, w-3, self.ROW_HEIGHT-7,
                     fill="#323C36" if selected else ROW_HOVER if hovered else ROW_FILL,
                     outline="#6E8D79" if selected else "#626262" if hovered else "#414141")
            if self.image_provider:
                photo = self.image_provider(dict(kind="app", name=ntpath.basename(row["path"]),
                                                  path=row["path"], color="#82AFFF", color_dot=False))
                self.photos.append(photo)
                self.canvas.create_image(13, y+13, image=photo, anchor="nw", tags="rows")
            else:
                self.canvas.create_text(30, y+27, text="▣", fill=MUTED,
                                        font=("Segoe UI", 19), tags="rows")
            text, available = row["name"], max(40, w-100)
            while text and self.name_font.measure(text+"…") > available:
                text = text[:-1]
            if text != row["name"]:
                text += "…"
            self.canvas.create_text(63, y+27, text=text, fill=INK, anchor="w",
                                    font=self.name_font, tags="rows")
            if selected:
                self.canvas.create_text(w-22, y+27, text="✓", fill="#9DD5B2",
                                        font=("Segoe UI", 14, "bold"), tags="rows")

    def destroyed(self, event):
        if event.widget == self:
            self.closed = True
            if self.poll_id is not None:
                self.after_cancel(self.poll_id)
                self.poll_id = None
            self.query.trace_remove("write", self.trace_id)
