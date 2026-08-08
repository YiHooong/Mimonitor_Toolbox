import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

_qt_application = QApplication.instance() or QApplication([])


class WidgetConstructionTests(unittest.TestCase):
    """捕获仅在对话框构造时才暴露的控件依赖遗漏。"""

    def test_close_confirmation_dialog_constructs(self):
        from mimonitor_toolbox.widgets import CloseConfirmDialog

        dialog = CloseConfirmDialog()
        self.assertIsNotNone(dialog.chk_remember)
        dialog.deleteLater()
        _qt_application.processEvents()


if __name__ == "__main__":
    unittest.main()
