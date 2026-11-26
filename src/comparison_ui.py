"""Seven-day application rankings and a pie chart with a virtualized legend."""
import colorsys
import tkinter as tk
from tkinter import font
from PIL import Image, ImageDraw, ImageTk
from dashboard_model import COLORS, OTHER, display_name, duration
from comparison_model import pie_parts
from ui_widgets import (CARD, INK, MUTED, ROW_FILL, ROW_HOVER, RoundedScrollbar,
                        SearchField, SoftButton, row_card)


MAX_PIE_PARTS = 128


def comparison_color(index):
    """Keep the familiar first colors; give ordinary app lists individual colors."""
    if index < len(COLORS):
        return COLORS[index]
    if index >= MAX_PIE_PARTS-1:
        return OTHER
    hue = ((index-len(COLORS))*.618033988749895+.06) % 1
    saturation = .42 if index % 2 else .58
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, .91)
    return f"#{round(red*255):02X}{round(green*255):02X}{round(blue*255):02X}"


def raster_pie(slices, size):
    """Draw at triple resolution so the circle and slice edges stay smooth."""
    scale = 3
    picture = Image.new("RGB", (size*scale, size*scale), CARD)
    draw = ImageDraw.Draw(picture)
    inset = min(2, size/4)*scale
    bounds = (inset, inset, size*scale-inset-1, size*scale-inset-1)
    for part in slices:
        if len(slices) == 1:
            draw.ellipse(bounds, fill=part["color"])
        elif part["extent"] > 0:
            draw.pieslice(bounds, part["start"], part["start"]+part["extent"],
                          fill=part["color"], outline=CARD, width=scale)
    return picture.resize((size, size), Image.Resampling.LANCZOS)


class ComparisonView(tk.Frame):
    ROW_HEIGHT = 78
    LEGEND_ROW_HEIGHT = 68

    def __init__(self, parent, image_provider):
        super().__init__(parent, bg=CARD)
        self.image_provider = image_provider
        self.ranked_rows, self.visible_rows = [], []
        self.pie_rows, self.legend_rows = [], []
        self.other_expanded = False
        self.legend_query = None
        self.total = 0.
        self.period = None
        self.signature = None
        self.draw_signature = None
        self.pie_draw_signature = None
        self.pie_slices = []
        self.pie_picture = None
        self.legend_draw_signature = None
        self.row_images = []
        self.legend_images = []
        self.mode = "ranking"
        self.hovered_rank = self.hovered_legend = None
        self.name_font = font.Font(self, family="Segoe UI", size=11)
        self.query = tk.StringVar()
        header = tk.Frame(self, bg=CARD)
        header.pack(fill="x", pady=(0, 8))
        tk.Label(header, text="Thống kê ứng dụng · 7 ngày", bg=CARD, fg=INK,
                 font=("Segoe UI", 12, "bold")).pack(side="left")
        SearchField(header, self.query, width=200).pack(side="right")
        modes = tk.Frame(self, bg=CARD)
        modes.pack(fill="x", pady=(0, 10))
        self.ranking_button = SoftButton(modes, "Xếp hạng", lambda: self.set_mode("ranking"),
                                         width=112, height=34, radius=17)
        self.ranking_button.pack(side="left")
        self.pie_button = SoftButton(modes, "Biểu đồ tròn", lambda: self.set_mode("pie"),
                                     width=132, height=34, radius=17)
        self.pie_button.pack(side="left", padx=(6, 0))
        self.summary = tk.StringVar()
        tk.Label(self, textvariable=self.summary, bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(0, 14))
        self.columns = tk.Canvas(self, height=30, bg=CARD, highlightthickness=0)
        self.columns.pack(fill="x", padx=(0, 19))
        self.columns.bind("<Configure>", lambda _: self.draw_headers())
        self.table = tk.Frame(self, bg=CARD)
        self.table.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(self.table, bg=CARD, highlightthickness=0, takefocus=True,
                                yscrollincrement=20)
        self.scroll = RoundedScrollbar(self.table, self.canvas.yview)
        self.scroll.pack(side="right", fill="y", padx=(5, 0))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scrolled)
        self.canvas.bind("<Configure>", lambda _: self.layout_rows())
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta/120)*3, "units"))
        self.canvas.bind("<Button-1>", lambda _: self.canvas.focus_set())
        self.canvas.bind("<Motion>", self.rank_motion)
        self.canvas.bind("<Leave>", lambda _: self.rank_motion())
        for key, amount, unit in (("Up", -1, "units"), ("Down", 1, "units"),
                                  ("Prior", -1, "pages"), ("Next", 1, "pages")):
            self.canvas.bind(f"<{key}>", lambda event, a=amount, u=unit: self.canvas.yview_scroll(a, u))
        self.canvas.bind("<Home>", lambda _: self.canvas.yview_moveto(0))
        self.canvas.bind("<End>", lambda _: self.canvas.yview_moveto(1))
        self.pie_panel = tk.Frame(self, bg=CARD)
        self.pie_panel.grid_columnconfigure(0, weight=1, uniform="pie")
        self.pie_panel.grid_columnconfigure(1, weight=1, uniform="pie")
        self.pie_panel.grid_rowconfigure(0, weight=1)
        chart = tk.Frame(self.pie_panel, bg=CARD)
        chart.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.pie_canvas = tk.Canvas(chart, bg=CARD, highlightthickness=0, width=300, height=280)
        self.pie_canvas.pack(fill="both", expand=True)
        self.pie_canvas.bind("<Configure>", lambda _: self.draw_pie())
        self.pie_note = tk.StringVar()
        tk.Label(chart, textvariable=self.pie_note, bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9), wraplength=300, justify="center").pack(fill="x", pady=(8, 10))
        legend = tk.Frame(self.pie_panel, bg=CARD)
        legend.grid(row=0, column=1, sticky="nsew")
        tk.Label(legend, text="Ứng dụng · Thời gian · Tỷ lệ", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(2, 12))
        legend_list = tk.Frame(legend, bg=CARD)
        legend_list.pack(fill="both", expand=True)
        self.legend_canvas = tk.Canvas(legend_list, bg=CARD, highlightthickness=0,
                                        width=300, yscrollincrement=20, takefocus=True)
        self.legend_scroll = RoundedScrollbar(legend_list, self.legend_canvas.yview)
        self.legend_scroll.pack(side="right", fill="y", padx=(5, 0))
        self.legend_canvas.pack(side="left", fill="both", expand=True)
        self.legend_canvas.configure(yscrollcommand=self.legend_scrolled)
        self.legend_canvas.bind("<Configure>", lambda _: self.layout_legend())
        self.legend_canvas.bind("<MouseWheel>", lambda event: self.legend_canvas.yview_scroll(-int(event.delta/120)*3, "units"))
        self.legend_canvas.bind("<Button-1>", self.legend_clicked)
        self.legend_canvas.bind("<Motion>", self.legend_motion)
        self.legend_canvas.bind("<Leave>", lambda _: self.legend_motion())
        self.legend_canvas.bind("<Return>", lambda _: self.toggle_other())
        self.legend_canvas.bind("<space>", lambda _: self.toggle_other())
        for key, amount, unit in (("Up", -1, "units"), ("Down", 1, "units"),
                                  ("Prior", -1, "pages"), ("Next", 1, "pages")):
            self.legend_canvas.bind(f"<{key}>", lambda event, a=amount, u=unit: self.legend_canvas.yview_scroll(a, u))
        self.legend_canvas.bind("<Home>", lambda _: self.legend_canvas.yview_moveto(0))
        self.legend_canvas.bind("<End>", lambda _: self.legend_canvas.yview_moveto(1))
        self.trace_id = self.query.trace_add("write", self.filter_changed)
        self.bind("<Destroy>", self.cleanup)
        self.paint_modes()
        self.filter_changed()

    def paint_modes(self):
        for button, mode in ((self.ranking_button, "ranking"), (self.pie_button, "pie")):
            button.fill = "#424242" if self.mode == mode else "#2D2D2D"
            button.foreground = INK if self.mode == mode else MUTED
            button.paint()

    def set_mode(self, mode):
        if mode not in {"ranking", "pie"}:
            return
        self.mode = mode
        self.paint_modes()
        if mode == "pie":
            self.columns.pack_forget()
            self.table.pack_forget()
            self.pie_panel.pack(fill="both", expand=True)
            self.draw_pie()
            self.layout_legend()
        else:
            self.pie_panel.pack_forget()
            self.columns.pack(fill="x", padx=(0, 19))
            self.table.pack(fill="both", expand=True)
            self.layout_rows()
        self.filter_changed(reset_scroll=False)

    def set_data(self, rows, total, icon_revision=0, period=None):
        signature = tuple((r["name"], r["rank"], int(r["screen"]), round(r["share"], 2), r["color"],
                           r["screen"]/total*100 < 5 if total > 0 else False) for r in rows), int(total), icon_revision, period
        if signature == self.signature:
            return
        self.signature = signature
        self.ranked_rows = [dict(row, color=comparison_color(index)) for index, row in enumerate(rows)]
        if period != self.period:
            self.other_expanded = False
            self.legend_query = None
        self.total, self.period = total, period
        self.pie_rows = pie_parts(self.ranked_rows, total)
        self.filter_changed(reset_scroll=False)

    @staticmethod
    def matches(row, query):
        return query in row["name"].casefold() or query in display_name(row["name"]).casefold()

    def build_legend(self):
        query = self.query.get().strip().casefold()
        rows = []
        for part in self.pie_rows:
            if not part["is_other"]:
                if self.matches(part, query):
                    rows.append(dict(part, legend_kind="app"))
                continue
            group_match = bool(query) and self.matches(part, query)
            children = [row for row in part["children"] if group_match or self.matches(row, query)]
            if not children:
                continue
            expanded = self.other_expanded
            rows.append(dict(part, legend_kind="other", expanded=expanded))
            if expanded:
                rows.extend(dict(child, color=OTHER, legend_kind="child") for child in children)
        self.legend_rows = rows

    def toggle_other(self):
        if any(row["is_other"] for row in self.pie_rows):
            self.other_expanded = not self.other_expanded
            self.build_legend()
            self.legend_draw_signature = None
            self.layout_legend()
        return "break"

    def legend_at(self, y):
        index = int(self.legend_canvas.canvasy(y)//self.LEGEND_ROW_HEIGHT)
        return self.legend_rows[index] if 0 <= index < len(self.legend_rows) else None

    def legend_clicked(self, event):
        self.legend_canvas.focus_set()
        row = self.legend_at(event.y)
        if row and row["legend_kind"] == "other":
            self.toggle_other()

    def rank_motion(self, event=None):
        index = int(self.canvas.canvasy(event.y)//self.ROW_HEIGHT) if event else -1
        hovered = index if 0 <= index < len(self.visible_rows) else None
        if hovered != self.hovered_rank:
            self.hovered_rank = hovered
            self.draw_rows()

    def legend_motion(self, event=None):
        index = int(self.legend_canvas.canvasy(event.y)//self.LEGEND_ROW_HEIGHT) if event else -1
        hovered = index if 0 <= index < len(self.legend_rows) else None
        row = self.legend_rows[hovered] if hovered is not None else None
        self.legend_canvas.configure(cursor="hand2" if row and row["legend_kind"] == "other" else "")
        if hovered != self.hovered_legend:
            self.hovered_legend = hovered
            self.draw_legend()

    def filter_changed(self, *_, reset_scroll=True):
        query = self.query.get().strip().casefold()
        self.visible_rows = [r for r in self.ranked_rows if query in r["name"].casefold() or query in display_name(r["name"]).casefold()]
        if query and query != self.legend_query:
            if any(part["is_other"] and (self.matches(part, query)
                   or any(self.matches(row, query) for row in part["children"])) for part in self.pie_rows):
                self.other_expanded = True
        self.legend_query = query
        self.build_legend()
        matched_count = len(self.visible_rows)
        if self.mode == "pie" and query:
            names = {row["name"] for row in self.visible_rows}
            for row in self.pie_rows:
                if row["is_other"] and self.matches(row, query):
                    names.update(row["members"])
            matched_count = len(names)
        count = f"{matched_count}/{len(self.ranked_rows)}" if query else str(len(self.ranked_rows))
        period = ""
        if self.period:
            start, end = self.period
            period = f"{start[8:10]}/{start[5:7]}/{start[:4]} – {end[8:10]}/{end[5:7]}/{end[:4]}  ·  "
        self.summary.set(f"{period}{count} ứng dụng  ·  Tổng {duration(self.total)}")
        if reset_scroll:
            self.canvas.yview_moveto(0)
            self.legend_canvas.yview_moveto(0)
        self.draw_signature = None
        self.pie_draw_signature = None
        self.legend_draw_signature = None
        self.pie_note.set("Tỷ lệ trên tổng 7 ngày · Tìm kiếm chỉ lọc chú thích" if query
                          else "Tỷ lệ trên tổng thời gian sử dụng trong 7 ngày")
        if any(row["is_other"] for row in self.pie_rows):
            self.pie_note.set(self.pie_note.get()+"\nKhác: các app dưới 5% · Bấm Khác để xem danh sách")
        self.layout_rows()
        self.layout_legend()
        self.draw_pie()

    def draw_headers(self):
        w = self.columns.winfo_width()
        self.columns.delete("all")
        for x, title, anchor in ((20, "Hạng", "center"), (101, "Ứng dụng", "w"),
                                  (w-116, "Thời gian", "e"), (w-18, "Tỷ lệ", "e")):
            self.columns.create_text(x, 14, text=title, fill=MUTED, font=("Segoe UI", 9), anchor=anchor)

    def layout_rows(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.configure(scrollregion=(0, 0, w, max(h, len(self.visible_rows)*self.ROW_HEIGHT)))
        self.draw_rows()

    def scrolled(self, first, last):
        self.scroll.set(first, last)
        self.draw_rows()

    def fit_name(self, text, available):
        if self.name_font.measure(text) <= available:
            return text
        while text and self.name_font.measure(text+"…") > available:
            text = text[:-1]
        return text+"…" if text else "…"

    def draw_rows(self):
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        top = self.canvas.canvasy(0)
        first = max(0, int(top//self.ROW_HEIGHT))
        last = min(len(self.visible_rows), int((top+h)//self.ROW_HEIGHT)+2)
        signature = self.signature, self.query.get(), w, h, first, last, self.hovered_rank
        if signature == self.draw_signature:
            return
        self.draw_signature = signature
        self.canvas.delete("rows")
        self.canvas.delete("rowchrome")
        self.row_images.clear()
        if not self.visible_rows:
            self.canvas.create_text(w/2, max(50, h/2), text="Không tìm thấy ứng dụng." if self.query.get().strip() else "Chưa có dữ liệu sử dụng ứng dụng trong 7 ngày này.",
                                    fill=MUTED, font=("Segoe UI", 10), tags="rows")
            return
        for index in range(first, last):
            row = self.visible_rows[index]
            y = index*self.ROW_HEIGHT
            hovered = index == self.hovered_rank
            row_card(self.canvas, 0, y+2, w-2, self.ROW_HEIGHT-8,
                     fill=ROW_HOVER if hovered else ROW_FILL,
                     outline="#626262" if hovered else "#414141")
            self.canvas.create_text(20, y+25, text=str(row["rank"]), fill=INK if row["rank"] == 1 else MUTED,
                                    font=("Segoe UI", 12, "bold"), tags="rows")
            photo = self.image_provider(row)
            self.row_images.append(photo)
            self.canvas.create_image(50, y+13, image=photo, anchor="nw", tags="rows")
            self.canvas.create_text(101, y+25, text=self.fit_name(display_name(row["name"]), max(25, w-375)),
                                    fill=INK, font=self.name_font, anchor="w", tags="rows")
            self.canvas.create_text(w-116, y+25, text=duration(row["screen"]), fill=INK,
                                    font=("Segoe UI", 10), anchor="e", tags="rows")
            self.canvas.create_text(w-18, y+25, text=f"{row['share']:.1f}%", fill=MUTED,
                                    font=("Segoe UI", 10), anchor="e", tags="rows")
            start, end = 104, max(105, w-22)
            self.canvas.create_line(start, y+51, end, y+51, width=6, fill="#383838", capstyle="round", tags="rows")
            length = (end-start)*row["share"]/100
            if length >= 1:
                self.canvas.create_line(start, y+51, start+length, y+51, width=6, fill=row["color"], capstyle="round", tags="rows")

    def draw_pie(self):
        w, h = self.pie_canvas.winfo_width(), self.pie_canvas.winfo_height()
        signature = self.signature, self.query.get(), w, h
        if signature == self.pie_draw_signature:
            return
        self.pie_draw_signature = signature
        self.pie_canvas.delete("all")
        self.pie_slices = []
        self.pie_picture = None
        parts = self.pie_rows
        if not parts or self.total <= 0:
            self.pie_canvas.create_text(w/2, h/2, text="Chưa có dữ liệu trong 7 ngày này.",
                                        fill=MUTED, font=("Segoe UI", 10), width=max(1, w-24))
            return
        side = max(1, min(w-24, h-24, 340))
        query = self.query.get().strip().casefold()
        start = -90.
        for part in parts:
            matched = (not query or self.matches(part, query)
                       or any(query in name.casefold() or query in display_name(name).casefold()
                              for name in part["members"]))
            color = part["color"] if matched else "#414141"
            extent = min(360., max(0., part["screen"]/self.total*360))
            self.pie_slices.append(dict(name=part["name"], color=color, start=start, extent=extent))
            start += extent
        self.pie_picture = ImageTk.PhotoImage(raster_pie(self.pie_slices, side), master=self)
        self.pie_canvas.create_image(w/2, h/2, image=self.pie_picture, tags="pie")

    def layout_legend(self):
        w, h = self.legend_canvas.winfo_width(), self.legend_canvas.winfo_height()
        self.legend_canvas.configure(scrollregion=(0, 0, w, max(h, len(self.legend_rows)*self.LEGEND_ROW_HEIGHT)))
        self.draw_legend()

    def legend_scrolled(self, first, last):
        self.legend_scroll.set(first, last)
        self.draw_legend()

    def draw_legend(self):
        w, h = self.legend_canvas.winfo_width(), self.legend_canvas.winfo_height()
        top = self.legend_canvas.canvasy(0)
        first = max(0, int(top//self.LEGEND_ROW_HEIGHT))
        last = min(len(self.legend_rows), int((top+h)//self.LEGEND_ROW_HEIGHT)+2)
        signature = self.signature, self.query.get(), self.other_expanded, w, h, first, last, self.hovered_legend
        if signature == self.legend_draw_signature:
            return
        self.legend_draw_signature = signature
        self.legend_canvas.delete("rows")
        self.legend_canvas.delete("rowchrome")
        self.legend_images.clear()
        if not self.legend_rows:
            self.legend_canvas.create_text(w/2, max(40, h/2),
                                            text="Không tìm thấy ứng dụng." if self.query.get().strip() else "Chưa có ứng dụng.",
                                            fill=MUTED, font=("Segoe UI", 10), tags="rows")
            return
        for index in range(first, last):
            row = self.legend_rows[index]
            y = index*self.LEGEND_ROW_HEIGHT
            grouped, child = row["legend_kind"] == "other", row["legend_kind"] == "child"
            inset = 18 if child else 0
            hovered = index == self.hovered_legend
            row_card(self.legend_canvas, inset, y+2, w-inset-4, self.LEGEND_ROW_HEIGHT-7,
                     fill=ROW_HOVER if hovered else "#303030" if grouped else "#272727" if child else ROW_FILL,
                     outline="#626262" if hovered else "#505050" if grouped else "#383838" if child else "#414141")
            self.legend_canvas.create_oval(inset+12, y+22, inset+20, y+30,
                                             fill=row["color"], outline="", tags="rows")
            if grouped:
                self.legend_canvas.create_text(42, y+23, text="⌄" if row["expanded"] else "›",
                                                fill=INK, font=("Segoe UI", 15), tags="rows")
            else:
                photo = self.image_provider(dict(row, color_dot=False))
                self.legend_images.append(photo)
                self.legend_canvas.create_image(inset+29, y+12, image=photo, anchor="nw", tags="rows")
            self.legend_canvas.create_text(inset+73, y+20,
                                            text=self.fit_name(display_name(row["name"]), max(12, w-inset-151)),
                                            fill=INK, font=self.name_font, anchor="w", tags="rows")
            self.legend_canvas.create_text(w-15, y+20, text=f"{row['share']:.1f}%",
                                            fill=MUTED if grouped or child else row["color"], font=("Segoe UI", 10, "bold"),
                                            anchor="e", tags="rows")
            detail = duration(row["screen"])
            if grouped:
                detail += f" · {len(row['children'])} ứng dụng"
            self.legend_canvas.create_text(inset+73, y+43, text=detail,
                                            fill=MUTED, font=("Segoe UI", 9), anchor="w", tags="rows")

    def cleanup(self, event):
        if event.widget == self:
            self.query.trace_remove("write", self.trace_id)
