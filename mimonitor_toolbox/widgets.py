"""应用使用的自定义 Qt 控件。"""

import sys

from PyQt6.QtCore import (
    QEvent,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
)
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    Slider,
    SubtitleLabel,
)

from .windows import user32

class OverlayResizeFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            for child in obj.findChildren(QWidget, "_loading_overlay"):
                child.setGeometry(obj.rect())
        return super().eventFilter(obj, event)
class OsdHud(QWidget):
    def __init__(self, parent=None):
        super().__init__(None) # Independent floating window!
        self._hud_size = QSize(360, 112)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self._hud_size)
        
        # Outer container frame
        self.frame = QFrame(self)
        self.frame.setGeometry(0, 0, self._hud_size.width(), self._hud_size.height())
        self.frame.setObjectName("OsdFrame")
        self.frame.setStyleSheet("""
            #OsdFrame {
                background-color: rgba(20, 20, 20, 215);
                border: 1px solid rgba(255, 255, 255, 45);
                border-radius: 16px;
            }
        """)
        
        # Shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(25)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 8)
        self.frame.setGraphicsEffect(shadow)
        
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(25, 18, 25, 18)
        layout.setSpacing(6)
        
        self.title_lbl = QLabel(self)
        self.title_lbl.setStyleSheet("color: rgba(255, 255, 255, 160); font-size: 13px; font-weight: bold; font-family: 'Segoe UI', 'Microsoft YaHei'; background: transparent;")
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_lbl)
        
        self.val_lbl = QLabel(self)
        self.val_lbl.setStyleSheet("color: #0078d4; font-size: 20px; font-weight: 900; font-family: 'Segoe UI', 'Microsoft YaHei'; background: transparent;")
        self.val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.val_lbl)
        
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide_smooth)
        
        # Fade animation
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(250)
        
    def show_hud(self, title, val):
        self.title_lbl.setText(title)
        self.val_lbl.setText(val)
        self.frame.setGeometry(0, 0, self._hud_size.width(), self._hud_size.height())
        
        # Center bottom of primary screen
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + screen.height() - self.height() - 150 # 150px from bottom offset
        self.move(x, y)
        
        self.timer.stop()
        self.anim.stop()
        try:
            self.anim.finished.disconnect()
        except Exception:
            pass
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()
        if sys.platform == "win32" and user32:
            try:
                user32.SetWindowPos(int(self.winId()), -1, x, y, self.width(), self.height(), 0x0010 | 0x0040)
            except Exception:
                pass
        QTimer.singleShot(0, self.raise_)
        
        # Show on screen for 1.8 seconds
        self.timer.start(1800)
        
    def hide_smooth(self):
        self.anim.stop()
        try:
            self.anim.finished.disconnect(self.hide)
        except (TypeError, RuntimeError):
            pass
        self.anim.setStartValue(self.windowOpacity())
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.finished.connect(self.hide)
        self.anim.start()


class CloseConfirmDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("退出确认")
        
        # Hide Windows system title bar & frame for borderless Fluent style
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(360, 215)
        
        # Center the dialog over the parent window
        if parent:
            self.setGeometry(
                parent.geometry().x() + (parent.width() - self.width()) // 2,
                parent.geometry().y() + (parent.height() - self.height()) // 2,
                self.width(),
                self.height()
            )
            
        top_layout = QVBoxLayout(self)
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        self.bg_frame = QFrame(self)
        self.bg_frame.setObjectName("BgFrame")
        self.bg_frame.setStyleSheet("""
            #BgFrame {
                background-color: #2b2b2b;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 12px;
            }
        """)
        top_layout.addWidget(self.bg_frame)
        
        layout = QVBoxLayout(self.bg_frame)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        
        title = SubtitleLabel("退出确认", self.bg_frame)

        layout.addWidget(title)
        
        desc = BodyLabel("请选择关闭窗口时的行为：\n最小化到系统托盘，还是直接退出程序？", self.bg_frame)

        layout.addWidget(desc)
        
        self.chk_remember = CheckBox("记住我的选择，以后不再提示", self.bg_frame)

        layout.addWidget(self.chk_remember)
        
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.btn_tray = PrimaryPushButton("最小化到托盘", self.bg_frame)
        self.btn_exit = PushButton("直接退出", self.bg_frame)
        self.btn_cancel = PushButton("取消", self.bg_frame)

        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_exit)
        btn_layout.addWidget(self.btn_tray)
        layout.addLayout(btn_layout)
        
        self.choice = None
        self.btn_tray.clicked.connect(self.choose_tray)
        self.btn_exit.clicked.connect(self.choose_exit)
        self.btn_cancel.clicked.connect(self.reject)
        
    def choose_tray(self):
        self.choice = "tray"
        self.accept()
        
    def choose_exit(self):
        self.choice = "exit"
        self.accept()


class LoadingSpinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(42, 42)
        self._angle = 0
        self._base_pen = QPen(QColor(255, 255, 255, 36), 4)
        self._base_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._arc_pen = QPen(QColor("#32e6f0"), 4)
        self._arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._timer = QTimer(self)
        self._timer.setInterval(35)
        self._timer.timeout.connect(self._rotate)
        self._timer.start()

    def _rotate(self):
        self._angle = (self._angle + 10) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(5, 5, -5, -5)
        painter.setPen(self._base_pen)
        painter.drawArc(rect, 0, 360 * 16)
        painter.setPen(self._arc_pen)
        painter.drawArc(rect, self._angle * 16, -115 * 16)


class PageScrollSlider(Slider):
    """横向数值条：忽略滚轮改值，把滚轮交给外层 ScrollArea 滚动页面。"""

    def wheelEvent(self, event):
        # 不 accept：事件继续向上传递，页面仍可滚动；也不改 slider 数值。
        event.ignore()


class InstallProgressDialog(QDialog):
    def __init__(self, apk_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("正在安装 APK")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedSize(380, 178)

        if parent:
            self.setGeometry(
                parent.geometry().x() + (parent.width() - self.width()) // 2,
                parent.geometry().y() + (parent.height() - self.height()) // 2,
                self.width(),
                self.height()
            )

        top_layout = QVBoxLayout(self)
        top_layout.setContentsMargins(0, 0, 0, 0)

        frame = QFrame(self)
        frame.setObjectName("InstallProgressFrame")
        frame.setStyleSheet("""
            #InstallProgressFrame {
                background-color: #2b2b2b;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 12px;
            }
        """)
        top_layout.addWidget(frame)

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(18)

        layout.addWidget(LoadingSpinner(frame), 0, Qt.AlignmentFlag.AlignVCenter)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(8)
        title = SubtitleLabel("正在安装 APK", frame)

        text_layout.addWidget(title)

        desc = BodyLabel(f"正在安装 {apk_name}\n请保持显示器连接，完成前不要关闭软件。", frame)
        desc.setWordWrap(True)

        text_layout.addWidget(desc)
        layout.addLayout(text_layout, 1)
