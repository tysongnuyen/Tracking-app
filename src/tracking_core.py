"""Bounded aggregate storage. Raw samples and complete URLs are never stored."""
from __future__ import annotations

import csv
import io
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

CATEGORIES = ("Công việc", "Học tập", "Giải trí", "Mạng xã hội", "Chưa phân loại")


def category_for(kind: str, name: str) -> str:
    s = name.lower().removesuffix(".exe")
    domain = s.split(".")[-2] if "." in s else s
    if domain in {"youtube", "tiktok", "netflix", "twitch", "spotify"}:
        return "Giải trí"
    if domain in {"facebook", "instagram", "reddit", "twitter", "x", "discord", "telegram"} or s in {"zalo", "discord", "telegram"}:
        return "Mạng xã hội"
    if domain in {"coursera", "udemy", "khanacademy", "duolingo"}:
        return "Học tập"
    if s in {"code", "devenv", "excel", "winword", "powerpnt", "figma", "notion", "teams", "slack"} or domain in {"github", "gitlab"}:
        return "Công việc"
    return "Chưa phân loại"


@dataclass(frozen=True)
class Observation:
    kind: str
    name: str
    mode: str
    background_known: bool = False
    visibility_known: bool = False
    browser: str = ""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS daily (
                day TEXT, kind TEXT, name TEXT,
                screen REAL NOT NULL DEFAULT 0,
                background REAL NOT NULL DEFAULT 0,
                idle REAL NOT NULL DEFAULT 0,
                background_known REAL NOT NULL DEFAULT 0,
                visible REAL NOT NULL DEFAULT 0,
                covered REAL NOT NULL DEFAULT 0,
                visibility_known REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(day, kind, name));
            CREATE TABLE IF NOT EXISTS rules (
                kind TEXT, name TEXT, category TEXT,
                PRIMARY KEY(kind, name));
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS browser_daily (
                day TEXT, browser TEXT, name TEXT,
                screen REAL NOT NULL DEFAULT 0,
                background REAL NOT NULL DEFAULT 0,
                idle REAL NOT NULL DEFAULT 0,
                background_known REAL NOT NULL DEFAULT 0,
                visible REAL NOT NULL DEFAULT 0,
                covered REAL NOT NULL DEFAULT 0,
                visibility_known REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(day, browser, name));
        """)
        # Additive migration: old focus/unfocused counters are preserved unchanged.
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(daily)")}
        for column in ("visible", "covered", "visibility_known"):
            if column not in columns:
                self.db.execute(f"ALTER TABLE daily ADD COLUMN {column} REAL NOT NULL DEFAULT 0")
        self.db.commit()
        self.pending: dict[tuple[str, str, str], list[float]] = {}
        self.browser_pending: dict[tuple[str, str, str], list[float]] = {}
        self._browser_known: dict[str, set[tuple[str, str]]] = {}
        self.current_window: dict[tuple[str, str], float] = {}
        self.slot = None
        self.completed_windows = 0
        self._known: dict[str, set[tuple[str, str]]] = {}
        self.save_error = ""

    def setting(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_setting(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))

    def has_activity(self):
        with self.lock:
            return any(any(value > 0 for value in delta) for delta in self.pending.values()) or bool(self.db.execute(
                "SELECT 1 FROM daily WHERE screen>0 OR background>0 OR visible>0 OR covered>0 LIMIT 1").fetchone())

    def set_category(self, kind, name, category):
        if category not in CATEGORIES:
            raise ValueError("Nhóm không hợp lệ")
        with self.lock, self.db:
            count = self.db.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
            existing = self.db.execute("SELECT 1 FROM rules WHERE kind=? AND name=?", (kind, name)).fetchone()
            if count >= 1000 and not existing:
                raise ValueError("Đã đạt giới hạn 1.000 quy tắc nhóm")
            self.db.execute("INSERT OR REPLACE INTO rules VALUES (?, ?, ?)", (kind, name, category))

    def _target(self, day, kind, name, browser=False):
        cache = self._browser_known if browser else self._known
        if day not in cache:
            query = "SELECT browser,name FROM browser_daily WHERE day=?" if browser else "SELECT kind,name FROM daily WHERE day=?"
            cache[day] = set(self.db.execute(query, (day,)))
        known = cache[day]
        if (kind, name) not in known and len(known) >= 1000:
            name = "[other targets]"
        known.add((kind, name))
        return day, kind, name

    @staticmethod
    def _unique(observations, per_browser=False):
        priority = {"screen": 0, "idle": 1, "visible": 2, "background": 3, "unknown": 4}
        unique = {}
        for item in observations:
            if per_browser and (item.kind != "web" or not item.browser):
                continue
            key = (item.browser, item.name) if per_browser else (item.kind, item.name)
            old = unique.get(key)
            selected = item if old is None or priority[item.mode] < priority[old.mode] else old
            unique[key] = Observation(selected.kind, selected.name, selected.mode,
                item.background_known or bool(old and old.background_known),
                selected.visibility_known or bool(old and old.mode == item.mode and old.visibility_known),
                selected.browser)
        return unique

    @staticmethod
    def _add(delta, item, seconds):
        if item.mode in {"background", "visible", "unknown"}:
            delta[1] += seconds
        else:
            delta[0] += seconds
            if item.mode == "idle":
                delta[2] += seconds
        if item.background_known:
            delta[3] += seconds
        if item.visibility_known:
            delta[6] += seconds
            if item.mode in {"screen", "idle", "visible"}:
                delta[4] += seconds
            elif item.mode == "background":
                delta[5] += seconds

    def record(self, start: datetime, end: datetime, observations):
        if end <= start:
            return
        # One source can be visible in multiple browser windows. Foreground wins.
        observations = list(observations)
        unique = self._unique(observations)
        websites = self._unique(observations, per_browser=True)
        with self.lock:
            cursor, stop = start.timestamp(), end.timestamp()
            while cursor < stop:
                local = datetime.fromtimestamp(cursor).astimezone()
                slot = int(cursor // 600)
                if slot != self.slot:
                    self.completed_windows += self.slot is not None
                    self.current_window.clear()
                    self.slot = slot
                # Construct tomorrow in local time rather than adding 24h across DST.
                tomorrow = datetime.combine(local.date() + timedelta(days=1), datetime.min.time()).astimezone()
                boundary = min(stop, (slot + 1) * 600, tomorrow.timestamp())
                seconds = boundary - cursor
                day = local.date().isoformat()
                for item in unique.values():
                    key = self._target(day, item.kind, item.name)
                    delta = self.pending.setdefault(key, [0.] * 7)
                    self._add(delta, item, seconds)
                    self.current_window[key[1:]] = self.current_window.get(key[1:], 0.) + seconds
                for item in websites.values():
                    key = self._target(day, item.browser, item.name, browser=True)
                    self._add(self.browser_pending.setdefault(key, [0.] * 7), item, seconds)
                cursor = boundary
            cutoff = (end.astimezone().date() - timedelta(days=29)).isoformat()
            self.pending = {k: v for k, v in self.pending.items() if k[0] >= cutoff}
            self._known = {k: v for k, v in self._known.items() if k >= cutoff}
            self.browser_pending = {k: v for k, v in self.browser_pending.items() if k[0] >= cutoff}
            self._browser_known = {k: v for k, v in self._browser_known.items() if k >= cutoff}

    def flush(self, today=None):
        with self.lock:
            try:
                with self.db:
                    self.db.executemany("""INSERT INTO daily
                        (day,kind,name,screen,background,idle,background_known,visible,covered,visibility_known)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(day,kind,name) DO UPDATE SET
                        screen=screen+excluded.screen,
                        background=background+excluded.background,
                        idle=idle+excluded.idle,
                        background_known=background_known+excluded.background_known,
                        visible=visible+excluded.visible,
                        covered=covered+excluded.covered,
                        visibility_known=visibility_known+excluded.visibility_known""",
                        [(*key, *delta) for key, delta in self.pending.items()])
                    self.db.executemany("""INSERT INTO browser_daily
                        (day,browser,name,screen,background,idle,background_known,visible,covered,visibility_known)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(day,browser,name) DO UPDATE SET
                        screen=screen+excluded.screen, background=background+excluded.background,
                        idle=idle+excluded.idle, background_known=background_known+excluded.background_known,
                        visible=visible+excluded.visible, covered=covered+excluded.covered,
                        visibility_known=visibility_known+excluded.visibility_known""",
                        [(*key, *delta) for key, delta in self.browser_pending.items()])
                    cutoff = ((today or datetime.now().date()) - timedelta(days=29)).isoformat()
                    self.db.execute("DELETE FROM daily WHERE day < ?", (cutoff,))
                    self.db.execute("DELETE FROM browser_daily WHERE day < ?", (cutoff,))
                self.pending.clear()
                self.browser_pending.clear()
                self._known = {k: v for k, v in self._known.items() if k >= cutoff}
                self._browser_known = {k: v for k, v in self._browser_known.items() if k >= cutoff}
                self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
                self.save_error = ""
            except sqlite3.Error as exc:
                self.save_error = "Chưa lưu được số liệu: " + str(exc)
                raise

    def report(self, day, end_day=None):
        """Return counters per source for an inclusive local date range.

        A single day retains the original report shape. Dates must be canonical
        YYYY-MM-DD strings; invalid or reversed ranges raise ValueError.
        """
        try:
            start = date.fromisoformat(day).isoformat()
            stop = date.fromisoformat(end_day).isoformat() if end_day is not None else start
        except (TypeError, ValueError):
            raise ValueError("Ngày phải có định dạng YYYY-MM-DD hợp lệ") from None
        if start != day or (end_day is not None and stop != end_day):
            raise ValueError("Ngày phải có định dạng YYYY-MM-DD hợp lệ")
        if stop < start:
            raise ValueError("Ngày kết thúc phải bằng hoặc sau ngày bắt đầu")
        with self.lock:
            merged = {(r[0], r[1]): list(r[2:]) for r in self.db.execute(
                """SELECT kind,name,SUM(screen),SUM(background),SUM(idle),
                   SUM(background_known),SUM(visible),SUM(covered),SUM(visibility_known)
                   FROM daily WHERE day BETWEEN ? AND ? GROUP BY kind,name""", (start, stop))}
            for (d, kind, name), delta in self.pending.items():
                if start <= d <= stop:
                    row = merged.setdefault((kind, name), [0.] * 7)
                    for i in range(7):
                        row[i] += delta[i]
            return [dict(kind=k, name=n, **self._counters(v)) for (k, n), v in merged.items()]

    @staticmethod
    def _counters(v):
        return dict(screen=v[0], background=v[1], idle=v[2], background_known=bool(v[3]),
                    visible=v[4], covered=v[5], visibility_known=bool(v[6]))

    def browser_report(self, day, browser=None):
        with self.lock:
            merged = {(r[0], r[1]): list(r[2:]) for r in self.db.execute(
                "SELECT browser,name,screen,background,idle,background_known,visible,covered,visibility_known FROM browser_daily WHERE day=?", (day,))}
            for (d, client, name), delta in self.browser_pending.items():
                if d == day:
                    row = merged.setdefault((client, name), [0.] * 7)
                    for i in range(7):
                        row[i] += delta[i]
            return [dict(kind="web", browser=client, name=name, **self._counters(v))
                    for (client, name), v in merged.items() if browser is None or client == browser]

    def close(self):
        with self.lock:
            self.flush()
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.db.close()


def export_csv(day, rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Ngày", "Loại", "Trình duyệt", "Nguồn", "Được chọn (giây)", "Đang hiển thị (giây, gồm được chọn)", "Bị che/thu nhỏ (giây)", "Không thao tác (nằm trong được chọn)", "Không được chọn (bộ đếm cũ, gồm cả hiển thị)"])
    def safe(value):
        text = str(value)
        return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
    for r in rows:
        writer.writerow([day, r["kind"], safe(r.get("browser", "")), safe(r["name"]), round(r["screen"], 1),
                         round(r["visible"], 1) if r["visibility_known"] else "chưa đo",
                         round(r["covered"], 1) if r["visibility_known"] else "chưa đo",
                         round(r["idle"], 1), round(r["background"], 1)])
    return output.getvalue()
