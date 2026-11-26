"""Click-to-select calendar using the dashboard's native dark widgets."""
import calendar
from datetime import date
import tkinter as tk
from ui_widgets import CARD, INK, MUTED, SoftButton, rounded_photo


class ChevronButton(SoftButton):
    def __init__(self, parent, command, direction="left", **kwargs):
        self.direction = direction
        super().__init__(parent, "", command, width=36, height=36, fill="#2D2D2D", radius=18, **kwargs)
        self.arrow = self.create_line(0, 0, 1, 1, 2, 2, width=1.7, capstyle="round", joinstyle="round", fill=INK)
        self.paint()

    def paint(self):
        super().paint()
        if hasattr(self, "arrow"):
            x, y = self.winfo_width()/2, self.winfo_height()/2
            flip = 1 if self.direction == "left" else -1
            self.coords(self.arrow, x+flip*2.5, y-4.5, x-flip*2, y, x+flip*2.5, y+4.5)
            self.itemconfigure(self.arrow, fill=INK if self.widget_state != "disabled" else "#575757")


class DateButton(SoftButton):
    def __init__(self, parent, command):
        super().__init__(parent, "", command, width=180, height=38, fill="#2D2D2D", radius=19)
        self.calendar_outline = self.create_rectangle(17, 12, 30, 25, outline=MUTED, width=1.2)
        self.calendar_top = self.create_line(17, 16, 30, 16, fill=MUTED, width=1.2)
        self.calendar_rings = [self.create_line(x, 10, x, 14, fill=MUTED, width=1.4, capstyle="round") for x in (20, 27)]
        self.down = self.create_line(0, 0, 1, 1, 2, 2, fill=MUTED, width=1.2, capstyle="round", joinstyle="round")
        self.paint()

    def paint(self):
        super().paint()
        if hasattr(self, "down"):
            w, y = self.winfo_width(), self.winfo_height()/2
            self.coords(self.text_id, w/2+2, y)
            self.coords(self.down, w-23, y-2, w-19, y+2, w-15, y-2)


class CalendarPopup(tk.Toplevel):
    def __init__(self, owner, anchor, selected, on_select):
        super().__init__(owner)
        self.withdraw()
        self.owner, self.anchor = owner, anchor
        self.selected, self.on_select = selected, on_select
        self.year, self.month = selected.year, selected.month
        self.day_buttons = {}
        self.pending_callbacks = set()
        self.transient(owner)
        self.overrideredirect(True)
        self.configure(bg="#010101")
        try:
            self.attributes("-transparentcolor", "#010101")
        except tk.TclError:
            self.configure(bg=CARD)
        self.canvas = tk.Canvas(self, width=336, height=382, bg=self.cget("bg"), highlightthickness=0)
        self.canvas.pack()
        self.panel_photo = rounded_photo(self.canvas, 336, 382, self.cget("bg"), CARD, 22, "#424242")
        self.canvas.create_image(0, 0, anchor="nw", image=self.panel_photo)
        header = tk.Frame(self.canvas, bg=CARD)
        self.canvas.create_window(18, 14, anchor="nw", window=header, width=300, height=36)
        header.columnconfigure(1, weight=1)
        self.previous_month = ChevronButton(header, lambda: self.shift_month(-1))
        self.previous_month.grid(row=0, column=0)
        self.month_label = tk.Label(header, bg=CARD, fg=INK, font=("Segoe UI", 11, "bold"))
        self.month_label.grid(row=0, column=1, sticky="ew")
        self.next_month = ChevronButton(header, lambda: self.shift_month(1), direction="right")
        self.next_month.grid(row=0, column=2)
        for i, name in enumerate(("T2", "T3", "T4", "T5", "T6", "T7", "CN")):
            self.canvas.create_text(39+i*42, 72, text=name, fill=MUTED, font=("Segoe UI", 9))
        self.days = tk.Frame(self.canvas, bg=CARD)
        self.canvas.create_window(18, 88, anchor="nw", window=self.days, width=300, height=240)
        self.today_button = SoftButton(self.canvas, "Hôm nay", lambda: self.choose(date.today()),
                                       width=294, height=34, fill="#333333")
        self.canvas.create_window(18, 337, anchor="nw", window=self.today_button)
        self.render_month()
        self.bind("<Escape>", lambda _: self.dismiss())
        self.bind("<FocusOut>", lambda _: self.schedule(self.close_if_unfocused))
        self.outside_binding = owner.bind("<ButtonPress-1>", self.outside_click, add="+")
        self.bind("<Destroy>", self.cleanup)
        self.update_idletasks()
        width, height = 336, 382
        x = anchor.winfo_rootx()+anchor.winfo_width()-width
        y = anchor.winfo_rooty()+anchor.winfo_height()+8
        # Constrain the popup to the owner so it does not cover another window.
        bounds = (owner.winfo_rootx(), owner.winfo_rooty(),
                  owner.winfo_rootx()+owner.winfo_width(), owner.winfo_rooty()+owner.winfo_height())
        x = max(bounds[0]+8, min(x, bounds[2]-width-8))
        if y+height > bounds[3]-8:
            y = max(bounds[1]+8, anchor.winfo_rooty()-height-8)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.deiconify()
        self.lift()
        self.schedule(self.focus_selection)

    def schedule(self, callback):
        def run():
            self.pending_callbacks.discard(timer)
            if self.winfo_exists():
                callback()
        timer = self.after_idle(run)
        self.pending_callbacks.add(timer)

    def render_month(self):
        for button in self.days.winfo_children():
            button.destroy()
        self.day_buttons.clear()
        today = date.today()
        self.month_label.configure(text=f"Tháng {self.month}, {self.year}")
        self.next_month.configure(state="disabled" if (self.year, self.month) >= (today.year, today.month) else "normal")
        self.previous_month.configure(state="disabled" if (self.year, self.month) == (1, 1) else "normal")
        for week_index, week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(self.year, self.month)):
            for weekday, number in enumerate(week):
                if not number:
                    continue
                value = date(self.year, self.month, number)
                fill = INK if value == self.selected else "#3A3A3A" if value == today else CARD
                foreground = CARD if value == self.selected else INK
                button = SoftButton(self.days, str(number), lambda day=value: self.choose(day),
                                     width=40, height=36, fill=fill, foreground=foreground,
                                     hover="#CACACA" if value == self.selected else "#3B3B3B")
                button.grid(row=week_index, column=weekday, padx=1, pady=2)
                if value > today:
                    button.configure(state="disabled")
                self.day_buttons[value] = button

    def shift_month(self, delta):
        serial = self.year*12+self.month-1+delta
        year, month = divmod(serial, 12)
        month += 1
        if year < 1 or (year, month) > (date.today().year, date.today().month):
            return
        self.year, self.month = year, month
        self.render_month()

    def choose(self, value):
        if value > date.today():
            return
        self.dismiss()
        self.on_select(value)

    def focus_selection(self):
        if not self.winfo_exists():
            return
        button = self.day_buttons.get(self.selected)
        (button or self.today_button).focus_set()

    def outside_click(self, event):
        if self.winfo_exists() and event.widget != self.anchor and event.widget.winfo_toplevel() != self:
            self.dismiss()

    def close_if_unfocused(self):
        if not self.winfo_exists():
            return
        focused = self.focus_get()
        if focused is not self.anchor and (focused is None or focused.winfo_toplevel() != self):
            self.dismiss()

    def cleanup(self, event):
        if event.widget == self:
            for timer in self.pending_callbacks:
                self.after_cancel(timer)
            self.pending_callbacks.clear()
            if self.outside_binding:
                self.owner.unbind("<ButtonPress-1>", self.outside_binding)
                self.outside_binding = None

    def dismiss(self):
        if self.winfo_exists():
            self.destroy()
