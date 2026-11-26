"""Daily limits for an explicitly selected executable and its process family.

Only aggregate seconds and notification flags are stored. Process discovery and
termination live in the injected blocker so the accounting can be tested safely.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math
import ntpath
import threading


WARNING_MINUTES = (15, 10, 5)
MAX_RULES = 100


class AppLimits:
    def __init__(self, store, blocker, notify=None, now=None):
        self.store = store
        self.blocker = blocker
        self.notify = notify
        self.now = now or datetime.now
        self.lock = threading.RLock()
        self._pruned_day = None
        with store.lock, store.db:
            store.db.executescript("""
                CREATE TABLE IF NOT EXISTS app_limit_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    minutes INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    error TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS app_limit_daily (
                    day TEXT NOT NULL,
                    path TEXT NOT NULL,
                    used REAL NOT NULL DEFAULT 0,
                    last_end REAL NOT NULL DEFAULT 0,
                    warning_mask INTEGER NOT NULL DEFAULT 0,
                    blocked_notified INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(day, path));
            """)

    @staticmethod
    def _local(value):
        return value.astimezone() if value.tzinfo is not None else value

    def _today(self, value=None):
        return self._local(value if value is not None else self.now()).date().isoformat()

    def rules(self, now=None):
        day = self._today(now)
        with self.lock, self.store.lock:
            rows = self.store.db.execute("""
                SELECT r.id,r.path,r.name,r.minutes,r.enabled,r.error,
                       COALESCE(d.used,0)
                FROM app_limit_rules r LEFT JOIN app_limit_daily d
                  ON d.path=r.path AND d.day=?
                ORDER BY r.name COLLATE NOCASE,r.path
            """, (day,)).fetchall()
            return [dict(id=rid, path=path, name=name, minutes=minutes,
                         enabled=bool(enabled), used=used,
                         remaining=max(0., minutes * 60 - used),
                         blocked=bool(enabled and used >= minutes * 60),
                         error=error) for rid, path, name, minutes, enabled, error, used in rows]

    def save_rule(self, path, minutes, enabled=True, name=None):
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or int(minutes) != minutes or not 1 <= minutes <= 1440:
            raise ValueError("Giới hạn phải là số phút nguyên từ 1 đến 1.440")
        canonical = self.blocker.validate_path(path)
        if name is not None and not isinstance(name, str):
            raise ValueError("Tên ứng dụng phải là văn bản")
        display_name = " ".join("".join(char for char in (name or "")
                                      if char.isprintable() or char.isspace()).split())[:160]
        with self.lock, self.store.lock, self.store.db:
            old = self.store.db.execute("SELECT id,name FROM app_limit_rules WHERE path=?", (canonical,)).fetchone()
            if name is None and old is not None:
                display_name = old[1]
            display_name = display_name or ntpath.basename(canonical)
            if old is None and self.store.db.execute("SELECT COUNT(*) FROM app_limit_rules").fetchone()[0] >= MAX_RULES:
                raise ValueError("Chỉ có thể lưu tối đa 100 giới hạn ứng dụng")
            self.store.db.execute("""INSERT INTO app_limit_rules(path,name,minutes,enabled)
                VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                name=excluded.name,minutes=excluded.minutes,enabled=excluded.enabled,error=''
            """, (canonical, display_name, int(minutes), int(bool(enabled))))
            return self.store.db.execute("SELECT id FROM app_limit_rules WHERE path=?", (canonical,)).fetchone()[0]

    def set_enabled(self, rule_id, enabled):
        with self.lock, self.store.lock, self.store.db:
            result = self.store.db.execute("UPDATE app_limit_rules SET enabled=?,error='' WHERE id=?",
                                           (int(bool(enabled)), rule_id))
            if not result.rowcount:
                raise ValueError("Không tìm thấy giới hạn ứng dụng")

    def delete_rule(self, rule_id):
        # Today's counters remain if the same executable is added again.
        with self.lock, self.store.lock, self.store.db:
            result = self.store.db.execute("DELETE FROM app_limit_rules WHERE id=?", (rule_id,))
            if not result.rowcount:
                raise ValueError("Không tìm thấy giới hạn ứng dụng")

    @staticmethod
    def _event(rule, kind, **values):
        return dict(type=kind, rule_id=rule["id"], name=rule["name"], path=rule["path"], **values)

    def _set_error(self, rule, message, events):
        if rule["error"] != message:
            self.store.db.execute("UPDATE app_limit_rules SET error=? WHERE id=?", (message, rule["id"]))
            rule["error"] = message
            if message:
                events.append(self._event(rule, "error", error=message))

    @classmethod
    def _segments(cls, start, end):
        cursor, stop = start.timestamp(), end.timestamp()
        while cursor < stop:
            local = datetime.fromtimestamp(cursor)
            tomorrow = datetime.combine(local.date() + timedelta(days=1), datetime.min.time())
            boundary = min(stop, tomorrow.timestamp())
            yield local.date().isoformat(), cursor, boundary
            cursor = boundary

    def _usage(self, rule, day):
        self.store.db.execute("INSERT OR IGNORE INTO app_limit_daily(day,path) VALUES (?,?)", (day, rule["path"]))
        return self.store.db.execute("""SELECT used,last_end,warning_mask,blocked_notified
            FROM app_limit_daily WHERE day=? AND path=?""", (day, rule["path"])).fetchone()

    def _record(self, rule, start, end):
        for day, begin, stop in self._segments(start, end):
            used, last_end, _, _ = self._usage(rule, day)
            seconds = max(0., stop - max(begin, last_end))
            if seconds:
                self.store.db.execute("""UPDATE app_limit_daily SET used=?,last_end=?
                    WHERE day=? AND path=?""", (used + seconds, max(stop, last_end), day, rule["path"]))

    def tick(self, start, end, foreground_pid=None, count_usage=True):
        """Account one collector interval and enforce all active limits.

        Suspended/late intervals over three seconds are ignored, just like the
        collector. Enforcement still runs while accounting is paused. Returning
        events is useful to callers without a notification queue.
        """
        events = []
        with self.lock:
            day = self._today(end)
            configured = self.rules(end)
            active = [rule for rule in configured if rule["enabled"]]
            if not active:
                return events
            # Keep known orphan children attached while a rule is temporarily
            # disabled. Only enabled rules below may accrue time or terminate.
            paths = [rule["path"] for rule in configured]
            try:
                snapshots = self.blocker.scan(paths)
            except Exception as exc:
                with self.store.lock, self.store.db:
                    for rule in active:
                        self._set_error(rule, f"Không thể kiểm tra tiến trình: {exc}", events)
            else:
                elapsed = (end - start).total_seconds()
                with self.store.lock, self.store.db:
                    if self._pruned_day != day:
                        cutoff = (self._local(end).date() - timedelta(days=30)).isoformat()
                        self.store.db.execute("DELETE FROM app_limit_daily WHERE day<?", (cutoff,))
                        self._pruned_day = day
                for rule in active:
                    snapshot = snapshots.get(rule["path"], {})
                    scan_errors = snapshot.get("errors", ())
                    with self.store.lock, self.store.db:
                        if count_usage and foreground_pid is not None and 0 < elapsed <= 3 and foreground_pid in snapshot.get("pids", set()):
                            self._record(rule, start, end)
                        used, _, mask, blocked_notified = self._usage(rule, day)
                        remaining = rule["minutes"] * 60 - used
                        if remaining > 0:
                            eligible = [(bit, minutes) for bit, minutes in enumerate(WARNING_MINUTES)
                                        if rule["minutes"] > minutes and remaining <= minutes * 60]
                            unseen = [(bit, minutes) for bit, minutes in eligible if not mask & (1 << bit)]
                            if unseen:
                                for bit, _ in eligible:
                                    mask |= 1 << bit
                                self.store.db.execute("UPDATE app_limit_daily SET warning_mask=? WHERE day=? AND path=?",
                                                      (mask, day, rule["path"]))
                                events.append(self._event(rule, "warning", remaining_minutes=min(minutes for _, minutes in unseen)))
                            message = "; ".join(str(error) for error in scan_errors)
                            if snapshot.get("denied"):
                                message = message or "Không đủ quyền kiểm tra một số tiến trình của ứng dụng"
                            self._set_error(rule, message, events)
                            continue
                    # Commit the consumed time before touching any process. A
                    # crash or restart must not replenish an exhausted limit.
                    try:
                        result = self.blocker.terminate(rule["path"])
                        failed = set(result.get("alive", ())) | set(result.get("denied", ())) | set(result.get("protected", ()))
                        errors = list(scan_errors) + list(result.get("errors", ()))
                        message = "; ".join(str(error) for error in errors)
                        if failed:
                            message = message or f"Không thể đóng {len(failed)} tiến trình của ứng dụng; hãy kiểm tra quyền truy cập"
                    except Exception as exc:
                        message = f"Không thể đóng ứng dụng: {exc}"
                    with self.store.lock, self.store.db:
                        self._set_error(rule, message, events)
                        if not blocked_notified:
                            self.store.db.execute("UPDATE app_limit_daily SET blocked_notified=1 WHERE day=? AND path=?",
                                                  (day, rule["path"]))
                            events.append(self._event(rule, "blocked", remaining_minutes=0))
        if self.notify is not None:
            for event in events:
                try:
                    self.notify(event)
                except Exception:
                    # Notification delivery must never disable process enforcement.
                    pass
        return events
