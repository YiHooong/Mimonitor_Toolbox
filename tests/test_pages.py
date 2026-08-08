import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QAbstractButton, QApplication

_qt_application = QApplication.instance() or QApplication([])


class PageContractTests(unittest.TestCase):
    """捕获页面方法遗漏或被重新散落到主窗口的回归。"""

    def test_all_page_builders_are_owned_by_pages_mixin(self):
        from mimonitor_toolbox.pages import PagesMixin

        expected = {
            "setup_ui",
            "_make_home_page",
            "_make_picture_page",
            "_make_game_page",
            "_make_source_page",
            "_make_light_page",
            "_make_tools_page",
            "_make_remote_page",
            "_add_slider",
            "_add_color_gain_slider",
            "_add_light_slider",
            "_btn_section",
        }

        self.assertTrue(expected.issubset(vars(PagesMixin)))

    def test_app_builds_all_pages_without_missing_module_dependencies(self):
        from mimonitor_toolbox.main_window import App

        with mock.patch.object(App, "register_global_hotkeys"), \
                mock.patch.object(App, "setup_tray"):
            window = App()

        expected = (
            "home_page",
            "picture_page",
            "game_page",
            "source_page",
            "light_page",
            "tools_page",
            "remote_page",
        )
        self.assertEqual(
            [name for name in expected if not hasattr(window, name)],
            [],
        )
        window._cleanup_done = True
        window.deleteLater()
        _qt_application.processEvents()

    def test_adb_cmd_and_shell_buttons_share_one_tool_card(self):
        from mimonitor_toolbox.main_window import App

        with mock.patch.object(App, "register_global_hotkeys"), \
                mock.patch.object(App, "setup_tray"):
            window = App()

        buttons = {
            button.text(): button
            for button in window.tools_page.findChildren(QAbstractButton)
        }
        self.assertIn("打开 ADB CMD", buttons)
        self.assertIn("进入 ADB Shell", buttons)
        self.assertIs(
            buttons["打开 ADB CMD"].parent(),
            buttons["进入 ADB Shell"].parent(),
        )
        window._cleanup_done = True
        window.deleteLater()
        _qt_application.processEvents()


if __name__ == "__main__":
    unittest.main()
