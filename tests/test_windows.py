import os
import unittest
from unittest import mock


class WindowsRuntimeTests(unittest.TestCase):
    """捕获 HDR 查询在非 Windows 崩溃和自启动路径漂移。"""

    def test_hdr_query_is_unavailable_off_windows(self):
        from mimonitor_toolbox import windows

        with mock.patch.object(windows.sys, "platform", "linux"):
            self.assertIsNone(windows.query_windows_hdr_enabled())

    def test_autostart_path_uses_windows_startup_folder(self):
        from mimonitor_toolbox import windows

        roaming = r"C:\Users\tester\AppData\Roaming"
        with mock.patch.dict(os.environ, {"APPDATA": roaming}, clear=False):
            path = windows.get_autostart_path()

        self.assertEqual(
            path,
            os.path.join(
                roaming,
                r"Microsoft\Windows\Start Menu\Programs\Startup",
                "RedmiToolbox.bat",
            ),
        )


if __name__ == "__main__":
    unittest.main()
