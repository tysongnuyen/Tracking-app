"""Ghép tín hiệu Windows và trình duyệt thành số liệu theo từng khoảng thời gian."""
from __future__ import annotations
from datetime import datetime, timezone
import threading
import time
import queue
from tracking_core import Observation
from windows_api import WindowsSampler, BROWSERS
from browser_reader import BrowserProbe
from visibility import area,intersect

def match_window(sample,client,entry,entry_count):
    """Tìm cửa sổ Windows tương ứng với một cửa sổ do extension báo về.

    sample là mẫu Windows hiện tại; client là tên browser, ví dụ chrome.exe.
    entry là dữ liệu một cửa sổ từ extension; entry_count là số cửa sổ browser
    trong bản dữ liệu đó. Ưu tiên cửa sổ có focus hoặc trường hợp chỉ có một
    cửa sổ. Nếu cần, so độ trùng vị trí/kích thước để ghép hai nguồn dữ liệu.
    Trả về HWND (mã cửa sổ), hoặc None khi thiếu thông tin hay ghép còn mơ hồ.
    """
    windows=[w for w in getattr(sample,"windows",[]) if w.app==client]
    if entry.get("focused") and sample.foreground==client:return sample.hwnd
    if len(windows)==1 and entry_count==1:return windows[0].hwnd
    bounds=entry.get("bounds")
    if not bounds:return None
    scores=[]
    for window in windows:
        if not window.rect:continue
        overlap=intersect(tuple(bounds),window.rect)
        overlap_area=area(overlap) if overlap else 0
        union=area(tuple(bounds))+area(window.rect)-overlap_area
        score=overlap_area/union if union else 0
        if score>.9:scores.append((score,window.hwnd))
    scores.sort(reverse=True)
    return scores[0][1] if scores and (len(scores)==1 or scores[0][0]-scores[1][0]>.05) else None

def uia_candidates(sample,snapshots):
    """Chọn những cửa sổ browser cần BrowserProbe đọc bằng UI Automation.

    snapshots chứa trạng thái tab do Bridge nhận từ extension. Cửa sổ đang
    hiển thị và chưa được extension mô tả đầy đủ sẽ được đưa vào danh sách.
    Trả về các cặp (HWND, PID), ưu tiên cửa sổ đang có focus.
    """
    supplied=set()
    for client,snapshot in snapshots.items():
        entries=snapshot.get("windows",[])
        for entry in entries:
            if entry.get("complete"):
                native=match_window(sample,client,entry,len(entries))
                if native:supplied.add(native)
    candidates=[(w.hwnd,w.pid) for w in sample.windows if w.app in BROWSERS and sample.shown.get(w.hwnd) and w.hwnd not in supplied]
    candidates.sort(key=lambda p:p[0]!=sample.hwnd)
    return candidates

def observations_for(sample, idle_threshold, host, snapshots,window_hosts=None):
    """Ghép tín hiệu Windows và browser thành danh sách trạng thái Observation.

    sample chứa các app, cửa sổ, focus và số giây không thao tác trên Windows.
    idle_threshold là ngưỡng idle tính bằng giây; 0 nghĩa là tắt nhận diện idle.
    host là tên miền của browser có focus; snapshots là dữ liệu từ extension;
    window_hosts ánh xạ HWND sang tên miền do BrowserProbe đọc được.

    Mỗi Observation mô tả một app/website đang có focus, idle, còn hiển thị,
    chạy nền hoặc chưa rõ trạng thái. Khi máy khóa, trả về danh sách rỗng.
    Collector._run() dùng danh sách này để tính thời gian ở nhịp kế tiếp.
    """
    if not sample.unlocked:
        return []
    mode = "idle" if idle_threshold and sample.idle_seconds >= idle_threshold else "screen"
    visible=getattr(sample,"visible",set())
    geometry_known=getattr(sample,"geometry_known",False)
    items = [Observation("app",app,mode if app==sample.foreground else "visible" if app in visible else "background",
                         True,geometry_known or app==sample.foreground) for app in sample.apps]
    # Complete snapshots from the optional bridge take priority over UIA.
    has_selected_snapshot = False
    for client, snapshot in snapshots.items():
        if client not in sample.apps:
            continue
        selected = sample.foreground == client and snapshot["focused"]
        windows=snapshot.get("windows",[])
        for entry in windows:
            native=match_window(sample,client,entry,len(windows))
            shown=getattr(sample,"shown",{}).get(native) if native else None
            if native==sample.hwnd and sample.foreground==client:shown=True
            for website in entry["hosts"]:
                if selected and entry["focused"] and website==snapshot["active"]:
                    state,known=mode,True
                elif shown is False or entry["minimized"]:
                    state,known="background",True
                elif website in entry["visible"] and shown is True:
                    state,known="visible",True
                elif entry["complete"] and website not in entry["visible"]:
                    state,known="background",True
                else:state,known="unknown",False
                items.append(Observation("web",website,state,True,known,client))
        described=set().union(*(w["hosts"] for w in windows)) if windows else set()
        for website in snapshot["hosts"]-described:
            active=selected and website==snapshot["active"]
            items.append(Observation("web",website,mode if active else "unknown",True,active,client))
        has_selected_snapshot |= selected
    if host and not has_selected_snapshot and sample.foreground in BROWSERS:
        items.append(Observation("web", host, mode, False,True,sample.foreground))
    for window in getattr(sample,"windows",[]):
        website=(window_hosts or {}).get(window.hwnd)
        if not website or not getattr(sample,"shown",{}).get(window.hwnd):continue
        if window.hwnd==sample.hwnd and has_selected_snapshot:continue
        state=mode if window.hwnd==sample.hwnd else "visible"
        items.append(Observation("web",website,state,False,True,window.app))
    return items

class Collector:
    """Điều phối lấy mẫu, cộng thời gian qua Store và cập nhật giới hạn app."""
    def __init__(self, store, bridge):
        """Chuẩn bị Collector và khôi phục các cài đặt đã lưu.

        store quản lý số liệu và SQLite; bridge cung cấp trạng thái tab từ
        extension. Tạo WindowsSampler, BrowserProbe, khóa đồng bộ và hàng đợi
        thông báo giới hạn. Đọc trạng thái chạy/tạm dừng, mốc đã bắt đầu và
        ngưỡng idle từ Store. Luồng thu thập được tạo và chờ start() để chạy.
        """
        self.store, self.bridge = store, bridge
        self.sampler = WindowsSampler()
        self.probe = BrowserProbe()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        history = store.has_activity()
        saved_pause = store.setting("paused")
        self.paused = saved_pause == "1" if saved_pause is not None else not history
        saved_started = store.setting("tracking_started")
        self.has_started = saved_started == "1" or history or not self.paused
        if saved_started != str(int(self.has_started)):
            store.set_setting("tracking_started", int(self.has_started))
        self.idle_threshold = int(store.setting("idle_seconds", "180"))
        self.current_app = "Đang khởi động"
        self.current_host = None
        self.web_status = "Chọn một cửa sổ trình duyệt để đọc website"
        self.error = ""
        self.limit_error = ""
        self.limits = None
        self.limit_events = queue.Queue(maxsize=128)
        self.generation = 0
        self.thread = threading.Thread(target=self._run, daemon=True, name="WindowsCollector")

    def start(self):
        """Bật theo dõi sự kiện Windows và chạy vòng lấy mẫu ở luồng nền."""
        self.sampler.start()
        self.thread.start()

    def set_paused(self, value):
        """Tạm dừng khi value=True, bắt đầu hoặc tiếp tục khi value=False.

        Lưu trạng thái vào Store để giữ qua lần mở app sau. Tăng generation
        mỗi khi đổi trạng thái để _run() bỏ qua khoảng thời gian đi ngang
        thời điểm thay đổi, tránh cộng nhầm theo mẫu trước đó.
        """
        with self.lock:
            value = bool(value)
            if self.paused == value:
                return
            if not value:
                self.store.set_setting("tracking_started", 1)
            self.store.set_setting("paused", int(value))
            self.paused = value
            if not value:
                self.has_started = True
            self.generation += 1
            self.current_host = None

    def set_idle(self, value):
        """Đổi và lưu ngưỡng không thao tác, với value tính bằng giây.

        Chấp nhận 0 (tắt), 60, 180, 300 hoặc 900 giây. Giá trị khác gây lỗi
        ValueError. Tăng generation để lần lấy mẫu sau dùng cài đặt mới.
        """
        if value not in {0, 60, 180, 300, 900}:
            raise ValueError("Ngưỡng không hợp lệ")
        with self.lock:
            self.idle_threshold = value
            self.generation += 1
            self.store.set_setting("idle_seconds", value)

    def status(self):
        """Trả trạng thái hiện tại để dashboard và system tray hiển thị.

        Kết quả là dict gồm trạng thái chạy/tạm dừng, app và website hiện tại,
        thông báo đọc website, ngưỡng idle và lỗi đang có. Đọc trong khóa để
        các giá trị nhất quán khi luồng thu thập đang cập nhật dữ liệu.
        """
        with self.lock:
            return dict(paused=self.paused, has_started=self.has_started, app=self.current_app, host=self.current_host,
                        web_status=self.web_status, error=self.error or self.limit_error or self.store.save_error,idle_threshold=self.idle_threshold)

    def queue_limit_event(self, event):
        """Đưa một sự kiện giới hạn app vào hàng đợi để system tray thông báo.

        Hàng đợi chứa tối đa 128 sự kiện. Khi đầy, bỏ sự kiện cũ nhất rồi thử
        thêm sự kiện mới. Dùng put_nowait() để luồng thu thập tiếp tục chạy.
        """
        try:self.limit_events.put_nowait(event)
        except queue.Full:
            try:self.limit_events.get_nowait()
            except queue.Empty:pass
            try:self.limit_events.put_nowait(event)
            except queue.Full:pass

    def pop_limit_event(self):
        """Lấy sự kiện giới hạn cũ nhất; trả None ngay nếu hàng đợi đang rỗng."""
        try:return self.limit_events.get_nowait()
        except queue.Empty:return None

    def _run(self):
        """Chạy vòng thu thập cho đến khi stop() yêu cầu kết thúc.

        Mỗi nhịp cộng khoảng thời gian vừa qua cho mẫu trước bằng Store.record(),
        rồi lấy mẫu Windows và dữ liệu website để chuẩn bị cho nhịp kế tiếp.
        Bỏ qua khoảng quá dài, đổi đồng hồ, tạm dừng hoặc đổi cài đặt giữa nhịp.

        Store.record() cộng dồn trong RAM; Store.flush() ghi SQLite khoảng
        30 giây một lần. AppLimits cũng được cập nhật trong vòng này để tính
        hạn mức và áp dụng chặn app. Giữa các nhịp nghỉ khoảng 1 giây.
        Khi vòng lặp kết thúc, đóng BrowserProbe và WindowsSampler.
        """
        previous = []
        previous_pid = None
        last_mono, last_wall = time.monotonic(), datetime.now(timezone.utc)
        last_flush = last_mono
        previous_generation = self.generation
        try:
            while not self.stop_event.is_set():
                now_mono, now_wall = time.monotonic(), datetime.now(timezone.utc)
                limit_pid, limit_count = previous_pid, False
                with self.lock:
                    try:
                        elapsed, wall_elapsed = now_mono - last_mono, (now_wall - last_wall).total_seconds()
                        if not self.paused and previous_generation == self.generation and 0 < elapsed <= 3 and abs(elapsed - wall_elapsed) < 1:
                            self.store.record(last_wall, now_wall, previous)
                            limit_count = True
                        if self.paused:
                            if self.probe.process:self.probe.close()
                            previous = []
                            previous_pid = None
                            self.current_app = "Đã tạm dừng"
                            self.current_host = None
                            self.web_status = "Không ghi nhận website khi tạm dừng"
                        else:
                            sample = self.sampler.sample()
                            snapshots = self.bridge.current()
                            browser = sample.foreground in BROWSERS and sample.unlocked
                            candidates=uia_candidates(sample,snapshots)
                            window_hosts=self.probe.poll_many(candidates,self.sampler.events.title_revision)
                            host=window_hosts.get(sample.hwnd)
                            snap = snapshots.get(sample.foreground)
                            if snap and snap["focused"]:
                                host = snap["active"]
                                self.web_status = "Website + tab nền qua tiện ích tùy chọn"
                            else:
                                self.web_status = self.probe.status if browser else "Website được đọc khi bạn chọn trình duyệt"
                            self.current_app = (sample.foreground or "Tracking App / chưa có cửa sổ được chọn") if sample.unlocked else "Máy đang khóa"
                            self.current_host = host if browser else None
                            previous = observations_for(sample, self.idle_threshold, self.current_host, snapshots,window_hosts)
                            previous_pid = sample.pid if sample.unlocked and sample.foreground else None
                        if now_mono - last_flush >= 30:
                            self.store.flush()
                            last_flush = now_mono
                        self.error = ""
                    except Exception:
                        previous = []
                        previous_pid = None
                        self.error = self.store.save_error or "Chưa đọc được trạng thái Windows · đang thử lại"
                    previous_generation = self.generation
                if self.limits is not None:
                    try:
                        self.limits.tick(last_wall, now_wall, limit_pid, count_usage=limit_count)
                        self.limit_error = ""
                    except Exception:
                        self.limit_error = "Chưa cập nhật được giới hạn ứng dụng · đang thử lại"
                last_mono, last_wall = now_mono, now_wall
                self.stop_event.wait(1)
        finally:
            self.probe.close()
            self.sampler.close()

    def stop(self):
        """Yêu cầu dừng vòng lấy mẫu và chờ luồng kết thúc tối đa 6 giây.

        stop_event đánh thức cả lúc _run() đang nghỉ giữa hai nhịp. Nếu luồng
        vẫn chạy sau thời gian chờ, báo RuntimeError để nơi gọi xử lý.
        """
        self.stop_event.set()
        self.thread.join(6)
        if self.thread.is_alive():
            raise RuntimeError("Bộ thu thập chưa dừng xong; hãy thử đóng lại")
