"""Discover selectable desktop apps locally, without launching or scanning disks.

Only visible app processes and Win32 shortcuts under the two Start Menu Programs
folders are inspected. The caller runs this on a disposable GUI worker; the
background collector never imports or polls the catalogue.
"""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import re
import uuid

import psutil

from process_blocker import ProcessBlocker


# A shortcut to a command interpreter or shared launcher is not the identity of
# the application named by that shortcut. Unsupported packaged apps are omitted.
_HOSTS = frozenset({
    "cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe",
    "mshta.exe", "rundll32.exe", "regsvr32.exe", "explorer.exe",
    "applicationframehost.exe", "launchwinapp.exe", "java.exe", "javaw.exe",
    "python.exe", "pythonw.exe", "py.exe", "update.exe", "squirrel.exe",
})
_MAX_SHORTCUTS = 4096


class _GUID(c.Structure):
    _fields_ = [("data1", w.DWORD), ("data2", w.WORD), ("data3", w.WORD),
                ("data4", c.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _com_method(pointer, index, result, *arguments):
    table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(result, c.c_void_p, *arguments)(table[index])


class _ShortcutReader:
    """Use IShellLink directly: no generated COM wrappers or Shell execution."""

    def __init__(self):
        self.ole = c.WinDLL("ole32")
        self.link, self.persist = c.c_void_p(), c.c_void_p()
        self.initialized = False

    def __enter__(self):
        self.ole.CoInitializeEx.argtypes = [c.c_void_p, w.DWORD]
        self.ole.CoInitializeEx.restype = c.c_long
        result = self.ole.CoInitializeEx(None, 2)
        self.initialized = result >= 0
        # RPC_E_CHANGED_MODE means COM is already initialized on this thread.
        if result < 0 and result != -2147417850:
            raise OSError("Không thể đọc lối tắt ứng dụng.")
        try:
            self.ole.CoCreateInstance.argtypes = [c.POINTER(_GUID), c.c_void_p,
                                                 w.DWORD, c.POINTER(_GUID),
                                                 c.POINTER(c.c_void_p)]
            self.ole.CoCreateInstance.restype = c.c_long
            clsid = _GUID.parse("00021401-0000-0000-C000-000000000046")
            iid = _GUID.parse("000214F9-0000-0000-C000-000000000046")
            if self.ole.CoCreateInstance(c.byref(clsid), None, 1, c.byref(iid),
                                         c.byref(self.link)) < 0:
                raise OSError("Không thể mở bộ đọc lối tắt.")
            iid_file = _GUID.parse("0000010b-0000-0000-C000-000000000046")
            query = _com_method(self.link, 0, c.c_long, c.POINTER(_GUID),
                                c.POINTER(c.c_void_p))
            if query(self.link, c.byref(iid_file), c.byref(self.persist)) < 0:
                raise OSError("Không thể đọc tệp lối tắt.")
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        for pointer in (self.persist, self.link):
            if pointer:
                _com_method(pointer, 2, w.ULONG)(pointer)
                pointer.value = None
        if self.initialized:
            self.ole.CoUninitialize()
            self.initialized = False

    def read(self, shortcut):
        load = _com_method(self.persist, 5, c.c_long, w.LPCWSTR, w.DWORD)
        if load(self.persist, str(shortcut), 0) < 0:
            return None
        target, arguments, icon = (c.create_unicode_buffer(32768) for _ in range(3))
        get_path = _com_method(self.link, 3, c.c_long, w.LPWSTR, c.c_int,
                               c.c_void_p, w.DWORD)
        if get_path(self.link, target, len(target), None, 4) < 0 or not target.value:
            return None
        get_args = _com_method(self.link, 10, c.c_long, w.LPWSTR, c.c_int)
        get_args(self.link, arguments, len(arguments))
        get_icon = _com_method(self.link, 16, c.c_long, w.LPWSTR, c.c_int,
                               c.POINTER(c.c_int))
        icon_index = c.c_int()
        get_icon(self.link, icon, len(icon), c.byref(icon_index))
        return (os.path.expandvars(target.value), arguments.value,
                os.path.expandvars(icon.value))


def _start_menu_roots():
    roots = []
    for variable in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(variable)
        if base:
            roots.append(Path(base) / "Microsoft/Windows/Start Menu/Programs")
    return roots


def _local_path(path):
    value = os.fspath(path)
    if not os.path.isabs(value) or value.startswith(("\\\\", "//")):
        return False
    if os.name == "nt":
        root = os.path.splitdrive(value)[0] + "\\"
        kernel = c.WinDLL("kernel32")
        kernel.GetDriveTypeW.argtypes = [w.LPCWSTR]
        kernel.GetDriveTypeW.restype = w.UINT
        return kernel.GetDriveTypeW(root) in {2, 3, 5, 6}
    return True


def _shortcut_files(roots=None):
    count = 0
    for root in roots if roots is not None else _start_menu_roots():
        root = Path(root)
        if not _local_path(root) or not root.is_dir():
            continue
        for directory, children, files in os.walk(root, followlinks=False):
            # Avoid junctions and symlink folders, including links outside Menu.
            kept = []
            for name in sorted(children, key=str.casefold):
                child = Path(directory) / name
                try:
                    attributes = child.stat(follow_symlinks=False).st_file_attributes
                    if not attributes & 0x400 and len(child.relative_to(root).parts) <= 12:
                        kept.append(name)
                except (OSError, AttributeError):
                    if not child.is_symlink() and len(child.relative_to(root).parts) <= 12:
                        kept.append(name)
            children[:] = kept
            for name in sorted(files, key=str.casefold):
                if not name.casefold().endswith(".lnk"):
                    continue
                yield Path(directory) / name
                count += 1
                if count >= _MAX_SHORTCUTS:
                    return


def _shortcut_target(target, arguments, icon):
    if not _local_path(target):
        return None
    target = Path(target)
    if target.name.casefold() == "update.exe":
        # Squirrel shortcuts point at Update.exe, not the app process. An icon
        # may identify the real installed app exactly; never offer Update.exe.
        match = re.search(r'(?:^|\s)--processStart\s+(?:"([^"]+)"|(\S+))',
                          arguments, re.IGNORECASE)
        app = (match.group(1) or match.group(2)) if match else ""
        if app and Path(app).name == app and app.lower().endswith(".exe"):
            icon_path = Path(icon)
            if (icon and _local_path(icon_path) and
                    icon_path.name.casefold() == app.casefold() and icon_path.is_file()):
                return icon_path
        return None
    if target.name.casefold() in _HOSTS:
        return None
    if re.match(r"(?:unins\d*|uninstall.*)\.exe$", target.name, re.IGNORECASE):
        return None
    if re.search(r'(?:^|\s)(?:--?uninstall|/uninstall)(?:\s|$)', arguments,
                 re.IGNORECASE):
        return None
    return target


def _installed_apps():
    try:
        with _ShortcutReader() as reader:
            for shortcut in _shortcut_files():
                try:
                    if re.match(r"(?:uninstall\b|gỡ\s+cài\s+đặt\b)", shortcut.stem,
                                re.IGNORECASE):
                        continue
                    values = reader.read(shortcut)
                    if values:
                        target = _shortcut_target(*values)
                        if target is not None:
                            yield {"path": str(target), "name": shortcut.stem}
                except (OSError, ValueError):
                    continue
    except OSError:
        return


def _executable_name(path):
    """Friendly EXE description, without reading a user's window/page title."""
    fallback = Path(path).stem
    try:
        version = c.WinDLL("version")
        version.GetFileVersionInfoSizeW.argtypes = [w.LPCWSTR, c.POINTER(w.DWORD)]
        version.GetFileVersionInfoSizeW.restype = w.DWORD
        version.GetFileVersionInfoW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p]
        version.VerQueryValueW.argtypes = [c.c_void_p, w.LPCWSTR,
                                          c.POINTER(c.c_void_p), c.POINTER(w.UINT)]
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size or size > 4 * 1024 * 1024:
            return fallback
        data = c.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, data):
            return fallback
        address, length = c.c_void_p(), w.UINT()
        if not version.VerQueryValueW(data, r"\VarFileInfo\Translation",
                                      c.byref(address), c.byref(length)) or length.value < 4:
            return fallback
        language, codepage = c.cast(address, c.POINTER(w.WORD))[:2]
        query = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\FileDescription"
        if version.VerQueryValueW(data, query, c.byref(address), c.byref(length)):
            return c.wstring_at(address, min(length.value, 257)).rstrip("\0").strip() or fallback
    except (OSError, ValueError, AttributeError):
        pass
    return fallback


def _running_apps():
    from windows_api import CALLBACK, dwm, user

    pids = set()

    @CALLBACK
    def visit(hwnd, _):
        if not user.IsWindowVisible(hwnd) or not user.GetWindowTextLengthW(hwnd):
            return True
        cloaked = w.DWORD()
        dwm.DwmGetWindowAttribute(hwnd, 14, c.byref(cloaked), c.sizeof(cloaked))
        if cloaked.value:
            return True
        cls = c.create_unicode_buffer(64)
        user.GetClassNameW(hwnd, cls, len(cls))
        if cls.value in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
            return True
        pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, c.byref(pid))
        if pid.value:
            pids.add(pid.value)
        return True

    user.EnumWindows(visit, 0)
    for pid in sorted(pids):
        try:
            path = psutil.Process(pid).exe()
            if path and Path(path).name.casefold() not in _HOSTS:
                yield {"path": path, "name": _executable_name(path)}
        except (psutil.Error, OSError):
            continue


def discover_apps(*, running_provider=None, shortcut_provider=None, validator=None):
    """Return stable ``{path, name}`` rows; paths can also supply native icons.

    Providers and a validator are injectable for deterministic tests. Duplicate
    executable paths merge, while unrelated apps sharing a name remain separate.
    A failed source does not hide the other source's available applications.
    """
    validate = validator or ProcessBlocker().validate_path
    rows = {}
    for provider in (running_provider or _running_apps,
                     shortcut_provider or _installed_apps):
        try:
            for item in provider():
                try:
                    if not _local_path(item["path"]):
                        continue
                    path = validate(item["path"])
                    label = item.get("name", "")
                    if not isinstance(label, str):
                        label = ""
                    label = " ".join(label.replace("\0", "").split())[:100]
                    if not label:
                        label = Path(path).stem
                    # Start Menu names override the less specific EXE fallback.
                    rows[path] = {"path": path, "name": label}
                except (OSError, ValueError, TypeError, KeyError):
                    continue
        except (OSError, ValueError, psutil.Error):
            continue
    return sorted(rows.values(), key=lambda row: (row["name"].casefold(), row["path"]))
