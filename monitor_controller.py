#!/usr/bin/env python3

import os
import sys
import subprocess
import socket
import threading
import time
import ctypes

# Native Windows Hotkey support variables
user32 = None
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

if sys.platform == "win32":
    try:
        import ctypes.wintypes
        user32 = ctypes.windll.user32
    except Exception as e:
        print(f"Failed to load user32: {e}")
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QObject, QTimer, QEasingCurve, QPropertyAnimation
from PyQt6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor, QPen
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QGridLayout, QSpacerItem, QSizePolicy, QFileDialog, QTextEdit,
    QSystemTrayIcon, QMenu, QDialog, QGraphicsDropShadowEffect, QLabel
)
from qfluentwidgets import (
    FluentWindow, PushButton, PrimaryPushButton, ToggleButton, Slider, ComboBox, LineEdit,
    ScrollArea, BodyLabel, SubtitleLabel, TitleLabel, SimpleCardWidget,
    FluentIcon as FIF, MessageBox, Theme, setTheme, CheckBox
)

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

import json

def get_settings_path():
    """获取跨平台、无需管理员权限的软件配置保存路径"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.environ.get("USERPROFILE", os.path.expanduser("~")))
    else:
        base = os.path.expanduser("~")
    folder = os.path.join(base, ".gpro_controller")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")

def load_settings():
    path = get_settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"close_behavior": "tray", "never_ask_close": False, "saved_ip": ""}

def save_settings(s):
    path = get_settings_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=4, ensure_ascii=False)
    except Exception:
        pass

def get_local_subnet():
    """获取本机网段前缀，如 192.168.5"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ".".join(ip.split(".")[:3])
    except:
        return "192.168.1"


def get_adb_path():
    if hasattr(sys, '_MEIPASS'):
        for n in ["adb.exe", "adb"]:
            p = os.path.join(sys._MEIPASS, n)
            if os.path.exists(p): return p
    base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
    for n in ["adb.exe", "adb"]:
        p = os.path.join(base, n)
        if os.path.exists(p): return p
    return "adb"

ADB = get_adb_path()

# ===== 日志文件 =====
_log_file = None
_log_path = None
_log_to_file_enabled = False
_adb_processes = set()

def _adb_log(msg):
    """写入ADB操作日志到文件"""
    if _log_file and _log_to_file_enabled:
        try:
            _log_file.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            _log_file.flush()
        except: pass

def adb_run(args, timeout=10):
    proc = None
    try:
        proc = subprocess.Popen([ADB]+args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        _adb_processes.add(proc)
        stdout, stderr = proc.communicate(timeout=timeout)
        out = stdout.strip()
        if not out and stderr:
            out = stderr.strip()
        _adb_log(f"adb {' '.join(args)} => {out[:200]}")
        return out
    except subprocess.TimeoutExpired:
        if proc:
            proc.kill()
            try:
                proc.communicate(timeout=1)
            except Exception:
                pass
        _adb_log(f"adb {' '.join(args)} => TIMEOUT")
        return ""
    except Exception as e:
        _adb_log(f"adb {' '.join(args)} => ERROR: {e}")
        return ""
    finally:
        if proc:
            _adb_processes.discard(proc)

def cleanup_adb_processes(kill_server=False):
    for proc in list(_adb_processes):
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
        _adb_processes.discard(proc)
    if kill_server:
        try:
            subprocess.run([ADB, "kill-server"], capture_output=True, text=True, timeout=3,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        except Exception:
            pass


class Adb:
    def __init__(self, ip="192.168.5.205"): self.ip = ip
    def shell(self, cmd):
        out = adb_run(["-s", f"{self.ip}:5555", "shell", cmd])
        return out
    def check_and_heal_jar(self):
        sd_exists = self.shell("[ -f /sdcard/MtkDirectTool.jar ] && echo YES")
        if "YES" not in sd_exists:
            local_jar = None
            if hasattr(sys, '_MEIPASS'):
                p = os.path.join(sys._MEIPASS, "MtkDirectTool.jar")
                if os.path.exists(p): local_jar = p
            if not local_jar:
                base = os.path.dirname(os.path.abspath(sys.argv[0]))
                p = os.path.join(base, "MtkDirectTool.jar")
                if os.path.exists(p): local_jar = p
            if not local_jar:
                p = "/home/hq/mitv/build/MtkDirectTool.jar"
                if os.path.exists(p): local_jar = p
            if local_jar:
                adb_run(["-s", f"{self.ip}:5555", "push", local_jar, "/sdcard/MtkDirectTool.jar"])
            else:
                _adb_log("WARNING: MtkDirectTool.jar 本地未找到，无法推送到设备")
                return
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        self.shell(f'service call TvService 3 s16 "sh -c \\"[ -f {jar} ] || cp /sdcard/MtkDirectTool.jar {jar}\\""')
    def connect(self):
        o = adb_run(["connect", f"{self.ip}:5555"])
        return "connected" in o and "cannot" not in o
    def get(self, k):
        v = self.shell(f"settings get global {k}")
        _adb_log(f"settings get {k} => {v}")
        return v
    def put(self, k, v):
        _adb_log(f"settings put {k} = {v}")
        self.shell(f"settings put global {k} {v}")
    def key(self, k):
        _adb_log(f"keyevent {k}")
        self.shell(f"input keyevent {k}")
    def jni_set(self, key, val, upd=3):
        _adb_log(f"jni_set {key} = {val}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        self.shell(f'service call TvService 3 s16 "sh -c eval\\${{IFS}}CLASSPATH={jar}\\${{IFS}}/system/bin/app_process\\${{IFS}}/data/data/mitv.service/cache\\${{IFS}}MtkDirectTool\\${{IFS}}set\\${{IFS}}{key}\\${{IFS}}{val}\\${{IFS}}{upd}"')
    def jni_get(self, key):
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        self.shell("logcat -c")
        self.shell(f'service call TvService 3 s16 "sh -c eval\\${{IFS}}CLASSPATH={jar}\\${{IFS}}/system/bin/app_process\\${{IFS}}/data/data/mitv.service/cache\\${{IFS}}MtkDirectTool\\${{IFS}}get\\${{IFS}}{key}"')
        time.sleep(0.8)
        log = self.shell(f"logcat -d | grep 'GET {key}' | tail -1")
        i = log.find("= ")
        v = log[i+2:].strip() if i >= 0 else "N/A"
        _adb_log(f"jni_get {key} => {v}")
        return v
    def refresh_pq(self):
        _adb_log("refresh_pq")
        self.shell("am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7")
    def get_model(self):
        m = self.shell("getprop ro.product.model")
        _adb_log(f"get_model => {m}")
        return m


def scan_adb(base="192.168.5", cb=None, log=None):
    found = []
    lock = threading.Lock()

    def chk(ip):
        try:
            s = socket.socket()
            s.settimeout(0.3)
            if s.connect_ex((ip, 5555)) == 0:
                s.close()
                if log: log(f"[扫描] {ip}:5555 开放")
                o = adb_run(["connect", f"{ip}:5555"], 5)
                if log: log(f"[扫描] {ip} adb: {o}")
                if "connected" in o and "cannot" not in o:
                    m = adb_run(["-s", f"{ip}:5555", "shell", "getprop ro.product.model"], 3) or "?"
                    with lock:
                        found.append((ip, m))
                    if cb: cb(ip, m)
            else:
                s.close()
        except:
            pass

    if log: log(f"[扫描] 开始 {base}.1~254")
    ts = []
    for i in range(1, 255):
        t = threading.Thread(target=chk, args=(f"{base}.{i}",), daemon=True)
        t.start(); ts.append(t)
        if len(ts) >= 60:
            for t in ts: t.join(timeout=3)
            ts = []
    for t in ts: t.join(timeout=3)
    if log: log(f"[扫描] 完成，发现 {len(found)} 台")
    return found


def async_run(fn): threading.Thread(target=fn, daemon=True).start()


class OsdHud(QWidget):
    def __init__(self, parent=None):
        super().__init__(None) # Independent floating window!
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        
        # Outer container frame
        self.frame = QFrame(self)
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
        
        # Calculate size dynamically based on text layout
        self.frame.adjustSize()
        self.resize(self.frame.size())
        
        # Center bottom of primary screen
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = screen.height() - self.height() - 150 # 150px from bottom offset
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
            self.anim.finished.disconnect()
        except Exception:
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
        title.setStyleSheet("color: white; font-weight: bold; font-size: 16px;")
        layout.addWidget(title)
        
        desc = BodyLabel("请选择关闭窗口时的行为：\n最小化到系统托盘，还是直接退出程序？", self.bg_frame)
        desc.setStyleSheet("color: rgba(255, 255, 255, 0.85); font-size: 13px; line-height: 1.5;")
        layout.addWidget(desc)
        
        self.chk_remember = CheckBox("记住我的选择，以后不再提示", self.bg_frame)
        self.chk_remember.setStyleSheet("color: rgba(255, 255, 255, 0.95); font-size: 12px;")
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
        base_pen = QPen(QColor(255, 255, 255, 36), 4)
        base_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(base_pen)
        painter.drawArc(rect, 0, 360 * 16)

        arc_pen = QPen(QColor("#32e6f0"), 4)
        arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        painter.drawArc(rect, self._angle * 16, -115 * 16)


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
        title.setStyleSheet("color: white; font-size: 17px; font-weight: 700;")
        text_layout.addWidget(title)

        desc = BodyLabel(f"正在安装 {apk_name}\n请保持显示器连接，完成前不要关闭软件。", frame)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: rgba(255, 255, 255, 0.78); font-size: 13px;")
        text_layout.addWidget(desc)
        layout.addLayout(text_layout, 1)


class App(FluentWindow):
    # Signals for thread-safe UI updates
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    values_signal = pyqtSignal(dict)
    jni_values_signal = pyqtSignal(dict)
    devices_signal = pyqtSignal(list)
    message_signal = pyqtSignal(str, str, str) # type, title, text
    apk_install_finished = pyqtSignal(bool, str, str)

    def __init__(self):
        super().__init__()
        QApplication.setQuitOnLastWindowClosed(False)
        self.adb = Adb()
        self.mode_btns = {}
        self.sliders = {}
        self.state_buttons = {}
        self._source_names = {23: "HDMI 1", 24: "HDMI 2", 29: "DP", 30: "USBC", "23": "HDMI 1", "24": "HDMI 2", "29": "DP", "30": "USBC"}
        self.source_var_text = "未知"
        self._page_loaded = set()  # 已加载数据的页面 objectName 集合
        self._page_loading = set()  # 正在加载中的页面
        self._page_data_keys = {
            "picturePage": {
                "settings": ["picture_mode", "picture_backlight", "xiaomi_picture_backlight",
                             "picture_brightness", "picture_contrast", "picture_saturation",
                             "picture_hue", "picture_sharpness", "picture_color_temperature",
                             "tv_picture_video_local_dimming", "picture_dynamic_definition",
                             "picture_response_time", "tv_picture_video_color_space"],
                "jni": ["g_disp__disp_back_light", "g_video__vid_gamut_mapping_mode", "g_video__clr_temp", "g_video__vid_local_dimming"],
            },
            "gamePage": {
                "settings": ["front_sight_index", "mt_game_dynamic_ft", "mt_game_scope",
                             "mt_game_scope_night", "monitor_menu_fps_counter",
                             "monitor_menu_stopwatch", "monitor_menu_timer", "mitv.tvplayer.hdmi.last.source"],
                "jni_mode": True,  # 需要根据输入源选择不同的 JNI key
            },
            "sourcePage": {
                "settings": ["mitv.tvplayer.hdmi.last.source"],
            },
        }

        # 初始化日志文件路径（等用户开启时再创建文件）
        global _log_path
        _log_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "logs", f"log_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        self.is_forcing_exit = False
        self.page_status_indicators = []
        self.adb_connected = False
        self._cleanup_done = False

        # Window properties
        self.setWindowTitle("红米 G Pro 27U Toolbox")
        self.resize(1000, 750)

        # Connect signals
        self.log_signal.connect(self._on_log)
        self.status_signal.connect(self._on_status)
        self.values_signal.connect(self._apply_polled_values)
        self.jni_values_signal.connect(self._apply_polled_jni_values)
        self.devices_signal.connect(self._update_scanned_devices)
        self.message_signal.connect(self._show_message_box)
        self.apk_install_finished.connect(self._on_apk_install_finished)

        # Setup layout and components
        self.osd = OsdHud(self)
        self.setup_ui()
        self.setup_tray()
        self.current_vals = {}
        self.register_global_hotkeys()

        # 页面切换时按需加载数据
        self.stackedWidget.currentChanged.connect(self._on_page_changed)
        self.adb_keepalive_timer = QTimer(self)
        self.adb_keepalive_timer.setInterval(15000)
        self.adb_keepalive_timer.timeout.connect(self._keep_adb_alive)
        self.adb_keepalive_timer.start()
        QApplication.instance().aboutToQuit.connect(self.cleanup_before_exit)

    def setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)

        # Create a beautiful, crisp G Pro theme icon dynamically!
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#734EFF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 28, 28)
        painter.setPen(QColor("white"))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(18)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "G")
        painter.end()
        icon = QIcon(pixmap)

        self.tray_icon.setIcon(icon)
        self.setWindowIcon(icon)
        self.tray_icon.setToolTip("红米 G Pro 27U Toolbox")
        
        menu = QMenu()
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self.show_and_raise)
        
        exit_action = QAction("退出程序", self)
        exit_action.triggered.connect(self.force_exit)
        
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def show_and_raise(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger or reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.show_and_raise()

    def register_global_hotkeys(self):
        if sys.platform != "win32" or not user32:
            return
            
        self.unregister_all_hotkeys()
        
        settings = load_settings()
        hotkeys = settings.get("hotkeys", {})
        
        self.hotkey_registry = {}
        
        mod_map = {
            "无": 0,
            "Ctrl + Alt": MOD_CONTROL | MOD_ALT,
            "Ctrl + Shift": MOD_CONTROL | MOD_SHIFT,
            "Alt + Shift": MOD_ALT | MOD_SHIFT,
            "Win + Shift": MOD_WIN | MOD_SHIFT
        }
        
        vk_map = {}
        for i in range(1, 13):
            vk_map[f"F{i}"] = 0x6F + i
        for i in range(0, 10):
            vk_map[str(i)] = 0x30 + i
        for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            vk_map[char] = ord(char)
            
        hwnd = int(self.winId())
        
        hotkey_id = 1
        for action_name, hk_conf in hotkeys.items():
            mod_str = hk_conf.get("modifier", "无")
            key_str = hk_conf.get("key", "无")
            if mod_str == "无" and key_str == "无":
                continue
                
            mod_val = mod_map.get(mod_str, 0)
            vk_val = vk_map.get(key_str, 0)
            if vk_val == 0:
                continue
                
            res = user32.RegisterHotKey(hwnd, hotkey_id, mod_val, vk_val)
            if res:
                self.hotkey_registry[hotkey_id] = action_name
                hotkey_id += 1

    def unregister_all_hotkeys(self):
        if sys.platform != "win32" or not user32 or not hasattr(self, "hotkey_registry"):
            return
        hwnd = int(self.winId())
        for hid in list(getattr(self, "hotkey_registry", {}).keys()):
            user32.UnregisterHotKey(hwnd, hid)
        self.hotkey_registry = {}

    def nativeEvent(self, eventType, message):
        if sys.platform == "win32" and eventType == b"windows_generic_MSG" and user32:
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                if hotkey_id in getattr(self, "hotkey_registry", {}):
                    action = self.hotkey_registry[hotkey_id]
                    self.trigger_hotkey_action(action)
                return True, 0
        return super().nativeEvent(eventType, message)

    def trigger_hotkey_action(self, action):
        if not getattr(self, "adb_connected", False):
            return
            
        if not hasattr(self, "pending_notifications"):
            self.pending_notifications = {}
            
        actions_map = {
            "picture_mode_cycle": (
                "picture_mode",
                [(14, "标准"), (10, "游戏"), (9, "电影")],
                "画面模式",
                lambda val, name: self._set_mode(val, name)
            ),
            "local_dimming_cycle": (
                "tv_picture_video_local_dimming",
                [(0, "关"), (1, "低"), (2, "中"), (3, "高")],
                "精密控光",
                lambda val, name: self._jni("g_video__vid_local_dimming", val, "tv_picture_video_local_dimming", f"精密控光: {name}")
            ),
            "color_space_cycle": (
                "tv_picture_video_color_space",
                [(0, "自动"), (3, "sRGB"), (6, "DCI-P3"), (4, "AdobeRGB"), (5, "BT2020"), (7, "BT709")],
                "色域",
                lambda val, name: self._jni("g_video__vid_gamut_mapping_mode", val, "tv_picture_video_color_space", f"色域: {name}", "tv_picture_advanced_video_color_space")
            ),
            "color_temp_cycle": (
                "picture_color_temperature",
                [(3, "暖色"), (2, "标准"), (1, "冷色"), (6, "原色"), (0, "自定义")],
                "色温",
                lambda val, name: self._set_color_temp(4 if val==3 else (3 if val==2 else (2 if val==1 else (6 if val==6 else 1))), val, f"色温: {name}")
            ),
            "response_time_cycle": (
                "picture_response_time",
                [(1, "普通"), (2, "快速"), (3, "高速")],
                "灰阶响应时间",
                lambda val, name: self._jni("g_video__vid_od_response_time", val, "picture_response_time", f"响应时间: {name}")
            ),
            "freesync_toggle": (
                "freesync",
                [(0, "关"), (1, "开")],
                "FreeSync",
                lambda val, name: self._fsync(val == 1)
            ),
            "input_source_cycle": (
                "tv_input_source_id",
                [(23, "HDMI 1"), (24, "HDMI 2"), (29, "DP"), (30, "USBC")],
                "信号源切换",
                lambda val, name: self._set("tv_input_source_id", val, f"信号源: {name}")
            )
        }
        
        if action not in actions_map:
            return
            
        sk, state_tuples, label_name, exec_fn = actions_map[action]
        curr_val = getattr(self, "current_vals", {}).get(sk, state_tuples[0][0])
        
        curr_idx = -1
        for idx, (val, name) in enumerate(state_tuples):
            if val == curr_val:
                curr_idx = idx
                break
                
        next_idx = (curr_idx + 1) % len(state_tuples)
        next_val, next_name = state_tuples[next_idx]
        
        self.pending_notifications[sk] = (next_val, label_name, next_name)
        if getattr(self, "osd", None):
            self.osd.show_hud(label_name, next_name)
        
        try:
            exec_fn(next_val, next_name)
            def verify():
                for _ in range(6):
                    time.sleep(0.5)
                    real_val = self.query_setting_or_jni(sk)
                    if str(real_val) == str(next_val):
                        if sk == "freesync":
                            self.jni_values_signal.emit({sk: real_val})
                        else:
                            self.values_signal.emit({sk: real_val})
                        break
            async_run(verify)
        except Exception as e:
            if sk in self.pending_notifications:
                del self.pending_notifications[sk]
            self.log(f"快捷键执行失败: {e}")

    def query_setting_or_jni(self, sk):
        if sk == "tv_picture_video_local_dimming":
            v = self.adb.jni_get("g_video__vid_local_dimming")
            try: return int(v)
            except: return 0
        elif sk == "picture_response_time":
            v = self.adb.jni_get("g_video__vid_od_response_time")
            try: return int(v)
            except: return 0
        elif sk == "freesync":
            src = self.adb.get("tv_input_source_id")
            try: src_id = int(src)
            except: src_id = 0
            if src_id in (29, 30):
                fs = self.adb.jni_get("g_video__dp_adaptive_sync")
                try: return 1 if int(fs) == 1 else 0
                except: return 0
            else:
                fs = self.adb.jni_get("g_video__freesync_switch")
                try: return 1 if int(fs) == 3 else 0
                except: return 0
        else:
            # Standard settings key
            v = self.adb.get(sk)
            try: return int(v)
            except: return v

    def _check_pending_notifications(self, new_vals):
        if not hasattr(self, "pending_notifications"):
            return
        for key, (target_val, feature_name, value_name) in list(self.pending_notifications.items()):
            if key in new_vals and str(new_vals[key]) == str(target_val):
                if getattr(self, "osd", None) and not self.osd.isVisible():
                    self.osd.show_hud(feature_name, value_name)
                elif not getattr(self, "osd", None):
                    self.tray_icon.showMessage(
                        "红米 G Pro 27U Toolbox",
                        f"{feature_name} 已成功设置为：{value_name}",
                        QSystemTrayIcon.MessageIcon.Information,
                        2500
                    )
                del self.pending_notifications[key]

    def _keep_adb_alive(self):
        if getattr(self, "_cleanup_done", False):
            return
        if not getattr(self, "adb_connected", False) or not self.adb.ip:
            return
        ip = self.adb.ip

        def do():
            state = adb_run(["-s", f"{ip}:5555", "get-state"], timeout=3).strip().lower()
            if state != "device":
                adb_run(["connect", f"{ip}:5555"], timeout=5)

        async_run(do)

    def cleanup_before_exit(self):
        if getattr(self, "_cleanup_done", False):
            return
        self._cleanup_done = True
        try:
            self.unregister_all_hotkeys()
        except Exception:
            pass
        try:
            if hasattr(self, "adb_keepalive_timer"):
                self.adb_keepalive_timer.stop()
        except Exception:
            pass
        try:
            if self.adb.ip:
                adb_run(["disconnect", f"{self.adb.ip}:5555"], timeout=3)
        except Exception:
            pass
        cleanup_adb_processes(kill_server=True)
        try:
            if _log_file:
                _log_file.flush()
                _log_file.close()
        except Exception:
            pass

    def force_exit(self):
        self.is_forcing_exit = True
        self.tray_icon.hide()
        self.cleanup_before_exit()
        QApplication.quit()

    def closeEvent(self, event):
        if getattr(self, "is_forcing_exit", False):
            self.cleanup_before_exit()
            event.accept()
            return
            
        settings = load_settings()
        if settings.get("never_ask_close", False):
            behavior = settings.get("close_behavior", "tray")
            if behavior == "tray":
                event.ignore()
                self.hide()
            else:
                self.tray_icon.hide()
                self.cleanup_before_exit()
                event.accept()
                QApplication.quit()
        else:
            dialog = CloseConfirmDialog(self)
            if dialog.exec():
                choice = dialog.choice
                remember = dialog.chk_remember.isChecked()
                
                settings["close_behavior"] = choice
                settings["never_ask_close"] = remember
                save_settings(settings)
                
                if choice == "tray":
                    event.ignore()
                    self.hide()
                    self.tray_icon.showMessage(
                        "红米 G Pro 27U Toolbox",
                        "程序已最小化到系统托盘。双击托盘图标可重新打开，您也可以在 [工具与设置] 页面更改设置。",
                        QSystemTrayIcon.MessageIcon.Information,
                        3000
                    )
                else:
                    self.tray_icon.hide()
                    self.cleanup_before_exit()
                    event.accept()
                    QApplication.quit()
            else:
                event.ignore()

    def _on_log(self, text):
        self.log_widget.append(text)
        self.log_widget.ensureCursorVisible()
        if _log_file and _log_to_file_enabled:
            try:
                _log_file.write(text + "\n")
                _log_file.flush()
            except: pass

    def _toggle_log_file(self, state):
        global _log_to_file_enabled, _log_file
        _log_to_file_enabled = (state == 2)
        if _log_to_file_enabled and not _log_file:
            os.makedirs(os.path.dirname(_log_path), exist_ok=True)
            _log_file = open(_log_path, "a", encoding="utf-8")
            _log_file.write(f"{'='*60}\n")
            _log_file.write(f"红米 G Pro 27U Toolbox 日志 - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            _log_file.write(f"{'='*60}\n")
            _log_file.flush()
        self.log(f"本地日志记录: {'开启' if _log_to_file_enabled else '关闭'}")

    def _toggle_autostart(self, state):
        enabled = (state == 2)
        s = load_settings()
        s["autostart"] = enabled
        save_settings(s)
        if enabled:
            self._install_autostart()
            self.log("已设置开机自启动")
        else:
            self._remove_autostart()
            self.log("已取消开机自启动")

    def _get_autostart_path(self):
        if sys.platform == "win32":
            startup = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
            return os.path.join(startup, "RedmiToolbox.bat")
        else:
            return os.path.expanduser("~/.config/autostart/mitvcontroller.desktop")

    def _get_exe_path(self):
        if getattr(sys, 'frozen', False):
            return sys.executable
        return os.path.abspath(sys.argv[0])

    def _install_autostart(self):
        exe = self._get_exe_path()
        path = self._get_autostart_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if sys.platform == "win32":
                with open(path, "w") as f:
                    f.write(f'start /min "" "{exe}" --minimized\n')
            else:
                with open(path, "w") as f:
                    f.write(f"[Desktop Entry]\nType=Application\nName=RedmiToolbox\nExec=python3 {exe} --minimized\nX-GNOME-Autostart-enabled=true\n")
                os.chmod(path, 0o755)
        except Exception as e:
            self.log(f"设置自启动失败: {e}")

    def _remove_autostart(self):
        path = self._get_autostart_path()
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            self.log(f"取消自启动失败: {e}")

    def _toggle_4k_ui(self, state):
        enable = (state == 2)
        if enable:
            w = MessageBox("需要重启显示器", "启用 4K UI 需要重启显示器才能生效。\n\n点击确定后将设置分辨率为 3840×2160、DPI 640，并重启显示器。\n\n是否继续？", self)
            if not w.exec():
                self.chk_4k.blockSignals(True)
                self.chk_4k.setChecked(False)
                self.chk_4k.blockSignals(False)
                return
            self.adb.shell("wm size 3840x2160")
            self.adb.shell("wm density 640")
            self.log("已设置 4K UI (3840×2160 / DPI 640)")
            self.log("正在重启显示器...")
            self.adb.shell("reboot")
        else:
            self.adb.shell("wm size 1920x1080")
            self.adb.shell("wm density 320")
            self.log("已恢复 1080p UI (1920×1080 / DPI 320)")

    def _check_4k_state(self):
        """检测 Override size，存在且大于1080p则为4K模式"""
        if not getattr(self, "adb_connected", False):
            return
        try:
            res = self.adb.shell("wm size")
            is_4k = False
            for line in res.split("\n"):
                if "Override size" in line:
                    parts = line.split(":")[-1].strip().split("x")
                    if len(parts) == 2:
                        w, h = int(parts[0]), int(parts[1])
                        is_4k = (w > 1920 or h > 1080)
                    break
            self.chk_4k.blockSignals(True)
            self.chk_4k.setChecked(is_4k)
            self.chk_4k.blockSignals(False)
        except:
            pass

    def _export_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出日志", f"mitv_log_{time.strftime('%Y%m%d_%H%M%S')}.txt", "文本文件 (*.txt)")
        if path:
            try:
                if _log_file: _log_file.flush()
                import shutil
                shutil.copy2(_log_path, path)
                self.log(f"日志已导出: {path}")
            except Exception as e:
                self.log(f"导出失败: {e}")

    def _open_log_dir(self):
        log_dir = os.path.dirname(_log_path) if _log_path else os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "logs")
        if sys.platform == "win32":
            os.startfile(log_dir)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", log_dir])
        else:
            subprocess.Popen(["xdg-open", log_dir])

    def _on_status(self, text):
        self.status_label.setText(text)
        was_connected = getattr(self, "adb_connected", False)
        self.adb_connected = ("已连接" in text)
        # 连接状态变化时清除页面缓存
        if was_connected and not self.adb_connected:
            self._page_loaded.clear()
        elif not was_connected and self.adb_connected:
            self._page_loaded.clear()
            # 触发当前页面加载
            page = self.stackedWidget.currentWidget()
            if page:
                self._on_page_changed(self.stackedWidget.currentIndex())
            # 检测 4K 状态
            QTimer.singleShot(1500, self._check_4k_state)
        
        if "扫描中" in text:
            status_suffix = "🟡 正在扫描内网..."
            self.status_label.setStyleSheet("color: #b85c00; font-weight: bold; font-size: 14px;")
        elif "扫描完成" in text:
            status_suffix = f"🟡 {text}"
            self.status_label.setStyleSheet("color: #b85c00; font-weight: bold; font-size: 14px;")
            # 扫描完成后自动连接优先匹配到的显示器
            if self.dev_combo.count() > 0 and not self.adb_connected:
                self._on_dev_sel(self.dev_combo.currentIndex())
        elif "未连接" in text or "失败" in text:
            status_suffix = "🔴 未连接"
            self.status_label.setStyleSheet("color: #d83b01; font-weight: bold; font-size: 14px;")
        elif "连接中" in text:
            status_suffix = "🟡 连接中"
            self.status_label.setStyleSheet("color: #b85c00; font-weight: bold; font-size: 14px;")
        elif "已连接" in text:
            status_suffix = f"🟢 已连接 ({text.replace('已连接: ', '')})"
            self.status_label.setStyleSheet("color: #107c41; font-weight: bold; font-size: 14px;")
        else:
            status_suffix = text
            self.status_label.setStyleSheet("color: #107c41; font-weight: bold; font-size: 14px;")
            
        self.setWindowTitle(f"红米 G Pro 27U Toolbox - {status_suffix}")

    def _show_message_box(self, mtype, title, text):
        w = MessageBox(title, text, self)
        w.exec()

    def setup_ui(self):
        self.home_page = self._make_home_page()
        self.picture_page = self._make_picture_page()
        self.game_page = self._make_game_page()
        self.source_page = self._make_source_page()
        self.tools_page = self._make_tools_page()
        self.remote_page = self._make_remote_page()

        self.home_page.setObjectName("homePage")
        self.picture_page.setObjectName("picturePage")
        self.game_page.setObjectName("gamePage")
        self.source_page.setObjectName("sourcePage")
        self.tools_page.setObjectName("toolsPage")
        self.remote_page.setObjectName("remotePage")

        # Add routes
        self.addSubInterface(self.home_page, FIF.HOME, "主页 & 连接")
        self.addSubInterface(self.picture_page, FIF.PALETTE, "画面设置")
        self.addSubInterface(self.game_page, FIF.GAME, "游戏模式")
        self.addSubInterface(self.source_page, FIF.SYNC, "信号源切换")
        self.addSubInterface(self.tools_page, FIF.DEVELOPER_TOOLS, "工具与设置")
        self.addSubInterface(self.remote_page, FIF.TILES, "遥控器")

        # Hide return (back) button
        self.navigationInterface.setReturnButtonVisible(False)

    def log(self, m):
        self.log_signal.emit(f"[{time.strftime('%H:%M:%S')}] {m}")

    # ===== Pages Creators =====
    def _make_home_page(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = TitleLabel("红米 G Pro 27U Toolbox", container)
        title_font = title.font()
        title_font.setPixelSize(28)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = BodyLabel("通过无线 ADB 连接并调优您的 MiniLED 旗舰显示器", container)
        sub_font = subtitle.font()
        sub_font.setPixelSize(14)
        subtitle.setFont(sub_font)
        layout.addWidget(subtitle)

        # Connection Card
        conn_card = SimpleCardWidget(container)
        conn_layout = QVBoxLayout(conn_card)
        conn_layout.setContentsMargins(20, 20, 20, 20)
        conn_layout.setSpacing(15)

        conn_title = SubtitleLabel("连接到显示器", conn_card)
        conn_layout.addWidget(conn_title)

        # IP Row
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        
        ip_label = BodyLabel("显示器 IP:", conn_card)
        row1.addWidget(ip_label)
        
        self.ip_entry = LineEdit(conn_card)
        self.ip_entry.setPlaceholderText("请输入 IP 地址")
        settings = load_settings()
        saved_ip = settings.get("saved_ip", "")
        self.ip_entry.setText(saved_ip)
        self.ip_entry.setFixedWidth(250)
        row1.addWidget(self.ip_entry)

        self.connect_btn = PrimaryPushButton("🔌 开始连接", conn_card)
        self.connect_btn.clicked.connect(self.connect)
        row1.addWidget(self.connect_btn)

        self.scan_btn = PushButton("🔍 扫描内网", conn_card)
        self.scan_btn.clicked.connect(self.scan_net)
        row1.addWidget(self.scan_btn)

        self.disconnect_btn = PushButton("🔌 断开连接", conn_card)
        self.disconnect_btn.clicked.connect(self.disconnect_adb)
        row1.addWidget(self.disconnect_btn)
        row1.addStretch(1)
        conn_layout.addLayout(row1)

        # Dropdown and Status
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        dev_label = BodyLabel("已扫描设备:", conn_card)
        row2.addWidget(dev_label)

        self.dev_combo = ComboBox(conn_card)
        self.dev_combo.setPlaceholderText("请选择扫描到的显示器...")
        self.dev_combo.setFixedWidth(250)
        self.dev_combo.currentIndexChanged.connect(self._on_dev_sel)
        row2.addWidget(self.dev_combo)

        status_prefix = BodyLabel("连接状态:", conn_card)
        row2.addWidget(status_prefix)

        self.status_label = BodyLabel("未连接", conn_card)
        self.status_label.setStyleSheet("color: #d83b01; font-weight: bold; font-size: 14px;")
        row2.addWidget(self.status_label)

        row2.addStretch(1)
        conn_layout.addLayout(row2)

        layout.addWidget(conn_card)

        # Log Card
        log_card = SimpleCardWidget(container)
        log_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(15, 15, 15, 15)
        log_layout.setSpacing(12)
        
        log_title = SubtitleLabel("实时操作日志", log_card)
        log_layout.addWidget(log_title)

        self.log_widget = QTextEdit(log_card)
        self.log_widget.setReadOnly(True)
        self.log_widget.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #00ff00;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 4px;
            }
        """)
        self.log_widget.setFixedHeight(220)
        self.log_widget.append("[00:00:00] 系统就绪，等待连接...")
        log_layout.addWidget(self.log_widget)

        log_btn_row = QHBoxLayout()
        self.log_file_toggle = CheckBox("记录到本地文件", log_card)
        self.log_file_toggle.setChecked(False)
        self.log_file_toggle.stateChanged.connect(self._toggle_log_file)
        log_btn_row.addWidget(self.log_file_toggle)
        log_btn_row.addStretch(1)
        export_log_btn = PushButton("导出日志", log_card)
        export_log_btn.clicked.connect(self._export_log)
        log_btn_row.addWidget(export_log_btn)
        open_log_btn = PushButton("打开日志目录", log_card)
        open_log_btn.clicked.connect(self._open_log_dir)
        log_btn_row.addWidget(open_log_btn)
        log_layout.addLayout(log_btn_row)

        layout.addWidget(log_card, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
        return container

    def _make_picture_page(self):
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setObjectName("Container")
        container.setStyleSheet("#Container { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)



        title_row = QHBoxLayout()
        title = SubtitleLabel("画面设置", container)
        title.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 5px;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        refresh_pic_btn = PushButton("刷新数据", container)
        refresh_pic_btn.clicked.connect(lambda: self._force_refresh_page("picturePage"))
        title_row.addWidget(refresh_pic_btn)
        layout.addLayout(title_row)

        # Mode Selector Card
        lf = SimpleCardWidget(container)
        lf_layout = QVBoxLayout(lf)
        lf_layout.setContentsMargins(15, 15, 15, 15)
        lf_layout.addWidget(BodyLabel("画面模式", lf))
        
        h = QHBoxLayout()
        h.setSpacing(10)
        h.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for val, name in [(14, "标准"), (10, "游戏"), (9, "电影")]:
            b = ToggleButton(name, lf)
            b.setCheckable(True)
            b.setFixedWidth(100)
            b.clicked.connect(lambda checked=False, v=val, n=name: self._set_mode(v, n))
            h.addWidget(b)
            self.mode_btns[val] = b
        h.addSpacing(20)
        reset_mode_btn = PushButton("恢复默认", lf)
        reset_mode_btn.setFixedWidth(100)
        reset_mode_btn.clicked.connect(self._reset_current_mode)
        h.addWidget(reset_mode_btn)
        lf_layout.addLayout(h)
        layout.addWidget(lf)

        # Sliders
        self._add_slider(layout, "背光", "backlight", 1, 100, 50, jni_key="g_disp__disp_back_light", settings_keys=["picture_backlight", "xiaomi_picture_backlight"])
        self._add_slider(layout, "黑色级别", "black_level", 1, 100, 50, settings_keys=["picture_brightness"])
        self._add_slider(layout, "对比度", "contrast", 0, 100, 50, settings_keys=["picture_contrast"])
        self._add_slider(layout, "饱和度", "saturation", 0, 100, 50, settings_keys=["picture_saturation"])
        self._add_slider(layout, "色调", "hue", 1, 100, 50, settings_keys=["picture_hue"])
        self._add_slider(layout, "锐度", "sharpness", 0, 100, 1, settings_keys=["picture_sharpness"])

        # Button Groups
        self._btn_section(layout, "色温", [
            ("暖色", 3, lambda _: self._set_color_temp(4, 3, "色温: 暖色")),
            ("标准", 2, lambda _: self._set_color_temp(3, 2, "色温: 标准")),
            ("冷色", 1, lambda _: self._set_color_temp(2, 1, "色温: 冷色")),
            ("原色", 6, lambda _: self._set_color_temp(6, 6, "色温: 原色")),
            ("自定义", 0, lambda _: self._set_color_temp(1, 0, "色温: 自定义")),
        ], state_key="picture_color_temperature")

        self._btn_section(layout, "精密控光", [
            ("关", 0, lambda _: self._jni("g_video__vid_local_dimming", 0, "tv_picture_video_local_dimming", "精密控光: 关")),
            ("低", 1, lambda _: self._jni("g_video__vid_local_dimming", 1, "tv_picture_video_local_dimming", "精密控光: 低")),
            ("中", 2, lambda _: self._jni("g_video__vid_local_dimming", 2, "tv_picture_video_local_dimming", "精密控光: 中")),
            ("高", 3, lambda _: self._jni("g_video__vid_local_dimming", 3, "tv_picture_video_local_dimming", "精密控光: 高")),
        ], state_key="tv_picture_video_local_dimming")

        self._btn_section(layout, "动态清晰度", [
            ("关", 0, lambda _: self._jni("g_video__vid_insert_black", 0, "picture_dynamic_definition", "动态清晰度: 关")),
            ("低", 1, lambda _: self._jni("g_video__vid_insert_black", 1, "picture_dynamic_definition", "动态清晰度: 低")),
            ("中", 2, lambda _: self._jni("g_video__vid_insert_black", 2, "picture_dynamic_definition", "动态清晰度: 中")),
            ("高", 3, lambda _: self._jni("g_video__vid_insert_black", 3, "picture_dynamic_definition", "动态清晰度: 高")),
        ], state_key="picture_dynamic_definition")

        self._btn_section(layout, "灰阶响应时间", [
            ("普通", 1, lambda _: self._jni("g_video__vid_od_response_time", 1, "picture_response_time", "响应时间: 普通")),
            ("快速", 2, lambda _: self._jni("g_video__vid_od_response_time", 2, "picture_response_time", "响应时间: 快速")),
            ("高速", 3, lambda _: self._jni("g_video__vid_od_response_time", 3, "picture_response_time", "响应时间: 高速")),
        ], state_key="picture_response_time")

        self._btn_section(layout, "色域", [
            ("自动", 0, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 0, "tv_picture_video_color_space", "色域: 自动", "tv_picture_advanced_video_color_space")),
            ("sRGB", 3, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 3, "tv_picture_video_color_space", "色域: sRGB", "tv_picture_advanced_video_color_space")),
            ("DCI-P3", 6, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 6, "tv_picture_video_color_space", "色域: DCI-P3", "tv_picture_advanced_video_color_space")),
            ("AdobeRGB", 4, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 4, "tv_picture_video_color_space", "色域: Adobe RGB", "tv_picture_advanced_video_color_space")),
            ("BT2020", 5, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 5, "tv_picture_video_color_space", "色域: BT2020", "tv_picture_advanced_video_color_space")),
            ("BT709", 7, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 7, "tv_picture_video_color_space", "色域: BT709", "tv_picture_advanced_video_color_space")),
        ], state_key="tv_picture_video_color_space")

        scroll.setWidget(container)
        return scroll

    def _make_game_page(self):
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setObjectName("Container")
        container.setStyleSheet("#Container { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)



        title_row = QHBoxLayout()
        title = SubtitleLabel("游戏模式", container)
        title.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 5px;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        refresh_game_btn = PushButton("刷新数据", container)
        refresh_game_btn.clicked.connect(lambda: self._force_refresh_page("gamePage"))
        title_row.addWidget(refresh_game_btn)
        layout.addLayout(title_row)

        # Game Switches
        self._btn_section(layout, "准星", [("关", 0, lambda _: self._fs(0))]+[(str(i), i, lambda _, v=i: self._fs(v)) for i in range(1,6)], state_key="front_sight_index")
        
        self._btn_section(layout, "动态准星", [
            ("关", 0, lambda _: self._set("mt_game_dynamic_ft", 0, "动态准星: 关")),
            ("开", 1, lambda _: self._set("mt_game_dynamic_ft", 1, "动态准星: 开")),
        ], state_key="mt_game_dynamic_ft")

        self._btn_section(layout, "狙击镜", [
            ("关", 0, lambda _: self._set("mt_game_scope", 0, "狙击镜: 关")),
            ("1.1x", 1, lambda _: self._set("mt_game_scope", 1, "狙击镜: 1.1x")),
            ("1.3x", 3, lambda _: self._set("mt_game_scope", 3, "狙击镜: 1.3x")),
            ("1.5x", 5, lambda _: self._set("mt_game_scope", 5, "狙击镜: 1.5x")),
            ("1.7x", 7, lambda _: self._set("mt_game_scope", 7, "狙击镜: 1.7x")),
            ("2.0x", 10, lambda _: self._set("mt_game_scope", 10, "狙击镜: 2.0x")),
        ], state_key="mt_game_scope")

        self._btn_section(layout, "狙击镜夜视", [
            ("关", 0, lambda _: self._set("mt_game_scope_night", 0, "狙击镜夜视: 关")),
            ("开", 1, lambda _: self._set("mt_game_scope_night", 1, "狙击镜夜视: 开")),
        ], state_key="mt_game_scope_night")

        self._btn_section(layout, "320Hz竞技模式", [
            ("关", 0, lambda _: self._320(False)),
            ("开", 1, lambda _: self._320(True)),
        ], state_key="mode_320")

        self._btn_section(layout, "FreeSync Premium Pro", [
            ("关", 0, lambda _: self._fsync(False)),
            ("开", 1, lambda _: self._fsync(True)),
        ], state_key="freesync")

        self._btn_section(layout, "FPS计数器", [
            ("关", 0, lambda _: self._set("monitor_menu_fps_counter", 0, "FPS: 关")),
            ("刷新率", 1, lambda _: self._set("monitor_menu_fps_counter", 1, "FPS: 刷新率")),
            ("柱状图", 2, lambda _: self._set("monitor_menu_fps_counter", 2, "FPS: 柱状图")),
        ], state_key="monitor_menu_fps_counter")

        self._btn_section(layout, "秒表", [
            ("关", 0, lambda _: self._set("monitor_menu_stopwatch", 0, "秒表: 关")),
            ("开", 1, lambda _: self._set("monitor_menu_stopwatch", 1, "秒表: 开")),
        ], state_key="monitor_menu_stopwatch")

        self._btn_section(layout, "定时器", [
            ("关", 0, lambda _: self._set("monitor_menu_timer", 0, "定时器: 关")),
            ("1分钟", 60, lambda _: self._set("monitor_menu_timer", 60, "定时器: 1分钟")),
            ("5分钟", 300, lambda _: self._set("monitor_menu_timer", 300, "定时器: 5分钟")),
            ("30分钟", 1800, lambda _: self._set("monitor_menu_timer", 1800, "定时器: 30分钟")),
            ("60分钟", 3600, lambda _: self._set("monitor_menu_timer", 3600, "定时器: 60分钟")),
        ], state_key="monitor_menu_timer")

        scroll.setWidget(container)
        return scroll

    def _make_source_page(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title_row = QHBoxLayout()
        title = SubtitleLabel("信号源切换", container)
        title.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 5px;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        refresh_source_btn = PushButton("刷新数据", container)
        refresh_source_btn.clicked.connect(lambda: self._force_refresh_page("sourcePage"))
        title_row.addWidget(refresh_source_btn)
        layout.addLayout(title_row)

        card = SimpleCardWidget(container)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(15)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if "mitv.tvplayer.hdmi.last.source" not in self.state_buttons:
            self.state_buttons["mitv.tvplayer.hdmi.last.source"] = {}

        for v, n in [(23, "HDMI 1"), (24, "HDMI 2"), (29, "DP"), (30, "USBC")]:
            b = ToggleButton(n, card)
            b.setCheckable(True)
            b.setFixedSize(120, 45)
            b.clicked.connect(lambda checked=False, val=v, name=n: self._set("mitv.tvplayer.hdmi.last.source", val, name))
            btn_layout.addWidget(b)
            self.state_buttons["mitv.tvplayer.hdmi.last.source"][v] = b

        card_layout.addLayout(btn_layout)
        layout.addWidget(card)

        # Active Source Status Card
        status_card = SimpleCardWidget(container)
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(20, 20, 20, 20)
        status_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl = BodyLabel("当前活跃信号源", status_card)
        lbl.setStyleSheet("font-size: 14px; color: rgba(255, 255, 255, 0.6);")
        status_layout.addWidget(lbl)

        self.source_label = TitleLabel("未知", status_card)
        self.source_label.setStyleSheet("color: #00bcd4; font-size: 32px; font-weight: bold; margin-top: 10px;")
        status_layout.addWidget(self.source_label)

        layout.addWidget(status_card)
        return container

    def _make_tools_page(self):
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setObjectName("Container")
        container.setStyleSheet("#Container { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)



        title = SubtitleLabel("工具与设置", container)
        title.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(20)

        # ADB Shell Card
        card1 = SimpleCardWidget(container)
        c1_lay = QVBoxLayout(card1)
        c1_lay.setContentsMargins(20, 20, 20, 20)
        c1_lay.setSpacing(10)
        
        lbl_c1_title = SubtitleLabel("💻 打开 ADB Shell", card1)
        c1_lay.addWidget(lbl_c1_title)
        
        lbl_c1_desc = BodyLabel("在外部终端中弹出一个交互式的 ADB Shell 会话，供开发人员和高级用户直接调试显示器的 Android 系统参数。", card1)
        lbl_c1_desc.setWordWrap(True)
        lbl_c1_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px; height: 50px;")
        c1_lay.addWidget(lbl_c1_desc)

        btn_c1 = PrimaryPushButton("启动 Shell 终端", card1)
        btn_c1.clicked.connect(self._open_shell)
        c1_lay.addWidget(btn_c1)
        grid.addWidget(card1, 0, 0)

        # APK Install Card
        card2 = SimpleCardWidget(container)
        c2_lay = QVBoxLayout(card2)
        c2_lay.setContentsMargins(20, 20, 20, 20)
        c2_lay.setSpacing(10)
        
        lbl_c2_title = SubtitleLabel("📦 安装 APK 软件包", card2)
        c2_lay.addWidget(lbl_c2_title)
        
        lbl_c2_desc = BodyLabel("通过无线 ADB 安全、静默地向您的显示器安装第三方的 Android APK 应用软件包，支持完整的安装状态回执提示。", card2)
        lbl_c2_desc.setWordWrap(True)
        lbl_c2_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px; height: 50px;")
        c2_lay.addWidget(lbl_c2_desc)

        btn_c2 = PrimaryPushButton("选择并安装应用", card2)
        btn_c2.clicked.connect(self._install_apk)
        c2_lay.addWidget(btn_c2)
        grid.addWidget(card2, 0, 1)

        # Software Settings Card
        card3 = SimpleCardWidget(container)
        c3_lay = QVBoxLayout(card3)
        c3_lay.setContentsMargins(20, 20, 20, 20)
        c3_lay.setSpacing(15)
        
        lbl_c3_title = SubtitleLabel("⚙️ 软件设置", card3)
        c3_lay.addWidget(lbl_c3_title)
        
        close_behavior_layout = QHBoxLayout()
        close_behavior_layout.setSpacing(15)
        
        lbl_close_behavior = BodyLabel("窗口关闭行为:", card3)
        lbl_close_behavior.setFixedWidth(120)
        close_behavior_layout.addWidget(lbl_close_behavior)
        
        self.btn_setting_tray = ToggleButton("最小化到托盘", card3)
        self.btn_setting_exit = ToggleButton("直接退出程序", card3)
        
        self.btn_setting_tray.setCheckable(True)
        self.btn_setting_exit.setCheckable(True)
        self.btn_setting_tray.setFixedWidth(120)
        self.btn_setting_exit.setFixedWidth(120)
        
        close_behavior_layout.addWidget(self.btn_setting_tray)
        close_behavior_layout.addWidget(self.btn_setting_exit)
        close_behavior_layout.addStretch()
        
        c3_lay.addLayout(close_behavior_layout)
        
        # Load settings
        settings = load_settings()
        
        # Theme Row
        theme_layout = QHBoxLayout()
        theme_layout.setSpacing(15)
        
        lbl_theme = BodyLabel("应用主题:", card3)
        lbl_theme.setFixedWidth(120)
        theme_layout.addWidget(lbl_theme)
        
        self.theme_combo = ComboBox(card3)
        self.theme_combo.addItems(["跟随系统", "深色模式", "浅色模式"])
        self.theme_combo.setFixedWidth(255)
        theme_layout.addWidget(self.theme_combo)
        theme_layout.addStretch()
        
        c3_lay.addLayout(theme_layout)
        
        # Set initial theme index
        theme_val = settings.get("theme", "dark")
        if theme_val == "auto":
            self.theme_combo.setCurrentIndex(0)
        elif theme_val == "dark":
            self.theme_combo.setCurrentIndex(1)
        else:
            self.theme_combo.setCurrentIndex(2)
            
        def on_theme_changed(index):
            if index == 0:
                theme_str = "auto"
                setTheme(Theme.AUTO)
            elif index == 1:
                theme_str = "dark"
                setTheme(Theme.DARK)
            else:
                theme_str = "light"
                setTheme(Theme.LIGHT)
            
            s = load_settings()
            s["theme"] = theme_str
            save_settings(s)
            
        self.theme_combo.currentIndexChanged.connect(on_theme_changed)
        
        behavior = settings.get("close_behavior", "tray")
        if behavior == "tray":
            self.btn_setting_tray.setChecked(True)
        else:
            self.btn_setting_exit.setChecked(True)
            
        def on_choose_tray():
            self.btn_setting_tray.setChecked(True)
            self.btn_setting_exit.setChecked(False)
            settings["close_behavior"] = "tray"
            save_settings(settings)
            
        def on_choose_exit():
            self.btn_setting_tray.setChecked(False)
            self.btn_setting_exit.setChecked(True)
            settings["close_behavior"] = "exit"
            save_settings(settings)
            
        self.btn_setting_tray.clicked.connect(on_choose_tray)
        self.btn_setting_exit.clicked.connect(on_choose_exit)
        
        lbl_tip = BodyLabel("* 提示：当选择最小化到托盘时，关闭窗口会使程序在后台默默运行，可通过任务栏右下角的托盘图标随时恢复或退出。", card3)
        lbl_tip.setStyleSheet("color: rgba(255, 255, 255, 0.4); font-size: 11px;")
        c3_lay.addWidget(lbl_tip)

        # Auto-start minimized
        autostart_layout = QHBoxLayout()
        autostart_layout.setSpacing(15)
        self.chk_autostart = CheckBox("开机自动启动并最小化到托盘", card3)
        self.chk_autostart.setChecked(settings.get("autostart", False))
        self.chk_autostart.stateChanged.connect(self._toggle_autostart)
        autostart_layout.addWidget(self.chk_autostart)
        autostart_layout.addStretch()
        c3_lay.addLayout(autostart_layout)

        grid.addWidget(card3, 2, 0, 1, 2)

        # 4K UI Card
        card5 = SimpleCardWidget(container)
        c5_lay = QVBoxLayout(card5)
        c5_lay.setContentsMargins(20, 20, 20, 20)
        c5_lay.setSpacing(10)

        lbl_c5_title = SubtitleLabel("🖥️ 4K UI 模式", card5)
        c5_lay.addWidget(lbl_c5_title)

        lbl_c5_desc = BodyLabel("将显示器 UI 分辨率提升至 3840×2160，DPI 设为 640。开启或关闭后显示器将自动重启。", card5)
        lbl_c5_desc.setWordWrap(True)
        lbl_c5_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px;")
        c5_lay.addWidget(lbl_c5_desc)

        self.chk_4k = CheckBox("启用 4K UI", card5)
        self.chk_4k.stateChanged.connect(self._toggle_4k_ui)
        c5_lay.addWidget(self.chk_4k)

        grid.addWidget(card5, 1, 0, 1, 1)

        # Global Hotkey Settings Card
        card4 = SimpleCardWidget(container)
        c4_lay = QVBoxLayout(card4)
        c4_lay.setContentsMargins(20, 20, 20, 20)
        c4_lay.setSpacing(15)
        
        lbl_c4_title = SubtitleLabel("⌨️ 自定义全局快捷键 (Windows 独占)", card4)
        c4_lay.addWidget(lbl_c4_title)
        
        lbl_c4_desc = BodyLabel("为所有带档位切换的功能提供自定义全局快捷键支持。支持后台/游戏中静默控制，设置完成后自动弹出系统原生气泡通知。", card4)
        lbl_c4_desc.setWordWrap(True)
        lbl_c4_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px;")
        c4_lay.addWidget(lbl_c4_desc)
        
        self.hotkey_combos = {}
        hotkeys_settings = settings.get("hotkeys", {})
        
        actions_list = [
            ("picture_mode_cycle", "画面模式 循环切换"),
            ("local_dimming_cycle", "精密控光 循环切换"),
            ("color_space_cycle", "色域 循环切换"),
            ("color_temp_cycle", "色温 循环切换"),
            ("response_time_cycle", "响应时间 循环切换"),
            ("freesync_toggle", "FreeSync 开关切换"),
            ("input_source_cycle", "信号源 循环切换")
        ]
        
        def add_hotkey_row(row_layout_parent, label_text, action_name):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(15)
            
            lbl = BodyLabel(label_text, card4)
            lbl.setFixedWidth(180)
            row_layout.addWidget(lbl)
            
            mod_combo = ComboBox(card4)
            mod_combo.addItems(["无", "Ctrl + Alt", "Ctrl + Shift", "Alt + Shift", "Win + Shift"])
            mod_combo.setFixedWidth(130)
            
            key_combo = ComboBox(card4)
            key_combo.addItems(["无"] + [f"F{i}" for i in range(1, 13)] + [str(i) for i in range(0, 10)] + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
            key_combo.setFixedWidth(100)
            
            row_layout.addWidget(mod_combo)
            row_layout.addWidget(key_combo)
            row_layout.addStretch()
            row_layout_parent.addLayout(row_layout)
            
            hk_conf = hotkeys_settings.get(action_name, {"modifier": "无", "key": "无"})
            mod_idx = mod_combo.findText(hk_conf.get("modifier", "无"))
            if mod_idx >= 0: mod_combo.setCurrentIndex(mod_idx)
            key_idx = key_combo.findText(hk_conf.get("key", "无"))
            if key_idx >= 0: key_combo.setCurrentIndex(key_idx)
            
            self.hotkey_combos[action_name] = (mod_combo, key_combo)
            
        for act_name, label_txt in actions_list:
            add_hotkey_row(c4_lay, label_txt, act_name)
            
        btn_save_hotkeys = PrimaryPushButton("保存并应用全局快捷键", card4)
        c4_lay.addWidget(btn_save_hotkeys)
        
        def save_and_apply_hotkeys():
            new_hotkeys = {}
            for act_name, (m_combo, k_combo) in self.hotkey_combos.items():
                m_val = m_combo.currentText()
                k_val = k_combo.currentText()
                new_hotkeys[act_name] = {"modifier": m_val, "key": k_val}
                
            s = load_settings()
            s["hotkeys"] = new_hotkeys
            save_settings(s)
            
            self.register_global_hotkeys()
            self.log("全局快捷键保存并重新注册成功！")
            
            self.tray_icon.showMessage(
                "红米 G Pro 27U Toolbox",
                "全局快捷键已保存并重新应用！",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
            
        btn_save_hotkeys.clicked.connect(save_and_apply_hotkeys)
        grid.addWidget(card4, 3, 0, 1, 2)

        layout.addLayout(grid)

        github_link = BodyLabel(container)
        github_link.setText('仓库地址：<a href="https://github.com/YiHooong/Mimonitor_Toolbox" style="color: #734EFF;">https://github.com/YiHooong/Mimonitor_Toolbox</a>')
        github_link.setOpenExternalLinks(True)
        github_link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        github_link.setStyleSheet("font-size: 12px; padding: 10px;")
        layout.addWidget(github_link)

        scroll.setWidget(container)
        return scroll

    def _make_remote_page(self):
        container = QWidget()
        container.setObjectName("RemoteContainer")
        container.setStyleSheet("#RemoteContainer { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)



        title = SubtitleLabel("遥控器", container)
        title.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 5px;")
        layout.addWidget(title)

        main_frame = QFrame(container)
        main_frame.setStyleSheet("background: transparent;")
        main_layout = QHBoxLayout(main_frame)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(40)

        # High-End Remote Controller body (Simulated Hardware)
        remote_card = QFrame(main_frame)
        remote_card.setFixedSize(300, 520)
        remote_card.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #161616, stop:1 #232323);
                border: 2px solid rgba(255, 255, 255, 0.08);
                border-radius: 28px;
            }
        """)
        rc_layout = QVBoxLayout(remote_card)
        rc_layout.setContentsMargins(25, 25, 25, 25)
        rc_layout.setSpacing(18)
        rc_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Branding top
        logo_label = BodyLabel("G PRO CONTROL", remote_card)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label.setStyleSheet("""
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 3px;
            color: #0078d4;
            margin-bottom: 5px;
        """)
        rc_layout.addWidget(logo_label)

        # Top row: Power Button
        row_top = QHBoxLayout()
        row_top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        power_btn = PushButton("⏻", remote_card)
        power_btn.setFixedSize(44, 44)
        power_btn.setStyleSheet("""
            QPushButton {
                background-color: #e81123;
                border: 1px solid #ff4350;
                border-radius: 22px;
                color: white;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff2d3d;
                border-color: #ff5f6d;
            }
            QPushButton:pressed {
                background-color: #b30b18;
            }
        """)
        power_btn.clicked.connect(lambda: self._key("KEYCODE_POWER"))
        row_top.addWidget(power_btn)
        rc_layout.addLayout(row_top)

        # Menu buttons row (Home, Menu, Back)
        row_menu = QHBoxLayout()
        row_menu.setSpacing(10)
        
        btn_home = PushButton("主页", remote_card)
        btn_menu = PushButton("菜单", remote_card)
        btn_back = PushButton("返回", remote_card)

        for btn, key in [(btn_home, "KEYCODE_HOME"), (btn_menu, "KEYCODE_MENU"), (btn_back, "KEYCODE_BACK")]:
            btn.setFixedSize(72, 32)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #2c2c2c;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 16px;
                    color: #e3e3e3;
                    font-size: 12px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #383838;
                    border-color: rgba(255, 255, 255, 0.15);
                }
                QPushButton:pressed {
                    background-color: #1e1e1e;
                }
            """)
            btn.clicked.connect(lambda checked=False, k=key: self._key(k))
            row_menu.addWidget(btn)
            
        rc_layout.addLayout(row_menu)

        # Elegant Circular D-Pad Wheel
        dpad_container = QFrame(remote_card)
        dpad_container.setFixedSize(190, 190)
        dpad_container.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border: 2px solid rgba(255, 255, 255, 0.06);
                border-radius: 95px;
            }
        """)
        dpad_layout = QGridLayout(dpad_container)
        dpad_layout.setContentsMargins(5, 5, 5, 5)
        dpad_layout.setSpacing(0)

        btn_up = PushButton("▲", dpad_container)
        btn_down = PushButton("▼", dpad_container)
        btn_left = PushButton("◀", dpad_container)
        btn_right = PushButton("▶", dpad_container)
        btn_ok = PrimaryPushButton("OK", dpad_container)

        btn_up.setFixedSize(50, 42)
        btn_down.setFixedSize(50, 42)
        btn_left.setFixedSize(42, 50)
        btn_right.setFixedSize(42, 50)
        btn_ok.setFixedSize(62, 62)

        # Style the D-pad arrow keys
        arrow_style = """
            QPushButton {
                background: transparent;
                border: none;
                color: #b0b0b0;
                font-size: 18px;
            }
            QPushButton:hover {
                color: #0078d4;
            }
            QPushButton:pressed {
                color: #005a9e;
            }
        """
        for b in [btn_up, btn_down, btn_left, btn_right]:
            b.setStyleSheet(arrow_style)

        # Style the central circular OK button
        btn_ok.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                border: 1px solid #0078d4;
                border-radius: 31px;
                color: white;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #0086f0;
                border-color: #0086f0;
            }
            QPushButton:pressed {
                background-color: #006cc0;
            }
        """)

        btn_up.clicked.connect(lambda: self._key("KEYCODE_DPAD_UP"))
        btn_down.clicked.connect(lambda: self._key("KEYCODE_DPAD_DOWN"))
        btn_left.clicked.connect(lambda: self._key("KEYCODE_DPAD_LEFT"))
        btn_right.clicked.connect(lambda: self._key("KEYCODE_DPAD_RIGHT"))
        btn_ok.clicked.connect(lambda: self._key("KEYCODE_DPAD_CENTER"))

        dpad_layout.addWidget(btn_up, 0, 1, Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(btn_left, 1, 0, Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(btn_ok, 1, 1, Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(btn_right, 1, 2, Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(btn_down, 2, 1, Qt.AlignmentFlag.AlignCenter)

        # Add D-pad wrapper with alignment to layout
        dpad_wrapper = QHBoxLayout()
        dpad_wrapper.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dpad_wrapper.addWidget(dpad_container)
        rc_layout.addLayout(dpad_wrapper)

        # Vol Row: Pill layout (Vol-, Mute, Vol+)
        vol_layout = QHBoxLayout()
        vol_layout.setSpacing(8)
        vol_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_vol_down = PushButton("🔉 音量-", remote_card)
        btn_mute = PushButton("🔇 静音", remote_card)
        btn_vol_up = PushButton("🔊 音量+", remote_card)

        for btn, key in [(btn_vol_down, "KEYCODE_VOLUME_DOWN"), (btn_mute, "KEYCODE_MUTE"), (btn_vol_up, "KEYCODE_VOLUME_UP")]:
            btn.setFixedSize(74, 34)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #2c2c2c;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 17px;
                    color: #e3e3e3;
                    font-size: 11px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #383838;
                    border-color: rgba(255, 255, 255, 0.15);
                }
                QPushButton:pressed {
                    background-color: #1e1e1e;
                }
            """)
            btn.clicked.connect(lambda checked=False, k=key: self._key(k))
            vol_layout.addWidget(btn)

        rc_layout.addLayout(vol_layout)
        main_layout.addWidget(remote_card, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(main_frame)
        return container

    # ===== Helpers for Cards & Sections =====
    def _add_slider(self, parent_layout, title, name, lo, hi, default, jni_key=None, settings_keys=None):
        card = SimpleCardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)

        name_label = BodyLabel(title, card)
        name_label.setFixedWidth(100)
        layout.addWidget(name_label)

        slider = Slider(Qt.Orientation.Horizontal, card)
        slider.setRange(lo, hi)
        slider.setValue(default)
        layout.addWidget(slider)

        val_label = BodyLabel(str(default), card)
        val_label.setFixedWidth(40)
        val_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(val_label)

        slider.valueChanged.connect(lambda v: val_label.setText(str(v)))
        self.sliders[name] = (slider, val_label)

        _debounce_timer = QTimer(self)
        _debounce_timer.setSingleShot(True)
        _debounce_timer.setInterval(300)

        def on_commit():
            if not self.check_connection():
                return
            v = slider.value()
            def do():
                if jni_key:
                    self.adb.jni_set(jni_key, v)
                    self.adb.refresh_pq()
                if settings_keys:
                    for k in settings_keys: self.adb.put(k, str(v))
                self.log(f"{title}: {v}")
            async_run(do)

        _debounce_timer.timeout.connect(on_commit)
        slider.valueChanged.connect(lambda v: _debounce_timer.start())
        parent_layout.addWidget(card)

    def _btn_section(self, parent_layout, title, buttons, state_key=None):
        card = SimpleCardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)
        
        title_label = BodyLabel(title, card)
        title_label.setFixedWidth(180)
        layout.addWidget(title_label)
        
        btns_layout = QHBoxLayout()
        btns_layout.setSpacing(8)
        btns_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        if state_key:
            if state_key not in self.state_buttons:
                self.state_buttons[state_key] = {}
            for text, val, cmd in buttons:
                b = ToggleButton(text, card)
                b.setCheckable(True)
                b.setMinimumWidth(80)
                b.clicked.connect(cmd)
                btns_layout.addWidget(b)
                self.state_buttons[state_key][val] = b
        else:
            for text, cmd in buttons:
                b = PushButton(text, card)
                b.setMinimumWidth(80)
                b.clicked.connect(cmd)
                btns_layout.addWidget(b)
                
        layout.addLayout(btns_layout)
        parent_layout.addWidget(card)

    # ===== Control Setters =====
    def _highlight_btn(self, btn, is_active):
        if isinstance(btn, ToggleButton):
            btn.blockSignals(True)
            btn.setChecked(is_active)
            btn.blockSignals(False)
        else:
            if is_active:
                btn.setStyleSheet("""
                    QPushButton, PushButton {
                        background-color: #0078d4;
                        border: 1px solid #0078d4;
                        color: white;
                        font-weight: bold;
                        border-radius: 5px;
                    }
                    QPushButton:hover, PushButton:hover {
                        background-color: #0086f0;
                        border: 1px solid #0086f0;
                    }
                    QPushButton:pressed, PushButton:pressed {
                        background-color: #006cc0;
                        border: 1px solid #006cc0;
                    }
                """)
            else:
                btn.setStyleSheet("")

    def _highlight_mode(self, mode):
        for m, btn in self.mode_btns.items():
            self._highlight_btn(btn, str(m) == str(mode))

    def _set_mode(self, val, name):
        if not self.check_connection(): return
        self.adb.put("picture_mode", str(val))
        self.current_vals["picture_mode"] = val
        self._highlight_mode(val)
        self.log(f"模式: {name}")
        self._page_loaded.discard("picturePage")
        self._refresh_page_data("picturePage")

    _MODE_NAMES = {14: "标准", 10: "游戏", 9: "电影"}

    def _reset_current_mode(self):
        if not self.check_connection(): return
        cur = self.current_vals.get("picture_mode")
        if cur not in self._MODE_NAMES:
            self.log("无法获取当前模式")
            return
        mode_name = self._MODE_NAMES[cur]
        w = MessageBox("恢复默认设置", f"你确定要恢复当前模式：{mode_name} 的默认设置吗？", self)
        if w.exec():
            self.adb.jni_set("g_fusion_picture__pic_reset_def_bypicmode", 0)
            self.adb.refresh_pq()
            self.log(f"已恢复 {mode_name} 模式默认设置，等待生效...")
            self._page_loaded.discard("picturePage")
            QTimer.singleShot(3000, lambda: self._refresh_page_data("picturePage"))

    def _optimistic_highlight(self, key, val):
        if key in self.state_buttons:
            for v, btn in self.state_buttons[key].items():
                self._highlight_btn(btn, str(v) == str(val))

    def _set(self, k, v, m):
        if not self.check_connection(): return
        # 信号源切换使用 intent 方式
        if k == "mitv.tvplayer.hdmi.last.source":
            self.adb.shell(f"am force-stop com.xiaomi.mitv.tvplayer")
            self.adb.shell(f"am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY -n com.xiaomi.mitv.tvplayer/.ExternalSourceActivity --ei input {v} -f 0x10000000")
        else:
            self.adb.put(k, str(v))
        self.adb.refresh_pq()
        self.log(m)
        self.current_vals[k] = v
        self._optimistic_highlight(k, v)
        if k == "mitv.tvplayer.hdmi.last.source":
            self.source_var_text = self._source_names.get(v, f"未知 ({v})")
            self.source_label.setText(self.source_var_text)

    def _jni(self, jk, v, sk, m, osd_sk=None):
        if not self.check_connection(): return
        self.adb.jni_set(jk, v)
        self.adb.put(sk, str(v))
        if osd_sk:
            self.adb.put(osd_sk, str(v))
        self.adb.refresh_pq()
        self.log(m)
        self.current_vals[sk] = v
        self._optimistic_highlight(sk, v)

    def _set_color_temp(self, jv, sv, m):
        if not self.check_connection(): return
        self.adb.jni_set("g_video__clr_temp", jv)
        self.adb.put("picture_color_temperature", str(sv))
        self.adb.refresh_pq()
        self.log(m)
        self.current_vals["picture_color_temperature"] = sv
        self._optimistic_highlight("picture_color_temperature", sv)

    def _fs(self, v):
        if not self.check_connection(): return
        self.adb.put("front_sight_index", str(v))
        if self.adb.get("picture_mode") == "10":
            self.adb.put("picture_mode", "14")
            time.sleep(0.5)
            self.adb.put("picture_mode", "10")
        self.log(f"准星: {'关' if v==0 else v}")
        self.current_vals["front_sight_index"] = v
        self._optimistic_highlight("front_sight_index", v)

    def _get_input_source(self):
        """获取当前输入源"""
        return self.adb.get("mitv.tvplayer.hdmi.last.source")

    def _320(self, on):
        if not self.check_connection(): return
        src = self._get_input_source()
        if src in ("29","30"): self.adb.jni_set("g_fusion_picture__dp_edid_version", 3 if on else 2)
        else: self.adb.jni_set("g_fusion_picture__hdmi_edid_version", 6 if on else 1)
        self.adb.refresh_pq()
        self.log(f"320Hz: {'开' if on else '关'}")
        self.current_vals["mode_320"] = 1 if on else 0
        self._optimistic_highlight("mode_320", 1 if on else 0)

    def _fsync(self, on):
        if not self.check_connection(): return
        src = self._get_input_source()
        if src in ("29","30"): self.adb.jni_set("g_video__dp_adaptive_sync", 1 if on else 0)
        else: self.adb.jni_set("g_video__freesync_switch", 3 if on else 0)
        self.adb.refresh_pq()
        self.log(f"FreeSync: {'开' if on else '关'}")
        self.current_vals["freesync"] = 1 if on else 0
        self._optimistic_highlight("freesync", 1 if on else 0)

    def _key(self, kcode):
        if not self.check_connection(): return
        def do():
            self.adb.key(kcode)
            self.log(f"按键: {kcode}")
        async_run(do)

    def check_connection(self):
        if not getattr(self, "adb_connected", False):
            self.message_signal.emit("warn", "未连接显示器", "当前未连接到显示器，无法完成此操作！请先在主页建立连接。")
            return False
        return True

    def disconnect_adb(self):
        if self.adb.ip:
            self.log(f"正在断开与 {self.adb.ip} 的连接...")
            def do():
                adb_run(["disconnect", f"{self.ip_entry.text().strip()}:5555"])
                self.adb.ip = ""
                self.status_signal.emit("未连接")
                self.log("连接已断开")
            async_run(do)

    # ===== ADB & Connect Slots =====
    def connect(self):
        ip = self.ip_entry.text().strip()
        if not ip:
            return
        self.adb.ip = ip
        self.status_signal.emit("连接中...")
        def do():
            ok = self.adb.connect()
            if ok:
                self.status_signal.emit("已连接")
                self.log(f"连接成功: {self.adb.ip}")
                settings = load_settings()
                settings["saved_ip"] = ip
                save_settings(settings)
                self.adb.check_and_heal_jar()
                m = self.adb.get_model()
                self.status_signal.emit(f"已连接: {m}")
            else:
                self.status_signal.emit("连接失败")
                self.message_signal.emit("error", "错误", "连接显示器失败，请检查IP和网络连接！")
                self.log("连接失败")
        async_run(do)

    def scan_net(self):
        self.status_signal.emit("扫描中...")
        self.dev_combo.clear()
        subnet = get_local_subnet()
        self.log(f"扫描 {subnet}.x ...")
        
        found_devices = []
        def do():
            def cb(ip, model):
                found_devices.append((ip, model))
                self.devices_signal.emit(found_devices)

            scan_adb(base=subnet, cb=cb, log=self.log)
            self.status_signal.emit(f"扫描完成: {len(found_devices)}台")
        async_run(do)

    def _update_scanned_devices(self, dev_list):
        # Temporarily block signals during combobox population to prevent autoconnect loop
        self.dev_combo.blockSignals(True)
        self.dev_combo.clear()
        for ip, model in dev_list:
            self.dev_combo.addItem(f"{model} ({ip})")
        preferred_index = 0
        for i, (ip, model) in enumerate(dev_list):
            if "mitv" in str(model).lower():
                preferred_index = i
                break
        if dev_list:
            self.dev_combo.setCurrentIndex(preferred_index)
        self.dev_combo.blockSignals(False)

    def _on_dev_sel(self, index):
        if index < 0:
            return
        v = self.dev_combo.itemText(index)
        if "(" in v:
            ip = v.split("(")[1].rstrip(")")
            self.ip_entry.setText(ip)
            self.connect()

    def _open_shell(self):
        if not self.adb.ip or not getattr(self, "adb_connected", False):
            self._show_message_box("error", "错误", "请先连接显示器！")
            return
        self.log("正在打开 ADB Shell 终端...")
        if sys.platform == "win32":
            subprocess.Popen(f"start cmd /k {ADB} -s {self.adb.ip}:5555 shell", shell=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{ADB} -s {self.adb.ip}:5555 shell"'])
        else:
            launched = False
            for term in ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"]:
                if subprocess.run(["which", term], capture_output=True).returncode == 0:
                    if term == "gnome-terminal":
                        subprocess.Popen([term, "--", ADB, "-s", f"{self.adb.ip}:5555", "shell"])
                    else:
                        subprocess.Popen([term, "-e", f"{ADB} -s {self.adb.ip}:5555 shell"])
                    launched = True
                    break
            if not launched:
                self._show_message_box("error", "错误", "未找到可用的终端模拟器，请手动在终端中运行: adb shell")

    def _install_apk(self):
        if not self.adb.ip or not getattr(self, "adb_connected", False):
            self._show_message_box("error", "错误", "请先连接显示器！")
            return
        apk_path, _ = QFileDialog.getOpenFileName(self, "选择要安装的 APK 文件", "", "APK Files (*.apk)")
        if apk_path:
            apk_name = os.path.basename(apk_path)
            self.log(f"正在安装: {apk_name} ...")
            self.apk_install_dialog = InstallProgressDialog(apk_name, self)
            self.apk_install_dialog.show()
            def do():
                r = adb_run(["-s", f"{self.adb.ip}:5555", "install", "-r", apk_path], timeout=60)
                if "Success" in r:
                    self.apk_install_finished.emit(True, apk_name, "")
                else:
                    self.apk_install_finished.emit(False, apk_name, r.strip())
            async_run(do)

    def _on_apk_install_finished(self, ok, apk_name, detail):
        dialog = getattr(self, "apk_install_dialog", None)
        if dialog:
            dialog.accept()
            dialog.deleteLater()
            self.apk_install_dialog = None

        if ok:
            self.log("APK 安装成功")
            self._show_message_box("info", "安装成功", f"应用 {apk_name} 安装成功！")
        else:
            self.log("APK 安装失败")
            self._show_message_box("error", "安装失败", f"安装失败: {detail[:1800]}")

    # ===== 按需数据加载 =====

    def _apply_polled_values(self, vals):
        # 设备存的是 MTK 值，需要转成 settings 值才能匹配按钮
        _MTK_TO_SETTINGS_COLOR_TEMP = {1: 0, 2: 1, 3: 2, 4: 3, 6: 6}
        if "picture_color_temperature" in vals:
            mtk_val = vals["picture_color_temperature"]
            if mtk_val in _MTK_TO_SETTINGS_COLOR_TEMP:
                vals["picture_color_temperature"] = _MTK_TO_SETTINGS_COLOR_TEMP[mtk_val]
        self.current_vals.update(vals)
        self._check_pending_notifications(vals)
        slider_mappings = {
            "picture_backlight": "backlight",
            "xiaomi_picture_backlight": "backlight",
            "picture_brightness": "black_level",
            "picture_contrast": "contrast",
            "picture_saturation": "saturation",
            "picture_hue": "hue",
            "picture_sharpness": "sharpness",
        }
        for k, name in slider_mappings.items():
            if k in vals and name in self.sliders:
                val = vals[k]
                if isinstance(val, int):
                    slider, label_widget = self.sliders[name]
                    if not slider.isSliderDown() and slider.value() != val:
                        slider.setValue(val)
                        label_widget.setText(str(val))

        for key, btn_map in self.state_buttons.items():
            if key in vals:
                active_val = vals[key]
                for val, btn in btn_map.items():
                    self._highlight_btn(btn, str(active_val) == str(val))
                        
        if "picture_mode" in vals:
            self._highlight_mode(vals["picture_mode"])

        if "mitv.tvplayer.hdmi.last.source" in vals:
            sid = vals["mitv.tvplayer.hdmi.last.source"]
            self.source_var_text = self._source_names.get(sid, f"未知 ({sid})")
            self.source_label.setText(self.source_var_text)

    def _apply_polled_jni_values(self, vals):
        self.current_vals.update(vals)
        self._check_pending_notifications(vals)
        if "g_disp__disp_back_light" in vals and "backlight" in self.sliders:
            val = vals["g_disp__disp_back_light"]
            slider, label_widget = self.sliders["backlight"]
            if not slider.isSliderDown() and slider.value() != val:
                slider.setValue(val)
                label_widget.setText(str(val))

        for key in ("mode_320", "freesync"):
            if key in vals and key in self.state_buttons:
                active_val = vals[key]
                for val, btn in self.state_buttons[key].items():
                    self._highlight_btn(btn, str(active_val) == str(val))

    # ===== 页面按需加载 =====

    _PAGES_NEED_CONNECTION = {"picturePage", "gamePage", "sourcePage", "remotePage"}

    def _on_page_changed(self, index):
        page = self.stackedWidget.widget(index)
        if not page:
            return
        name = page.objectName()
        # 未连接时阻止进入需要连接的页面
        if name in self._PAGES_NEED_CONNECTION and not getattr(self, "adb_connected", False):
            self.message_signal.emit("warn", "未连接显示器", "请先在主页连接显示器！")
            # 跳回主页
            for i in range(self.stackedWidget.count()):
                w = self.stackedWidget.widget(i)
                if w and w.objectName() == "homePage":
                    self.stackedWidget.setCurrentIndex(i)
                    return
        if name in self._page_data_keys and name not in self._page_loaded and name not in self._page_loading:
            self._refresh_page_data(name)

    def _refresh_page_data(self, page_name):
        if not getattr(self, "adb_connected", False):
            return
        if page_name in self._page_loading:
            return
        self._page_loading.add(page_name)
        self._show_loading_overlay(page_name)
        self.log(f"正在刷新 {page_name} 数据...")

        def do():
            try:
                cfg = self._page_data_keys[page_name]
                settings_vals = {}
                jni_vals = {}

                # 读取 settings
                settings_keys = cfg.get("settings", [])
                if settings_keys:
                    keys_str = " ".join(settings_keys)
                    cmd = f"for k in {keys_str}; do echo $k=$(settings get global $k); done"
                    res = self.adb.shell(cmd)
                    for line in res.split("\n"):
                        if "=" in line:
                            parts = line.strip().split("=", 1)
                            if len(parts) == 2:
                                k, v = parts[0], parts[1]
                                if v not in ("", "null", "N/A"):
                                    try: settings_vals[k] = int(v)
                                    except: settings_vals[k] = v

                # 读取 JNI 背光
                if "g_disp__disp_back_light" in cfg.get("jni", []):
                    bl = self.adb.jni_get("g_disp__disp_back_light")
                    try: jni_vals["g_disp__disp_back_light"] = int(bl)
                    except: pass

                # 读取 JNI 色域 (覆盖 settings 中的 tv_picture_video_color_space)
                if "g_video__vid_gamut_mapping_mode" in cfg.get("jni", []):
                    gamut = self.adb.jni_get("g_video__vid_gamut_mapping_mode")
                    try:
                        gamut_val = int(gamut)
                        # MTK 值和 settings 值一致，直接覆盖
                        settings_vals["tv_picture_video_color_space"] = gamut_val
                    except: pass

                # 读取 JNI 色温 (MTK 值需转换为 settings 值)
                if "g_video__clr_temp" in cfg.get("jni", []):
                    _MTK_TO_SETTINGS_COLOR_TEMP = {1: 0, 2: 1, 3: 2, 4: 3, 6: 6}
                    clr = self.adb.jni_get("g_video__clr_temp")
                    try:
                        clr_val = int(clr)
                        if clr_val in _MTK_TO_SETTINGS_COLOR_TEMP:
                            settings_vals["picture_color_temperature"] = _MTK_TO_SETTINGS_COLOR_TEMP[clr_val]
                    except: pass

                # 读取 JNI 控光 (直接覆盖 settings)
                if "g_video__vid_local_dimming" in cfg.get("jni", []):
                    dim = self.adb.jni_get("g_video__vid_local_dimming")
                    try:
                        settings_vals["tv_picture_video_local_dimming"] = int(dim)
                    except: pass

                # 读取 JNI 模式 (game page)
                if cfg.get("jni_mode"):
                    src = settings_vals.get("mitv.tvplayer.hdmi.last.source")
                    if src in (29, 30):
                        edid = self.adb.jni_get("g_fusion_picture__dp_edid_version")
                        try: jni_vals["mode_320"] = 1 if int(edid) == 3 else 0
                        except: pass
                        fs = self.adb.jni_get("g_video__dp_adaptive_sync")
                        try: jni_vals["freesync"] = 1 if int(fs) == 1 else 0
                        except: pass
                    else:
                        edid = self.adb.jni_get("g_fusion_picture__hdmi_edid_version")
                        try: jni_vals["mode_320"] = 1 if int(edid) == 6 else 0
                        except: pass
                        fs = self.adb.jni_get("g_video__freesync_switch")
                        try: jni_vals["freesync"] = 1 if int(fs) == 3 else 0
                        except: pass

                # 应用数据到 UI
                if settings_vals:
                    self.values_signal.emit(settings_vals)
                if jni_vals:
                    self.jni_values_signal.emit(jni_vals)

                self._page_loaded.add(page_name)
                self.log(f"页面数据刷新完成")
            except Exception as e:
                print(f"Page data refresh error: {e}")
            finally:
                self._page_loading.discard(page_name)
                self._hide_loading_overlay(page_name)

        async_run(do)

    def _show_loading_overlay(self, page_name):
        pages = {
            "picturePage": self.picture_page,
            "gamePage": self.game_page,
            "sourcePage": self.source_page,
        }
        page = pages.get(page_name)
        if not page:
            return
        overlay = QWidget(page)
        overlay.setObjectName("_loading_overlay")
        overlay.setStyleSheet("background-color: rgba(0, 0, 0, 120);")
        overlay.setGeometry(page.rect())
        label = BodyLabel("正在刷新数据...", overlay)
        label.setStyleSheet("color: white; font-size: 16px; background: transparent;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setGeometry(overlay.rect())
        overlay.show()
        overlay.raise_()

    def _hide_loading_overlay(self, page_name):
        pages = {
            "picturePage": self.picture_page,
            "gamePage": self.game_page,
            "sourcePage": self.source_page,
        }
        page = pages.get(page_name)
        if not page:
            return
        for child in page.findChildren(QWidget):
            if child.objectName() == "_loading_overlay":
                child.deleteLater()
                break

    def _force_refresh_page(self, page_name):
        self._page_loaded.discard(page_name)
        self._refresh_page_data(page_name)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    server_name = "mitv_gpro27u_controller_single_instance"
    
    check_socket = QLocalSocket()
    check_socket.connectToServer(server_name)
    if check_socket.waitForConnected(500):
        # Already running! Wake up existing instance and exit this second instance silently
        check_socket.write(b"show")
        check_socket.waitForBytesWritten(500)
        sys.exit(0)
        
    local_server = QLocalServer()
    local_server.removeServer(server_name)
    if not local_server.listen(server_name):
        sys.exit(1)
        
    settings = load_settings()
    theme_val = settings.get("theme", "dark")
    if theme_val == "auto":
        setTheme(Theme.AUTO)
    elif theme_val == "light":
        setTheme(Theme.LIGHT)
    else:
        setTheme(Theme.DARK)
    w = App()
    
    def on_new_connection():
        client_socket = local_server.nextPendingConnection()
        if client_socket:
            if client_socket.waitForReadyRead(500):
                msg = client_socket.readAll().data().decode("utf-8")
                if msg == "show":
                    w.show_and_raise()
            client_socket.close()
            
    local_server.newConnection.connect(on_new_connection)

    if "--minimized" not in sys.argv:
        w.show()
    sys.exit(app.exec())
