"""Giao diện quản lý giới hạn app hằng ngày, chạy trong process dashboard."""
from __future__ import annotations

import ntpath
import tkinter as tk
from tkinter import font

from dashboard_model import COLORS, display_name, short_duration
from ui_widgets import (CARD, INK, MUTED, ROW_FILL, RoundedScrollbar, SoftButton,
                        install_dark_frame, row_card)
from app_picker import AppPicker


def limit_minutes(value):
    """Đổi nội dung ô nhập thành số phút nguyên trong khoảng 1 đến 1440.

    Bỏ khoảng trắng hai đầu. Chỉ nhận chữ số ASCII và báo ValueError nếu
    nhập số thập phân, chữ, số ngoài khoảng hoặc để trống.
    """
    value = str(value).strip()
    if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 1440:
        raise ValueError("Nhập số phút nguyên từ 1 đến 1440.")
    return int(value)


class _LimitRow(tk.Canvas):
    """Một card giới hạn app, được tái sử dụng khi người dùng cuộn danh sách."""
    def __init__(self, parent, view):
        """Tạo card gồm icon, tên app, đường dẫn, thời gian và các nút thao tác.

        parent là widget chứa card; view là LimitsView quản lý danh sách.
        Gắn sự kiện đổi kích thước, rê chuột và cuộn cho các thành phần.
        update_rule() sẽ đưa dữ liệu giới hạn cụ thể vào card này.
        """
        super().__init__(parent, bg=CARD, highlightthickness=0)
        self.view, self.rule, self.photo = view, None, None
        self.hovered = False
        self.chrome_signature = None
        self.inner = inner = tk.Frame(self, bg=ROW_FILL)
        self.inner_window = self.create_window(12, 4, window=inner, anchor="nw")
        inner.columnconfigure(1, weight=1)
        self.icon = tk.Label(inner, text="▣", bg=ROW_FILL, fg=MUTED, width=3,
                             font=("Segoe UI", 18))
        self.icon.grid(row=0, column=0, rowspan=3, sticky="n", padx=(6, 13), pady=(12, 0))
        self.name = tk.Label(inner, bg=ROW_FILL, fg=INK, anchor="w", font=("Segoe UI", 11, "bold"))
        self.name.grid(row=0, column=1, sticky="ew", pady=(12, 0))
        self.name.bind("<Configure>", lambda _: self.fit_labels())
        self.path = tk.Label(inner, bg=ROW_FILL, fg=MUTED, anchor="w", font=("Segoe UI", 8))
        self.path.grid(row=1, column=1, sticky="ew", pady=(3, 0))
        self.state = tk.Label(inner, bg=ROW_FILL, fg=MUTED, anchor="w", font=("Segoe UI", 9))
        self.state.grid(row=2, column=1, sticky="ew", pady=(5, 0))
        timer = tk.Frame(inner, bg=ROW_FILL, width=142)
        timer.grid(row=0, column=2, rowspan=3, sticky="n", padx=(14, 14), pady=(13, 0))
        self.usage = tk.Label(timer, bg=ROW_FILL, fg=INK, anchor="e", font=("Segoe UI", 10))
        self.usage.pack(anchor="e")
        self.remaining = tk.Label(timer, bg=ROW_FILL, fg=MUTED, anchor="e", font=("Segoe UI", 9))
        self.remaining.pack(anchor="e", pady=(5, 0))
        actions = tk.Frame(inner, bg=ROW_FILL)
        actions.grid(row=0, column=3, rowspan=3, sticky="n", padx=(0, 4), pady=(15, 0))
        self.edit = SoftButton(actions, "Chỉnh sửa", lambda: view.open_dialog(self.rule),
                               width=86, height=34, fill="#303030")
        self.edit.pack(side="left", padx=(0, 5))
        self.toggle = SoftButton(actions, "Tắt", lambda: view.toggle(self.rule),
                                 width=82, height=34, fill="#303030")
        self.toggle.pack(side="left", padx=(0, 5))
        self.remove = SoftButton(actions, "Xóa", lambda: view.remove(self.rule),
                                 width=48, height=34, foreground=MUTED)
        self.remove.pack(side="left")
        self.progress = tk.Canvas(inner, height=16, bg=ROW_FILL, highlightthickness=0)
        self.progress.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(2, 10), pady=(3, 0))
        self.progress.bind("<Configure>", lambda _: self.paint_progress())
        self.bind("<Configure>", self.layout_card)
        for widget in (self, inner, self.icon, self.name, self.path, self.state, timer,
                       self.usage, self.remaining, actions, self.progress,
                       self.edit, self.toggle, self.remove):
            widget.bind("<MouseWheel>", view.wheel)
            widget.bind("<Enter>", lambda _: self.hover(True), add="+")
            widget.bind("<Leave>", self.leave, add="+")

    def layout_card(self, _=None):
        """Chỉnh khung nội dung theo kích thước card rồi cập nhật viền và chữ.

        Tham số _ nhận sự kiện Configure của Tkinter khi kích thước thay đổi.
        """
        width, height = self.winfo_width(), self.winfo_height()
        self.itemconfigure(self.inner_window, width=max(1, width-24), height=max(1, height-14))
        self.paint_card()
        self.fit_labels()

    def hover(self, value):
        """Cập nhật trạng thái rê chuột và vẽ lại viền khi trạng thái thay đổi."""
        if value != self.hovered:
            self.hovered = value
            self.paint_card()

    def leave(self, event):
        """Kiểm tra chuột còn nằm trong card sau sự kiện Leave của widget con.

        Giữ hiệu ứng hover khi chuột chỉ di chuyển giữa các nút trong card.
        """
        x, y = self.winfo_pointerxy()
        inside = (self.winfo_rootx() <= x < self.winfo_rootx()+self.winfo_width()
                  and self.winfo_rooty() <= y < self.winfo_rooty()+self.winfo_height())
        self.hover(inside)

    def paint_card(self):
        """Vẽ nền và viền bo tròn theo kích thước cùng trạng thái hover.

        Cache bộ giá trị này trong chrome_signature để chỉ vẽ lại khi cần.
        """
        signature = self.winfo_width(), self.winfo_height(), self.hovered
        if signature == self.chrome_signature:
            return
        self.chrome_signature = signature
        self.delete("rowchrome")
        item = row_card(self, 0, 2, signature[0]-2, signature[1]-8,
                        outline="#626262" if self.hovered else "#414141")
        self.tag_lower(item)

    def update_rule(self, rule, index):
        """Đưa dữ liệu một quy tắc giới hạn vào card đang được tái sử dụng.

        rule chứa đường dẫn EXE, tên app, hạn mức, thời gian đã dùng/còn lại,
        trạng thái bật/chặn và lỗi. index chọn màu trong bảng COLORS.
        Cập nhật icon, nhãn, nút bật/tắt và thanh tiến độ theo quy tắc đó.
        """
        self.rule = rule
        self.color = COLORS[index % len(COLORS)]
        exe = ntpath.basename(rule["path"])
        self.full_name = display_name(rule.get("name") or exe)
        self.full_path = rule["path"]
        if self.view.image_provider:
            self.photo = self.view.image_provider(dict(kind="app", name=exe, color=self.color,
                                                        path=rule["path"]))
            self.icon.configure(image=self.photo, text="", width=36)
        self.usage.configure(text=f"{short_duration(rule['used'])} / {rule['minutes']} phút")
        enabled, blocked, error = rule["enabled"], rule["blocked"], rule.get("error", "")
        state = error or ("Đã tắt giới hạn" if not enabled else "Đã hết thời gian · Đang chặn" if blocked else "Đang áp dụng")
        self.state.configure(text=state, fg="#E6AD78" if error else "#E39494" if blocked else MUTED)
        self.remaining.configure(text=f"Còn {short_duration(rule['remaining'])}" if enabled and not blocked else "Đã bỏ chặn" if not enabled else "Hết thời gian")
        self.toggle.configure(text="Bật" if not enabled else "Bỏ chặn" if blocked else "Tắt")
        self.fit_labels()
        self.paint_progress()

    def fit_labels(self):
        """Rút gọn tên app, đường dẫn và lỗi bằng dấu … để vừa cột nội dung."""
        if self.rule is None:
            return
        # Đo chiều rộng cột chữ để các nút thao tác vẫn nằm trong card.
        available = max(20, self.name.winfo_width())
        self.name.configure(text=self.view.fit(self.full_name, available, self.view.name_font))
        self.path.configure(text=self.view.fit(self.full_path, available, self.view.path_font))
        state = self.rule.get("error", "")
        if state:
            self.state.configure(text=self.view.fit(state, available, self.view.state_font))

    def paint_progress(self):
        """Vẽ tỷ lệ thời gian đã dùng trên hạn mức của app.

        used tính bằng giây, minutes tính bằng phút. Giới hạn tỷ lệ trong
        khoảng 0 đến 1; quy tắc đang tắt dùng thanh màu xám.
        """
        if self.rule is None:
            return
        w = self.progress.winfo_width()
        self.progress.delete("all")
        self.progress.create_line(3, 8, max(3, w-3), 8, fill="#3B3B3B", width=5, capstyle="round")
        share = min(1., max(0., self.rule["used"] / (self.rule["minutes"]*60)))
        if share:
            self.progress.create_line(3, 8, 3+max(0, w-6)*share, 8, width=5,
                                      fill=self.color if self.rule["enabled"] else "#626262", capstyle="round")


class LimitsView(tk.Frame):
    """Trang Giới hạn app, điều khiển danh sách card và form thêm/chỉnh sửa."""
    ROW_HEIGHT = 117

    def __init__(self, parent, service, image_provider=None, catalog_provider=None):
        """Dựng trang giới hạn và chuẩn bị các biến dùng trong form.

        service cung cấp thao tác đọc, lưu, bật/tắt và xóa quy tắc.
        image_provider cấp icon; catalog_provider cấp danh sách app để chọn.
        Canvas và pool card giúp chỉ dựng các dòng quanh vùng đang nhìn thấy.
        """
        super().__init__(parent, bg=CARD)
        self.service, self.image_provider = service, image_provider
        self.catalog_provider = catalog_provider
        self.picker = None
        self.selected_app = None
        self.dialog_photo = None
        self.dialog_icon = None
        self.rows, self.pool = [], []
        self.signature = None
        self.dialog = None
        self.path_var = tk.StringVar(self)
        self.minutes_var = tk.StringVar(self, "180")
        self.status_var = tk.StringVar(self)
        self.dialog_status = tk.StringVar(self)
        self.selection_text = tk.StringVar(self)
        self.editing_rule = None
        self.name_font = font.Font(self, family="Segoe UI", size=11, weight="bold")
        self.path_font = font.Font(self, family="Segoe UI", size=8)
        self.state_font = font.Font(self, family="Segoe UI", size=9)
        header = tk.Frame(self, bg=CARD)
        header.pack(fill="x", pady=(0, 5))
        tk.Label(header, text="Giới hạn ứng dụng", bg=CARD, fg=INK,
                 font=("Segoe UI", 12, "bold")).pack(side="left")
        self.add_button = SoftButton(header, "+  Thêm ứng dụng", self.open_dialog,
                                      width=158, height=38, fill="#353535")
        self.add_button.pack(side="right")
        tk.Label(self, text="Tính từ khi bật giới hạn · Đặt lại lúc 00:00 · Nhắc trước 15, 10, 5 phút",
                 bg=CARD, fg=MUTED, anchor="w", font=("Segoe UI", 9)).pack(fill="x", pady=(0, 9))
        warning = tk.Label(self, text="Hết giờ sẽ đóng app và các tiến trình con. Hãy lưu công việc trước hạn.",
                            bg=CARD, fg="#D0B28E", anchor="w", font=("Segoe UI", 9))
        warning.pack(fill="x", pady=(0, 4))
        tk.Label(self, text="Tạm dừng tracking sẽ ngừng tính thêm thời gian; app đã hết hạn vẫn bị chặn.",
                 bg=CARD, fg=MUTED, anchor="w", font=("Segoe UI", 9)).pack(fill="x", pady=(0, 4))
        tk.Label(self, textvariable=self.status_var, bg=CARD, fg="#E6AD78", anchor="w",
                 font=("Segoe UI", 9)).pack(fill="x", pady=(0, 6))
        table = tk.Frame(self, bg=CARD)
        table.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(table, bg=CARD, highlightthickness=0, yscrollincrement=20,
                                 takefocus=True)
        self.scroll = RoundedScrollbar(table, self.canvas.yview)
        self.scroll.pack(side="right", fill="y", padx=(5, 0))
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=self.scrolled)
        self.canvas.bind("<Configure>", lambda _: self.layout())
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Button-1>", lambda _: self.canvas.focus_set())
        for key, amount, units in (("Up", -1, "units"), ("Down", 1, "units"),
                                   ("Prior", -1, "pages"), ("Next", 1, "pages")):
            self.canvas.bind(f"<{key}>", lambda _, a=amount, u=units: self.canvas.yview_scroll(a, u))
        self.canvas.bind("<Home>", lambda _: self.canvas.yview_moveto(0))
        self.canvas.bind("<End>", lambda _: self.canvas.yview_moveto(1))

    @staticmethod
    def fit(text, available, text_font):
        """Trả chuỗi vừa chiều rộng available tính bằng pixel.

        Dùng text_font đo chiều rộng thực tế, cắt bớt đuôi và thêm … khi cần.
        """
        if text_font.measure(text) <= available:
            return text
        while text and text_font.measure(text+"…") > available:
            text = text[:-1]
        return text+"…" if text else "…"

    def refresh(self):
        """Đọc quy tắc từ service và cập nhật danh sách nếu dữ liệu thay đổi.

        Làm mới icon trong form chỉnh sửa nếu có. Nếu service báo lỗi, đưa
        thông báo lên giao diện. signature giúp tránh vẽ lại danh sách cũ.
        """
        if self.dialog_icon is not None and self.editing_rule and self.image_provider:
            rule = self.editing_rule
            self.dialog_photo = self.image_provider(dict(kind="app", path=rule["path"],
                name=ntpath.basename(rule["path"]), color="#82AFFF"))
            self.dialog_icon.configure(image=self.dialog_photo)
        try:
            rows = self.service.rules()
        except Exception as exc:
            self.status_var.set(str(exc))
            return
        signature = tuple((r["id"], r["path"], r.get("name", ""), r["minutes"], r["enabled"],
                           int(r["used"]), int(r["remaining"]), r["blocked"], r.get("error", "")) for r in rows)
        if signature == self.signature:
            return
        self.signature, self.rows = signature, rows
        self.layout()

    def wheel(self, event):
        """Cuộn danh sách theo con lăn chuột và chặn xử lý cuộn trùng của Tk."""
        self.canvas.yview_scroll(-int(event.delta/120)*3, "units")
        return "break"

    def scrolled(self, first, last):
        """Đồng bộ scrollbar và vẽ lại các dòng sau khi Canvas đổi vị trí cuộn.

        first và last là hai mốc tỷ lệ vùng đang hiển thị, do Canvas cung cấp.
        """
        self.scroll.set(first, last)
        self.draw_rows()

    def layout(self):
        """Tính vùng cuộn từ số quy tắc và chiều cao dòng, rồi bố trí các card."""
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.configure(scrollregion=(0, 0, width, max(height, len(self.rows)*self.ROW_HEIGHT)))
        self.draw_rows()

    def draw_rows(self):
        """Hiển thị các card nằm quanh vùng nhìn thấy bằng pool widget dùng lại.

        Tính dòng đầu/cuối từ vị trí cuộn. Tạo thêm card khi thiếu, ẩn card
        thừa và bỏ tham chiếu icon cũ. Khi chưa có quy tắc, hiện hướng dẫn thêm.
        """
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        first = max(0, int(self.canvas.canvasy(0)//self.ROW_HEIGHT))
        last = min(len(self.rows), int((self.canvas.canvasy(0)+height)//self.ROW_HEIGHT)+2)
        needed = max(0, last-first)
        self.canvas.delete("empty")
        if not self.rows:
            self.canvas.create_text(width/2, max(45, height/2), text="Chưa có giới hạn. Thêm ứng dụng để bắt đầu.",
                                     fill=MUTED, font=("Segoe UI", 10), tags="empty")
        while len(self.pool) < needed:
            row = _LimitRow(self.canvas, self)
            item = self.canvas.create_window(0, 0, window=row, anchor="nw", width=width,
                                              height=self.ROW_HEIGHT)
            self.pool.append((row, item))
        for offset, (widget, item) in enumerate(self.pool):
            if offset >= needed:
                self.canvas.itemconfigure(item, state="hidden")
                widget.rule, widget.photo = None, None
                widget.icon.configure(image="")
                continue
            index = first+offset
            self.canvas.coords(item, 0, index*self.ROW_HEIGHT)
            self.canvas.itemconfigure(item, state="normal", width=width)
            widget.update_rule(self.rows[index], index)

    def toggle(self, rule):
        """Yêu cầu service đảo trạng thái bật/tắt của rule rồi làm mới danh sách.

        rule=None thì bỏ qua. Lỗi được hiển thị qua status_var.
        """
        if rule is None:
            return
        try:
            self.service.set_enabled(rule["id"], not rule["enabled"])
            self.status_var.set("")
            self.refresh()
        except Exception as exc:
            self.status_var.set(str(exc))

    def remove(self, rule):
        """Yêu cầu service xóa rule theo ID và cập nhật giao diện, hoặc hiện lỗi."""
        if rule is None:
            return
        try:
            self.service.delete_rule(rule["id"])
            self.status_var.set("")
            self.refresh()
        except Exception as exc:
            self.status_var.set(str(exc))

    def open_dialog(self, rule=None):
        """Mở form thêm giới hạn hoặc chỉnh sửa rule được truyền vào.

        Khi thêm mới, AppPicker cho chọn app theo tên và icon. Khi chỉnh sửa,
        form dùng app đã chọn và hạn mức hiện tại. Nếu form đã mở thì đưa nó
        lên trước. Form được đặt giữa cửa sổ và nhận thao tác cho đến khi đóng.
        """
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            self.dialog.focus_set()
            return
        self.editing_rule = dict(rule) if rule is not None else None
        self.selected_app = dict(rule) if rule is not None else None
        self.selection_text.set("")
        self.path_var.set(rule["path"] if rule else "")
        self.minutes_var.set(str(rule["minutes"]) if rule else "180")
        self.dialog_status.set("")
        dialog = self.dialog = tk.Toplevel(self)
        dialog.withdraw()
        dialog.title("Chỉnh sửa giới hạn" if rule else "Thêm giới hạn ứng dụng")
        dialog.configure(bg=CARD)
        dialog.resizable(False, False)
        dialog.transient(self.winfo_toplevel())
        install_dark_frame(dialog)
        body = tk.Frame(dialog, bg=CARD, padx=24, pady=22)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        tk.Label(body, text="Ứng dụng", bg=CARD, fg=INK, anchor="w",
                 font=("Segoe UI", 11, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        if rule:
            # Form chỉnh sửa hiển thị app đã gắn với quy tắc đang chọn.
            selected = tk.Frame(body, bg=CARD)
            selected.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(13, 20))
            if self.image_provider:
                self.dialog_photo = self.image_provider(dict(kind="app", path=rule["path"],
                    name=ntpath.basename(rule["path"]), color="#82AFFF"))
                self.dialog_icon = tk.Label(selected, image=self.dialog_photo, bg=CARD)
                self.dialog_icon.pack(side="left", padx=(0, 10))
            tk.Label(selected, text=display_name(rule.get("name") or ntpath.basename(rule["path"])),
                     bg=CARD, fg=INK, font=("Segoe UI", 11)).pack(side="left")
        else:
            self.picker = AppPicker(body, self.select_app, self.image_provider, self.catalog_provider)
            self.picker.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(12, 20))
        tk.Label(body, text="Giới hạn mỗi ngày (phút)", bg=CARD, fg=INK).grid(row=2, column=0, columnspan=2, sticky="w")
        self.minutes_entry = tk.Entry(body, textvariable=self.minutes_var, bg="#303030", fg=INK,
                                       insertbackground=INK, relief="flat", bd=8,
                                       highlightthickness=0, width=12)
        self.minutes_entry.grid(row=3, column=0, sticky="w", pady=(7, 12))
        if not rule:
            tk.Label(body, textvariable=self.selection_text, bg=CARD, fg=MUTED,
                     wraplength=300, anchor="e", font=("Segoe UI", 9)).grid(
                         row=3, column=1, sticky="e", pady=(7, 12))
        tk.Label(body, text="Nhắc trước 15, 10, 5 phút. Hết giờ sẽ đóng app và các tiến trình con.",
                 bg=CARD, fg=MUTED, anchor="w", justify="left", font=("Segoe UI", 9)).grid(row=4, column=0, columnspan=2, sticky="w")
        tk.Label(body, textvariable=self.dialog_status, bg=CARD, fg="#E6AD78", anchor="w",
                 justify="left", wraplength=495, font=("Segoe UI", 9)).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 12))
        buttons = tk.Frame(body, bg=CARD)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e")
        SoftButton(buttons, "Hủy", self.close_dialog, width=85, height=38).pack(side="left", padx=(0, 8))
        self.save_button = SoftButton(buttons, "Lưu giới hạn", self.save_dialog,
                                       width=128, height=38, fill="#3A3A3A")
        self.save_button.pack(side="left")
        dialog.protocol("WM_DELETE_WINDOW", self.close_dialog)
        dialog.bind("<Escape>", lambda _: self.close_dialog())
        self.minutes_entry.bind("<Return>", lambda _: self.save_dialog())
        dialog.update_idletasks()
        owner = self.winfo_toplevel()
        x = owner.winfo_rootx()+max(0, (owner.winfo_width()-dialog.winfo_reqwidth())//2)
        y = owner.winfo_rooty()+max(0, (owner.winfo_height()-dialog.winfo_reqheight())//2)
        dialog.geometry(f"+{x}+{y}")
        dialog.deiconify()
        dialog.grab_set()
        self.minutes_entry.focus_set()
        self.minutes_entry.selection_range(0, "end")

    def select_app(self, app):
        """Nhận app từ AppPicker và cập nhật tên, đường dẫn trong form.

        app là dict chứa name/path; None nghĩa là xóa lựa chọn hiện tại.
        """
        self.selected_app = app
        self.path_var.set(app["path"] if app else "")
        self.selection_text.set(app["name"] if app else "")
        self.dialog_status.set("")

    def save_dialog(self):
        """Kiểm tra số phút và đường dẫn EXE rồi gửi quy tắc cho service lưu.

        Quy tắc mới được bật mặc định; chỉnh sửa dùng trạng thái bật hiện tại.
        Thành công thì đóng form, làm mới danh sách và trả True. Khi lỗi, giữ
        form để sửa, hiển thị thông báo và trả False.
        """
        if self.dialog is None or not self.dialog.winfo_exists():
            return False
        try:
            minutes = limit_minutes(self.minutes_var.get())
            path = self.path_var.get().strip()
            if not ntpath.isabs(path) or not path.casefold().endswith(".exe"):
                raise ValueError("Chọn một ứng dụng trong danh sách trước khi lưu.")
            enabled = self.editing_rule["enabled"] if self.editing_rule else True
            if self.selected_app and not self.editing_rule:
                self.service.save_rule(path, minutes, enabled, name=self.selected_app["name"])
            else:
                self.service.save_rule(path, minutes, enabled)
        except Exception as exc:
            self.dialog_status.set(str(exc))
            return False
        self.close_dialog()
        self.status_var.set("")
        self.refresh()
        return True

    def close_dialog(self):
        """Đóng form, trả quyền thao tác cho cửa sổ và xóa tham chiếu lựa chọn/icon."""
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.grab_release()
            self.dialog.destroy()
        self.dialog = None
        self.editing_rule = None
        self.picker = None
        self.selected_app = None
        self.dialog_photo = None
        self.dialog_icon = None
