"""Read browser chrome through UIA; never activate a tab or inspect page fields."""
from __future__ import annotations
import multiprocessing as mp
import re
import time
from functools import lru_cache
from urllib.parse import urlsplit

ADDRESS_NAMES = (
    "Address and search bar", "Address bar", "Search or enter address",
    "Search with Google or enter address", "Search with Google or enter address bar",
    "Thanh địa chỉ và tìm kiếm", "Thanh địa chỉ", "Tìm kiếm hoặc nhập địa chỉ",
    "Tìm kiếm bằng Google hoặc nhập địa chỉ", "Search or enter web address",
)
ADDRESS_IDS = ("urlbar-input", "addressEditBox", "omnibox")
# Hàm xử lý tên miền địa chỉ của những trang web ta sử dụng
def hostname(value: str | None) -> str | None:
    if not value or len(value) > 16384:
        return None
    value = value.strip()
    if re.search(r"\s", value):
        return None
    if "://" not in value:
        if value.startswith(("about:", "file:", "data:", "javascript:", "chrome:", "edge:", "brave:", "view-source:")):
            return None
        value = "https://" + value
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or parts.username or parts.password:
            return None
        host = (parts.hostname or "").rstrip(".").lower().encode("idna").decode("ascii")
        host = host.removeprefix("www.")
        if host in {"localhost", "127.0.0.1", "::1"} or "." not in host:
            return None
        if len(host) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            return None
        return host
    except (ValueError, UnicodeError):
        return None

# Sử dụng để gọi COM Component object model, cho phép các app gọi nhau thông qua interface
@lru_cache(maxsize=1)
def ui_automation():
    import comtypes.client
    sdk = comtypes.client.GetModule("UIAutomationCore.dll")
    return sdk, comtypes.client.CreateObject(sdk.CUIAutomation, interface=sdk.IUIAutomation)

# Xử lý phần lấy luồng cho

def read_address(hwnd: int):
    if not hwnd:
        return None, "Không có cửa sổ trình duyệt"
    # UIA is imported only in the isolated child process, with COM initialized there.
    dll, ui = ui_automation()
    root = ui.ElementFromHandle(hwnd)
    if not root:
        return None, "Không tìm thấy cửa sổ trình duyệt"
    conditions = [ui.CreatePropertyCondition(dll.UIA_NamePropertyId, name) for name in ADDRESS_NAMES]
    conditions += [ui.CreatePropertyCondition(dll.UIA_AutomationIdPropertyId, aid) for aid in ADDRESS_IDS]
    match = conditions[0]
    for condition in conditions[1:]:
        match = ui.CreateOrCondition(match, condition)
    match = ui.CreateAndCondition(match, ui.CreatePropertyCondition(dll.UIA_ControlTypePropertyId, dll.UIA_EditControlTypeId))
    elements = root.FindAll(dll.TreeScope_Descendants, match)
    walker = ui.ControlViewWalker
    for i in range(min(elements.Length, 20)):
        edit = elements.GetElement(i)
        # Never accept a similarly named input inside a web document.
        parent, in_document = edit, False
        for _ in range(40):
            if parent.CurrentControlType == dll.UIA_DocumentControlTypeId:
                in_document = True
                break
            if ui.CompareElements(parent, root):
                break
            parent = walker.GetParentElement(parent)
            if not parent:
                in_document = True
                break
        else:
            in_document = True
        if in_document or edit.CurrentIsPassword or edit.CurrentIsOffscreen:
            continue
        if edit.CurrentHasKeyboardFocus:
            return None, "Đang nhập địa chỉ · chưa tính website"
        pattern = edit.GetCurrentPattern(dll.UIA_ValuePatternId)
        if not pattern:
            continue
        # Discard the full value in this child; only normalized hostname leaves it.
        host = hostname(pattern.QueryInterface(dll.IUIAutomationValuePattern).CurrentValue)
        return (host, "Đọc website qua Windows UI Automation") if host else (None, "Trang nội bộ / địa chỉ chưa xác định")
    return None, "Chưa đọc được thanh địa chỉ · app vẫn được tính"

# Gửi các sự kiện, tên miền, thời điểm vào trong collector (Một thằng phụ trách cho database)
def _worker(connection):
    import sys
    sys.coinit_flags = 0
    import comtypes
    comtypes.CoInitializeEx(0)
    try:
        ui_automation()
        while True:
            request = connection.recv()
            if request is None:
                return
            seq, hwnd, pid = request
            try:
                host, status = read_address(hwnd)
            except Exception:
                host, status = None, "UI Automation chưa đọc được website"
            connection.send((seq, hwnd, pid, host, status))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()
        comtypes.CoUninitialize()

# Quản lý việc giao tiếp, yêu cầu của worker tới máy
class BrowserProbe:
    def __init__(self):
        self.process = self.connection = None
        self.pending = None
        self.last_request = -100.
        self.sequence = 0
        self.result = None
        self.cache={}
        self.request_times={}
        self.last_candidate=time.monotonic()
        self.status = "Chọn một cửa sổ trình duyệt để nhận diện website"

    def _start(self):
        self.connection, child = mp.get_context("spawn").Pipe()
        self.process = mp.get_context("spawn").Process(target=_worker, args=(child,), daemon=True, name="TrackingApp-UIA")
        self.process.start()
        child.close()

    def poll_many(self,candidates,revision=lambda hwnd:0):
        now = time.monotonic()
        if candidates:self.last_candidate=now
        if not candidates and now-self.last_candidate>15 and self.process:
            self.close()
        if self.pending and now - self.pending[3] > 4.0:
            self.close()
            self.status = "Đọc website quá hạn · sẽ thử lại"
            self.last_request = now + 5
        if self.connection:
            try:
                if self.connection.poll():
                    seq, handle, process_id, host, status = self.connection.recv()
                    if self.pending and seq == self.pending[0]:
                        self.result = (handle, process_id, host, self.pending[3])
                        self.cache[(handle,process_id)]=(host,status,self.pending[3],self.pending[4])
                        self.status = status
                        self.pending = None
            except (EOFError, OSError):
                self.close()
                self.status = "Bộ đọc website đang khởi động lại"
        target=None
        for hwnd,pid in candidates[:8]:
            key=(hwnd,pid);cached=self.cache.get(key)
            due=not cached or now-cached[2]>=10 or cached[3]!=revision(hwnd)
            if due and now-self.request_times.get(key,-100)>=2:
                target=key;break
        if target and not self.pending and now-self.last_request>=1:
            hwnd,pid=target
            if not self.process or not self.process.is_alive():
                self.close()
                self._start()
            self.sequence += 1
            self.pending = (self.sequence, hwnd, pid, now, revision(hwnd))
            self.last_request = now
            self.request_times[target]=now
            try:
                self.connection.send(self.pending[:3])
            except OSError:
                self.close()
        valid=set(candidates[:8])
        self.cache={k:v for k,v in self.cache.items() if k in valid and now-v[2]<=12}
        self.request_times={k:v for k,v in self.request_times.items() if now-v<30}
        return {hwnd:data[0] for (hwnd,pid),data in self.cache.items() if data[0]}

    def poll(self,hwnd,pid,browser):
        return self.poll_many([(hwnd,pid)] if browser else []).get(hwnd)

    def close(self):
        if self.connection:
            try:
                self.connection.send(None)
            except (OSError, BrokenPipeError):
                pass
        if self.process:
            self.process.join(.15)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(.3)
            self.process.close()
        if self.connection:
            self.connection.close()
        self.process = self.connection = self.pending = None
        self.result = None
        self.cache.clear()
