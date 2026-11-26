"""Lên lịch báo cáo tuần theo giờ địa phương trong vòng kiểm tra của system tray."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from dashboard_model import rank_apps


def trailing_week(today: date) -> tuple[str, str]:
    """Trả ngày đầu/cuối của 7 ngày gần nhất tính cả today, theo dạng YYYY-MM-DD.

    Ví dụ today là 18/10 thì khoảng trả về là 12/10 đến 18/10 cùng năm.
    """
    return (today - timedelta(days=6)).isoformat(), today.isoformat()


def _validate_hour(hour: int) -> None:
    """Kiểm tra giờ gửi báo cáo là số nguyên từ 0 đến 23, loại cả giá trị bool.

    Báo ValueError nếu hour không hợp lệ; hợp lệ thì hàm kết thúc bình thường.
    """
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("Giờ báo cáo phải là số nguyên từ 0 đến 23")


def due_week(now: datetime, last_sent_week: str | None, hour: int) -> str | None:
    """Xác định có đến lượt gửi báo cáo tuần tại thời điểm now hay chưa.

    Nơi gọi truyền now theo giờ địa phương. Trả ngày thứ Hai dạng YYYY-MM-DD
    nếu hôm nay là thứ Hai, đã đến hour và last_sent_week chưa ghi nhận ngày đó.
    Mở app muộn trong thứ Hai vẫn gửi được. Các trường hợp còn lại trả None.
    """
    _validate_hour(hour)
    monday = now.date().isoformat()
    if now.weekday() == 0 and now.hour >= hour and monday != last_sent_week:
        return monday
    return None


class WeeklyReporter:
    """Tổng hợp báo cáo và lưu mốc đã gửi để mỗi thứ Hai chỉ gửi một lần."""
    LAST_SENT_KEY = "weekly_report_last_sent_week"

    def __init__(self, store, hour: int):
        """Nhận Store, kiểm tra giờ gửi và đọc mốc báo cáo đã gửi từ settings."""
        _validate_hour(hour)
        self.store = store
        self.hour = hour
        self.last_sent_week = store.setting(self.LAST_SENT_KEY)

    def poll(self, now: datetime, notify):
        """Kiểm tra lịch và gửi báo cáo qua callback notify khi đến lượt.

        now là giờ địa phương. Tổng hợp 7 ngày bằng Store.report() và rank_apps(),
        rồi gọi notify(payload) với ngày đầu/cuối, tổng số giây và hạng các app.
        Callback thành công thì lưu mốc đã gửi vào Store và cập nhật bộ nhớ.
        Trả payload khi hoàn tất, hoặc None khi chưa đến lượt. Nếu gửi hay lưu
        gây lỗi, lỗi được chuyển cho nơi gọi và mốc trong bộ nhớ chưa được đổi,
        cho phép vòng system tray thử lại ở lượt kiểm tra sau.
        """
        week = due_week(now, self.last_sent_week, self.hour)
        if week is None:
            return None
        start_day, end_day = trailing_week(now.date())
        apps, total = rank_apps(self.store.report(start_day, end_day))
        payload = dict(week=week, start_day=start_day, end_day=end_day,
                       total_seconds=total, apps=apps)
        notify(payload)
        self.store.set_setting(self.LAST_SENT_KEY, week)
        self.last_sent_week = week
        return payload
