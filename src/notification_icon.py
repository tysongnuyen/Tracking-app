"""Add notification-click handling to the app's pystray Win32 icon."""
import pystray


# shellapi.h: NIN_BALLOONUSERCLICK = WM_USER + 5. The installed pystray
# backend leaves NOTIFYICON_VERSION unchanged, so the event is the full lParam.
# https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shell_notifyiconw
NIN_BALLOONUSERCLICK = 0x0400 + 5


class NotificationIcon(pystray.Icon):
    """A tray icon whose balloon notifications can open their report.

    ``on_notification_click`` is an optional no-argument callback, invoked on
    the tray's message-loop thread. It should return promptly and delegate any
    GUI work to the existing dashboard host.
    """

    def __init__(self, *args, on_notification_click=None, **kwargs):
        self.on_notification_click = on_notification_click
        super().__init__(*args, **kwargs)

    def _on_notify(self, wparam, lparam):
        if lparam == NIN_BALLOONUSERCLICK and self.on_notification_click is not None:
            self.on_notification_click()
            return
        return super()._on_notify(wparam, lparam)
