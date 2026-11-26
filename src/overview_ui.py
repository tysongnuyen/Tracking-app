"""A compact, virtual app list for the monitoring dashboard."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont

from dashboard_model import BROWSER_NAMES, display_name, duration
from ui_widgets import CARD, INK, MUTED, ROW_FILL, ROW_BORDER, ROW_HOVER, row_card


class UsageTable(tk.Canvas):
    """Keep rows as metadata and create pictures only for the visible viewport.

    The small selection API is shared with the dashboard's browser drilldown.
    A manually scrolled viewport keeps the heading fixed without extra widgets.
    """

    HEADER_HEIGHT = 36
    ROW_HEIGHT = 54
    ROW_PITCH = 62

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("bg", CARD)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("takefocus", True)
        kwargs.setdefault("width", 430)
        kwargs.setdefault("height", 300)
        super().__init__(parent, **kwargs)
        self.rows = ()
        self.row_ids = ()
        self._indices = {}
        self.row_images = []
        self.total = 0.
        self.image_provider = None
        self._selected = ""
        self._hovered = ""
        self._offset = 0.
        self._scroll_command = None
        self._draw_pending = None
        self._name_font = tkfont.Font(self, family="Segoe UI", size=10)
        self._value_font = tkfont.Font(self, family="Segoe UI", size=10)
        self._head_font = tkfont.Font(self, family="Segoe UI", size=9)
        self._time_width = self._value_font.measure("0 giây")
        self.bind("<Configure>", self._resized)
        self.bind("<ButtonPress-1>", self._pressed)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self.bind("<FocusIn>", lambda _: self._schedule_draw())
        self.bind("<FocusOut>", lambda _: self._schedule_draw())
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda _: self.yview_scroll(-1, "units"))
        self.bind("<Button-5>", lambda _: self.yview_scroll(1, "units"))
        for key in ("Up", "Down", "Home", "End", "Prior", "Next"):
            self.bind(f"<{key}>", self._key)
        self.bind("<Destroy>", self._destroyed, add="+")

    def set_data(self, rows, total, image_provider=None):
        self.rows = tuple(dict(row) for row in rows)
        self.row_ids = tuple(f"{row['kind']}:{row['name']}" for row in self.rows)
        self._indices = {iid: index for index, iid in enumerate(self.row_ids)}
        self.total = max(0., float(total))
        self.image_provider = image_provider
        # The greatest duration need not be the widest label: minute/second
        # labels can be wider than hour/minute labels in the same report.
        durations = {duration(row["screen"]) for row in self.rows} or {"0 giây"}
        self._time_width = max(self._value_font.measure(text) for text in durations)
        if self._selected not in self._indices:
            self._selected = ""
        if self._hovered not in self._indices:
            self._hovered = ""
        self._clamp_offset()
        self._notify_scroll()
        self._schedule_draw()

    def get_children(self, item=None):
        return self.row_ids

    def selection(self):
        return (self._selected,) if self._selected else ()

    def selection_set(self, iid):
        if isinstance(iid, (tuple, list)):
            iid = iid[0] if iid else ""
        self._selected = iid if iid in self._indices else ""
        self._schedule_draw()

    def identify_row(self, y):
        y = float(y)
        if y < self.HEADER_HEIGHT or y >= self.winfo_height():
            return ""
        logical = y-self.HEADER_HEIGHT+self._offset
        index = int(logical // self.ROW_PITCH)
        if (0 <= index < len(self.rows)
                and logical-index*self.ROW_PITCH < self.ROW_HEIGHT):
            return self.row_ids[index]
        return ""

    def item(self, iid, option=None):
        """Read values for the dashboard's small inspection/self-check API."""
        row = self.rows[self._indices[iid]]
        name = display_name(row["name"]) if row["kind"] == "app" else row["name"]
        share = row["screen"] / self.total * 100 if self.total else 0.
        values = (name, duration(row["screen"]), f"{share:.1f}%",
                  "›" if self._is_browser(row) else "")
        return values if option == "values" else {"values": values}

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None
        if "yscrollcommand" in kwargs:
            self._scroll_command = kwargs.pop("yscrollcommand")
            if hasattr(self, "rows"):
                self._notify_scroll()
        return super().configure(cnf, **kwargs) if cnf is not None or kwargs else None

    config = configure

    def _viewport_height(self):
        return max(1, self.winfo_height()-self.HEADER_HEIGHT)

    def _content_height(self):
        return max(0, len(self.rows)*self.ROW_PITCH-(self.ROW_PITCH-self.ROW_HEIGHT))

    def _clamp_offset(self):
        maximum = max(0., self._content_height()-self._viewport_height())
        self._offset = min(maximum, max(0., self._offset))

    def yview(self, *args):
        if not args:
            height = self._content_height()
            if height <= self._viewport_height():
                return 0., 1.
            return self._offset/height, min(1., (self._offset+self._viewport_height())/height)
        if args[0] == "moveto":
            self.yview_moveto(float(args[1]))
        elif args[0] == "scroll":
            self.yview_scroll(int(args[1]), args[2])

    def yview_moveto(self, fraction):
        self._move_to(float(fraction)*self._content_height())

    def yview_scroll(self, number, what):
        step = self._viewport_height()*.9 if what == "pages" else self.ROW_PITCH
        self._move_to(self._offset+int(number)*step)

    def _move_to(self, offset):
        self._offset = offset
        self._clamp_offset()
        self._hovered = ""
        self._notify_scroll()
        self._schedule_draw()

    def _notify_scroll(self):
        if self._scroll_command:
            values = self.yview()
            if callable(self._scroll_command):
                self._scroll_command(*values)
            else:
                self.tk.call(self._scroll_command, *values)

    def _resized(self, _):
        self._clamp_offset()
        self._notify_scroll()
        self._schedule_draw()

    def _pressed(self, event):
        self.focus_set()
        self.selection_set(self.identify_row(event.y))

    def _motion(self, event):
        iid = self.identify_row(event.y)
        if iid != self._hovered:
            self._hovered = iid
            row = self.rows[self._indices[iid]] if iid else None
            self.configure(cursor="hand2" if row and self._is_browser(row) else "")
            self._schedule_draw()

    def _leave(self, _):
        self._hovered = ""
        self.configure(cursor="")
        self._schedule_draw()

    def _wheel(self, event):
        steps = -int(event.delta/120)
        if steps == 0 and event.delta:
            steps = -1 if event.delta > 0 else 1
        self.yview_scroll(steps, "units")
        return "break"

    def _key(self, event):
        if not self.rows:
            return "break"
        index = self._indices.get(self._selected, -1)
        page = max(1, int(self._viewport_height()/self.ROW_PITCH))
        target = {"Home": 0, "End": len(self.rows)-1,
                  "Up": max(0, index-1), "Down": index+1,
                  "Prior": max(0, index-page), "Next": max(0, index)+page}[event.keysym]
        target = min(len(self.rows)-1, target)
        self.selection_set(self.row_ids[target])
        top = target*self.ROW_PITCH
        bottom = top+self.ROW_HEIGHT
        if top < self._offset:
            self._move_to(top)
        elif bottom > self._offset+self._viewport_height():
            self._move_to(bottom-self._viewport_height())
        return "break"

    @staticmethod
    def _is_browser(row):
        return row["kind"] == "app" and row["name"] in BROWSER_NAMES

    @staticmethod
    def _fit(text, font, available):
        if available <= 0:
            return ""
        if font.measure(text) <= available:
            return text
        ellipsis = "…"
        if font.measure(ellipsis) > available:
            return ""
        left, right = 0, len(text)
        while left < right:
            middle = (left+right+1)//2
            if font.measure(text[:middle]+ellipsis) <= available:
                left = middle
            else:
                right = middle-1
        return text[:left].rstrip()+ellipsis

    def _schedule_draw(self):
        if self._draw_pending is None:
            self._draw_pending = self.after_idle(self._draw)

    def _draw(self):
        self._draw_pending = None
        self.delete("rows", "rowchrome", "header")
        self.row_images.clear()
        width = self.winfo_width()
        if width <= 2:
            return
        # Reserve numeric columns before sizing the app label. This also keeps
        # long domain names away from durations at the minimum window width.
        detail_x, share_x = width-15, width-34
        share_width = self._value_font.measure("100.0%")
        time_x = share_x-share_width-18
        time_width = self._time_width
        name_x = 56
        available_name = time_x-time_width-16-name_x
        if available_name < 60:
            # Extra-small embedded views keep a short duration but retain the
            # same full value through item(..., 'values').
            time_width = max(60, min(time_width, int(width*.30)))
            available_name = time_x-time_width-14-name_x
        first = max(0, int(self._offset//self.ROW_PITCH))
        last = min(len(self.rows), int(math.ceil((self._offset+self._viewport_height())/self.ROW_PITCH)))
        for index in range(first, last):
            row, iid = self.rows[index], self.row_ids[index]
            top = self.HEADER_HEIGHT+index*self.ROW_PITCH-self._offset
            selected = iid == self._selected
            hovered = iid == self._hovered
            fill = "#343434" if selected else ROW_HOVER if hovered else ROW_FILL
            outline = "#747474" if selected else "#5B5B5B" if hovered else ROW_BORDER
            row_card(self, 1, top, width-2, self.ROW_HEIGHT,
                     fill=fill, outline=outline, radius=12)
            middle = top+self.ROW_HEIGHT/2
            photo = self.image_provider(row) if self.image_provider else None
            if photo is not None:
                self.row_images.append(photo)
                self.create_image(14, middle, image=photo, anchor="w", tags="rows")
            else:
                self.create_oval(23, middle-4, 31, middle+4,
                                 fill=row.get("color", MUTED), outline="", tags="rows")
            name = display_name(row["name"]) if row["kind"] == "app" else row["name"]
            self.create_text(name_x, middle, text=self._fit(name, self._name_font, available_name),
                             fill=INK, font=self._name_font, anchor="w", tags="rows")
            self.create_text(time_x, middle,
                             text=self._fit(duration(row["screen"]), self._value_font, time_width),
                             fill=INK, font=self._value_font, anchor="e", tags="rows")
            share = row["screen"]/self.total*100 if self.total else 0.
            self.create_text(share_x, middle, text=f"{share:.1f}%", fill=MUTED,
                             font=self._value_font, anchor="e", tags="rows")
            if self._is_browser(row):
                self.create_text(detail_x, middle-1, text="›", fill=MUTED,
                                 font=("Segoe UI", 16), tags="rows")
        self.create_rectangle(0, 0, width, self.HEADER_HEIGHT-1,
                              fill=self.cget("bg"), outline="", tags="header")
        name_heading = "Website" if self.rows and self.rows[0]["kind"] != "app" else "Ứng dụng"
        for text, x, anchor in ((name_heading, 14, "w"), ("Thời gian", time_x, "e"),
                                ("Tỷ lệ", share_x, "e")):
            self.create_text(x, 14, text=text, fill=MUTED, font=self._head_font,
                             anchor=anchor, tags="header")

    def _destroyed(self, event):
        if event.widget == self and self._draw_pending is not None:
            self.after_cancel(self._draw_pending)
            self._draw_pending = None
