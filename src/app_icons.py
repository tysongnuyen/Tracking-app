"""Read application icons locally on a bounded, disposable GUI worker."""
from collections import OrderedDict
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import queue
import shutil
import threading
import winreg
from PIL import Image


class BITMAPINFOHEADER(c.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
                ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
                ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG),
                ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]


class BITMAPINFO(c.Structure):
    _fields_ = [("header", BITMAPINFOHEADER), ("colors", w.DWORD * 1)]


def resolve_executable(name, hint=None):
    if not name.lower().endswith(".exe") or any(char in name for char in "\\/"):
        return None
    candidates = [hint] if hint else []
    registry_path = "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\" + name
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, registry_path, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    if isinstance(value, str):
                        candidates.append(os.path.expandvars(value.strip('"')))
            except OSError:
                pass
    candidates.append(shutil.which(name))
    for candidate in candidates:
        if candidate and not str(candidate).startswith("\\\\"):
            path = Path(candidate)
            if path.is_absolute() and path.is_file() and path.name.lower() == name.lower():
                return str(path)
    return None


def extract_icon(path, size=32):
    shell, user, gdi = c.WinDLL("shell32"), c.WinDLL("user32"), c.WinDLL("gdi32")
    shell.ExtractIconExW.argtypes = [w.LPCWSTR, c.c_int, c.POINTER(w.HICON), c.POINTER(w.HICON), w.UINT]
    shell.ExtractIconExW.restype = w.UINT
    user.DestroyIcon.argtypes = [w.HICON]
    user.DrawIconEx.argtypes = [w.HDC, c.c_int, c.c_int, w.HICON, c.c_int, c.c_int, w.UINT, w.HBRUSH, w.UINT]
    user.DrawIconEx.restype = w.BOOL
    gdi.CreateCompatibleDC.argtypes = [w.HDC]
    gdi.CreateCompatibleDC.restype = w.HDC
    gdi.CreateDIBSection.argtypes = [w.HDC, c.POINTER(BITMAPINFO), w.UINT, c.POINTER(c.c_void_p), w.HANDLE, w.DWORD]
    gdi.CreateDIBSection.restype = w.HBITMAP
    gdi.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
    gdi.SelectObject.restype = w.HGDIOBJ
    gdi.DeleteObject.argtypes = [w.HGDIOBJ]
    gdi.DeleteDC.argtypes = [w.HDC]
    icon = w.HICON()
    dc = bitmap = previous = None
    try:
        count = shell.ExtractIconExW(str(path), 0, c.byref(icon), None, 1)
        if count != 1 or not icon:
            return None
        dc = gdi.CreateCompatibleDC(None)
        if not dc:
            return None
        info = BITMAPINFO()
        info.header = BITMAPINFOHEADER(c.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, size*size*4, 0, 0, 0, 0)
        bits = c.c_void_p()
        bitmap = gdi.CreateDIBSection(dc, c.byref(info), 0, c.byref(bits), None, 0)
        if not bitmap or not bits:
            return None
        previous = gdi.SelectObject(dc, bitmap)
        layers = []
        for background in (0, 255):
            c.memset(bits, background, size*size*4)
            if not user.DrawIconEx(dc, 0, 0, icon, size, size, 0, None, 3):
                return None
            gdi.GdiFlush()
            layers.append(c.string_at(bits, size*size*4))
        # Recover straight alpha for both legacy masked and modern premultiplied icons.
        black, white = layers
        pixels = bytearray(size*size*4)
        for i in range(0, len(pixels), 4):
            alpha = max(0, min(255, 255-max(white[i+j]-black[i+j] for j in range(3))))
            if alpha:
                pixels[i:i+4] = bytes((min(255, round(black[i+2]*255/alpha)),
                                       min(255, round(black[i+1]*255/alpha)),
                                       min(255, round(black[i]*255/alpha)), alpha))
        return Image.frombytes("RGBA", (size, size), bytes(pixels))
    finally:
        if previous and dc:
            gdi.SelectObject(dc, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if dc:
            gdi.DeleteDC(dc)
        if icon:
            user.DestroyIcon(icon)


class AppIcons:
    def __init__(self):
        self.cache = OrderedDict()
        self.pending = set()
        self.requests = queue.Queue(maxsize=64)
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = None
        self.revision = 0

    def get(self, name, hint=None):
        key = name.lower(), hint or ""
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            if key in self.pending or len(self.pending) >= 64 or self.stopped.is_set():
                return None
            self.pending.add(key)
            self.requests.put_nowait(key)
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True, name="AppIcons")
                self.thread.start()
        return None

    def run(self):
        while not self.stopped.is_set():
            try:
                key = self.requests.get(timeout=.5)
            except queue.Empty:
                continue
            if key is None:
                return
            try:
                path = resolve_executable(*key)
                picture = extract_icon(path) if path else None
            except (OSError, ValueError, c.ArgumentError):
                picture = None
            with self.lock:
                self.pending.discard(key)
                self.cache[key] = picture
                while len(self.cache) > 128:
                    self.cache.popitem(last=False)
                if picture is not None:
                    self.revision += 1

    def close(self):
        self.stopped.set()
        try:
            self.requests.put_nowait(None)
        except queue.Full:
            pass
