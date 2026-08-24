import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QTextEdit

_qt_application = QApplication.instance() or QApplication([])


class WidgetConstructionTests(unittest.TestCase):
    """捕获仅在对话框构造时才暴露的控件依赖遗漏。"""

    def test_close_confirmation_dialog_constructs(self):
        from mimonitor_toolbox.widgets import CloseConfirmDialog

        dialog = CloseConfirmDialog()
        self.assertIsNotNone(dialog.chk_remember)
        dialog.deleteLater()
        _qt_application.processEvents()

    def test_new_log_scrolls_to_bottom_when_text_cursor_is_in_middle(self):
        from mimonitor_toolbox import adb as adb_runtime
        from mimonitor_toolbox.main_window import App

        log_widget = QTextEdit()
        log_widget.resize(400, 220)
        log_widget.show()
        for index in range(200):
            log_widget.append(f"line {index}")
        _qt_application.processEvents()

        cursor = log_widget.textCursor()
        cursor.setPosition(log_widget.document().characterCount() // 2)
        log_widget.setTextCursor(cursor)
        scroll_bar = log_widget.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

        fake_app = type("FakeApp", (), {"log_widget": log_widget})()
        with mock.patch.object(adb_runtime, "_log_file", None):
            App._on_log(fake_app, "new log")
        _qt_application.processEvents()

        self.assertEqual(scroll_bar.value(), scroll_bar.maximum())
        log_widget.close()
        log_widget.deleteLater()
        _qt_application.processEvents()


if __name__ == "__main__":
    unittest.main()
