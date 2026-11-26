"""Exact-executable application families for opt-in local app limits.

No process is stopped until ``terminate`` is explicitly called for a selected
path. Family members retain PID + creation time + executable identity, including
children that outlive their parent. Process names are never kill selectors.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys

import psutil


PROTECTED_NAMES = frozenset({
    "system", "registry", "secure system", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe",
    "svchost.exe", "fontdrvhost.exe", "dwm.exe", "sihost.exe", "explorer.exe",
    "wudfhost.exe", "audiodg.exe", "memory compression",
})


def canonical_path(path):
    """Normalize local identity; Windows paths compare without case differences."""
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


@dataclass(frozen=True)
class _Member:
    pid: int
    created: float | None
    exe: str | None
    ppid: int | None
    name: str = ""
    denied: bool = False


class ProcessBlocker:
    def __init__(self, excluded_pids=(), process_iter=None, process_factory=None):
        self.excluded_pids = excluded_pids
        self._iter = process_iter or psutil.process_iter
        self._default_iter = process_iter is None
        self._process = process_factory or psutil.Process
        self._own_pid = os.getpid()
        self._tracker_paths = {canonical_path(sys.executable)}
        try:
            self._tracker_paths.add(canonical_path(self._process(self._own_pid).exe()))
        except (psutil.Error, OSError):
            pass
        self._families = {}
        self._snapshots = {}
        self._paths = set()

    def _allowed_path(self, path):
        normalized = canonical_path(path)
        if Path(normalized).name.casefold() in PROTECTED_NAMES:
            raise ValueError("Không thể chặn tiến trình Windows thiết yếu.")
        if normalized in self._tracker_paths:
            raise ValueError("Không thể chặn chính Tracking App.")
        return normalized

    def validate_path(self, path):
        if not isinstance(path, (str, os.PathLike)) or not os.fspath(path).strip():
            raise ValueError("Hãy chọn tệp ứng dụng .exe.")
        normalized = self._allowed_path(path)
        if Path(normalized).suffix.casefold() != ".exe" or not Path(normalized).is_file():
            raise ValueError("Hãy chọn một tệp .exe đang tồn tại.")
        return normalized

    @staticmethod
    def _read(process, parents=None):
        denied = False
        values = {}
        fields = [("create_time", None), ("exe", None), ("name", "")]
        if parents is None:
            fields.append(("ppid", None))
        else:
            values["ppid"] = parents.get(process.pid)
        for key, fallback in fields:
            try:
                if key == "create_time" and os.name == "nt" and isinstance(process, psutil.Process):
                    # The public method falls back to a whole-system NT query
                    # for inaccessible admin/system processes. Such processes
                    # cannot be safely killed with this token anyway. Reuse the
                    # same fast-only API psutil uses for Process identity.
                    values[key] = process._proc.create_time(fast_only=True)
                else:
                    values[key] = getattr(process, key)()
            except psutil.AccessDenied:
                values[key] = fallback
                denied = True
        exe = canonical_path(values["exe"]) if values["exe"] else None
        return _Member(int(process.pid), values["create_time"], exe,
                       values["ppid"], values["name"].casefold(), denied)

    @staticmethod
    def _same_identity(member, other):
        return (member.created is not None and member.created == other.created
                and member.exe is not None and member.exe == other.exe)

    @staticmethod
    def _child_of(child, parent):
        return (child.ppid == parent.pid and parent.created is not None
                and (child.created is None or child.created >= parent.created))

    def _protected(self, records):
        protected = {0, 4, self._own_pid, *self.excluded_pids}
        current = records.get(self._own_pid)
        seen = set()
        while current and current.pid not in seen:
            seen.add(current.pid)
            if current.ppid is None:
                break
            protected.add(current.ppid)
            parent = records.get(current.ppid)
            if parent and not self._child_of(current, parent):
                break
            current = parent
        # Protect the tracker's GUI, UIA workers, and other descendants, not all
        # children of the shell that happened to launch Tracking App.
        descendants = {self._own_pid, *self.excluded_pids}
        while True:
            added = {r.pid for r in records.values() if r.ppid in descendants
                     and r.pid not in descendants
                     and (r.ppid not in records or self._child_of(r, records[r.ppid]))}
            if not added:
                break
            descendants.update(added)
        protected.update(descendants)
        protected.update(r.pid for r in records.values()
                         if r.name in PROTECTED_NAMES or r.exe in self._tracker_paths
                         or (r.exe and Path(r.exe).name.casefold() in PROTECTED_NAMES))
        return protected

    def scan(self, paths):
        """Refresh selected families; an empty selection forgets all rules.

        ``denied`` includes relevant discovery uncertainty, never every protected
        system process on the computer. ``identities`` contains creation times.
        """
        selected = {self._allowed_path(path) for path in paths}
        self._paths = selected
        self._families = {path: self._families.get(path, {}) for path in selected}
        if not selected:
            self._snapshots = {}
            return {}
        records, errors, parents = {}, [], None
        try:
            # Recent psutil versions cache Process objects between iterations;
            # their cached creation times must not survive a PID being reused.
            if self._default_iter:
                psutil.process_iter.cache_clear()
                # psutil 7.2's Windows Process.ppid() rebuilds a whole-machine
                # map for each call. Reuse its batch snapshot once per scan,
                # avoiding quadratic polling cost on a normal desktop. psutil
                # is pinned; retain a portable fallback for other platforms.
                platform = getattr(psutil, "_psplatform", None)
                parent_snapshot = getattr(platform, "ppid_map", None)
                if parent_snapshot is not None:
                    parents = parent_snapshot()
            for process in self._iter():
                try:
                    member = self._read(process, parents)
                    records[member.pid] = member
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    continue
                except (psutil.Error, OSError) as exc:
                    errors.append(f"PID {getattr(process, 'pid', '?')}: {type(exc).__name__}")
        except (psutil.Error, OSError) as exc:
            errors.append(f"Không thể đọc danh sách tiến trình: {type(exc).__name__}")
        protected = self._protected(records)
        snapshots = {}
        for path in selected:
            family, uncertain = {}, set()
            for pid, old in self._families[path].items():
                current = records.get(pid)
                if current is None:
                    if errors:
                        family[pid] = old
                        uncertain.add(pid)
                    continue
                if self._same_identity(old, current):
                    family[pid] = current
                elif ((current.created is None and current.exe in (None, old.exe))
                      or (current.created == old.created and current.exe is None)
                      or (old.created is None and current.exe in (None, old.exe))):
                    # Keep the previous identity for a retry, but never kill an
                    # identity that cannot be verified immediately before kill.
                    family[pid] = old
                    uncertain.add(pid)
            for current in records.values():
                if current.exe == path:
                    family[current.pid] = current
                    if current.created is None:
                        uncertain.add(current.pid)
                elif current.exe is None and current.name == Path(path).name.casefold():
                    uncertain.add(current.pid)
            while True:
                added = {}
                for current in records.values():
                    parent = family.get(current.ppid)
                    if (current.pid not in family and parent is not None
                            and parent.pid not in protected and self._child_of(current, parent)):
                        added[current.pid] = current
                if not added:
                    break
                family.update(added)
            denied = uncertain | {p.pid for p in family.values()
                                  if p.created is None or p.exe is None or p.denied}
            members = set(family) | uncertain
            snapshot = {
                "pids": members,
                "identities": {pid: member.created for pid, member in family.items()},
                "denied": denied,
                "protected": members & protected,
                "errors": list(errors),
            }
            snapshots[path] = snapshot
            self._families[path] = family
        self._snapshots = snapshots
        return snapshots

    def contains(self, path, pid):
        member = self._families.get(canonical_path(path), {}).get(pid)
        snapshot = self._snapshots.get(canonical_path(path), {})
        return bool(member and member.created is not None and member.exe is not None
                    and pid not in snapshot.get("denied", ())
                    and pid not in snapshot.get("protected", ()))

    def terminate(self, path, passes=3):
        """Stop all verified family members, including multiple app instances.

        Bounded rescans catch children spawned during shutdown. The caller must
        retry while a rule is blocked, and surface alive/denied/protected/errors.
        A successful kill request alone is never reported as confirmed exit.
        """
        path = self._allowed_path(path)
        paths = self._paths | {path}
        killed, denied_kill, errors, held, resume_failed = set(), set(), [], {}, set()
        try:
            # Suspend parents before children, then rescan: a running child may
            # have created a grandchild just before suspension. This closes the
            # usual respawn race without killing the parent and losing the tree.
            for _ in range(max(1, min(int(passes), 5))):
                snapshot = self.scan(paths)[path]
                errors.extend(snapshot["errors"])
                ordered = sorted(self._families[path].values(),
                                 key=lambda m: (m.created or 0, m.pid))
                changed = False
                for member in ordered:
                    if (member.pid in snapshot["protected"] or member.pid in snapshot["denied"]
                            or (member.pid in held and self._same_identity(member, held[member.pid]))):
                        continue
                    try:
                        fresh = self._process(member.pid)
                        current = self._read(fresh, {})
                        if not self._same_identity(member, current):
                            continue
                        if current.name in PROTECTED_NAMES or current.exe in self._tracker_paths:
                            continue
                        fresh.suspend()
                        held[member.pid] = member
                        changed = True
                    except (psutil.NoSuchProcess, psutil.ZombieProcess):
                        continue
                    except (psutil.AccessDenied, OSError):
                        denied_kill.add(member.pid)
                if not changed:
                    break
            snapshot = self.scan(paths)[path]
            errors.extend(snapshot["errors"])
            unheld = {pid for pid, member in self._families[path].items()
                      if pid not in snapshot["protected"] and pid not in snapshot["denied"]
                      and pid not in denied_kill
                      and (pid not in held or not self._same_identity(member, held[pid]))}
            if unheld:
                errors.append("Cây tiến trình tiếp tục thay đổi; sẽ thử chặn lại.")
            else:
                # Every accessible family member is suspended, so descendants
                # cannot race a parent shutdown and escape the captured family.
                for member in sorted(held.values(), key=lambda m: (m.created or 0, m.pid), reverse=True):
                    if member.pid in snapshot["protected"] or member.pid in snapshot["denied"]:
                        continue
                    try:
                        fresh = self._process(member.pid)
                        current = self._read(fresh, {})
                        if (self._same_identity(member, current)
                                and current.name not in PROTECTED_NAMES
                                and current.exe not in self._tracker_paths):
                            fresh.kill()
                            killed.add(member.pid)
                    except (psutil.NoSuchProcess, psutil.ZombieProcess):
                        continue
                    except (psutil.AccessDenied, OSError):
                        denied_kill.add(member.pid)
        finally:
            # Failed enforcement must not leave an application suspended. Only
            # resume the exact process instance this invocation suspended.
            for member in held.values():
                try:
                    fresh = self._process(member.pid)
                    if self._same_identity(member, self._read(fresh, {})):
                        fresh.resume()
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    pass
                except (psutil.AccessDenied, OSError):
                    resume_failed.add(member.pid)
        final = self.scan(paths)[path]
        alive = set(final["pids"])
        errors.extend(f"Không thể tiếp tục PID {pid} sau lần chặn lỗi."
                      for pid in sorted(resume_failed & alive))
        return {
            "killed": sorted(killed),
            "alive": sorted(alive),
            "denied": sorted(set(final["denied"]) | (denied_kill & alive)),
            "protected": sorted(final["protected"]),
            "errors": list(dict.fromkeys(errors + final["errors"])),
        }
