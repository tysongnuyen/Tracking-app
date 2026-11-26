"""Website time is a breakdown inside browser time, never extra usage."""
from __future__ import annotations

BROWSER_NAMES = {
    "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge",
    "brave.exe": "Brave", "firefox.exe": "Firefox", "opera.exe": "Opera",
}
APP_NAMES = {
    **BROWSER_NAMES, "code.exe": "Visual Studio Code", "excel.exe": "Microsoft Excel",
    "winword.exe": "Microsoft Word", "powerpnt.exe": "PowerPoint",
    "explorer.exe": "File Explorer", "notepad.exe": "Notepad",
}
COLORS = ("#82AFFF", "#B59AFF", "#EFB76C", "#7CC7B0", "#E8919D", "#9F9BED", "#80BFCB")
OTHER = "#62666D"

def display_name(name):
    return APP_NAMES.get(name.lower(), name.removesuffix(".exe"))

def duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} giờ {minutes:02d} phút"
    return f"{minutes} phút {seconds:02d} giây" if minutes else f"{seconds} giây"

def short_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"

def overview(rows):
    apps = sorted((dict(r) for r in rows if r["kind"] == "app"), key=lambda r: (-r["screen"], r["name"]))
    return decorate(apps), sum(r["screen"] for r in apps)

def rank_apps(rows):
    """Rank app focus time; website breakdowns never increase the total.

    Rank and share belong to the complete reporting period, so a view can filter the
    returned rows without changing either value. Input report rows are copied.
    """
    apps = sorted((dict(r) for r in rows if r["kind"] == "app" and r["screen"] > 0),
                  key=lambda r: (-r["screen"], r["name"].casefold(), r["name"]))
    total = sum(r["screen"] for r in apps)
    for rank, row in enumerate(decorate(apps), start=1):
        row["rank"] = rank
        row["share"] = row["screen"] / total * 100
    return apps, total

def browser_detail(app_row, websites):
    browser = app_row["name"]
    rows = [dict(r) for r in websites if r.get("browser") == browser]
    total = app_row["screen"]
    missing = max(0., total - sum(r["screen"] for r in rows))
    if missing > .01:
        rows.append(dict(kind="unknown", browser=browser, name="Chưa xác định website",
                         screen=missing, background=0., idle=0., visible=0., covered=0.,
                         background_known=False, visibility_known=False))
    rows.sort(key=lambda r: (-r["screen"], r["name"]))
    return decorate(rows), total

def decorate(rows):
    for index, row in enumerate(rows):
        row["color"] = COLORS[index] if index < len(COLORS) and row["kind"] != "unknown" else OTHER
    return rows

def chart_parts(rows):
    parts = [(r["screen"], r["color"]) for r in rows[:7] if r["screen"] > 0]
    remainder = sum(r["screen"] for r in rows[7:])
    if remainder:
        parts.append((remainder, OTHER))
    return parts
