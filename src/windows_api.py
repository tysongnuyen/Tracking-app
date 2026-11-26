"""Gọi Windows API để lấy mẫu app, focus, idle và vị trí các cửa sổ."""
from __future__ import annotations
import ctypes as c
from ctypes import wintypes as w
from dataclasses import dataclass, field
import os
import time
import psutil
from visibility import WindowGeometry, visible_windows
from window_events import WindowEvents

BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe"}
user = c.WinDLL("user32", use_last_error=True)
dwm = c.WinDLL("dwmapi", use_last_error=True)
kernel = c.WinDLL("kernel32", use_last_error=True)
CALLBACK = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
user.GetForegroundWindow.restype = w.HWND
user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
user.GetWindowThreadProcessId.restype = w.DWORD
user.IsWindowVisible.argtypes = [w.HWND]
user.GetWindowTextLengthW.argtypes = [w.HWND]
user.IsIconic.argtypes=[w.HWND]
user.GetWindowRect.argtypes=[w.HWND,c.POINTER(w.RECT)]
user.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
user.GetWindowLongW.argtypes=[w.HWND,c.c_int]
user.GetWindowLongW.restype=w.LONG
user.GetTopWindow.argtypes=[w.HWND]
user.GetTopWindow.restype=w.HWND
user.GetWindow.argtypes=[w.HWND,w.UINT]
user.GetWindow.restype=w.HWND
user.EnumWindows.argtypes = [CALLBACK, w.LPARAM]
user.EnumWindows.restype = w.BOOL
user.OpenInputDesktop.argtypes = [w.DWORD, w.BOOL, w.DWORD]
user.OpenInputDesktop.restype = w.HANDLE
user.CloseDesktop.argtypes = [w.HANDLE]
dwm.DwmGetWindowAttribute.argtypes = [w.HWND, w.DWORD, c.c_void_p, w.DWORD]
kernel.GetTickCount.restype = w.DWORD
kernel.CloseHandle.argtypes=[w.HANDLE]
user.MessageBoxW.argtypes=[w.HWND,w.LPCWSTR,w.LPCWSTR,w.UINT]

class LASTINPUTINFO(c.Structure):
    """Cấu trúc ctypes nhận mốc thao tác gần nhất từ GetLastInputInfo của Windows."""
    _fields_ = [("cbSize", w.UINT), ("dwTime", w.DWORD)]
user.GetLastInputInfo.argtypes = [c.POINTER(LASTINPUTINFO)]

@dataclass
class Sample:
    """Dữ liệu của một lần lấy mẫu, được Collector chuyển thành Observation.

    apps chứa tên app; foreground/hwnd/pid xác định cửa sổ đang có focus.
    idle_seconds là số giây không thao tác, unlocked cho biết đã truy cập được
    input desktop. windows giữ hình học cửa sổ, shown đánh dấu HWND còn thấy,
    visible chứa các app còn thấy và geometry_known báo có dữ liệu màn hình.
    """
    apps: set[str]
    foreground: str | None
    hwnd: int
    pid: int
    idle_seconds: float
    unlocked: bool
    visible:set[str]=field(default_factory=set)
    windows:list[WindowGeometry]=field(default_factory=list)
    shown:dict[int,bool]=field(default_factory=dict)
    geometry_known:bool=True

class WindowsSampler:
    """Lấy mẫu Windows và cache thông tin tiến trình, bố cục và độ hiển thị."""
    def __init__(self):
        """Chuẩn bị cache và WindowEvents, loại chính process Tracking khỏi app dùng.

        excluded_pids cũng được bổ sung PID của dashboard khi GUI khởi chạy.
        Các mốc layout dùng để quyết định khi nào cần đọc lại bố cục cửa sổ.
        """
        self.cache = {}
        self.own_pid = os.getpid()
        self.excluded_pids={self.own_pid}
        self.events=WindowEvents()
        self.layout=[]
        self.layout_at=-100.
        self.layout_revision=-1
        self.previous_foreground=0
        self.shown={}
        self.geometry_known=False

    def start(self):
        """Thiết lập nhận biết DPI theo màn hình nếu hỗ trợ, rồi bật WindowEvents.

        DPI awareness giúp tọa độ cửa sổ nhất quán trên màn hình có mức scale khác nhau.
        """
        try:user.SetProcessDpiAwarenessContext(c.c_void_p(-4))
        except (AttributeError,OSError):pass
        self.events.start()

    def close(self):
        """Dừng luồng và gỡ event hook do WindowEvents quản lý."""
        self.events.close()

    def app_paths(self, names):
        """Tìm đường dẫn EXE của các tên app để dashboard lấy icon.

        Xét tối đa 128 tên từ names và các cửa sổ có trong layout cache.
        Kiểm tra lại tên process trước khi dùng đường dẫn; bỏ qua process đã
        thoát hoặc thiếu quyền truy cập. Trả dict tên app -> đường dẫn EXE.
        """
        wanted = {name.lower() for name in names[:128] if isinstance(name, str)}
        result = {}
        for window in self.layout:
            if window.app in wanted and window.app not in result:
                try:
                    process = psutil.Process(window.pid)
                    if process.name().lower() == window.app:
                        result[window.app] = process.exe()
                except (psutil.Error, OSError):
                    pass
        return result

    def process(self, hwnd):
        """Tra PID và tên EXE viết thường của cửa sổ hwnd.

        Dùng cache tên process trong 30 giây. Nếu PID thuộc nhóm loại trừ,
        đã thoát hoặc thiếu quyền đọc, trả (pid, None).
        """
        pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, c.byref(pid))
        if not pid.value or pid.value in self.excluded_pids:
            return pid.value, None
        now = time.monotonic()
        cached = self.cache.get(pid.value)
        if cached and now - cached[0] < 30:
            return pid.value, cached[1]
        try:
            name = psutil.Process(pid.value).name().lower()
        except (psutil.Error, OSError):
            return pid.value, None
        self.cache[pid.value] = (now, name)
        return pid.value, name

    def monitors(self):
        """Trả danh sách khung tọa độ (trái, trên, phải, dưới) của các màn hình.

        EnumDisplayMonitors gọi visit() cho từng màn hình được Windows liệt kê.
        """
        monitors=[]
        monitor_callback=c.WINFUNCTYPE(w.BOOL,w.HANDLE,w.HDC,c.POINTER(w.RECT),w.LPARAM)
        @monitor_callback
        def visit(monitor,dc,rect,param):
            """Nhận RECT của một màn hình, thêm vào kết quả và tiếp tục liệt kê."""
            r=rect.contents;monitors.append((r.left,r.top,r.right,r.bottom));return True
        user.EnumDisplayMonitors(None,None,visit,0)
        return monitors

    def _layout(self):
        """Đọc hình học các cửa sổ theo Z-order, từ trên xuống dưới.

        Ghép HWND, PID, tên app, vùng chữ nhật và trạng thái thu nhỏ thành
        WindowGeometry. Lọc cửa sổ ẩn/cloaked và lớp nền desktop. Cửa sổ có
        kiểu trong suốt được đánh dấu để visible_windows() ước lượng che phủ.
        """
        handles=[]
        @CALLBACK
        def visit(hwnd,_):
            """Callback EnumWindows thêm HWND vào danh sách cửa sổ cấp cao nhất."""
            handles.append(int(hwnd));return True
        user.EnumWindows(visit,0)
        available=set(handles)
        current=user.GetTopWindow(None)
        ordered=[];seen=set()
        while current and int(current) not in seen and len(seen)<2048:
            seen.add(int(current))
            if int(current) in available:ordered.append(int(current))
            current=user.GetWindow(current,2)
        windows=[]
        for hwnd in ordered:
            if not user.IsWindowVisible(hwnd):continue
            cloaked=w.DWORD()
            dwm.DwmGetWindowAttribute(hwnd,14,c.byref(cloaked),c.sizeof(cloaked))
            if cloaked.value:continue
            cls=c.create_unicode_buffer(64);user.GetClassNameW(hwnd,cls,len(cls))
            if cls.value in {"Progman","WorkerW"}:continue
            pid,app=self.process(hwnd)
            if user.GetWindowTextLengthW(hwnd)==0 or cls.value in {"Shell_TrayWnd","Shell_SecondaryTrayWnd"}:app=None
            rect=w.RECT()
            ok=dwm.DwmGetWindowAttribute(hwnd,9,c.byref(rect),c.sizeof(rect))==0
            if not ok:ok=bool(user.GetWindowRect(hwnd,c.byref(rect)))
            bounds=(rect.left,rect.top,rect.right,rect.bottom) if ok else None
            exstyle=user.GetWindowLongW(hwnd,-20)
            windows.append(WindowGeometry(hwnd,pid,app,bounds,bool(user.IsIconic(hwnd)),not bool(exstyle & (0x20|0x80000))))
        return windows

    def sample(self):
        """Trả một Sample về focus, các app, thời gian idle và cửa sổ còn thấy.

        Kiểm tra truy cập input desktop trước; khi thất bại, trả mẫu unlocked=False.
        Bố cục được làm mới khi đổi focus, quá 15 giây hoặc nhận sự kiện thay đổi
        và đã qua tối thiểu 0,5 giây từ lần làm mới trước. Dùng visible_windows()
        ước lượng phần còn thấy, GetLastInputInfo tính số giây không thao tác.
        """
        desktop = user.OpenInputDesktop(0, False, 0x0100)
        if not desktop:
            return Sample(set(), None, 0, 0, 0, False)
        user.CloseDesktop(desktop)
        foreground = user.GetForegroundWindow()
        pid, name = self.process(foreground) if foreground else (0, None)
        now=time.monotonic()
        dirty=self.layout_revision!=self.events.revision
        if int(foreground or 0)!=self.previous_foreground or now-self.layout_at>=15 or (dirty and now-self.layout_at>=.5):
            self.layout=self._layout();self.layout_at=now;self.layout_revision=self.events.revision
            monitors=self.monitors()
            self.shown=visible_windows(self.layout,monitors)
            self.geometry_known=bool(monitors)
        self.previous_foreground=int(foreground or 0)
        shown=self.shown
        apps={window.app for window in self.layout if window.app}
        visible={window.app for window in self.layout if window.app and shown.get(window.hwnd)}
        if name:
            apps.add(name)
        info = LASTINPUTINFO(c.sizeof(LASTINPUTINFO), 0)
        idle = ((kernel.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000 if user.GetLastInputInfo(c.byref(info)) else 0
        now = time.monotonic()
        self.cache = {pid: data for pid, data in self.cache.items() if now - data[0] < 60}
        return Sample(apps, name, int(foreground or 0), pid, idle, True,visible,self.layout,shown,self.geometry_known)

def single_instance():
    """Tạo hoặc mở named mutex để nhận biết phiên Tracking đang chạy.

    Trả (handle mutex, là phiên đầu tiên). App dùng kết quả để lần mở tiếp theo
    gửi yêu cầu hiện dashboard cho phiên cũ. Nơi gọi chịu trách nhiệm đóng handle.
    """
    kernel.CreateMutexW.argtypes = [c.c_void_p, w.BOOL, w.LPCWSTR]
    kernel.CreateMutexW.restype = w.HANDLE
    mutex = kernel.CreateMutexW(None, False, "Local\\TrackingApp-Python")
    return mutex, c.get_last_error() != 183

def show_signal():
    """Tạo hoặc mở Windows event có tên chung để yêu cầu hiện dashboard.

    Các process dùng chung event này để gửi/nhận tín hiệu mở lại GUI.
    Trả handle event; nơi gọi đóng handle sau khi dùng xong.
    """
    kernel.CreateEventW.argtypes=[c.c_void_p,w.BOOL,w.BOOL,w.LPCWSTR]
    kernel.CreateEventW.restype=w.HANDLE
    kernel.WaitForSingleObject.argtypes=[w.HANDLE,w.DWORD]
    kernel.WaitForSingleObject.restype=w.DWORD
    kernel.SetEvent.argtypes=[w.HANDLE]
    kernel.CloseHandle.argtypes=[w.HANDLE]
    return kernel.CreateEventW(None,False,False,"Local\\TrackingAppPythonShow")

def reopen_instance():
    """Bật show_signal để phiên Tracking đang chạy mở dashboard, rồi đóng handle."""
    signal=show_signal()
    if signal:kernel.SetEvent(signal);kernel.CloseHandle(signal)
