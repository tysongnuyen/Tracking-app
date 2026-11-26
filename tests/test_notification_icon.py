import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import unittest
from unittest.mock import Mock, patch

import pystray
from pystray._util import win32

from notification_icon import NIN_BALLOONUSERCLICK, NotificationIcon


class NotificationIconTests(unittest.TestCase):
    def make_icon(self, callback=None):
        # Registering a window class is unnecessary for these message-handler
        # checks. No icon window, tray item, or notification is created.
        with patch.object(pystray.Icon, "_register_class", return_value=1):
            return NotificationIcon("tracking-test", title="Tracking",
                                    on_notification_click=callback)

    def test_constructor_preserves_icon_setup_and_installs_override(self):
        callback = Mock()
        icon = self.make_icon(callback)
        self.assertEqual((icon.name, icon.title, icon.visible),
                         ("tracking-test", "Tracking", False))
        self.assertIs(icon.on_notification_click, callback)
        self.assertEqual(icon._message_handlers[win32.WM_NOTIFY], icon._on_notify)
        callback.assert_not_called()

    def test_notification_click_calls_report_callback_only_once(self):
        callback = Mock()
        icon = self.make_icon(callback)
        self.assertEqual(NIN_BALLOONUSERCLICK, win32.WM_USER + 5)
        with patch.object(pystray.Icon, "_on_notify") as original:
            self.assertIsNone(icon._on_notify(47, NIN_BALLOONUSERCLICK))
        callback.assert_called_once_with()
        original.assert_not_called()

    def test_other_events_preserve_original_handler_and_parameters(self):
        callback = Mock()
        icon = self.make_icon(callback)
        events = (win32.WM_LBUTTONUP, win32.WM_RBUTTONUP,
                  win32.WM_USER + 2, win32.WM_USER + 3, win32.WM_USER + 4, 9999)
        for event in events:
            with self.subTest(event=event):
                with patch.object(pystray.Icon, "_on_notify", return_value=23) as original:
                    self.assertEqual(icon._on_notify(47, event), 23)
                    original.assert_called_once_with(47, event)
        callback.assert_not_called()

    def test_notification_click_without_callback_uses_original_handler(self):
        icon = self.make_icon()
        with patch.object(pystray.Icon, "_on_notify", return_value=23) as original:
            self.assertEqual(icon._on_notify(47, NIN_BALLOONUSERCLICK), 23)
        original.assert_called_once_with(47, NIN_BALLOONUSERCLICK)

    def test_existing_notify_api_forwards_content_without_displaying_it(self):
        icon = self.make_icon()
        with patch.object(pystray.Icon, "_notify") as notify:
            icon.notify("Weekly summary", "Tracking")
        notify.assert_called_once_with("Weekly summary", "Tracking")


if __name__ == "__main__":
    unittest.main()
