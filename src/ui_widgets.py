"""Small native Tk widgets for the dashboard; imported only by the GUI process."""
import tkinter as tk
from collections import OrderedDict
from PIL import Image, ImageDraw, ImageTk

BG, SIDEBAR, CARD = "#212121", "#181818", "#262626"
INK, MUTED, HOVER = "#ECECEC", "#999999", "#333333"
ROW_FILL, ROW_BORDER, ROW_HOVER = "#292929", "#414141", "#343434"


def rounded_points(w, h, r):
    r = min(r, w / 2, h / 2)
    return (r, 0, w-r, 0, w, 0, w, r, w, h-r, w, h,
            w-r, h, r, h, 0, h, 0, h-r, 0, r, 0, 0)


def rounded_photo(master, width, height, background, fill, radius, outline=None):
    width, height = max(1, int(width)), max(1, int(height))
    scale = 3
    picture = Image.new("RGB", (width*scale, height*scale), background)
    ImageDraw.Draw(picture).rounded_rectangle((0, 0, width*scale-1, height*scale-1),
        radius=min(radius, height/2, width/2)*scale, fill=fill, outline=outline, width=scale)
    return ImageTk.PhotoImage(picture.resize((width, height), Image.Resampling.LANCZOS), master=master)


def row_card(canvas, x, y, width, height, fill=ROW_FILL, outline=ROW_BORDER,
             radius=12, tags="rowchrome"):
    """Share smooth card edges across viewport rows, with a bounded local cache."""
    width, height = max(1, int(width)), max(1, int(height))
    cache = getattr(canvas, "_row_card_cache", None)
    if cache is None:
        cache = canvas._row_card_cache = OrderedDict()
    signature = width, height, canvas.cget("bg"), fill, radius, outline
    photo = cache.get(signature)
    if photo is None:
        photo = rounded_photo(canvas, width, height, signature[2], fill, radius, outline)
        cache[signature] = photo
        while len(cache) > 8:
            cache.popitem(last=False)
    else:
        cache.move_to_end(signature)
    return canvas.create_image(x, y, image=photo, anchor="nw", tags=tags)


def dark_titlebar(root):
    """Style this app's own title bar; unsupported Windows versions ignore it."""
    import ctypes
    from ctypes import wintypes
    try:
        user = ctypes.WinDLL("user32")
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        hwnd = user.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
        setter = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
        setter.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        setter.restype = ctypes.c_long
        values = ((20, 1), (34, 0x181818), (35, 0x181818), (36, 0xECECEC))
        for attribute, value in values:
            setting = wintypes.DWORD(value)
            setter(hwnd, attribute, ctypes.byref(setting), ctypes.sizeof(setting))
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)
    except (OSError, AttributeError):
        pass


def install_dark_frame(root):
    """Tk may recreate its wrapper when mapped; reapply to the current frame."""
    timers = set()
    def schedule(delay=0):
        def apply():
            timers.discard(timer)
            if root.winfo_exists():
                dark_titlebar(root)
        timer = root.after(delay, apply) if delay else root.after_idle(apply)
        timers.add(timer)
    def mapped(event):
        if event.widget == root:
            schedule()
            schedule(100)
    def cleanup(event):
        if event.widget == root:
            for timer in timers:
                root.after_cancel(timer)
            timers.clear()
    root.bind("<Map>", mapped, add="+")
    root.bind("<FocusIn>", lambda event: schedule() if event.widget == root else None, add="+")
    root.bind("<Destroy>", cleanup, add="+")
    schedule()


class SoftButton(tk.Canvas):
    def __init__(self, parent, text, command, width=150, height=40,
                 fill=None, hover=HOVER, foreground=INK, align="center", radius=10):
        self.caption, self.command = text, command
        self.fill = fill or parent.cget("bg")
        self.hover_fill, self.foreground, self.align = hover, foreground, align
        self.hovering = self.focused = self.pressed = False
        self.widget_state = "normal"
        self.radius = radius
        self.background_signature = None
        self.background_photo = None
        super().__init__(parent, bg=parent.cget("bg"), width=width, height=height,
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self.shape = self.create_image(0, 0, anchor="nw")
        self.text_id = self.create_text(0, 0, text=text, fill=foreground, font=("Segoe UI", 10))
        self.bind("<Configure>", lambda _: self.paint())
        self.bind("<Enter>", lambda _: self.hover(True))
        self.bind("<Leave>", lambda _: self.hover(False))
        self.bind("<FocusIn>", lambda _: self.focus(True))
        self.bind("<FocusOut>", lambda _: self.focus(False))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<ButtonRelease-1>", self.release)
        for key in ("<Return>", "<space>"):
            self.bind(key, self.invoke)

    def hover(self, value):
        self.hovering = value
        self.paint()

    def focus(self, value):
        self.focused = value
        self.paint()

    def paint(self):
        w, h = self.winfo_width(), self.winfo_height()
        enabled = self.widget_state != "disabled"
        fill = self.hover_fill if self.hovering and enabled else self.fill
        outline = ("#777777" if self.focused and enabled else
                   "#585858" if self.hovering and enabled else
                   "#464646" if self.fill != self.cget("bg") else None)
        signature = w, h, fill, outline
        if signature != self.background_signature:
            self.background_signature = signature
            self.background_photo = rounded_photo(self, w, h, self.cget("bg"), fill, self.radius, outline)
            self.itemconfigure(self.shape, image=self.background_photo)
        self.coords(self.text_id, 13 if self.align == "left" else w/2, h/2)
        self.itemconfigure(self.text_id, text=self.caption, anchor="w" if self.align == "left" else "center",
                           fill=self.foreground if enabled else "#5D5D5D")

    def press(self, event):
        if self.widget_state != "disabled":
            self.pressed = True
            self.focus_set()

    def release(self, event):
        pressed, self.pressed = self.pressed, False
        if pressed and 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.invoke()

    def invoke(self, event=None):
        if self.widget_state != "disabled":
            self.command()
        return "break"

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None
        if "text" in kwargs:
            self.caption = kwargs.pop("text")
        if "state" in kwargs:
            self.widget_state = kwargs.pop("state")
        result = super().configure(cnf, **kwargs) if cnf is not None or kwargs else None
        if hasattr(self, "shape"):
            self.paint()
        return result

    config = configure

    def cget(self, key):
        if key == "text":
            return self.caption
        if key == "state":
            return self.widget_state
        return super().cget(key)


class SearchField(tk.Canvas):
    def __init__(self, parent, variable, width=200, height=38):
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"), highlightthickness=0)
        self.variable, self.focused = variable, False
        self.background_signature = None
        self.shape = self.create_image(0, 0, anchor="nw")
        self.magnifier = self.create_oval(14, 12, 23, 21, outline=MUTED, width=1.5)
        self.handle = self.create_line(22, 20, 27, 25, fill=MUTED, width=1.5, capstyle="round")
        self.entry = tk.Entry(self, textvariable=variable, bg="#303030", fg=INK, insertbackground=INK,
                              relief="flat", bd=0, highlightthickness=0, font=("Segoe UI", 10))
        self.entry_window = self.create_window(38, height/2, anchor="w", window=self.entry, height=24)
        self.placeholder = tk.Label(self, text="Tìm kiếm", bg="#303030", fg=MUTED,
                                     anchor="w", padx=0, font=("Segoe UI", 10))
        self.placeholder_window = self.create_window(38, height/2, anchor="w", window=self.placeholder, height=24)
        self.placeholder.bind("<Button-1>", lambda _: self.entry.focus_set())
        self.bind("<Button-1>", lambda _: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda _: self.focus(True))
        self.entry.bind("<FocusOut>", lambda _: self.focus(False))
        self.bind("<Configure>", lambda _: self.paint())
        self.trace_id = variable.trace_add("write", self.changed)
        self.bind("<Destroy>", self.cleanup)
        self.changed()

    def focus(self, value):
        self.focused = value
        self.paint()

    def changed(self, *_):
        self.itemconfigure(self.placeholder_window, state="hidden" if self.variable.get() else "normal")

    def paint(self):
        w, h = self.winfo_width(), self.winfo_height()
        signature = w, h, self.focused
        if signature != self.background_signature:
            self.background_signature = signature
            self.background_photo = rounded_photo(self, w, h, self.cget("bg"), "#303030", h/2,
                                                    "#737373" if self.focused else "#484848")
            self.itemconfigure(self.shape, image=self.background_photo)
        self.coords(self.magnifier, 14, h/2-6, 23, h/2+3)
        self.coords(self.handle, 22, h/2+2, 27, h/2+7)
        for item in (self.entry_window, self.placeholder_window):
            self.coords(item, 38, h/2)
            self.itemconfigure(item, width=max(1, w-54))

    def cleanup(self, event):
        if event.widget == self:
            self.variable.trace_remove("write", self.trace_id)


class RoundedScrollbar(tk.Canvas):
    def __init__(self, parent, command):
        super().__init__(parent, width=14, bg=parent.cget("bg"), highlightthickness=0, takefocus=True)
        self.command = command
        self.first, self.last = 0., 1.
        self.drag_offset = None
        self.hovering = False
        self.thumb = self.create_line(7, 0, 7, 0, width=6, fill="#595959", capstyle="round", state="hidden")
        self.bind("<Configure>", lambda _: self.paint())
        self.bind("<Enter>", lambda _: self.hover(True))
        self.bind("<Leave>", lambda _: self.hover(False))
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<MouseWheel>", lambda event: self.command("scroll", -int(event.delta/120), "units"))
        for key, amount, unit in (("Up", -1, "units"), ("Down", 1, "units"),
                                  ("Prior", -1, "pages"), ("Next", 1, "pages")):
            self.bind(f"<{key}>", lambda event, a=amount, u=unit: self.command("scroll", a, u))
        self.bind("<Home>", lambda _: self.command("moveto", 0))
        self.bind("<End>", lambda _: self.command("moveto", 1))

    def set(self, first, last):
        self.first, self.last = max(0., float(first)), min(1., float(last))
        self.paint()

    def geometry(self):
        track = max(1., self.winfo_height()-12.)
        length = min(track, max(32., track * (self.last-self.first)))
        travel = track-length
        position = self.first / max(.0001, 1.-(self.last-self.first))
        return 6.+travel*min(1., position), length, travel

    def paint(self):
        if self.last-self.first >= .999:
            self.itemconfigure(self.thumb, state="hidden")
            return
        top, length, _ = self.geometry()
        width = 8 if self.hovering or self.drag_offset is not None else 6
        self.coords(self.thumb, 7, top+width/2, 7, top+length-width/2)
        self.itemconfigure(self.thumb, state="normal", width=width,
                           fill="#8B8B8B" if self.hovering or self.drag_offset is not None else "#595959")

    def hover(self, value):
        self.hovering = value
        self.paint()

    def press(self, event):
        if self.last-self.first >= .999:
            return
        self.focus_set()
        top, length, _ = self.geometry()
        self.drag_offset = event.y-top if top <= event.y <= top+length else length/2
        self.drag(event)

    def drag(self, event):
        if self.drag_offset is None:
            return
        _, _, travel = self.geometry()
        fraction = (event.y-6.-self.drag_offset) / max(1., travel)
        self.command("moveto", min(1., max(0., fraction)) * (1.-(self.last-self.first)))

    def release(self, event):
        self.drag_offset = None
        self.paint()


class Surface(tk.Canvas):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, **kwargs)
        self.panel = self.create_image(0, 0, anchor="nw")
        self.panel_signature = None
        self.panel_photo = None
        self.inner = tk.Frame(self, bg=CARD)
        self.window = self.create_window(22, 24, anchor="nw", window=self.inner)
        self.bind("<Configure>", self.resize)

    def resize(self, event):
        signature = event.width, event.height
        if signature != self.panel_signature:
            self.panel_signature = signature
            self.panel_photo = rounded_photo(self, event.width, event.height,
                self.cget("bg"), CARD, 20, "#393939")
            self.itemconfigure(self.panel, image=self.panel_photo)
        self.itemconfigure(self.window, width=max(1, event.width-44), height=max(1, event.height-48))
