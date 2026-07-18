#!/usr/bin/env python3

import os
import sys
import subprocess
import socket
import threading
import time
import ctypes
import tempfile
import json

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer, QEasingCurve, QPropertyAnimation
from PyQt6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor, QPen
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QGridLayout, QSizePolicy, QFileDialog, QTextEdit,
    QSystemTrayIcon, QMenu, QDialog, QGraphicsDropShadowEffect, QLabel
)
from qfluentwidgets import (
    FluentWindow, PushButton, PrimaryPushButton, ToggleButton, Slider, ComboBox, LineEdit,
    ScrollArea, BodyLabel, SubtitleLabel, TitleLabel, SimpleCardWidget,
    FluentIcon as FIF, MessageBox, Theme, setTheme, CheckBox, IconWidget
)

# Native Windows Hotkey support variables
user32 = None
WM_HOTKEY = 0x0312
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_DISPLAYCHANGE = 0x007E
WM_SETTINGCHANGE = 0x001A
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

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_settings_lock = threading.RLock()

def get_app_data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", os.environ.get("USERPROFILE", os.path.expanduser("~")))
    else:
        base = os.path.expanduser("~")
    folder = os.path.join(base, ".gpro_controller")
    os.makedirs(folder, exist_ok=True)
    return folder

def get_settings_path():
    """获取跨平台、无需管理员权限的软件配置保存路径"""
    folder = get_app_data_dir()
    return os.path.join(folder, "config.json")

def get_log_dir():
    """日志目录，跟随配置放在用户可写的 app-data 下（避免 exe 装在无写权限目录时失败）"""
    return os.path.join(get_app_data_dir(), "logs")

def _load_settings_unlocked():
    defaults = {
        "close_behavior": "tray",
        "never_ask_close": False,
        "saved_ip": "",
        "hdr_sdr_local_dimming_enabled": False,
        "local_dimming_memory": {"sdr": None, "hdr": None},
        "local_dimming_toggle_last_value": 3,
        "freesync_mode_memory_enabled": False,
        "freesync_previous_mode": None,
    }
    path = get_settings_path()
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    if not isinstance(data, dict):
        data = {}
    merged = defaults.copy()
    merged.update(data)
    memory = merged.get("local_dimming_memory")
    if not isinstance(memory, dict):
        memory = {}
    merged["local_dimming_memory"] = {
        "sdr": memory.get("sdr"),
        "hdr": memory.get("hdr"),
    }
    return merged

def load_settings():
    with _settings_lock:
        return _load_settings_unlocked()

def _write_settings_unlocked(settings):
    path = get_settings_path()
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(prefix="config.", suffix=".tmp", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        return True
    except Exception:
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def update_settings(changes):
    with _settings_lock:
        settings = _load_settings_unlocked()
        settings.update(changes)
        return _write_settings_unlocked(settings)

def get_local_subnet():
    """获取本机网段前缀，如 192.168.5"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ".".join(ip.split(".")[:3])
    except OSError:
        return "192.168.1"


def query_windows_hdr_enabled(window_handle=None):
    """Return True/False for the active Windows HDR color space, or None when unavailable."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes.wintypes as wt
        import uuid

        DXGI_ERROR_NOT_FOUND = 0x887A0002
        DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020 = 12
        MONITOR_DEFAULTTONEAREST = 2

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

        def make_guid(value):
            return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


        class RECTL(ctypes.Structure):
            _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]

        class DXGI_OUTPUT_DESC1(ctypes.Structure):
            _fields_ = [
                ("DeviceName", wt.WCHAR * 32),
                ("DesktopCoordinates", RECTL),
                ("AttachedToDesktop", wt.BOOL),
                ("Rotation", ctypes.c_int),
                ("Monitor", wt.HMONITOR),
                ("BitsPerColor", wt.UINT),
                ("ColorSpace", ctypes.c_int),
                ("RedPrimary", ctypes.c_float * 2),
                ("GreenPrimary", ctypes.c_float * 2),
                ("BluePrimary", ctypes.c_float * 2),
                ("WhitePoint", ctypes.c_float * 2),
                ("MinLuminance", ctypes.c_float),
                ("MaxLuminance", ctypes.c_float),
                ("MaxFullFrameLuminance", ctypes.c_float),
            ]

        def as_uint(hr):
            return hr & 0xFFFFFFFF

        def release(ptr):
            if not ptr:
                return
            vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            release_fn = ctypes.WINFUNCTYPE(wt.ULONG, ctypes.c_void_p)(vtbl[2])
            release_fn(ptr)

        def method(ptr, index, restype, *argtypes):
            vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])

        target_monitor = None
        if window_handle and user32:
            target_monitor = user32.MonitorFromWindow(wt.HWND(int(window_handle)), MONITOR_DEFAULTTONEAREST)
            try:
                target_monitor = int(target_monitor or 0)
            except Exception:
                target_monitor = None

        dxgi = ctypes.WinDLL("dxgi")
        create_factory = dxgi.CreateDXGIFactory1
        create_factory.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        create_factory.restype = ctypes.c_long

        iid_factory1 = make_guid("770aae78-f26f-4dba-a829-253c83d1b387")
        iid_output6 = make_guid("068346e8-aaec-4b84-add7-137f513f77a1")
        factory_ptr = ctypes.c_void_p()
        if create_factory(ctypes.byref(iid_factory1), ctypes.byref(factory_ptr)) != 0 or not factory_ptr.value:
            return None

        attached_states = []
        matched_state = None
        factory = factory_ptr.value
        try:
            enum_adapters1 = method(factory, 12, ctypes.c_long, wt.UINT, ctypes.POINTER(ctypes.c_void_p))
            adapter_index = 0
            while True:
                adapter_ptr = ctypes.c_void_p()
                hr = enum_adapters1(factory, adapter_index, ctypes.byref(adapter_ptr))
                if as_uint(hr) == DXGI_ERROR_NOT_FOUND:
                    break
                if hr != 0 or not adapter_ptr.value:
                    break
                adapter = adapter_ptr.value
                try:
                    enum_outputs = method(adapter, 7, ctypes.c_long, wt.UINT, ctypes.POINTER(ctypes.c_void_p))
                    output_index = 0
                    while True:
                        output_ptr = ctypes.c_void_p()
                        hr = enum_outputs(adapter, output_index, ctypes.byref(output_ptr))
                        if as_uint(hr) == DXGI_ERROR_NOT_FOUND:
                            break
                        if hr != 0 or not output_ptr.value:
                            break
                        output = output_ptr.value
                        try:
                            query_interface = method(output, 0, ctypes.c_long, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
                            output6_ptr = ctypes.c_void_p()
                            if query_interface(output, ctypes.byref(iid_output6), ctypes.byref(output6_ptr)) == 0 and output6_ptr.value:
                                output6 = output6_ptr.value
                                try:
                                    desc = DXGI_OUTPUT_DESC1()
                                    get_desc1 = method(output6, 27, ctypes.c_long, ctypes.POINTER(DXGI_OUTPUT_DESC1))
                                    if get_desc1(output6, ctypes.byref(desc)) == 0 and desc.AttachedToDesktop:
                                        is_hdr = desc.ColorSpace == DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020
                                        attached_states.append(is_hdr)
                                        try:
                                            monitor = int(desc.Monitor or 0)
                                        except Exception:
                                            monitor = None
                                        if target_monitor and monitor == target_monitor:
                                            matched_state = is_hdr
                                finally:
                                    release(output6)
                        finally:
                            release(output)
                        output_index += 1
                finally:
                    release(adapter)
                adapter_index += 1
        finally:
            release(factory)

        if matched_state is not None:
            return matched_state
        if attached_states:
            return any(attached_states)
        return None
    except Exception:
        return None


def get_app_base_dir():
    return os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))

def bundled_resource_path(*parts):
    if hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, *parts)
        if os.path.exists(p):
            return p
    p = os.path.join(get_app_base_dir(), *parts)
    if os.path.exists(p):
        return p
    return None

def ensure_persistent_adb_runtime(adb_path):
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return adb_path
    try:
        import shutil
        runtime_dir = os.path.join(get_app_data_dir(), "runtime")
        os.makedirs(runtime_dir, exist_ok=True)
        for filename in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
            src = bundled_resource_path("assets", "runtime", filename)
            if not src or not os.path.exists(src):
                continue
            dst = os.path.join(runtime_dir, filename)
            try:
                same_file = os.path.abspath(src).lower() == os.path.abspath(dst).lower()
            except Exception:
                same_file = False
            if same_file:
                continue
            should_copy = not os.path.exists(dst)
            if not should_copy:
                try:
                    should_copy = os.path.getsize(src) != os.path.getsize(dst) or int(os.path.getmtime(src)) > int(os.path.getmtime(dst))
                except Exception:
                    should_copy = True
            if should_copy:
                shutil.copy2(src, dst)
        persistent_adb = os.path.join(runtime_dir, "adb.exe")
        if os.path.exists(persistent_adb):
            return persistent_adb
    except Exception:
        pass
    return adb_path

def get_adb_path():
    adb_names = ["adb.exe"] if sys.platform == "win32" else ["adb"]
    for n in adb_names:
        p = bundled_resource_path("assets", "runtime", n)
        if p:
            return ensure_persistent_adb_runtime(p)
    return "adb"

ADB = get_adb_path()
ADB_SERVER_PORT = os.environ.get("MIMONITOR_ADB_SERVER_PORT", "5038")
ADB_DISCONNECTED_MARKERS = ("offline", "unauthorized", "no devices", "not found", "cannot", "failed", "error:")
GUARDIAN_PACKAGE = "com.example.adbguardian"
GUARDIAN_MAIN_ACTIVITY = f"{GUARDIAN_PACKAGE}/.MainActivity"
GUARDIAN_ACCESSIBILITY = f"{GUARDIAN_PACKAGE}/{GUARDIAN_PACKAGE}.AdbGuardianAccessibilityService"
GUARDIAN_APK_NAME = "adbguardian-signed.apk"
MTK_DIRECT_TOOL_NAME = "MtkDirectTool.jar"
COLORFUL_LED_TOOL_NAME = "ColorfulLedTool.jar"
MTK_BATCH_RESULT_FILE = "/sdcard/Download/Mimonitor_Toolbox/.mtk_batch_result.txt"
XIAOMI_TO_MTK_COLOR_TEMP = {0: 1, 1: 2, 2: 3, 3: 0, 4: 4, 5: 5, 8: 6}
MTK_TO_XIAOMI_COLOR_TEMP = {v: k for k, v in XIAOMI_TO_MTK_COLOR_TEMP.items()}
HDR_TONE_MAPPING_UI_TO_MTK = {0: 5, 1: 0, 2: 2, 3: 1}
HDR_TONE_MAPPING_MTK_TO_UI = {v: k for k, v in HDR_TONE_MAPPING_UI_TO_MTK.items()}
CUSTOM_COLOR_TEMP_VALUE = 3
HOTKEY_MODIFIERS = ["无", "Ctrl + Alt", "Ctrl + Shift", "Alt + Shift", "Win + Shift"]
HOTKEY_KEYS = ["无"] + [f"F{i}" for i in range(1, 13)] + [str(i) for i in range(0, 10)] + list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["+", "-", "PageUp", "PageDown", "↑", "↓", "←", "→"]
HOTKEY_EXTRA_VK = {"+": 0xBB, "-": 0xBD, "PageUp": 0x21, "PageDown": 0x22, "↑": 0x26, "↓": 0x28, "←": 0x25, "→": 0x27}
LOCAL_DIMMING_NAMES = {0: "关", 1: "低", 2: "中", 3: "高"}


def is_adb_server_alive(timeout=0.2):
    """检查本软件独立 ADB Server 的监听端口，不触发 ADB 自动拉起。"""
    sock = None
    try:
        port = int(ADB_SERVER_PORT)
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

def adb_text_has_disconnected_marker(text):
    lower = str(text or "").lower()
    return any(marker in lower for marker in ADB_DISCONNECTED_MARKERS)

def adb_connect_output_ok(text):
    lower = str(text or "").lower()
    if adb_text_has_disconnected_marker(lower):
        return False
    return "connected to" in lower or "already connected to" in lower

def adb_device_state(serial, timeout=3):
    out = adb_run(["-s", serial, "get-state"], timeout=timeout).strip()
    lower = out.lower()
    if lower in ("device", "offline", "unauthorized"):
        return lower
    if "unauthorized" in lower:
        return "unauthorized"
    if "offline" in lower:
        return "offline"
    if "not found" in lower:
        return "not found"
    if "no devices" in lower:
        return "no devices"
    if "cannot" in lower or "failed" in lower or "error:" in lower:
        return lower or "error"
    return lower or "unknown"

def adb_device_state_label(state):
    lower = str(state or "").lower()
    if "offline" in lower:
        return "设备离线"
    if "unauthorized" in lower:
        return "设备未授权"
    if "not found" in lower or "no devices" in lower:
        return "未找到设备"
    if "cannot" in lower or "failed" in lower or "error" in lower:
        return "连接异常"
    if lower == "unknown" or not lower:
        return "状态未知"
    return str(state)

def disconnected_status_text(detail):
    return f"未连接（{adb_device_state_label(detail)}）"

def is_connected_status_text(text):
    text = str(text or "")
    return (text == "已连接" or text.startswith("已连接:")) and not adb_text_has_disconnected_marker(text)

def normalize_status_text(text):
    text = str(text or "")
    if "已连接" in text and adb_text_has_disconnected_marker(text):
        return disconnected_status_text(text)
    return text

ADJUSTABLE_HOTKEY_PARAMS = {
    "backlight": {
        "label": "背光", "setting": "picture_backlight", "settings": ["picture_backlight", "xiaomi_picture_backlight"],
        "jni": "g_disp__disp_back_light", "slider": "backlight", "min": 1, "max": 100, "step": 5,
    },
    "black_level": {
        "label": "黑色级别", "setting": "picture_brightness", "settings": ["picture_brightness"],
        "slider": "black_level", "min": 0, "max": 100, "step": 5,
    },
    "contrast": {
        "label": "对比度", "setting": "picture_contrast", "settings": ["picture_contrast"],
        "slider": "contrast", "min": 0, "max": 100, "step": 5,
    },
    "saturation": {
        "label": "饱和度", "setting": "picture_saturation", "settings": ["picture_saturation"],
        "slider": "saturation", "min": 0, "max": 100, "step": 5,
    },
    "hue": {
        "label": "色调", "setting": "picture_hue", "settings": ["picture_hue"],
        "slider": "hue", "min": 0, "max": 100, "step": 5,
    },
    "sharpness": {
        "label": "锐度", "setting": "picture_sharpness", "settings": ["picture_sharpness"],
        "slider": "sharpness", "min": 0, "max": 100, "step": 1,
    },
    "red_gain": {
        "label": "红色增益", "setting": "picture_red_gain", "slider": "red_gain",
        "jni": "g_video__clr_gain_r", "min": 524, "max": 1524, "step": 10, "color_gain": True,
    },
    "green_gain": {
        "label": "绿色增益", "setting": "picture_green_gain", "slider": "green_gain",
        "jni": "g_video__clr_gain_g", "min": 524, "max": 1524, "step": 10, "color_gain": True,
    },
    "blue_gain": {
        "label": "蓝色增益", "setting": "picture_blue_gain", "slider": "blue_gain",
        "jni": "g_video__clr_gain_b", "min": 524, "max": 1524, "step": 10, "color_gain": True,
    },
    "atmosphere_illumination": {
        "label": "屏幕灯亮度", "setting": "atmosphere_light_illumination", "slider": "atmosphere_illumination",
        "min": 1, "max": 15, "step": 1, "ui_offset": 1, "screen_light": True,
    },
}
PICTURE_MODE_GROUPS = {
    14: {14, 64, 65, 66, 67, 68},
    10: {10, 25, 26, 27, 28, 29},
    9: {9},
}
PICTURE_SCENE_NAMES = {
    9: "电影",
    10: "游戏",
    11: "Dolby Vision 明亮",
    12: "Dolby Vision 暗场",
    13: "Dolby Vision 自定义",
    14: "标准",
    15: "HDR 游戏",
    16: "HDR 图片",
    17: "HDR 电影",
    18: "Dolby Vision IQ",
    19: "Dolby Vision 游戏",
    21: "Filmmaker",
    22: "HDR 显示器",
    23: "HDR Filmmaker",
    24: "SDR 游戏 FPS",
    25: "SDR 游戏 RPG",
    26: "SDR 游戏 RTS",
    27: "SDR 游戏 MOBA",
    28: "SDR 游戏 SPT",
    29: "HDR 游戏 FPS",
    30: "HDR 游戏 RPG",
    31: "HDR 游戏 RTS",
    32: "HDR 游戏 MOBA",
    33: "HDR 游戏 SPT",
    34: "SDR PC AdobeRGB",
    35: "SDR PC DCI-P3",
    36: "SDR PC CG",
    37: "SDR PC 暗房",
    38: "SDR PC sRGB",
    39: "HDR PC AdobeRGB",
    40: "HDR PC DCI-P3",
    41: "HDR PC CG",
    42: "HDR PC 暗房",
    43: "HDR PC sRGB",
    44: "HDR Vivid",
    64: "标准预设",
    65: "标准预设",
    66: "标准预设",
    67: "标准预设",
    68: "标准预设",
}
HDR_TONE_MAPPING_PICTURE_MODES = {
    11, 12, 13, 15, 16, 17, 18, 19,
    22, 23,
    29, 30, 31, 32, 33,
    39, 40, 41, 42, 43, 44,
}

def is_hdr_tone_mapping_picture_mode(mode):
    try:
        return int(mode) in HDR_TONE_MAPPING_PICTURE_MODES
    except Exception:
        return False

def get_guardian_apk_path():
    return bundled_resource_path("assets", "adb_guardian", GUARDIAN_APK_NAME) or os.path.join(get_app_base_dir(), "assets", "adb_guardian", GUARDIAN_APK_NAME)

def get_mtk_direct_tool_path():
    return bundled_resource_path("assets", "runtime", MTK_DIRECT_TOOL_NAME)

def get_colorful_led_tool_path():
    return bundled_resource_path("assets", "runtime", COLORFUL_LED_TOOL_NAME)

# ===== 日志文件 =====
_log_file = None
_log_path = None
_log_to_file_enabled = False
_adb_processes = set()
_adb_spawn_blocked = False
_adb_command_lock = threading.RLock()
_adb_process_lock = threading.RLock()

def adb_command(args):
    cmd = [ADB]
    if ADB_SERVER_PORT:
        cmd += ["-P", str(ADB_SERVER_PORT)]
    return cmd + args

def adb_command_text(args):
    parts = [f'"{ADB}"']
    if ADB_SERVER_PORT:
        parts += ["-P", str(ADB_SERVER_PORT)]
    return " ".join(parts + args)

def _adb_log(msg):
    """写入ADB操作日志到文件"""
    if _log_file and _log_to_file_enabled:
        try:
            _log_file.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            _log_file.flush()
        except Exception: pass

def adb_run(args, timeout=10, check=False):
    proc = None
    with _adb_command_lock:
        try:
            cmd = adb_command(args)
            with _adb_process_lock:
                if _adb_spawn_blocked:
                    _adb_log(f"{adb_command_text(args)} => SKIPPED: shutting down")
                    if check:
                        raise RuntimeError("应用正在退出，ADB 命令已取消")
                    return ""
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
                _adb_processes.add(proc)
            stdout, stderr = proc.communicate(timeout=timeout)
            out = stdout.strip()
            if not out and stderr:
                out = stderr.strip()
            _adb_log(f"{adb_command_text(args)} => {out[:200]}")
            if check and proc.returncode not in (0, None):
                raise RuntimeError(out or f"ADB 进程退出码 {proc.returncode}")
            return out
        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
                try:
                    proc.communicate(timeout=1)
                except Exception:
                    pass
            _adb_log(f"{adb_command_text(args)} => TIMEOUT")
            if check:
                raise RuntimeError(f"ADB 命令超时（{timeout} 秒）") from None
            return ""
        except Exception as e:
            _adb_log(f"{adb_command_text(args)} => ERROR: {e}")
            if check:
                raise
            return ""
        finally:
            if proc:
                with _adb_process_lock:
                    _adb_processes.discard(proc)

def cleanup_adb_processes(kill_server=False):
    with _adb_process_lock:
        processes = list(_adb_processes)
        _adb_processes.clear()
    for proc in processes:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
    if kill_server:
        try:
            subprocess.run(adb_command(["kill-server"]), capture_output=True, text=True, timeout=3,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        except Exception:
            pass

def block_adb_spawns():
    global _adb_spawn_blocked
    with _adb_process_lock:
        _adb_spawn_blocked = True

def unblock_adb_spawns():
    global _adb_spawn_blocked
    with _adb_process_lock:
        _adb_spawn_blocked = False


class Adb:
    def __init__(self, ip="192.168.5.205"): self.ip = ip
    def transaction(self):
        return _adb_command_lock
    def shell(self, cmd, check=False):
        out = adb_run(["-s", f"{self.ip}:5555", "shell", cmd], check=check)
        return out
    def check_and_heal_jar(self):
        with self.transaction():
            sd_size_lines = self.shell("stat -c %s /sdcard/MtkDirectTool.jar 2>/dev/null || echo 0").strip().splitlines()
            sd_size_text = sd_size_lines[-1] if sd_size_lines else "0"
            try:
                sd_size = int(sd_size_text)
            except Exception:
                sd_size = 0
            local_jar = get_mtk_direct_tool_path()
            local_size = 0
            if local_jar:
                try:
                    local_size = os.path.getsize(local_jar)
                except Exception:
                    local_size = 0
            if sd_size < 1000 or (local_size > 0 and sd_size != local_size):
                if local_jar:
                    adb_run(["-s", f"{self.ip}:5555", "push", local_jar, "/sdcard/MtkDirectTool.jar"])
                else:
                    _adb_log("WARNING: MtkDirectTool.jar 本地未找到，无法推送到设备")
                    return
            jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            self.shell(f'service call TvService 3 s16 "cp /sdcard/MtkDirectTool.jar {jar}"')
    def check_and_heal_colorful_led_tool(self, check=False):
        with self.transaction():
            sd_size_lines = self.shell("stat -c %s /sdcard/ColorfulLedTool.jar 2>/dev/null || echo 0", check=check).strip().splitlines()
            sd_size_text = sd_size_lines[-1] if sd_size_lines else "0"
            try:
                sd_size = int(sd_size_text)
            except Exception:
                sd_size = 0
            local_jar = get_colorful_led_tool_path()
            local_size = 0
            if local_jar:
                try:
                    local_size = os.path.getsize(local_jar)
                except Exception:
                    local_size = 0
            if sd_size < 1000 or (local_size > 0 and sd_size != local_size):
                if local_jar:
                    adb_run(["-s", f"{self.ip}:5555", "push", local_jar, "/sdcard/ColorfulLedTool.jar"], check=check)
                else:
                    _adb_log("WARNING: ColorfulLedTool.jar 本地未找到，无法推送到设备")
                    return False
            jar = "/data/data/mitv.service/cache/ColorfulLedTool.jar"
            self.shell(f'service call TvService 3 s16 "cp /sdcard/ColorfulLedTool.jar {jar}"', check=check)
            return True
    def connect(self):
        serial = f"{self.ip}:5555"
        o = adb_run(["connect", serial])
        return adb_connect_output_ok(o) and self.device_state(timeout=3) == "device"
    def ensure_connected(self):
        if not self.ip:
            return False, "unknown"
        serial = f"{self.ip}:5555"
        state = adb_device_state(serial, timeout=2)
        if state == "device":
            return True, state
        adb_run(["connect", serial], timeout=5)
        state = adb_device_state(serial, timeout=3)
        return state == "device", state
    def device_state(self, timeout=3):
        if not self.ip:
            return "unknown"
        return adb_device_state(f"{self.ip}:5555", timeout=timeout)
    def get(self, k, check=False):
        v = self.shell(f"settings get global {k}", check=check)
        _adb_log(f"settings get {k} => {v}")
        return v
    def put(self, k, v, check=False):
        _adb_log(f"settings put {k} = {v}")
        return self.shell(f"settings put global {k} {v}", check=check)
    def key(self, k, check=False):
        _adb_log(f"keyevent {k}")
        return self.shell(f"input keyevent {k}", check=check)
    def colorful_led(self, action, *args, check=False):
        with self.transaction():
            _adb_log(f"colorful_led {action} {' '.join(map(str, args))}")
            if not self.check_and_heal_colorful_led_tool(check=check):
                return ""
            jar = "/data/data/mitv.service/cache/ColorfulLedTool.jar"
            parts = ["ColorfulLedTool", str(action)] + [str(a) for a in args]
            cmd_args = "".join([f"\\${{IFS}}{p}" for p in parts])
            return self.shell(f'service call TvService 3 s16 "sh -c eval\\${{IFS}}CLASSPATH={jar}\\${{IFS}}/system/bin/app_process\\${{IFS}}/data/data/mitv.service/cache{cmd_args}"', check=check)
    def jni_set(self, key, val, upd=3, check=False):
        _adb_log(f"jni_set {key} = {val}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        return self.shell(f'service call TvService 3 s16 "sh -c eval\\${{IFS}}CLASSPATH={jar}\\${{IFS}}/system/bin/app_process\\${{IFS}}/data/data/mitv.service/cache\\${{IFS}}MtkDirectTool\\${{IFS}}set\\${{IFS}}{key}\\${{IFS}}{val}\\${{IFS}}{upd}"', check=check)
    def hdr_tone_mapping(self, val, upd=3, check=False):
        _adb_log(f"hdr_tone_mapping = {val}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        return self.shell(f'service call TvService 3 s16 "sh -c eval\\${{IFS}}CLASSPATH={jar}\\${{IFS}}/system/bin/app_process\\${{IFS}}/data/data/mitv.service/cache\\${{IFS}}MtkDirectTool\\${{IFS}}setHdrToneMapping\\${{IFS}}{val}\\${{IFS}}{upd}"', check=check)
    def jni_set_color_gains(self, red, green, blue, check=False):
        _adb_log(f"jni_set_color_gains r={red} g={green} b={blue}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        return self.shell(f'service call TvService 3 s16 "sh -c eval\\${{IFS}}CLASSPATH={jar}\\${{IFS}}/system/bin/app_process\\${{IFS}}/data/data/mitv.service/cache\\${{IFS}}MtkDirectTool\\${{IFS}}setColorGains\\${{IFS}}{red}\\${{IFS}}{green}\\${{IFS}}{blue}"', check=check)
    def jni_get(self, key, check=False):
        with self.transaction():
            jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            self.shell("logcat -c", check=check)
            self.shell(f'service call TvService 3 s16 "sh -c eval\\${{IFS}}CLASSPATH={jar}\\${{IFS}}/system/bin/app_process\\${{IFS}}/data/data/mitv.service/cache\\${{IFS}}MtkDirectTool\\${{IFS}}get\\${{IFS}}{key}"', check=check)
            time.sleep(0.8)
            log = self.shell(f"logcat -d | grep 'GET {key}' | tail -1", check=check)
        i = log.find("= ")
        if check and i < 0:
            raise RuntimeError(f"未读取到 JNI 值：{key}")
        v = log[i+2:].strip() if i >= 0 else "N/A"
        _adb_log(f"jni_get {key} => {v}")
        return v

    def jni_batch_get(self, keys, check=False):
        safe_keys = []
        for key in keys:
            key = str(key)
            if key and all(ch.isalnum() or ch == "_" for ch in key):
                safe_keys.append(key)
        if not safe_keys:
            return {}

        with self.transaction():
            jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            cmd_args = "".join([f"\\${{IFS}}{p}" for p in ["MtkDirectTool", "batchGet"] + safe_keys])
            cmd = (
                f"mkdir -p /sdcard/Download/Mimonitor_Toolbox; "
                f"rm -f {MTK_BATCH_RESULT_FILE}; "
                f'service call TvService 3 s16 "sh -c eval\\${{IFS}}CLASSPATH={jar}\\${{IFS}}/system/bin/app_process\\${{IFS}}/data/data/mitv.service/cache{cmd_args}" >/dev/null; '
                f"i=0; while [ $i -lt 30 ] && [ ! -f {MTK_BATCH_RESULT_FILE} ]; do sleep 0.1; i=$((i+1)); done; "
                f"cat {MTK_BATCH_RESULT_FILE} 2>/dev/null"
            )
            out = self.shell(cmd, check=check)

        vals = {}
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("__") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if raw.startswith("ERROR"):
                continue
            try:
                vals[key] = int(raw)
            except Exception:
                vals[key] = raw
        if check and not vals:
            raise RuntimeError("未读取到批量 JNI 值")
        _adb_log(f"jni_batch_get {','.join(safe_keys)} => {vals}")
        return vals

    def jni_batch_get_or_single(self, keys, check=False):
        vals = self.jni_batch_get(keys)
        for key in keys:
            if key in vals:
                continue
            try:
                vals[key] = int(self.jni_get(key, check=check))
            except Exception:
                if check:
                    raise
                pass
        return vals

    def refresh_pq(self, check=False):
        _adb_log("refresh_pq")
        return self.shell("am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7", check=check)
    def get_model(self):
        m = self.shell("getprop ro.product.model")
        _adb_log(f"get_model => {m}")
        return m


def scan_adb(base="192.168.5", cb=None, log=None):
    found = []

    # ── 阶段一：全量并发 socket 探测 ──
    # 254 个线程同时探测，0.3 秒超时，极轻量
    open_ips = []
    open_lock = threading.Lock()

    def probe(ip):
        s = None
        try:
            s = socket.socket()
            s.settimeout(0.3)
            if s.connect_ex((ip, 5555)) == 0:
                with open_lock:
                    open_ips.append(ip)
        except Exception:
            pass
        finally:
            if s is not None:
                try:
                    s.close()
                except Exception:
                    pass

    if log: log(f"[扫描] 开始 {base}.1~254")
    threads = []
    for i in range(1, 255):
        t = threading.Thread(target=probe, args=(f"{base}.{i}",), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=1)

    if log: log(f"[扫描] 端口探测完成，{len(open_ips)} 个 IP 的 5555 端口开放")

    # ── 阶段二：仅对端口开放的 IP 做 ADB 验证 ──
    # ADB 命令受全局锁限制本质串行，直接顺序处理即可
    for ip in open_ips:
        serial = f"{ip}:5555"
        try:
            if log: log(f"[扫描] {ip}:5555 开放，正在验证...")
            o = adb_run(["connect", serial], 5)
            if log: log(f"[扫描] {ip} adb: {o}")
            if adb_connect_output_ok(o) and adb_device_state(serial, timeout=3) == "device":
                m = adb_run(["-s", serial, "shell", "getprop ro.product.model"], 3) or "?"
                if adb_text_has_disconnected_marker(m):
                    adb_run(["disconnect", serial], 3)
                    continue
                found.append((ip, m))
                if cb: cb(ip, m)
            else:
                # 非目标设备或连接失败，清理 ADB transport 条目
                adb_run(["disconnect", serial], 3)
        except Exception:
            pass
    if log: log(f"[扫描] 完成，发现 {len(found)} 台")
    return found


def async_run(fn): threading.Thread(target=fn, daemon=True).start()


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
    guardian_status_signal = pyqtSignal(dict)
    auto_scan_signal = pyqtSignal()
    page_refresh_finished = pyqtSignal(str, bool)
    adb_action_finished = pyqtSignal(object, bool, str)
    adb_server_event = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        QApplication.setQuitOnLastWindowClosed(False)
        self.adb = Adb()
        self.mode_btns = {}
        self.sliders = {}
        self.state_buttons = {}
        self.color_gain_cards = []
        self._source_names = {23: "HDMI 1", 24: "HDMI 2", 29: "DP", 30: "USBC", "23": "HDMI 1", "24": "HDMI 2", "29": "DP", "30": "USBC"}
        self.source_var_text = "未知"
        self._page_loaded = set()  # 已加载数据的页面 objectName 集合
        self._page_loading = set()  # 正在加载中的页面
        self._page_data_keys = {
            "picturePage": {
                "settings": ["picture_mode", "picture_backlight", "xiaomi_picture_backlight",
                             "picture_preset_scenario",
                             "picture_brightness", "picture_contrast", "picture_saturation",
                             "picture_hue", "picture_sharpness", "picture_color_temperature",
                             "picture_red_gain", "picture_green_gain", "picture_blue_gain",
                             "picture_local_dimming", "tv_picture_video_local_dimming",
                             "picture_hdr_tone_mapping", "settings_display_hdr_color_tone",
                             "picture_dynamic_definition", "picture_response_time",
                             "tv_picture_advanced_video_color_space", "tv_picture_video_color_space"],
                "jni": ["g_disp__disp_back_light", "g_video__vid_gamut_mapping_mode", "g_video__clr_temp",
                        "g_video__vid_local_dimming", "g_video__vid_hdr_tone_mapping_mode"],
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
            "lightPage": {
                "settings": ["atmosphere_light_switcher_pm2", "atmosphere_light_illumination",
                             "atmosphere_light_color_temp", "atmosphere_light_color_value"],
            },
        }

        # 初始化日志文件路径（等用户开启时再创建文件）
        global _log_path
        _log_path = os.path.join(get_log_dir(), f"log_{time.strftime('%Y%m%d_%H%M%S')}.txt")
        self.is_forcing_exit = False
        self.adb_connected = False
        self._cleanup_done = False
        self._windows_session_ending = False

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
        self.guardian_status_signal.connect(self._apply_guardian_status)
        self.auto_scan_signal.connect(lambda: self.scan_net(auto=True))
        self.page_refresh_finished.connect(self._finish_page_refresh)
        self.adb_action_finished.connect(self._finish_adb_action)
        self.adb_server_event.connect(self._on_adb_server_event)

        # Setup layout and components
        self.osd = OsdHud(self)
        self.setup_ui()
        self.setup_tray()
        self.current_vals = {}
        self._local_dimming_toggle_suspended = False
        self._local_dimming_memory_suppress_until = 0.0
        self._picture_mode_switch_seq = 0
        self._cycle_hotkey_pending = {}
        self._cycle_hotkey_timers = {}
        self._control_rollback_values = {}
        self._adjust_hotkey_pending = {}
        self._adjust_hotkey_timers = {}
        self._adjust_hotkey_resolving = set()
        self._local_dimming_toggle_resolving = False
        self._adb_busy_until = 0.0
        self._adb_keepalive_checking = False
        self._adb_server_monitor_checking = False
        self._adb_server_down_notified = False
        self._adb_server_failure_notified = False
        self._adb_server_retry_after = 0.0
        self._hdr_last_state = None
        self._hdr_state_source = None
        self._hdr_windows_state = None
        self._hdr_memory_apply_timer = QTimer(self)
        self._hdr_memory_apply_timer.setSingleShot(True)
        self._hdr_memory_apply_timer.timeout.connect(self._apply_hdr_memory_for_current_state)
        self._hdr_picture_refresh_timer = QTimer(self)
        self._hdr_picture_refresh_timer.setSingleShot(True)
        self._hdr_picture_refresh_timer.timeout.connect(lambda: self._refresh_picture_data_after_hdr_change())
        self.register_global_hotkeys()

        # 页面切换时按需加载数据
        self.stackedWidget.currentChanged.connect(self._on_page_changed)
        self.adb_keepalive_timer = QTimer(self)
        self.adb_keepalive_timer.setInterval(15000)
        self.adb_keepalive_timer.timeout.connect(self._keep_adb_alive)
        self.adb_keepalive_timer.start()
        self.adb_server_monitor_timer = QTimer(self)
        self.adb_server_monitor_timer.setInterval(1000)
        self.adb_server_monitor_timer.timeout.connect(self._monitor_adb_server)
        self.adb_server_monitor_timer.start()
        self.hdr_memory_timer = QTimer(self)
        self.hdr_memory_timer.setInterval(3000)
        self.hdr_memory_timer.timeout.connect(lambda: self._poll_hdr_memory_state("timer"))
        self.hdr_memory_timer.start()
        QTimer.singleShot(0, self._update_hdr_memory_status_label)
        QTimer.singleShot(0, self._update_freesync_memory_status_label)
        QTimer.singleShot(900, self._auto_connect_on_startup)
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
        self._restore_main_window()
        QTimer.singleShot(0, self._restore_main_window)
        QTimer.singleShot(120, self._restore_main_window)
        QTimer.singleShot(250, self._check_adb_when_restored)

    def _check_adb_when_restored(self):
        if getattr(self, "adb_connected", False) and getattr(self.adb, "ip", ""):
            self._keep_adb_alive()

    def _restore_main_window(self):
        state = self.windowState()
        self.setWindowState((state & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.showNormal()
        self.raise_()
        self.activateWindow()

        if sys.platform == "win32" and user32:
            try:
                hwnd = int(self.winId())
                SW_RESTORE = 9
                HWND_TOPMOST = -1
                HWND_NOTOPMOST = -2
                SWP_NOSIZE = 0x0001
                SWP_NOMOVE = 0x0002
                SWP_SHOWWINDOW = 0x0040
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
                user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            except Exception:
                pass

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger or reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            is_minimized = bool(self.windowState() & Qt.WindowState.WindowMinimized)
            if self.isVisible() and not is_minimized:
                self.hide()
            else:
                self.show_and_raise()

    def register_global_hotkeys(self):
        if sys.platform != "win32" or not user32:
            return
            
        self.unregister_all_hotkeys()
        
        settings = load_settings()
        hotkeys = settings.get("hotkeys", {})
        adjust_hotkeys = settings.get("adjust_hotkeys", [])
        
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
        vk_map.update(HOTKEY_EXTRA_VK)
            
        hwnd = int(self.winId())
        
        hotkey_id = 1
        def register_one(payload, hk_conf):
            nonlocal hotkey_id
            mod_str = hk_conf.get("modifier", "无")
            key_str = hk_conf.get("key", "无")
            if mod_str == "无" and key_str == "无":
                return
                
            mod_val = mod_map.get(mod_str, 0)
            vk_val = vk_map.get(key_str, 0)
            if vk_val == 0:
                return
                
            res = user32.RegisterHotKey(hwnd, hotkey_id, mod_val, vk_val)
            if res:
                self.hotkey_registry[hotkey_id] = payload
                hotkey_id += 1
            else:
                label = payload.get("action") if isinstance(payload, dict) else str(payload)
                if isinstance(payload, dict) and payload.get("type") == "adjust":
                    cfg = ADJUSTABLE_HOTKEY_PARAMS.get(payload.get("rule", {}).get("param"), {})
                    label = cfg.get("label", "可调参数")
                self.log(f"快捷键注册失败或冲突: {label} ({mod_str} + {key_str})")

        for action_name, hk_conf in hotkeys.items():
            register_one({"type": "cycle", "action": action_name}, hk_conf)

        for rule in adjust_hotkeys:
            if isinstance(rule, dict):
                register_one({"type": "adjust", "rule": rule}, rule)

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
            if msg.message == WM_QUERYENDSESSION:
                self._windows_session_ending = True
                block_adb_spawns()
                return True, 1
            if msg.message == WM_ENDSESSION:
                if msg.wParam:
                    self._windows_session_ending = True
                    self.cleanup_before_exit()
                else:
                    self._windows_session_ending = False
                    unblock_adb_spawns()
                return True, 0
            if msg.message == WM_HOTKEY:
                hotkey_id = msg.wParam
                if hotkey_id in getattr(self, "hotkey_registry", {}):
                    payload = self.hotkey_registry[hotkey_id]
                    if isinstance(payload, dict) and payload.get("type") == "adjust":
                        self.trigger_adjust_hotkey(payload.get("rule", {}))
                    else:
                        action = payload.get("action") if isinstance(payload, dict) else payload
                        self.trigger_hotkey_action(action)
                return True, 0
            if msg.message in (WM_DISPLAYCHANGE, WM_SETTINGCHANGE):
                self._schedule_hdr_memory_check("windows_event", delay_ms=600)
        return super().nativeEvent(eventType, message)

    def trigger_hotkey_action(self, action):
        if not getattr(self, "adb_connected", False):
            return
            
        if action == "local_dimming_toggle_off":
            self._toggle_local_dimming_off_hotkey()
            return
            
        actions_map = {
            "picture_mode_cycle": (
                "picture_mode",
                [(14, "标准"), (10, "游戏"), (9, "电影")],
                "画面模式",
                lambda val, name: self._set_mode(val, name)
            ),
            "local_dimming_cycle": (
                "picture_local_dimming",
                [(0, "关"), (1, "低"), (2, "中"), (3, "高")],
                "精密控光",
                lambda val, name: self._jni("g_video__vid_local_dimming", val, "picture_local_dimming", f"精密控光: {name}", "tv_picture_video_local_dimming")
            ),
            "color_space_cycle": (
                "tv_picture_advanced_video_color_space",
                [(0, "自动"), (3, "sRGB"), (6, "DCI-P3"), (4, "AdobeRGB"), (5, "BT2020"), (7, "BT709")],
                "色域",
                lambda val, name: self._jni("g_video__vid_gamut_mapping_mode", val, "tv_picture_advanced_video_color_space", f"色域: {name}", "tv_picture_video_color_space")
            ),
            "color_temp_cycle": (
                "picture_color_temperature",
                [(0, "冷色"), (1, "标准"), (2, "暖色"), (8, "原色"), (3, "自定义")],
                "色温",
                lambda val, name: self._set_color_temp(XIAOMI_TO_MTK_COLOR_TEMP.get(val, 2), val, f"色温: {name}")
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
                "mitv.tvplayer.hdmi.last.source",
                [(23, "HDMI 1"), (24, "HDMI 2"), (29, "DP"), (30, "USBC")],
                "信号源切换",
                lambda val, name: self._set("mitv.tvplayer.hdmi.last.source", val, f"信号源: {name}")
            )
        }
        
        if action not in actions_map:
            return

        self._stage_cycle_hotkey_action(action, *actions_map[action])

    def _stage_cycle_hotkey_action(self, action, sk, state_tuples, label_name, exec_fn):
        pending = self._cycle_hotkey_pending.get(action)
        curr_val = pending.get("value") if pending else getattr(self, "current_vals", {}).get(sk, state_tuples[0][0])
        original_value = pending.get("original") if pending else curr_val

        curr_idx = -1
        for idx, (val, _name) in enumerate(state_tuples):
            if val == curr_val:
                curr_idx = idx
                break

        next_idx = (curr_idx + 1) % len(state_tuples)
        next_val, next_name = state_tuples[next_idx]

        self._cycle_hotkey_pending[action] = {
            "sk": sk,
            "label": label_name,
            "name": next_name,
            "value": next_val,
            "original": original_value,
            "exec": exec_fn,
        }
        self._preview_cycle_hotkey_value(sk, next_val)
        if getattr(self, "osd", None):
            self.osd.show_hud(label_name, next_name)

        timer = self._cycle_hotkey_timers.get(action)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(450)
            timer.timeout.connect(lambda a=action: self._commit_cycle_hotkey_action(a))
            self._cycle_hotkey_timers[action] = timer
        timer.start()

    def _preview_cycle_hotkey_value(self, sk, value):
        self.current_vals[sk] = value
        if sk == "picture_mode":
            self._highlight_mode(value)
            return
        if sk == "freesync":
            self.jni_values_signal.emit({sk: value})
            return
        self.values_signal.emit({sk: value})

    def _commit_cycle_hotkey_action(self, action):
        pending = self._cycle_hotkey_pending.pop(action, None)
        if not pending or not getattr(self, "adb_connected", False):
            return
        sk = pending["sk"]
        next_val = pending["value"]
        next_name = pending["name"]
        self._control_rollback_values[sk] = pending.get("original")
        try:
            pending["exec"](next_val, next_name)
        except Exception as e:
            self._control_rollback_values.pop(sk, None)
            self.log(f"快捷键执行失败: {e}")

    def _take_control_previous(self, key):
        if key in self._control_rollback_values:
            return self._control_rollback_values.pop(key)
        return self.current_vals.get(key)

    def _get_current_local_dimming_value(self):
        val = getattr(self, "current_vals", {}).get("picture_local_dimming")
        try:
            return max(0, min(3, int(val)))
        except Exception:
            return 0

    def _get_saved_local_dimming_toggle_value(self):
        try:
            val = int(load_settings().get("local_dimming_toggle_last_value", 3))
        except Exception:
            val = 3
        return max(1, min(3, val))

    def _save_local_dimming_toggle_value(self, value):
        try:
            value = max(1, min(3, int(value)))
        except Exception:
            value = 3
        update_settings({"local_dimming_toggle_last_value": value})

    def _toggle_local_dimming_off_hotkey(self):
        if "picture_local_dimming" not in self.current_vals:
            if self._local_dimming_toggle_resolving:
                return
            self._local_dimming_toggle_resolving = True
            result = {}

            def operation():
                result["value"] = self.query_setting_or_jni("picture_local_dimming", check=True)

            def success():
                self._local_dimming_toggle_resolving = False
                self.current_vals["picture_local_dimming"] = result["value"]
                self._toggle_local_dimming_off_hotkey()

            def failure():
                self._local_dimming_toggle_resolving = False

            self._run_adb_action("读取精密控光", operation, success, failure)
            return

        current = self._get_current_local_dimming_value()
        if current > 0:
            self._save_local_dimming_toggle_value(current)
            target = 0
            self._local_dimming_toggle_suspended = True
            message = f"精密控光开关: 已记录 {LOCAL_DIMMING_NAMES.get(current, current)}，切换为关"
        else:
            target = self._get_saved_local_dimming_toggle_value()
            self._local_dimming_toggle_suspended = False
            message = f"精密控光开关: 恢复 {LOCAL_DIMMING_NAMES.get(target, target)}"

        self._local_dimming_memory_suppress_until = time.monotonic() + 3.0
        value_name = LOCAL_DIMMING_NAMES.get(target, str(target))
        if getattr(self, "osd", None):
            self.osd.show_hud("精密控光", value_name)
        self._set_local_dimming_toggle_value(target, message)

    def _set_local_dimming_toggle_value(self, value, message):
        if not self.check_connection():
            return
        value = max(0, min(3, int(value)))
        self._mark_adb_busy(2.5)
        previous = self.current_vals.get("picture_local_dimming")

        def operation():
            with self.adb.transaction():
                self.adb.jni_set("g_video__vid_local_dimming", value, check=True)
                self.adb.put("picture_local_dimming", str(value), check=True)
                self.adb.put("tv_picture_video_local_dimming", str(value), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(message)
            self.current_vals["picture_local_dimming"] = value
            self.current_vals["tv_picture_video_local_dimming"] = value
            self._optimistic_highlight("picture_local_dimming", value)

        def failure():
            self._local_dimming_toggle_suspended = False
            if previous is not None:
                self._optimistic_highlight("picture_local_dimming", previous)

        self._run_adb_action("精密控光", operation, success, failure)

    def trigger_adjust_hotkey(self, rule):
        if not getattr(self, "adb_connected", False):
            return
        if not isinstance(rule, dict):
            return

        param = rule.get("param")
        cfg = ADJUSTABLE_HOTKEY_PARAMS.get(param)
        if not cfg:
            return

        setting = cfg["setting"]
        resolve_settings = [setting]
        if cfg.get("color_gain"):
            resolve_settings = ["picture_red_gain", "picture_green_gain", "picture_blue_gain"]
        missing_settings = [key for key in resolve_settings if key not in self.current_vals]
        if missing_settings:
            if param in self._adjust_hotkey_resolving:
                return
            self._adjust_hotkey_resolving.add(param)
            result = {}

            def operation():
                with self.adb.transaction():
                    for key in missing_settings:
                        result[key] = self.query_setting_or_jni(key, check=True)

            def success():
                self._adjust_hotkey_resolving.discard(param)
                self.current_vals.update(result)
                self.trigger_adjust_hotkey(rule)

            def failure():
                self._adjust_hotkey_resolving.discard(param)

            self._run_adb_action(f"读取{cfg['label']}", operation, success, failure)
            return

        direction = rule.get("direction", "increase")
        try:
            step = abs(int(rule.get("step", cfg.get("step", 1))))
        except Exception:
            step = cfg.get("step", 1)
        if step <= 0:
            step = cfg.get("step", 1)

        pending = self._adjust_hotkey_pending.get(param, {})
        curr_val = pending.get("value", self._get_adjustable_display_value(cfg))
        delta = step if direction == "increase" else -step
        next_val = max(cfg["min"], min(cfg["max"], curr_val + delta))
        value_name = str(next_val)

        if getattr(self, "osd", None):
            self.osd.show_hud(cfg["label"], value_name)

        try:
            self._stage_adjustable_display_value(param, cfg, next_val)
        except Exception as e:
            self.log(f"可调快捷键执行失败: {e}")

    def _stage_adjustable_display_value(self, param, cfg, value):
        setting = cfg["setting"]
        raw_value = value - int(cfg.get("ui_offset", 0))

        if cfg.get("screen_light"):
            self.current_vals[setting] = raw_value
            self.values_signal.emit({setting: raw_value})
        elif cfg.get("color_gain"):
            self._ensure_color_gain_values()
            self.current_vals[setting] = value
            self.values_signal.emit({setting: value})
        else:
            settings_keys = cfg.get("settings", [setting])
            for k in settings_keys:
                self.current_vals[k] = value
            self.values_signal.emit({setting: value})

        self._adjust_hotkey_pending[param] = {"cfg": cfg, "value": value}
        timer = self._adjust_hotkey_timers.get(param)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(450)
            timer.timeout.connect(lambda p=param: self._commit_pending_adjustment(p))
            self._adjust_hotkey_timers[param] = timer
        timer.start()

    def _commit_pending_adjustment(self, param):
        pending = self._adjust_hotkey_pending.pop(param, None)
        if not pending or not getattr(self, "adb_connected", False):
            return
        try:
            self._set_adjustable_display_value(param, pending["cfg"], pending["value"])
        except Exception as e:
            self.log(f"可调快捷键提交失败: {e}")

    def _get_adjustable_display_value(self, cfg):
        setting = cfg["setting"]
        if setting in getattr(self, "current_vals", {}):
            val = self.current_vals.get(setting)
        else:
            if cfg.get("slider") in self.sliders:
                val = self.sliders[cfg["slider"]][0].value()
            else:
                val = cfg.get("default", cfg["min"])

        try:
            val = int(val)
        except Exception:
            val = cfg.get("default", cfg["min"])
        if cfg.get("ui_offset"):
            val += int(cfg["ui_offset"])
        return max(cfg["min"], min(cfg["max"], val))

    def _set_adjustable_display_value(self, param, cfg, value):
        setting = cfg["setting"]
        raw_value = value - int(cfg.get("ui_offset", 0))

        if cfg.get("screen_light"):
            self._set_screen_light_illumination(value)
            self.current_vals[setting] = raw_value
            self.values_signal.emit({setting: raw_value})
            return

        if cfg.get("color_gain"):
            self._ensure_color_gain_values()
            self._set_color_gain(cfg["label"], setting, cfg.get("jni"), value)
            self.values_signal.emit({setting: value})
            return

        settings_keys = cfg.get("settings", [setting])
        for k in settings_keys:
            self.current_vals[k] = value
        self.values_signal.emit({setting: value})
        self._mark_adb_busy(2.5)

        def do():
            with self.adb.transaction():
                if cfg.get("jni"):
                    self.adb.jni_set(cfg["jni"], value, check=True)
                    self.adb.refresh_pq(check=True)
                for k in settings_keys:
                    self.adb.put(k, str(value), check=True)

        self._run_adb_action(
            cfg["label"],
            do,
            lambda: self.log(f"{cfg['label']}: {value}"),
            lambda: self._force_refresh_page("picturePage"),
        )

    def _ensure_color_gain_values(self):
        for key in ("picture_red_gain", "picture_green_gain", "picture_blue_gain"):
            if key in self.current_vals:
                continue
            try:
                self.current_vals[key] = int(self.query_setting_or_jni(key))
            except Exception:
                self.current_vals[key] = 1024

    def query_setting_or_jni(self, sk, check=False):
        def as_int(value, fallback=0):
            try:
                return int(value)
            except Exception:
                if check:
                    raise RuntimeError(f"{sk} 返回了无效值：{value}") from None
                return fallback

        if sk in ("picture_local_dimming", "tv_picture_video_local_dimming"):
            v = self.adb.jni_get("g_video__vid_local_dimming", check=check)
            return as_int(v)
        elif sk in ("tv_picture_advanced_video_color_space", "tv_picture_video_color_space"):
            v = self.adb.jni_get("g_video__vid_gamut_mapping_mode", check=check)
            return as_int(v)
        elif sk == "picture_response_time":
            v = self.adb.jni_get("g_video__vid_od_response_time", check=check)
            return as_int(v)
        elif sk == "freesync":
            src = self.adb.get("mitv.tvplayer.hdmi.last.source", check=check)
            src_id = as_int(src)
            if src_id in (29, 30):
                fs = self.adb.jni_get("g_video__dp_adaptive_sync", check=check)
                return 1 if as_int(fs) == 1 else 0
            else:
                fs = self.adb.jni_get("g_video__freesync_switch", check=check)
                return 1 if as_int(fs) == 3 else 0
        else:
            # Standard settings key
            v = self.adb.get(sk, check=check)
            return as_int(v, fallback=v)

    def _hdr_memory_enabled(self):
        return bool(load_settings().get("hdr_sdr_local_dimming_enabled", False))

    def _close_behavior_is_direct_exit(self):
        return load_settings().get("close_behavior", "tray") == "exit"

    def _toggle_hdr_local_dimming_memory(self, state):
        try:
            state_val = int(state)
        except Exception:
            state_val = getattr(state, "value", 0)
        enabled = state_val == Qt.CheckState.Checked.value
        update_settings({"hdr_sdr_local_dimming_enabled": enabled})
        if enabled:
            self._ensure_local_dimming_memory_defaults()
            self._hdr_last_state = None
            self._hdr_state_source = None
            self._hdr_windows_state = None
            self.log("HDR/SDR 分区控光记忆: 开启")
            self._update_hdr_memory_status_label("正在检测 Windows HDR")
            self._schedule_hdr_memory_check("enabled", delay_ms=80)
        else:
            self.log("HDR/SDR 分区控光记忆: 关闭")
            self._update_hdr_memory_status_label()

    def _freesync_mode_memory_enabled(self):
        return bool(load_settings().get("freesync_mode_memory_enabled", False))

    def _picture_mode_display_name(self, mode):
        try:
            mode_int = int(mode)
        except (TypeError, ValueError):
            return str(mode)
        group_name = self._picture_mode_group_name(mode_int)
        if group_name:
            return f"{group_name}（{mode_int}）"
        return f"{PICTURE_SCENE_NAMES.get(mode_int, '未知场景')}（{mode_int}）"

    def _get_freesync_memory_mode(self):
        try:
            return int(load_settings().get("freesync_previous_mode"))
        except (TypeError, ValueError):
            return None

    def _remember_freesync_previous_mode(self):
        """记录 FreeSync 开启前的画面模式（开启后显示器会自动切到游戏模式）"""
        try:
            mode = int(self.current_vals.get("picture_mode"))
        except (TypeError, ValueError):
            return
        update_settings({"freesync_previous_mode": mode})
        self._update_freesync_memory_status_label()
        self.log(f"FreeSync 模式记忆: 记录开启前模式 {self._picture_mode_display_name(mode)}")

    def _toggle_freesync_mode_memory(self, state):
        try:
            state_val = int(state)
        except Exception:
            state_val = getattr(state, "value", 0)
        enabled = state_val == Qt.CheckState.Checked.value
        update_settings({"freesync_mode_memory_enabled": enabled})
        self.log(f"FreeSync Pro 模式记忆: {'开启' if enabled else '关闭'}")
        self._update_freesync_memory_status_label()

    def _update_freesync_memory_status_label(self):
        label = getattr(self, "freesync_memory_status_label", None)
        if not label:
            return
        enabled = self._freesync_mode_memory_enabled()
        mode = self._get_freesync_memory_mode()
        mode_text = self._picture_mode_display_name(mode) if mode is not None else "--"
        prefix = "已开启" if enabled else "已关闭"
        label.setText(f"FreeSync 模式记忆：{prefix}，记录的开启前模式：{mode_text}（关闭 FreeSync 后自动切回）")

    def _mark_adb_busy(self, seconds=2.0):
        self._adb_busy_until = max(getattr(self, "_adb_busy_until", 0.0), time.monotonic() + seconds)

    def _adb_channel_busy(self):
        if time.monotonic() < getattr(self, "_adb_busy_until", 0.0):
            return True
        if getattr(self, "_page_loading", None):
            return True
        for timer in getattr(self, "_adjust_hotkey_timers", {}).values():
            if timer.isActive():
                return True
        for timer in getattr(self, "_cycle_hotkey_timers", {}).values():
            if timer.isActive():
                return True
        return False

    def _schedule_hdr_memory_check(self, reason="manual", delay_ms=500):
        QTimer.singleShot(delay_ms, lambda r=reason: self._poll_hdr_memory_state(r))

    def _query_windows_hdr_state(self):
        try:
            return query_windows_hdr_enabled(int(self.winId()))
        except Exception:
            return query_windows_hdr_enabled()

    def _poll_hdr_memory_state(self, reason="timer"):
        visible_interval = 3000
        background_interval = 8000
        target_interval = visible_interval if self.isVisible() and not self.isMinimized() else background_interval
        if hasattr(self, "hdr_memory_timer") and self.hdr_memory_timer.interval() != target_interval:
            self.hdr_memory_timer.setInterval(target_interval)

        win_state = self._query_windows_hdr_state()
        if win_state is None:
            self._hdr_windows_state = None
            self._hdr_last_state = None
            self._hdr_state_source = None
            self._update_hdr_memory_status_label("Windows HDR 状态未知")
            return
        self._on_hdr_memory_state_detected(win_state, "Windows HDR")

    def _on_hdr_memory_state_detected(self, state, source):
        if state is None:
            self._update_hdr_memory_status_label("信号状态未知")
            return
        self._hdr_windows_state = state
        self._reconcile_hdr_memory_state(source)

    def _reconcile_hdr_memory_state(self, source=None):
        windows_state = getattr(self, "_hdr_windows_state", None)
        if windows_state is None:
            self._hdr_last_state = None
            self._hdr_state_source = None
            self._update_hdr_memory_status_label(source or "信号状态未知")
            return
        state = windows_state

        previous = getattr(self, "_hdr_last_state", None)
        # previous 为 None 表示连接/启动后的首次检测：只是确立基线，并非真正的 HDR 切换。
        # 首次检测是否会走到“已连接”分支取决于轮询与连接完成的时序，之前靠这个竞争来决定
        # 要不要刷新，很不稳定；这里显式区分基线与真实切换，让行为确定。
        is_baseline = previous is None
        changed = state != previous
        self._hdr_last_state = state
        self._hdr_state_source = "Windows"
        self._update_hdr_memory_status_label(source)
        update_tone_visibility = getattr(self, "_update_hdr_tone_mapping_visibility", None)
        if callable(update_tone_visibility):
            update_tone_visibility()
        if changed:
            memory_enabled = self._hdr_memory_enabled()
            if memory_enabled:
                self._schedule_hdr_memory_apply(delay_ms=120)
            # 首次基线检测不当作“切换”：连接时已加载/预加载画面页，无需重复刷新。
            # 仅在真实切换(SDR↔HDR)、或开启记忆需要回读已应用结果时才刷新。
            if not is_baseline or memory_enabled:
                self._schedule_picture_refresh_after_hdr_change(
                    state,
                    initial_delay_ms=300 if memory_enabled else 0,
                    is_switch=not is_baseline,
                )

    def _schedule_picture_refresh_after_hdr_change(self, state, initial_delay_ms=0, is_switch=True):
        if not getattr(self, "adb_connected", False):
            return
        state_name = "HDR" if state else "SDR"
        if is_switch:
            self.log(f"检测到 Windows HDR 切换为 {state_name}，刷新画面数据...")
        else:
            self.log(f"检测到当前 Windows 为 {state_name}，应用 HDR 记忆并刷新画面数据...")
        self._page_loaded.discard("picturePage")
        QTimer.singleShot(initial_delay_ms, lambda: self._refresh_picture_data_after_hdr_change())
        timer = getattr(self, "_hdr_picture_refresh_timer", None)
        if timer:
            timer.stop()
            timer.start(3500)

    def _refresh_picture_data_after_hdr_change(self, retries=8):
        if not getattr(self, "adb_connected", False):
            return
        apply_timer = getattr(self, "_hdr_memory_apply_timer", None)
        if apply_timer and apply_timer.isActive():
            if retries > 0:
                QTimer.singleShot(400, lambda r=retries - 1: self._refresh_picture_data_after_hdr_change(r))
            return
        if self._adb_channel_busy():
            if retries > 0:
                QTimer.singleShot(400, lambda r=retries - 1: self._refresh_picture_data_after_hdr_change(r))
            return
        self._page_loaded.discard("picturePage")
        if "picturePage" in getattr(self, "_page_loading", set()):
            if retries > 0:
                QTimer.singleShot(400, lambda r=retries - 1: self._refresh_picture_data_after_hdr_change(r))
            return
        self._refresh_page_data("picturePage")

    def _update_hdr_memory_status_label(self, source=None):
        label = getattr(self, "hdr_memory_status_label", None)
        if not label:
            return
        enabled = self._hdr_memory_enabled()
        state = getattr(self, "_hdr_last_state", None)
        state_text = "未知" if state is None else ("HDR" if state else "SDR")
        state_source = getattr(self, "_hdr_state_source", None)
        state_source_text = f"（{state_source}）" if state_source and state is not None else ""
        memory = self._get_local_dimming_memory()
        sdr_val = memory.get("sdr")
        hdr_val = memory.get("hdr")
        sdr_text = LOCAL_DIMMING_NAMES.get(sdr_val, "--") if isinstance(sdr_val, int) else "--"
        hdr_text = LOCAL_DIMMING_NAMES.get(hdr_val, "--") if isinstance(hdr_val, int) else "--"
        prefix = "已开启" if enabled else "已关闭"
        source_text = f"，{source}" if source and state is None else ""
        runtime_note = ""
        if enabled and self._close_behavior_is_direct_exit():
            runtime_note = "；已选择直接退出，此功能仅在应用运行时生效"
            label.setStyleSheet("color: #d83b01; font-size: 12px;")
        else:
            label.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 12px;")
        label.setText(f"分区控光记忆：{prefix}，当前信号：{state_text}{state_source_text}，记忆模式：SDR={sdr_text}，HDR={hdr_text}{source_text}{runtime_note}")

    def _schedule_hdr_memory_apply(self, delay_ms=250):
        timer = getattr(self, "_hdr_memory_apply_timer", None)
        if not timer:
            return
        timer.stop()
        timer.start(delay_ms)

    def _apply_hdr_memory_for_current_state(self):
        if not self._hdr_memory_enabled() or not getattr(self, "adb_connected", False):
            return
        state = getattr(self, "_hdr_last_state", None)
        if state is None:
            return
        if self._adb_channel_busy():
            self._schedule_hdr_memory_apply()
            return
        bucket = "hdr" if state else "sdr"
        memory = self._get_local_dimming_memory()
        value = memory.get(bucket)
        if not isinstance(value, int):
            return
        try:
            current = int(self.current_vals.get("picture_local_dimming"))
        except Exception:
            current = None
        if current == value:
            return
        state_name = "HDR" if state else "SDR"
        value_name = LOCAL_DIMMING_NAMES.get(value, str(value))
        if getattr(self, "osd", None):
            self.osd.show_hud(f"{state_name} 精密控光", value_name)
        self._set_local_dimming_for_memory(value, f"{state_name} 精密控光记忆: {value_name}")

    def _set_local_dimming_for_memory(self, value, message):
        value = max(0, min(3, int(value)))
        self._mark_adb_busy(2.5)
        previous = self.current_vals.get("picture_local_dimming")
        self.current_vals["picture_local_dimming"] = value
        self.current_vals["tv_picture_video_local_dimming"] = value
        self.values_signal.emit({
            "picture_local_dimming": value,
            "tv_picture_video_local_dimming": value,
        })

        def operation():
            with self.adb.transaction():
                self.adb.jni_set("g_video__vid_local_dimming", value, check=True)
                self.adb.put("picture_local_dimming", str(value), check=True)
                self.adb.put("tv_picture_video_local_dimming", str(value), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(message)

        def failure():
            if previous is not None:
                self.current_vals["picture_local_dimming"] = previous
                self.current_vals["tv_picture_video_local_dimming"] = previous
                self.values_signal.emit({
                    "picture_local_dimming": previous,
                    "tv_picture_video_local_dimming": previous,
                })

        self._run_adb_action("HDR/SDR 精密控光", operation, success, failure)

    def _read_current_local_dimming_for_memory(self):
        if self._adb_channel_busy():
            self._schedule_hdr_memory_apply()
            return
        self._mark_adb_busy(1.5)

        def do():
            try:
                value = int(self.query_setting_or_jni("picture_local_dimming"))
            except Exception:
                return
            value = max(0, min(3, value))
            self.values_signal.emit({
                "picture_local_dimming": value,
                "tv_picture_video_local_dimming": value,
            })

        async_run(do)

    def _get_local_dimming_memory(self):
        memory = load_settings().get("local_dimming_memory", {})
        result = {}
        for key in ("sdr", "hdr"):
            try:
                value = int(memory.get(key))
                result[key] = max(0, min(3, value))
            except Exception:
                result[key] = None
        return result

    def _save_local_dimming_memory(self, memory):
        update_settings({"local_dimming_memory": memory})
        self._update_hdr_memory_status_label()

    def _ensure_local_dimming_memory_defaults(self):
        memory = self._get_local_dimming_memory()
        try:
            current = int(self.current_vals["picture_local_dimming"])
        except Exception:
            return
        current = max(0, min(3, current))
        changed = False
        for key in ("sdr", "hdr"):
            if not isinstance(memory.get(key), int):
                memory[key] = current
                changed = True
        if changed:
            self._save_local_dimming_memory(memory)

    def _remember_local_dimming_value(self, value, log_change=True, force_bucket=None):
        if not self._hdr_memory_enabled():
            return
        if not log_change:
            return
        try:
            value = max(0, min(3, int(value)))
        except Exception:
            return
        if time.monotonic() < getattr(self, "_local_dimming_memory_suppress_until", 0.0):
            return
        if value == 0 and getattr(self, "_local_dimming_toggle_suspended", False):
            return
        bucket = force_bucket
        if bucket is None:
            state = getattr(self, "_hdr_last_state", None)
            if state is None:
                state = self._query_windows_hdr_state()
            if state is None:
                return
            bucket = "hdr" if state else "sdr"
        memory = self._get_local_dimming_memory()
        if memory.get(bucket) == value:
            return
        memory[bucket] = value
        self._save_local_dimming_memory(memory)
        if log_change:
            state_name = "HDR" if bucket == "hdr" else "SDR"
            self.log(f"已记忆 {state_name} 精密控光: {LOCAL_DIMMING_NAMES.get(value, value)}")

    def _monitor_adb_server(self):
        if getattr(self, "_cleanup_done", False) or getattr(self, "_windows_session_ending", False):
            return
        if not getattr(self, "adb_connected", False) or not self.adb.ip:
            return
        if self._adb_server_monitor_checking or time.monotonic() < self._adb_server_retry_after:
            return

        self._adb_server_monitor_checking = True
        ip = self.adb.ip

        def do():
            if is_adb_server_alive():
                self.adb_server_event.emit("healthy", "")
                return

            self.adb_server_event.emit("restarting", "")
            try:
                adb_run(["start-server"], timeout=5, check=True)
                if not is_adb_server_alive(timeout=0.5):
                    raise RuntimeError("ADB Server 启动后未监听端口")

                reconnect_result = adb_run(["connect", f"{ip}:5555"], timeout=5)
                self.adb_server_event.emit("recovered", reconnect_result)
            except Exception as e:
                self.adb_server_event.emit("failed", str(e))

        async_run(do)

    def _on_adb_server_event(self, event, detail):
        if getattr(self, "_cleanup_done", False):
            return

        if event == "healthy":
            self._adb_server_monitor_checking = False
            self._adb_server_down_notified = False
            self._adb_server_failure_notified = False
            self._adb_server_retry_after = 0.0
            return

        if event == "restarting":
            if not self._adb_server_down_notified:
                self._adb_server_down_notified = True
                message = "检测到 ADB 进程被杀死，正在重启"
                self.log(message)
                if getattr(self, "osd", None):
                    self.osd.show_hud("ADB 进程", "正在重启")
                if getattr(self, "tray_icon", None):
                    self.tray_icon.showMessage(
                        "ADB 进程监控",
                        message,
                        QSystemTrayIcon.MessageIcon.Warning,
                        3500,
                    )
            return

        self._adb_server_monitor_checking = False
        if event == "recovered":
            self._adb_server_retry_after = 0.0
            self._adb_server_failure_notified = False
            self._adb_server_down_notified = False
            self.log("ADB 进程已重新启动，并已尝试恢复显示器连接")
            if getattr(self, "tray_icon", None):
                self.tray_icon.showMessage(
                    "ADB 进程监控",
                    "ADB 进程已重新启动",
                    QSystemTrayIcon.MessageIcon.Information,
                    2500,
                )
            return

        if event == "failed":
            self._adb_server_retry_after = time.monotonic() + 3.0
            self.log(f"ADB 进程重启失败，将继续重试: {detail}")
            if not self._adb_server_failure_notified:
                self._adb_server_failure_notified = True
                if getattr(self, "tray_icon", None):
                    self.tray_icon.showMessage(
                        "ADB 进程监控",
                        "ADB 进程重启失败，正在持续重试",
                        QSystemTrayIcon.MessageIcon.Critical,
                        4000,
                    )

    def _keep_adb_alive(self):
        if getattr(self, "_cleanup_done", False) or getattr(self, "_windows_session_ending", False):
            return
        if not getattr(self, "adb_connected", False) or not self.adb.ip:
            return
        if getattr(self, "_adb_keepalive_checking", False):
            return
        if time.monotonic() < getattr(self, "_adb_busy_until", 0.0):
            return
        self._adb_keepalive_checking = True
        ip = self.adb.ip

        def do():
            try:
                serial = f"{ip}:5555"
                state = adb_device_state(serial, timeout=3)
                if state == "device":
                    return

                # 先 disconnect 清除可能存在的 stale transport，
                # 否则 adb connect 会返回 "already connected" 但实际 TCP 已断
                self.status_signal.emit(f"连接中...（{adb_device_state_label(state)}，正在重连）")
                adb_run(["disconnect", serial], timeout=3)
                adb_run(["connect", serial], timeout=5)
                state = adb_device_state(serial, timeout=3)
                if state != "device":
                    self.status_signal.emit(disconnected_status_text(state))
                    return

                m = adb_run(["-s", serial, "shell", "getprop ro.product.model"], timeout=3).strip()
                if adb_text_has_disconnected_marker(m):
                    self.status_signal.emit(disconnected_status_text(m))
                    return
                self.status_signal.emit(f"已连接: {m or ip}")
            finally:
                self._adb_keepalive_checking = False

        async_run(do)

    def cleanup_before_exit(self):
        if getattr(self, "_cleanup_done", False):
            return
        self._cleanup_done = True
        session_ending = getattr(self, "_windows_session_ending", False)
        if session_ending:
            block_adb_spawns()
        try:
            self.unregister_all_hotkeys()
        except Exception:
            pass
        try:
            if hasattr(self, "adb_keepalive_timer"):
                self.adb_keepalive_timer.stop()
            if hasattr(self, "adb_server_monitor_timer"):
                self.adb_server_monitor_timer.stop()
        except Exception:
            pass
        try:
            if hasattr(self, "hdr_memory_timer"):
                self.hdr_memory_timer.stop()
            if hasattr(self, "_hdr_memory_apply_timer"):
                self._hdr_memory_apply_timer.stop()
            if hasattr(self, "_hdr_picture_refresh_timer"):
                self._hdr_picture_refresh_timer.stop()
        except Exception:
            pass
        if session_ending:
            cleanup_adb_processes(kill_server=False)
        else:
            with _adb_command_lock:
                try:
                    if self.adb.ip:
                        adb_run(["disconnect", f"{self.adb.ip}:5555"], timeout=3)
                except Exception:
                    pass
                block_adb_spawns()
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
                dialog.deleteLater()
                
                update_settings({
                    "close_behavior": choice,
                    "never_ask_close": remember,
                })
                
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
                dialog.deleteLater()
                event.ignore()

    def _on_log(self, text):
        self.log_widget.append(text)
        self.log_widget.ensureCursorVisible()
        if _log_file and _log_to_file_enabled:
            try:
                _log_file.write(text + "\n")
                _log_file.flush()
            except Exception: pass

    def _toggle_log_file(self, state):
        global _log_to_file_enabled, _log_file
        _log_to_file_enabled = (state == 2)
        if _log_to_file_enabled and not _log_file:
            try:
                os.makedirs(os.path.dirname(_log_path), exist_ok=True)
                _log_file = open(_log_path, "a", encoding="utf-8")
                _log_file.write(f"{'='*60}\n")
                _log_file.write(f"红米 G Pro 27U Toolbox 日志 - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                _log_file.write(f"{'='*60}\n")
                _log_file.flush()
            except OSError as e:
                # 目录不可写（如安装到 Program Files）等情况：回退开关，不让异常冒泡到 Qt 槽
                _log_to_file_enabled = False
                _log_file = None
                self.log_file_toggle.blockSignals(True)
                self.log_file_toggle.setChecked(False)
                self.log_file_toggle.blockSignals(False)
                self.log(f"无法创建日志文件（目录不可写: {os.path.dirname(_log_path)}）: {e}")
                return
        self.log(f"本地日志记录: {'开启' if _log_to_file_enabled else '关闭'}")

    def _toggle_autostart(self, state):
        enabled = (state == 2)
        update_settings({"autostart": enabled})
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
                with open(path, "w", encoding="utf-8") as f:
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
            accepted = w.exec()
            w.deleteLater()
            if not accepted:
                self.chk_4k.blockSignals(True)
                self.chk_4k.setChecked(False)
                self.chk_4k.blockSignals(False)
                return

        def operation():
            with self.adb.transaction():
                if enable:
                    self.adb.shell("wm size 3840x2160", check=True)
                    self.adb.shell("wm density 640", check=True)
                    self.adb.shell("reboot", check=True)
                else:
                    self.adb.shell("wm size 1920x1080", check=True)
                    self.adb.shell("wm density 320", check=True)

        def success():
            if enable:
                self.log("已设置 4K UI (3840×2160 / DPI 640)")
                self.log("正在重启显示器...")
            else:
                self.log("已恢复 1080p UI (1920×1080 / DPI 320)")

        def failure():
            self.chk_4k.blockSignals(True)
            self.chk_4k.setChecked(not enable)
            self.chk_4k.blockSignals(False)

        self._run_adb_action("4K UI", operation, success, failure)

    def _check_4k_state(self):
        """检测 Override size，存在且大于1080p则为4K模式"""
        if not getattr(self, "adb_connected", False):
            return
        result = {"is_4k": False}

        def operation():
            res = self.adb.shell("wm size", check=True)
            for line in res.split("\n"):
                if "Override size" not in line:
                    continue
                parts = line.split(":")[-1].strip().split("x")
                if len(parts) == 2:
                    w, h = int(parts[0]), int(parts[1])
                    result["is_4k"] = (w > 1920 or h > 1080)
                break

        def success():
            self.chk_4k.blockSignals(True)
            self.chk_4k.setChecked(result["is_4k"])
            self.chk_4k.blockSignals(False)

        self._run_adb_action("检测 4K UI", operation, success)

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
        log_dir = os.path.dirname(_log_path) if _log_path else get_log_dir()
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError as e:
            self.log(f"无法创建日志目录: {e}")
            return
        if sys.platform == "win32":
            os.startfile(log_dir)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", log_dir])
        else:
            subprocess.Popen(["xdg-open", log_dir])

    def _on_status(self, text):
        text = normalize_status_text(text)
        self.status_label.setText(text)
        was_connected = getattr(self, "adb_connected", False)
        self.adb_connected = is_connected_status_text(text)
        # 连接状态变化时清除页面缓存
        if was_connected and not self.adb_connected:
            self._page_loaded.clear()
            self._hdr_last_state = None
            self._hdr_state_source = None
            self._update_hdr_memory_status_label("等待显示器连接")
        elif not was_connected and self.adb_connected:
            self._page_loaded.clear()
            # 触发当前页面加载
            page = self.stackedWidget.currentWidget()
            if page:
                self._on_page_changed(self.stackedWidget.currentIndex())
            # 连接后预加载画面页与游戏页数据（无论当前停在哪一页），切换过去时无需再等读取
            self._refresh_pages(("picturePage", "gamePage"), delay_ms=800)
            # 检测 4K 状态
            QTimer.singleShot(1500, self._check_4k_state)
            # 首次连接后同步 ADB 保活守护状态，工具页卡片不需要再手动点检测
            QTimer.singleShot(1800, self._check_guardian_status)
            QTimer.singleShot(2200, lambda: self._poll_hdr_memory_state("connected"))
        
        if "扫描中" in text:
            status_suffix = "正在扫描内网..."
            self.status_label.setStyleSheet("color: #b85c00; font-weight: bold; font-size: 14px;")
        elif "扫描完成" in text:
            status_suffix = text
            self.status_label.setStyleSheet("color: #b85c00; font-weight: bold; font-size: 14px;")
            # 扫描完成后自动连接优先匹配到的显示器
            if self.dev_combo.count() > 0 and not self.adb_connected:
                self._on_dev_sel(self.dev_combo.currentIndex())
        elif "未连接" in text or "失败" in text or adb_text_has_disconnected_marker(text):
            status_suffix = "未连接"
            self.status_label.setStyleSheet("color: #d83b01; font-weight: bold; font-size: 14px;")
        elif "连接中" in text:
            status_suffix = "连接中"
            self.status_label.setStyleSheet("color: #b85c00; font-weight: bold; font-size: 14px;")
        elif "已连接" in text:
            status_suffix = f"已连接 ({text.replace('已连接: ', '')})"
            self.status_label.setStyleSheet("color: #107c41; font-weight: bold; font-size: 14px;")
        else:
            status_suffix = text
            self.status_label.setStyleSheet("color: #107c41; font-weight: bold; font-size: 14px;")
            
        self.setWindowTitle(f"红米 G Pro 27U Toolbox - {status_suffix}")

    def _show_message_box(self, mtype, title, text):
        w = MessageBox(title, text, self)
        w.exec()
        w.deleteLater()

    def setup_ui(self):
        self.home_page = self._make_home_page()
        self.picture_page = self._make_picture_page()
        self.game_page = self._make_game_page()
        self.source_page = self._make_source_page()
        self.light_page = self._make_light_page()
        self.tools_page = self._make_tools_page()
        self.remote_page = self._make_remote_page()

        self.home_page.setObjectName("homePage")
        self.picture_page.setObjectName("picturePage")
        self.game_page.setObjectName("gamePage")
        self.source_page.setObjectName("sourcePage")
        self.light_page.setObjectName("lightPage")
        self.tools_page.setObjectName("toolsPage")
        self.remote_page.setObjectName("remotePage")

        # Add routes
        self.addSubInterface(self.home_page, FIF.HOME, "主页 & 连接")
        self.addSubInterface(self.picture_page, FIF.PALETTE, "画面设置")
        self.addSubInterface(self.game_page, FIF.GAME, "游戏模式")
        self.addSubInterface(self.source_page, FIF.SYNC, "信号源切换")
        self.addSubInterface(self.light_page, FIF.BRIGHTNESS, "屏幕灯")
        self.addSubInterface(self.tools_page, FIF.DEVELOPER_TOOLS, "工具与设置")
        self.addSubInterface(self.remote_page, FIF.TILES, "遥控器")

        # Hide return (back) button
        self.navigationInterface.setReturnButtonVisible(False)

    def log(self, m):
        self.log_signal.emit(f"[{time.strftime('%H:%M:%S')}] {m}")

    def _add_icon_title(self, layout, icon, text, parent):
        row = QHBoxLayout()
        row.setSpacing(9)
        row.setContentsMargins(0, 0, 0, 0)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        icon_widget = IconWidget(icon, parent)
        icon_widget.setFixedSize(18, 18)
        row.addWidget(icon_widget, 0, Qt.AlignmentFlag.AlignVCenter)
        label = SubtitleLabel(text, parent)
        label.setFixedHeight(26)
        label.setStyleSheet("margin: 0; padding: 0;")
        row.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        wrapper = QWidget(parent)
        wrapper.setFixedHeight(26)
        wrapper.setLayout(row)
        layout.addWidget(wrapper)
        return label

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

        self.connect_btn = PrimaryPushButton(FIF.WIFI, "开始连接", conn_card)
        self.connect_btn.clicked.connect(self.connect)
        row1.addWidget(self.connect_btn)

        self.scan_btn = PushButton(FIF.SEARCH, "扫描内网", conn_card)
        self.scan_btn.clicked.connect(self.scan_net)
        row1.addWidget(self.scan_btn)

        self.disconnect_btn = PushButton(FIF.CLOSE, "断开连接", conn_card)
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
        self.log_widget.document().setMaximumBlockCount(500)
        self.log_widget.append("[00:00:00] 系统就绪，等待连接...")
        log_layout.addWidget(self.log_widget)

        log_btn_row = QHBoxLayout()
        self.log_file_toggle = CheckBox("记录到本地文件", log_card)
        self.log_file_toggle.setChecked(False)
        self.log_file_toggle.stateChanged.connect(self._toggle_log_file)
        log_btn_row.addWidget(self.log_file_toggle)
        log_btn_row.addStretch(1)
        export_log_btn = PushButton(FIF.SHARE, "导出日志", log_card)
        export_log_btn.clicked.connect(self._export_log)
        log_btn_row.addWidget(export_log_btn)
        open_log_btn = PushButton(FIF.FOLDER, "打开日志目录", log_card)
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
        refresh_pic_btn = PushButton(FIF.UPDATE, "刷新数据", container)
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
        self.picture_mode_hint_label = BodyLabel("当前场景：未刷新", lf)
        self.picture_mode_hint_label.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 12px;")
        h.addWidget(self.picture_mode_hint_label)
        h.addStretch(1)
        lf_layout.addLayout(h)
        layout.addWidget(lf)

        # Sliders
        self._add_slider(layout, "背光", "backlight", 1, 100, 50, jni_key="g_disp__disp_back_light", settings_keys=["picture_backlight", "xiaomi_picture_backlight"])
        self._add_slider(layout, "黑色级别", "black_level", 0, 100, 50, settings_keys=["picture_brightness"])
        self._add_slider(layout, "对比度", "contrast", 0, 100, 50, settings_keys=["picture_contrast"])
        self._add_slider(layout, "饱和度", "saturation", 0, 100, 50, settings_keys=["picture_saturation"])
        self._add_slider(layout, "色调", "hue", 0, 100, 50, settings_keys=["picture_hue"])
        self._add_slider(layout, "锐度", "sharpness", 0, 100, 1, settings_keys=["picture_sharpness"])

        # Button Groups
        self._btn_section(layout, "色温", [
            ("冷色", 0, lambda _: self._set_color_temp(1, 0, "色温: 冷色")),
            ("标准", 1, lambda _: self._set_color_temp(2, 1, "色温: 标准")),
            ("暖色", 2, lambda _: self._set_color_temp(3, 2, "色温: 暖色")),
            ("原色", 8, lambda _: self._set_color_temp(6, 8, "色温: 原色")),
            ("自定义", 3, lambda _: self._set_color_temp(0, 3, "色温: 自定义")),
        ], state_key="picture_color_temperature")
        self._add_color_gain_slider(layout, "红色增益", "red_gain", "picture_red_gain", "g_video__clr_gain_r")
        self._add_color_gain_slider(layout, "绿色增益", "green_gain", "picture_green_gain", "g_video__clr_gain_g")
        self._add_color_gain_slider(layout, "蓝色增益", "blue_gain", "picture_blue_gain", "g_video__clr_gain_b")

        self._btn_section(layout, "精密控光", [
            ("关", 0, lambda _: self._jni("g_video__vid_local_dimming", 0, "picture_local_dimming", "精密控光: 关", "tv_picture_video_local_dimming")),
            ("低", 1, lambda _: self._jni("g_video__vid_local_dimming", 1, "picture_local_dimming", "精密控光: 低", "tv_picture_video_local_dimming")),
            ("中", 2, lambda _: self._jni("g_video__vid_local_dimming", 2, "picture_local_dimming", "精密控光: 中", "tv_picture_video_local_dimming")),
            ("高", 3, lambda _: self._jni("g_video__vid_local_dimming", 3, "picture_local_dimming", "精密控光: 高", "tv_picture_video_local_dimming")),
        ], state_key="picture_local_dimming")

        self.hdr_tone_mapping_card = self._btn_section(layout, "HDR 色调映射", [
            ("HGiG", 0, lambda _: self._set_hdr_tone_mapping(0, "HGiG")),
            ("层次", 1, lambda _: self._set_hdr_tone_mapping(1, "层次")),
            ("动态", 2, lambda _: self._set_hdr_tone_mapping(2, "动态")),
            ("明亮", 3, lambda _: self._set_hdr_tone_mapping(3, "明亮")),
        ], state_key="settings_display_hdr_color_tone")
        self._update_hdr_tone_mapping_visibility()

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
            ("自动", 0, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 0, "tv_picture_advanced_video_color_space", "色域: 自动", "tv_picture_video_color_space")),
            ("sRGB", 3, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 3, "tv_picture_advanced_video_color_space", "色域: sRGB", "tv_picture_video_color_space")),
            ("DCI-P3", 6, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 6, "tv_picture_advanced_video_color_space", "色域: DCI-P3", "tv_picture_video_color_space")),
            ("AdobeRGB", 4, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 4, "tv_picture_advanced_video_color_space", "色域: Adobe RGB", "tv_picture_video_color_space")),
            ("BT2020", 5, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 5, "tv_picture_advanced_video_color_space", "色域: BT2020", "tv_picture_video_color_space")),
            ("BT709", 7, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 7, "tv_picture_advanced_video_color_space", "色域: BT709", "tv_picture_video_color_space")),
        ], state_key="tv_picture_advanced_video_color_space")

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
        refresh_game_btn = PushButton(FIF.UPDATE, "刷新数据", container)
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
        refresh_source_btn = PushButton(FIF.UPDATE, "刷新数据", container)
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
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(lbl)

        self.source_label = TitleLabel("未知", status_card)
        self.source_label.setStyleSheet("color: #00bcd4; font-size: 32px; font-weight: bold; margin-top: 10px;")
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self.source_label)

        layout.addWidget(status_card)
        return container

    def _make_light_page(self):
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
        title = SubtitleLabel("屏幕灯", container)
        title.setStyleSheet("font-size: 24px; font-weight: bold; margin-bottom: 5px;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        refresh_light_btn = PushButton(FIF.UPDATE, "刷新数据", container)
        refresh_light_btn.clicked.connect(lambda: self._force_refresh_page("lightPage"))
        title_row.addWidget(refresh_light_btn)
        layout.addLayout(title_row)

        self._btn_section(layout, "炫彩灯模式", [
            ("关闭", 4, lambda _: self._set_screen_light_mode(4, "关闭")),
            ("照明", 0, lambda _: self._set_screen_light_mode(0, "照明")),
            ("纯色", 2, lambda _: self._set_screen_light_mode(2, "纯色")),
            ("屏幕同色", 1, lambda _: self._set_screen_light_mode(1, "屏幕同色")),
            ("七彩梦境（循环）", 3, lambda _: self._set_screen_light_mode(3, "七彩梦境（循环）")),
        ], state_key="atmosphere_light_switcher_pm2")

        self._add_light_slider(layout, "亮度挡位", "atmosphere_illumination", 1, 15, 10)

        self._btn_section(layout, "照明色温", [
            ("2700K", 0, lambda _: self._set_screen_light_color_temp(0, "2700K")),
            ("4000K", 1, lambda _: self._set_screen_light_color_temp(1, "4000K")),
            ("6500K", 2, lambda _: self._set_screen_light_color_temp(2, "6500K")),
        ], state_key="atmosphere_light_color_temp")

        self._btn_section(layout, "纯色颜色", [
            ("冰蓝", 0, lambda _: self._set_screen_light_color_value(0, "冰蓝")),
            ("流金", 1, lambda _: self._set_screen_light_color_value(1, "流金")),
            ("天青", 2, lambda _: self._set_screen_light_color_value(2, "天青")),
            ("草地", 3, lambda _: self._set_screen_light_color_value(3, "草地")),
            ("日落", 4, lambda _: self._set_screen_light_color_value(4, "日落")),
        ], state_key="atmosphere_light_color_value")

        scroll.setWidget(container)
        return scroll

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
        
        self._add_icon_title(c1_lay, FIF.COMMAND_PROMPT, "打开 ADB Shell", card1)
        
        lbl_c1_desc = BodyLabel("在外部终端中弹出一个交互式的 ADB Shell 会话，供开发人员和高级用户直接调试显示器的 Android 系统参数。", card1)
        lbl_c1_desc.setWordWrap(True)
        lbl_c1_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px; height: 50px;")
        c1_lay.addWidget(lbl_c1_desc)

        btn_c1 = PrimaryPushButton(FIF.COMMAND_PROMPT, "启动 Shell 终端", card1)
        btn_c1.clicked.connect(self._open_shell)
        c1_lay.addWidget(btn_c1)
        grid.addWidget(card1, 0, 0)

        # APK Install Card
        card2 = SimpleCardWidget(container)
        c2_lay = QVBoxLayout(card2)
        c2_lay.setContentsMargins(20, 20, 20, 20)
        c2_lay.setSpacing(10)
        
        self._add_icon_title(c2_lay, FIF.APPLICATION, "安装 APK 软件包", card2)
        
        lbl_c2_desc = BodyLabel("通过无线 ADB 安全、静默地向您的显示器安装第三方的 Android APK 应用软件包，支持完整的安装状态回执提示。", card2)
        lbl_c2_desc.setWordWrap(True)
        lbl_c2_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px; height: 50px;")
        c2_lay.addWidget(lbl_c2_desc)

        btn_c2 = PrimaryPushButton(FIF.APPLICATION, "选择并安装应用", card2)
        btn_c2.clicked.connect(self._install_apk)
        c2_lay.addWidget(btn_c2)
        grid.addWidget(card2, 0, 1)

        # Software Settings Card
        card3 = SimpleCardWidget(container)
        c3_lay = QVBoxLayout(card3)
        c3_lay.setContentsMargins(20, 20, 20, 20)
        c3_lay.setSpacing(15)
        
        self._add_icon_title(c3_lay, FIF.SETTING, "软件设置", card3)
        
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
            
            update_settings({"theme": theme_str})
            
        self.theme_combo.currentIndexChanged.connect(on_theme_changed)
        
        behavior = settings.get("close_behavior", "tray")
        if behavior == "tray":
            self.btn_setting_tray.setChecked(True)
        else:
            self.btn_setting_exit.setChecked(True)
            
        def on_choose_tray():
            self.btn_setting_tray.setChecked(True)
            self.btn_setting_exit.setChecked(False)
            update_settings({"close_behavior": "tray"})
            self._update_hdr_memory_status_label()
            
        def on_choose_exit():
            self.btn_setting_tray.setChecked(False)
            self.btn_setting_exit.setChecked(True)
            update_settings({"close_behavior": "exit"})
            self._update_hdr_memory_status_label()
            
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

        hdr_memory_layout = QHBoxLayout()
        hdr_memory_layout.setSpacing(15)
        self.chk_hdr_local_dimming_memory = CheckBox("HDR/SDR 分区控光记忆", card3)
        self.chk_hdr_local_dimming_memory.setChecked(settings.get("hdr_sdr_local_dimming_enabled", False))
        self.chk_hdr_local_dimming_memory.stateChanged.connect(self._toggle_hdr_local_dimming_memory)
        hdr_memory_layout.addWidget(self.chk_hdr_local_dimming_memory)
        hdr_memory_layout.addStretch()
        c3_lay.addLayout(hdr_memory_layout)

        self.hdr_memory_status_label = BodyLabel("当前信号：未检测", card3)
        self.hdr_memory_status_label.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 12px;")
        c3_lay.addWidget(self.hdr_memory_status_label)

        freesync_memory_layout = QHBoxLayout()
        freesync_memory_layout.setSpacing(15)
        self.chk_freesync_mode_memory = CheckBox("FreeSync Pro 模式记忆", card3)
        self.chk_freesync_mode_memory.setChecked(settings.get("freesync_mode_memory_enabled", False))
        self.chk_freesync_mode_memory.stateChanged.connect(self._toggle_freesync_mode_memory)
        freesync_memory_layout.addWidget(self.chk_freesync_mode_memory)
        freesync_memory_layout.addStretch()
        c3_lay.addLayout(freesync_memory_layout)

        self.freesync_memory_status_label = BodyLabel("FreeSync 模式记忆：未启用", card3)
        self.freesync_memory_status_label.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 12px;")
        c3_lay.addWidget(self.freesync_memory_status_label)

        grid.addWidget(card3, 2, 0, 1, 2)

        # 4K UI Card
        card5 = SimpleCardWidget(container)
        c5_lay = QVBoxLayout(card5)
        c5_lay.setContentsMargins(20, 20, 20, 20)
        c5_lay.setSpacing(10)

        self._add_icon_title(c5_lay, FIF.FIT_PAGE, "4K UI 模式", card5)

        lbl_c5_desc = BodyLabel("将显示器 UI 分辨率提升至 3840×2160，DPI 设为 640。开启或关闭后显示器将自动重启。", card5)
        lbl_c5_desc.setWordWrap(True)
        lbl_c5_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px;")
        c5_lay.addWidget(lbl_c5_desc)

        self.chk_4k = CheckBox("启用 4K UI", card5)
        self.chk_4k.stateChanged.connect(self._toggle_4k_ui)
        c5_lay.addWidget(self.chk_4k)

        grid.addWidget(card5, 1, 0, 1, 1)

        # ADB Guardian Card
        card6 = SimpleCardWidget(container)
        c6_lay = QVBoxLayout(card6)
        c6_lay.setContentsMargins(20, 20, 20, 20)
        c6_lay.setSpacing(10)

        self._add_icon_title(c6_lay, FIF.VPN, "ADB 保活守护", card6)

        lbl_c6_desc = BodyLabel("部署电视端 AdbGuardian，重启、待机或唤醒后自动恢复无线 ADB，并保持 5555 端口可用。", card6)
        lbl_c6_desc.setWordWrap(True)
        lbl_c6_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px;")
        c6_lay.addWidget(lbl_c6_desc)

        self.guardian_status_label = BodyLabel("状态：未检测", card6)
        self.guardian_status_label.setStyleSheet("color: rgba(255, 255, 255, 0.82); font-size: 13px;")
        c6_lay.addWidget(self.guardian_status_label)

        guardian_btn_row = QHBoxLayout()
        guardian_btn_row.setSpacing(8)
        btn_guardian_check = PushButton(FIF.SEARCH, "检测状态", card6)
        btn_guardian_check.clicked.connect(self._check_guardian_status)
        guardian_btn_row.addWidget(btn_guardian_check)

        btn_guardian_deploy = PrimaryPushButton(FIF.APPLICATION, "部署/修复", card6)
        btn_guardian_deploy.clicked.connect(self._deploy_guardian)
        guardian_btn_row.addWidget(btn_guardian_deploy)

        btn_guardian_start = PushButton(FIF.CONNECT, "启动保活", card6)
        btn_guardian_start.clicked.connect(self._start_guardian)
        guardian_btn_row.addWidget(btn_guardian_start)
        guardian_btn_row.addStretch(1)
        c6_lay.addLayout(guardian_btn_row)

        grid.addWidget(card6, 1, 1, 1, 1)

        # Global Hotkey Settings Card
        card4 = SimpleCardWidget(container)
        c4_lay = QVBoxLayout(card4)
        c4_lay.setContentsMargins(20, 20, 20, 20)
        c4_lay.setSpacing(15)
        
        self._add_icon_title(c4_lay, FIF.TAG, "自定义全局快捷键 (Windows 独占)", card4)
        
        lbl_c4_desc = BodyLabel("为所有带档位切换的功能提供自定义全局快捷键支持。支持后台/游戏中静默控制，设置完成后自动弹出系统原生气泡通知。", card4)
        lbl_c4_desc.setWordWrap(True)
        lbl_c4_desc.setStyleSheet("color: rgba(255, 255, 255, 0.6); font-size: 12px;")
        c4_lay.addWidget(lbl_c4_desc)
        
        self.hotkey_combos = {}
        self.adjust_hotkey_rows = []
        hotkeys_settings = settings.get("hotkeys", {})
        adjust_hotkeys_settings = settings.get("adjust_hotkeys", [])
        
        actions_list = [
            ("picture_mode_cycle", "画面模式 循环切换"),
            ("local_dimming_cycle", "精密控光 循环切换"),
            ("local_dimming_toggle_off", "精密控光 开关切换"),
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
            mod_combo.addItems(HOTKEY_MODIFIERS)
            mod_combo.setFixedWidth(130)
            
            key_combo = ComboBox(card4)
            key_combo.addItems(HOTKEY_KEYS)
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

        adjust_title = BodyLabel("可调参数快捷键", card4)
        adjust_title.setStyleSheet("font-size: 13px; font-weight: bold; color: rgba(255, 255, 255, 0.88);")
        c4_lay.addWidget(adjust_title)

        param_keys = list(ADJUSTABLE_HOTKEY_PARAMS.keys())
        param_labels = [ADJUSTABLE_HOTKEY_PARAMS[k]["label"] for k in param_keys]

        def add_adjust_hotkey_row(rule=None):
            rule = rule or {
                "param": "backlight",
                "direction": "increase",
                "step": ADJUSTABLE_HOTKEY_PARAMS["backlight"]["step"],
                "modifier": "无",
                "key": "无",
            }
            row_widget = QWidget(card4)
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            param_combo = ComboBox(row_widget)
            param_combo.addItems(param_labels)
            param_combo.setFixedWidth(120)
            param_idx = param_keys.index(rule.get("param")) if rule.get("param") in param_keys else 0
            param_combo.setCurrentIndex(param_idx)

            direction_combo = ComboBox(row_widget)
            direction_combo.addItems(["增加", "减少"])
            direction_combo.setFixedWidth(70)
            direction_combo.setCurrentIndex(1 if rule.get("direction") == "decrease" else 0)

            step_edit = LineEdit(row_widget)
            step_edit.setFixedWidth(54)
            step_edit.setText(str(rule.get("step", ADJUSTABLE_HOTKEY_PARAMS[param_keys[param_idx]].get("step", 1))))
            step_edit.setPlaceholderText("步进")

            mod_combo = ComboBox(row_widget)
            mod_combo.addItems(HOTKEY_MODIFIERS)
            mod_combo.setFixedWidth(120)
            mod_idx = mod_combo.findText(rule.get("modifier", "无"))
            if mod_idx >= 0:
                mod_combo.setCurrentIndex(mod_idx)

            key_combo = ComboBox(row_widget)
            key_combo.addItems(HOTKEY_KEYS)
            key_combo.setFixedWidth(82)
            key_idx = key_combo.findText(rule.get("key", "无"))
            if key_idx >= 0:
                key_combo.setCurrentIndex(key_idx)

            delete_btn = PushButton("删除", row_widget)
            delete_btn.setFixedWidth(58)

            row_layout.addWidget(param_combo)
            row_layout.addWidget(direction_combo)
            row_layout.addWidget(step_edit)
            row_layout.addWidget(mod_combo)
            row_layout.addWidget(key_combo)
            row_layout.addWidget(delete_btn)
            row_layout.addStretch(1)
            insert_before = getattr(self, "adjust_hotkey_add_button", None)
            insert_index = c4_lay.indexOf(insert_before) if insert_before else -1
            if insert_index >= 0:
                c4_lay.insertWidget(insert_index, row_widget)
            else:
                c4_lay.addWidget(row_widget)

            row_ref = {
                "widget": row_widget,
                "param": param_combo,
                "direction": direction_combo,
                "step": step_edit,
                "modifier": mod_combo,
                "key": key_combo,
            }
            self.adjust_hotkey_rows.append(row_ref)

            def remove_row():
                if row_ref in self.adjust_hotkey_rows:
                    self.adjust_hotkey_rows.remove(row_ref)
                row_widget.setParent(None)
                row_widget.deleteLater()

            delete_btn.clicked.connect(remove_row)

        for rule in adjust_hotkeys_settings:
            if isinstance(rule, dict):
                add_adjust_hotkey_row(rule)

        btn_add_adjust_hotkey = PushButton("新建可调快捷键", card4)
        self.adjust_hotkey_add_button = btn_add_adjust_hotkey
        btn_add_adjust_hotkey.clicked.connect(lambda: add_adjust_hotkey_row())
        c4_lay.addWidget(btn_add_adjust_hotkey)
            
        btn_save_hotkeys = PrimaryPushButton(FIF.TAG, "保存并应用全局快捷键", card4)
        c4_lay.addWidget(btn_save_hotkeys)
        
        def save_and_apply_hotkeys():
            new_hotkeys = {}
            for act_name, (m_combo, k_combo) in self.hotkey_combos.items():
                m_val = m_combo.currentText()
                k_val = k_combo.currentText()
                new_hotkeys[act_name] = {"modifier": m_val, "key": k_val}

            new_adjust_hotkeys = []
            for row in self.adjust_hotkey_rows:
                if not row["widget"].parent():
                    continue
                param_idx = row["param"].currentIndex()
                param_key = param_keys[param_idx] if 0 <= param_idx < len(param_keys) else "backlight"
                cfg = ADJUSTABLE_HOTKEY_PARAMS[param_key]
                try:
                    step_val = abs(int(row["step"].text().strip()))
                except Exception:
                    step_val = cfg.get("step", 1)
                if step_val <= 0:
                    step_val = cfg.get("step", 1)
                new_adjust_hotkeys.append({
                    "param": param_key,
                    "direction": "decrease" if row["direction"].currentText() == "减少" else "increase",
                    "step": step_val,
                    "modifier": row["modifier"].currentText(),
                    "key": row["key"].currentText(),
                })
                
            update_settings({
                "hotkeys": new_hotkeys,
                "adjust_hotkeys": new_adjust_hotkeys,
            })
            
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

        for btn, key in [(btn_vol_down, "KEYCODE_VOLUME_DOWN"), (btn_mute, "KEYCODE_VOLUME_MUTE"), (btn_vol_up, "KEYCODE_VOLUME_UP")]:
            btn.setFixedSize(74, 34)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #2c2c2c;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 17px;
                    color: #e3e3e3;
                    font-size: 12px;
                    font-weight: bold;
                    padding: 0;
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

        slider = PageScrollSlider(Qt.Orientation.Horizontal, card)
        slider.setRange(lo, hi)
        slider.setValue(default)
        layout.addWidget(slider)

        val_label = BodyLabel(str(default), card)
        val_label.setFixedWidth(40)
        val_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(val_label)

        slider.valueChanged.connect(lambda v: val_label.setText(str(v)))
        self.sliders[name] = (slider, val_label)

        _debounce_timer = QTimer(card)
        _debounce_timer.setSingleShot(True)
        _debounce_timer.setInterval(300)

        def on_commit():
            if not self.check_connection():
                return
            v = slider.value()
            self._mark_adb_busy(2.5)
            def operation():
                with self.adb.transaction():
                    if jni_key:
                        self.adb.jni_set(jni_key, v, check=True)
                        self.adb.refresh_pq(check=True)
                    if settings_keys:
                        for k in settings_keys: self.adb.put(k, str(v), check=True)
            self._run_adb_action(
                title,
                operation,
                lambda: self.log(f"{title}: {v}"),
                lambda: self._force_refresh_page("picturePage"),
            )

        _debounce_timer.timeout.connect(on_commit)
        slider.valueChanged.connect(lambda v: _debounce_timer.start())
        parent_layout.addWidget(card)

    def _add_color_gain_slider(self, parent_layout, title, name, settings_key, jni_key):
        card = SimpleCardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)

        name_label = BodyLabel(title, card)
        name_label.setFixedWidth(100)
        layout.addWidget(name_label)

        slider = PageScrollSlider(Qt.Orientation.Horizontal, card)
        slider.setRange(524, 1524)
        slider.setValue(1024)
        layout.addWidget(slider)

        val_label = BodyLabel("1024", card)
        val_label.setFixedWidth(48)
        val_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(val_label)

        slider.valueChanged.connect(lambda v: val_label.setText(str(v)))
        self.sliders[name] = (slider, val_label)

        _debounce_timer = QTimer(card)
        _debounce_timer.setSingleShot(True)
        _debounce_timer.setInterval(300)

        def on_commit():
            self._set_color_gain(title, settings_key, jni_key, slider.value())

        _debounce_timer.timeout.connect(on_commit)
        slider.valueChanged.connect(lambda _v: _debounce_timer.start())
        parent_layout.addWidget(card)
        self.color_gain_cards.append(card)
        card.setVisible(False)

    def _add_light_slider(self, parent_layout, title, name, lo, hi, default):
        card = SimpleCardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)

        name_label = BodyLabel(title, card)
        name_label.setFixedWidth(100)
        layout.addWidget(name_label)

        slider = PageScrollSlider(Qt.Orientation.Horizontal, card)
        slider.setRange(lo, hi)
        slider.setValue(default)
        layout.addWidget(slider)

        val_label = BodyLabel(str(default), card)
        val_label.setFixedWidth(40)
        val_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(val_label)

        slider.valueChanged.connect(lambda v: val_label.setText(str(v)))
        self.sliders[name] = (slider, val_label)

        _debounce_timer = QTimer(card)
        _debounce_timer.setSingleShot(True)
        _debounce_timer.setInterval(300)
        _debounce_timer.timeout.connect(lambda: self._set_screen_light_illumination(slider.value()))
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
        return card

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
        try:
            mode_int = int(mode)
        except Exception:
            mode_int = mode
        for m, btn in self.mode_btns.items():
            group = PICTURE_MODE_GROUPS.get(m, {m})
            self._highlight_btn(btn, mode_int in group or str(m) == str(mode))
        self._update_picture_mode_hint(mode_int)
        self._update_hdr_tone_mapping_visibility(mode_int)

    def _picture_mode_group_name(self, mode):
        for primary, name in ((14, "标准"), (10, "游戏"), (9, "电影")):
            if mode in PICTURE_MODE_GROUPS.get(primary, {primary}):
                return name
        return None

    def _active_picture_scene_mode(self):
        current_vals = getattr(self, "current_vals", {})
        for key in ("picture_preset_scenario", "picture_mode"):
            value = current_vals.get(key)
            try:
                return int(value)
            except Exception:
                pass
        return None

    def _update_hdr_tone_mapping_visibility(self, mode=None):
        card = getattr(self, "hdr_tone_mapping_card", None)
        if not card:
            return
        if mode is None:
            mode = App._active_picture_scene_mode(self)
        visible = is_hdr_tone_mapping_picture_mode(mode)
        card.setVisible(visible)

    def _update_picture_mode_hint(self, mode):
        label = getattr(self, "picture_mode_hint_label", None)
        if not label:
            return

        try:
            mode_int = int(mode)
        except Exception:
            mode_int = None

        if mode_int is None:
            label.setText(f"当前场景：未知（{mode}）")
            label.setStyleSheet("color: #f0b85a; font-size: 12px;")
            return

        group_name = self._picture_mode_group_name(mode_int)
        if group_name:
            label.setText(f"当前场景：{group_name}（{mode_int}）")
            label.setStyleSheet("color: rgba(255, 255, 255, 0.55); font-size: 12px;")
            return

        scene_name = PICTURE_SCENE_NAMES.get(mode_int, "未知场景")
        label.setText(f"当前场景：{scene_name}（{mode_int}），不匹配上方模式按钮")
        label.setStyleSheet("color: #f0b85a; font-size: 12px;")

    def _set_mode(self, val, name):
        if not self.check_connection(): return
        self._mark_adb_busy(3.0)
        self._picture_mode_switch_seq += 1
        seq = self._picture_mode_switch_seq
        previous = self._take_control_previous("picture_mode")

        def operation():
            self.adb.put("picture_mode", str(val), check=True)

        def success():
            self.current_vals["picture_mode"] = val
            self._highlight_mode(val)
            self.log(f"模式: {name}")
            self._page_loaded.discard("picturePage")
            QTimer.singleShot(1200, lambda seq=seq, val=val: self._refresh_picture_page_after_mode_switch(seq, val))

        def failure():
            if previous is not None:
                self.current_vals["picture_mode"] = previous
                self._highlight_mode(previous)

        self._run_adb_action("画面模式", operation, success, failure)

    def _refresh_picture_page_after_mode_switch(self, seq, expected_mode):
        if seq != getattr(self, "_picture_mode_switch_seq", 0):
            return
        if not getattr(self, "adb_connected", False):
            return
        if str(self.current_vals.get("picture_mode")) != str(expected_mode):
            return
        if "picturePage" in self._page_loading:
            QTimer.singleShot(500, lambda seq=seq, expected_mode=expected_mode: self._refresh_picture_page_after_mode_switch(seq, expected_mode))
            return
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
        accepted = w.exec()
        w.deleteLater()
        if accepted:
            self._mark_adb_busy(4.0)

            def operation():
                with self.adb.transaction():
                    self.adb.jni_set("g_fusion_picture__pic_reset_def_bypicmode", 0, check=True)
                    self.adb.refresh_pq(check=True)

            def success():
                self.log(f"已恢复 {mode_name} 模式默认设置，等待生效...")
                self._page_loaded.discard("picturePage")
                QTimer.singleShot(3000, lambda: self._refresh_page_data("picturePage"))

            self._run_adb_action("恢复画面默认设置", operation, success)

    def _optimistic_highlight(self, key, val):
        if key in self.state_buttons:
            for v, btn in self.state_buttons[key].items():
                self._highlight_btn(btn, str(v) == str(val))
        if key == "picture_color_temperature":
            self._update_color_gain_visibility(val)

    def _update_color_gain_visibility(self, color_temp=None):
        is_custom = str(color_temp if color_temp is not None else self.current_vals.get("picture_color_temperature")) == str(CUSTOM_COLOR_TEMP_VALUE)
        for card in getattr(self, "color_gain_cards", []):
            card.setVisible(is_custom)

    def _set(self, k, v, m):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous(k)

        def operation():
            with self.adb.transaction():
                if k == "mitv.tvplayer.hdmi.last.source":
                    self.adb.shell("am force-stop com.xiaomi.mitv.tvplayer", check=True)
                    self.adb.shell(f"am start -a com.xiaomi.mitv.tvplayer.EXTSRC_PLAY -n com.xiaomi.mitv.tvplayer/.ExternalSourceActivity --ei input {v} -f 0x10000000", check=True)
                else:
                    self.adb.put(k, str(v), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(m)
            self.current_vals[k] = v
            self._optimistic_highlight(k, v)
            if k == "mitv.tvplayer.hdmi.last.source":
                self.source_var_text = self._source_names.get(v, f"未知 ({v})")
                self.source_label.setText(self.source_var_text)

        def failure():
            if previous is not None:
                self._optimistic_highlight(k, previous)

        self._run_adb_action(m.split(":", 1)[0], operation, success, failure)

    def _jni(self, jk, v, sk, m, osd_sk=None):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous(sk)

        def operation():
            with self.adb.transaction():
                self.adb.jni_set(jk, v, check=True)
                self.adb.put(sk, str(v), check=True)
                if osd_sk:
                    self.adb.put(osd_sk, str(v), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(m)
            self.current_vals[sk] = v
            if osd_sk:
                self.current_vals[osd_sk] = v
            self._optimistic_highlight(sk, v)
            if sk == "picture_local_dimming":
                self._local_dimming_toggle_suspended = False
                self._local_dimming_memory_suppress_until = 0.0
                self._remember_local_dimming_value(v)

        def failure():
            if previous is not None:
                self.current_vals[sk] = previous
                self._optimistic_highlight(sk, previous)

        self._run_adb_action(m.split(":", 1)[0], operation, success, failure)

    def _set_hdr_tone_mapping(self, ui_value, name):
        if not self.check_connection():
            return
        mtk_value = HDR_TONE_MAPPING_UI_TO_MTK.get(ui_value)
        if mtk_value is None:
            return

        self._mark_adb_busy(2.5)
        state_key = "settings_display_hdr_color_tone"
        previous = self._take_control_previous(state_key)

        def operation():
            with self.adb.transaction():
                self.adb.check_and_heal_jar()
                self.adb.hdr_tone_mapping(mtk_value, check=True)
                self.adb.put("picture_hdr_tone_mapping", str(mtk_value), check=True)
                self.adb.put(state_key, str(ui_value), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(f"HDR 色调映射: {name}")
            self.current_vals["picture_hdr_tone_mapping"] = mtk_value
            self.current_vals[state_key] = ui_value
            self._optimistic_highlight(state_key, ui_value)

        def failure():
            if previous is not None:
                self.current_vals[state_key] = previous
                self._optimistic_highlight(state_key, previous)

        self._run_adb_action("HDR 色调映射", operation, success, failure)

    def _set_color_temp(self, jv, sv, m):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous("picture_color_temperature")

        def operation():
            with self.adb.transaction():
                self.adb.jni_set("g_video__clr_temp", jv, check=True)
                self.adb.put("picture_color_temperature", str(sv), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(m)
            self.current_vals["picture_color_temperature"] = sv
            self._optimistic_highlight("picture_color_temperature", sv)

        def failure():
            if previous is not None:
                self._optimistic_highlight("picture_color_temperature", previous)

        self._run_adb_action("色温", operation, success, failure)

    def _set_color_gain(self, title, settings_key, jni_key, value):
        if not self.check_connection():
            return
        self._mark_adb_busy(3.0)
        previous_temp = self.current_vals.get("picture_color_temperature")
        previous_values = {
            key: self.current_vals.get(key, 1024)
            for key in ("picture_red_gain", "picture_green_gain", "picture_blue_gain")
        }
        if str(self.current_vals.get("picture_color_temperature")) != str(CUSTOM_COLOR_TEMP_VALUE):
            self.current_vals["picture_color_temperature"] = CUSTOM_COLOR_TEMP_VALUE
            self._optimistic_highlight("picture_color_temperature", CUSTOM_COLOR_TEMP_VALUE)

        gain_controls = [
            ("picture_red_gain", "red_gain", "g_video__clr_gain_r"),
            ("picture_green_gain", "green_gain", "g_video__clr_gain_g"),
            ("picture_blue_gain", "blue_gain", "g_video__clr_gain_b"),
        ]
        values = {}
        for setting, slider_name, _jni_key in gain_controls:
            if setting == settings_key:
                gain = value
            elif setting in self.current_vals:
                gain = self.current_vals.get(setting, 1024)
            elif slider_name in self.sliders:
                gain = self.sliders[slider_name][0].value()
            else:
                gain = self.current_vals.get(setting, 1024)
            try:
                gain = int(gain)
            except Exception:
                gain = 1024
            values[setting] = max(524, min(1524, gain))
        self.current_vals.update(values)

        def operation():
            with self.adb.transaction():
                self.adb.jni_set("g_video__clr_temp", XIAOMI_TO_MTK_COLOR_TEMP[CUSTOM_COLOR_TEMP_VALUE], check=True)
                self.adb.put("picture_color_temperature", str(CUSTOM_COLOR_TEMP_VALUE), check=True)
                self.adb.jni_set_color_gains(
                    values["picture_red_gain"],
                    values["picture_green_gain"],
                    values["picture_blue_gain"],
                    check=True,
                )
                for setting, _slider_name, _gain_jni_key in gain_controls:
                    self.adb.put(setting, str(values[setting]), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(f"{title}: {value}")

        def failure():
            self.current_vals.update(previous_values)
            if previous_temp is not None:
                self.current_vals["picture_color_temperature"] = previous_temp
            restored = dict(previous_values)
            if previous_temp is not None:
                restored["picture_color_temperature"] = previous_temp
            self.values_signal.emit(restored)

        self._run_adb_action(title, operation, success, failure)

    def _fs(self, v):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous("front_sight_index")

        def operation():
            with self.adb.transaction():
                self.adb.put("front_sight_index", str(v), check=True)
                if self.adb.get("picture_mode", check=True) == "10":
                    self.adb.put("picture_mode", "14", check=True)
                    time.sleep(0.5)
                    self.adb.put("picture_mode", "10", check=True)

        def success():
            self.log(f"准星: {'关' if v==0 else v}")
            self.current_vals["front_sight_index"] = v
            self._optimistic_highlight("front_sight_index", v)

        def failure():
            if previous is not None:
                self._optimistic_highlight("front_sight_index", previous)

        self._run_adb_action("准星", operation, success, failure)

    def _get_input_source(self, check=False):
        """获取当前输入源"""
        return self.adb.get("mitv.tvplayer.hdmi.last.source", check=check)

    def _320(self, on):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous("mode_320")

        def operation():
            with self.adb.transaction():
                src = self._get_input_source(check=True)
                if src in ("29","30"): self.adb.jni_set("g_fusion_picture__dp_edid_version", 3 if on else 2, check=True)
                else: self.adb.jni_set("g_fusion_picture__hdmi_edid_version", 6 if on else 1, check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(f"320Hz: {'开' if on else '关'}")
            self.current_vals["mode_320"] = 1 if on else 0
            self._optimistic_highlight("mode_320", 1 if on else 0)
            # 切换 EDID 版本会连带影响刷新率 / FreeSync / 画面参数，延迟刷新画面页和游戏页以回读真实值
            self._refresh_pages(("picturePage", "gamePage"), delay_ms=1500, force=True)

        def failure():
            if previous is not None:
                self._optimistic_highlight("mode_320", previous)

        self._run_adb_action("320Hz", operation, success, failure)

    def _fsync(self, on):
        if not self.check_connection(): return
        self._mark_adb_busy(2.5)
        previous = self._take_control_previous("freesync")

        # FreeSync Pro 模式记忆：开启前记录画面模式，关闭时切回（开启后显示器会自动切到游戏模式）
        restore_mode = None
        if self._freesync_mode_memory_enabled():
            if on:
                # 仅在从关到开时记录，避免重复开启把记忆覆盖成游戏模式
                if self.current_vals.get("freesync") != 1:
                    self._remember_freesync_previous_mode()
            elif self.current_vals.get("freesync") == 1:
                restore_mode = self._get_freesync_memory_mode()

        def operation():
            with self.adb.transaction():
                src = self._get_input_source(check=True)
                if src in ("29","30"): self.adb.jni_set("g_video__dp_adaptive_sync", 1 if on else 0, check=True)
                else: self.adb.jni_set("g_video__freesync_switch", 3 if on else 0, check=True)
                # 关闭 FreeSync 且开启模式记忆：先切回开启前的画面模式，再刷新数据（切换早于刷新）
                if not on and restore_mode is not None:
                    self.adb.put("picture_mode", str(restore_mode), check=True)
                self.adb.refresh_pq(check=True)

        def success():
            self.log(f"FreeSync: {'开' if on else '关'}")
            self.current_vals["freesync"] = 1 if on else 0
            self._optimistic_highlight("freesync", 1 if on else 0)
            if not on and restore_mode is not None:
                self.current_vals["picture_mode"] = restore_mode
                self._highlight_mode(restore_mode)
                self.log(f"FreeSync 模式记忆: 已切回 {self._picture_mode_display_name(restore_mode)}")
            # FreeSync 改的是 adaptive sync / EDID 相关开关，开关后刷新画面页和游戏页以回读真实值
            self._refresh_pages(("picturePage", "gamePage"), delay_ms=1500, force=True)

        def failure():
            if previous is not None:
                self._optimistic_highlight("freesync", previous)

        self._run_adb_action("FreeSync", operation, success, failure)

    def _screen_light_int(self, key, default):
        try:
            return int(self.current_vals.get(key, default))
        except (TypeError, ValueError):
            return default

    def _commit_screen_light(self, message, updates):
        if not self.check_connection():
            return
        self._mark_adb_busy(2.0)
        previous = {key: self.current_vals.get(key) for key in updates}
        self.current_vals.update(updates)
        for key, val in updates.items():
            self._optimistic_highlight(key, val)

        mode = self._screen_light_int("atmosphere_light_switcher_pm2", 4)
        illumination = self._screen_light_int("atmosphere_light_illumination", 9)
        color_temp = self._screen_light_int("atmosphere_light_color_temp", 1)
        color_value = self._screen_light_int("atmosphere_light_color_value", 0)

        def operation():
            with self.adb.transaction():
                self.adb.put("atmosphere_light_switcher_pm2", str(mode), check=True)
                self.adb.put("atmosphere_light_illumination", str(illumination), check=True)
                self.adb.put("atmosphere_light_color_temp", str(color_temp), check=True)
                self.adb.put("atmosphere_light_color_value", str(color_value), check=True)
                if mode == 0:
                    self.adb.colorful_led("lighting", illumination, color_temp, check=True)
                elif mode == 1:
                    self.adb.colorful_led("ambient", check=True)
                elif mode == 2:
                    self.adb.colorful_led("solid", illumination, color_value, check=True)
                elif mode == 3:
                    self.adb.colorful_led("cycle", check=True)
                else:
                    self.adb.colorful_led("off", check=True)

        def success():
            self.log(message)

        def failure():
            restored = {key: val for key, val in previous.items() if val is not None}
            if restored:
                self.current_vals.update(restored)
                self.values_signal.emit(restored)

        self._run_adb_action("屏幕灯", operation, success, failure)

    def _set_screen_light_mode(self, val, name):
        self._commit_screen_light(f"屏幕灯模式: {name}", {"atmosphere_light_switcher_pm2": val})

    def _set_screen_light_illumination(self, ui_val):
        raw_val = max(0, min(14, int(ui_val) - 1))
        self._commit_screen_light(f"屏幕灯亮度挡位: {int(ui_val)}", {"atmosphere_light_illumination": raw_val})

    def _set_screen_light_color_temp(self, val, name):
        self._commit_screen_light(
            f"屏幕灯色温: {name}",
            {"atmosphere_light_switcher_pm2": 0, "atmosphere_light_color_temp": val}
        )

    def _set_screen_light_color_value(self, val, name):
        self._commit_screen_light(
            f"屏幕灯颜色: {name}",
            {"atmosphere_light_switcher_pm2": 2, "atmosphere_light_color_value": val}
        )

    def _key(self, kcode):
        if not self.check_connection(): return
        self._mark_adb_busy(1.0)

        def operation():
            self.adb.key(kcode, check=True)

        self._run_adb_action(
            "遥控器按键",
            operation,
            lambda: self.log(f"按键: {kcode}"),
        )

    def check_connection(self):
        if not getattr(self, "adb_connected", False):
            self.message_signal.emit("warn", "未连接显示器", "当前未连接到显示器，无法完成此操作！请先在主页建立连接。")
            return False
        return True

    def _run_adb_action(self, label, operation, on_success=None, on_failure=None):
        if getattr(self, "_cleanup_done", False):
            return
        context = {
            "label": label,
            "on_success": on_success,
            "on_failure": on_failure,
        }

        def do():
            try:
                if getattr(self, "adb_connected", False) and self.adb.ip:
                    ok, state = self.adb.ensure_connected()
                    if not ok:
                        self.status_signal.emit(disconnected_status_text(state))
                        raise RuntimeError(f"ADB 连接已断开（{adb_device_state_label(state)}）")
                operation()
                self.adb_action_finished.emit(context, True, "")
            except Exception as e:
                self.adb_action_finished.emit(context, False, str(e))

        async_run(do)

    def _finish_adb_action(self, context, ok, detail):
        if getattr(self, "_cleanup_done", False):
            return
        callback = context.get("on_success") if ok else context.get("on_failure")
        if callable(callback):
            callback()
        if ok:
            return
        label = context.get("label", "ADB 操作")
        message = f"{label}失败：{detail or '未知错误'}"
        self.log(message)
        if getattr(self, "osd", None):
            self.osd.show_hud(label, "失败")
        if getattr(self, "tray_icon", None):
            self.tray_icon.showMessage(
                "红米 G Pro 27U Toolbox",
                message,
                QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )

    def disconnect_adb(self):
        if self.adb.ip:
            self.log(f"正在断开与 {self.adb.ip} 的连接...")
            ip = self.adb.ip

            def do():
                adb_run(["disconnect", f"{ip}:5555"])
                self.adb.ip = ""
                self.status_signal.emit("未连接")
                self.log("连接已断开")
            async_run(do)

    # ===== ADB & Connect Slots =====
    def _auto_connect_on_startup(self):
        if getattr(self, "adb_connected", False):
            return

        saved_ip = load_settings().get("saved_ip", "").strip()
        if not saved_ip:
            self.log("启动自动连接: 未找到上次设备，开始扫描内网")
            self.auto_scan_signal.emit()
            return

        self.ip_entry.setText(saved_ip)
        self.adb.ip = saved_ip
        self.status_signal.emit("连接中...")
        self.log(f"启动自动连接: 尝试连接上次设备 {saved_ip}")

        def do():
            ok = self.adb.connect()
            if ok:
                self.status_signal.emit("已连接")
                self.log(f"启动自动连接成功: {self.adb.ip}")
                self.adb.check_and_heal_jar()
                m = self.adb.get_model().strip()
                if adb_text_has_disconnected_marker(m):
                    self.status_signal.emit(disconnected_status_text(m))
                    self.log(f"启动自动连接后设备状态异常: {m}")
                    return
                self.status_signal.emit(f"已连接: {m or self.adb.ip}")
            else:
                self.log(f"启动自动连接失败: {saved_ip}，开始扫描内网")
                self.status_signal.emit("未连接")
                self.auto_scan_signal.emit()

        async_run(do)

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
                update_settings({"saved_ip": ip})
                self.adb.check_and_heal_jar()
                m = self.adb.get_model().strip()
                if adb_text_has_disconnected_marker(m):
                    self.status_signal.emit(disconnected_status_text(m))
                    self.log(f"连接后设备状态异常: {m}")
                    return
                self.status_signal.emit(f"已连接: {m or self.adb.ip}")
            else:
                self.status_signal.emit("连接失败")
                self.message_signal.emit("error", "错误", "连接显示器失败，请检查IP和网络连接！")
                self.log("连接失败")
        async_run(do)

    def scan_net(self, auto=False):
        if getattr(self, "adb_connected", False):
            self.log("已连接显示器，跳过自动扫描" if auto else "已连接显示器，如需重新扫描请先断开连接")
            return
        self.status_signal.emit("扫描中...")
        self.dev_combo.clear()
        subnet = get_local_subnet()
        self.log(f"{'启动自动扫描' if auto else '扫描'} {subnet}.x ...")
        
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
        for i, (_ip, model) in enumerate(dev_list):
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
        shell_args = ["-s", f"{self.adb.ip}:5555", "shell"]
        shell_cmd = adb_command_text(shell_args)
        if sys.platform == "win32":
            subprocess.Popen(f"start cmd /k {shell_cmd}", shell=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{shell_cmd}"'])
        else:
            launched = False
            for term in ["x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal", "xterm"]:
                if subprocess.run(["which", term], capture_output=True).returncode == 0:
                    if term == "gnome-terminal":
                        subprocess.Popen([term, "--"] + adb_command(shell_args))
                    else:
                        subprocess.Popen([term, "-e", shell_cmd])
                    launched = True
                    break
            if not launched:
                self._show_message_box("error", "错误", f"未找到可用的终端模拟器，请手动在终端中运行: {shell_cmd}")

    def _guardian_shell(self, cmd):
        return self.adb.shell(cmd).strip().replace("\r", "")

    def _read_guardian_status(self):
        with self.adb.transaction():
            services = self._guardian_shell("settings get secure enabled_accessibility_services 2>/dev/null")
            user_state = self._guardian_shell(f'dumpsys package {GUARDIAN_PACKAGE} 2>/dev/null | grep "User 0:" | head -n 1')
            status = {
                "installed": bool(self._guardian_shell(f"pm path {GUARDIAN_PACKAGE} 2>/dev/null")),
                "pid": self._guardian_shell(f"pidof {GUARDIAN_PACKAGE} 2>/dev/null || true"),
                "mask": self._guardian_shell("getprop persist.appcontrol_w_mask"),
                "adb_enabled": self._guardian_shell("settings get global adb_enabled"),
                "adb_wifi_enabled": self._guardian_shell("settings get global adb_wifi_enabled"),
                "service_port": self._guardian_shell("getprop service.adb.tcp.port"),
                "persist_port": self._guardian_shell("getprop persist.adb.tcp.port"),
                "adbd": self._guardian_shell("getprop init.svc.adbd"),
                "accessibility": GUARDIAN_ACCESSIBILITY in services,
                "stopped": "stopped=false" in user_state,
            }
        status["ok"] = (
            status["installed"] and status["pid"] and
            status["adb_enabled"] == "1" and status["adb_wifi_enabled"] == "1" and
            status["service_port"] == "5555" and status["persist_port"] == "5555" and
            status["adbd"] == "running" and status["accessibility"] and
            status["mask"] == "-134250497" and status["stopped"]
        )
        return status

    def _apply_guardian_status(self, status):
        label = getattr(self, "guardian_status_label", None)
        if not label:
            return
        if "error" in status:
            label.setText(f"状态：检测失败 - {status['error']}")
            label.setStyleSheet("color: #ff6b5f; font-size: 13px;")
            return
        if status.get("deploying"):
            label.setText("状态：正在部署/修复...")
            label.setStyleSheet("color: #d89614; font-size: 13px;")
            return
        if status.get("checking"):
            label.setText("状态：正在检测...")
            label.setStyleSheet("color: #d89614; font-size: 13px;")
            return
        if status.get("starting"):
            label.setText("状态：正在启动保活...")
            label.setStyleSheet("color: #d89614; font-size: 13px;")
            return

        if status.get("ok"):
            text = "状态：正常运行，ADB 保活已启用"
            color = "#20c46b"
        elif not status.get("installed"):
            text = "状态：未安装 AdbGuardian"
            color = "#ff6b5f"
        else:
            missing = []
            if not status.get("pid"):
                missing.append("进程未运行")
            if not status.get("accessibility"):
                missing.append("辅助功能未启用")
            if status.get("mask") != "-134250497":
                missing.append("休眠保护未写入")
            if status.get("adb_enabled") != "1" or status.get("adb_wifi_enabled") != "1":
                missing.append("ADB 开关异常")
            if status.get("service_port") != "5555" or status.get("persist_port") != "5555":
                missing.append("端口异常")
            text = "状态：" + ("，".join(missing) if missing else "已安装，等待复检")
            color = "#d89614"
        label.setText(text)
        label.setStyleSheet(f"color: {color}; font-size: 13px;")

    def _check_guardian_status(self):
        if not self.check_connection():
            return
        self.guardian_status_signal.emit({"checking": True})

        def do():
            try:
                status = self._read_guardian_status()
                self.guardian_status_signal.emit(status)
                self.log("ADB 保活守护状态检测完成")
            except Exception as e:
                self.guardian_status_signal.emit({"error": str(e)})
                self.log(f"ADB 保活守护状态检测失败: {e}")

        async_run(do)

    def _enable_guardian_accessibility(self):
        with self.adb.transaction():
            current = self._guardian_shell("settings get secure enabled_accessibility_services 2>/dev/null")
            if current in ("", "null"):
                new_services = GUARDIAN_ACCESSIBILITY
            elif GUARDIAN_ACCESSIBILITY in current.split(":"):
                new_services = current
            else:
                new_services = f"{current}:{GUARDIAN_ACCESSIBILITY}"
            self.adb.shell(f"settings put secure enabled_accessibility_services '{new_services}'")
            self.adb.shell("settings put secure accessibility_enabled 1")

    def _start_guardian_commands(self):
        with self.adb.transaction():
            self.adb.shell(f"am start -n {GUARDIAN_MAIN_ACTIVITY} >/dev/null")
            self.adb.shell(f"am broadcast -a {GUARDIAN_PACKAGE}.ACTION_KEEP_ALIVE -p {GUARDIAN_PACKAGE} >/dev/null")

    def _start_guardian(self):
        if not self.check_connection():
            return
        self.guardian_status_signal.emit({"starting": True})

        def do():
            try:
                self._enable_guardian_accessibility()
                self._start_guardian_commands()
                time.sleep(2)
                self.guardian_status_signal.emit(self._read_guardian_status())
                self.log("ADB 保活守护已启动")
            except Exception as e:
                self.guardian_status_signal.emit({"error": str(e)})
                self.log(f"启动 ADB 保活守护失败: {e}")

        async_run(do)

    def _deploy_guardian(self):
        if not self.check_connection():
            return
        apk_path = get_guardian_apk_path()
        if not os.path.exists(apk_path):
            self._show_message_box("error", "缺少 APK", f"找不到保活 APK：{apk_path}")
            return
        self.guardian_status_signal.emit({"deploying": True})
        self.log("正在部署 ADB 保活守护...")

        def do():
            try:
                with self.adb.transaction():
                    serial = f"{self.adb.ip}:5555"
                    r = adb_run(["-s", serial, "install", "-r", "-d", apk_path], timeout=90)
                    if "Success" not in r:
                        raise RuntimeError(r or "adb install 没有返回成功")
                    self.adb.shell(f"pm grant {GUARDIAN_PACKAGE} android.permission.WRITE_SECURE_SETTINGS 2>/dev/null || true")
                    self.adb.shell(f"cmd deviceidle whitelist +{GUARDIAN_PACKAGE} 2>/dev/null || true")
                    self._enable_guardian_accessibility()
                    self._start_guardian_commands()
                    time.sleep(3)
                    adb_run(["connect", serial], timeout=5)
                    status = self._read_guardian_status()
                self.guardian_status_signal.emit(status)
                if status.get("ok"):
                    self.message_signal.emit("info", "部署完成", "ADB 保活守护已部署并正常运行。")
                else:
                    self.message_signal.emit("warn", "部署完成", "ADB 保活守护已部署，但部分状态仍需复检。")
                self.log("ADB 保活守护部署完成")
            except Exception as e:
                self.guardian_status_signal.emit({"error": str(e)})
                self.message_signal.emit("error", "部署失败", f"ADB 保活守护部署失败: {e}")
                self.log(f"ADB 保活守护部署失败: {e}")

        async_run(do)

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

    def _force_slider_handle_update(self, slider):
        adjust_handle = getattr(slider, "_adjustHandlePos", None)
        if callable(adjust_handle):
            adjust_handle()
        handle = getattr(slider, "handle", None)
        if handle:
            handle.update()
            handle.repaint()
        slider.update()
        slider.repaint()

    def _sync_slider_value(self, slider, label_widget, value):
        value = max(slider.minimum(), min(slider.maximum(), int(value)))
        was_blocked = slider.blockSignals(True)
        try:
            if slider.isSliderDown():
                slider.setSliderDown(False)
            slider.setValue(value)
            slider.setSliderPosition(value)
        finally:
            slider.blockSignals(was_blocked)
        label_widget.setText(str(value))
        self._force_slider_handle_update(slider)
        QTimer.singleShot(0, lambda s=slider: self._force_slider_handle_update(s))
        QTimer.singleShot(80, lambda s=slider: self._force_slider_handle_update(s))

    def _apply_polled_values(self, vals):
        # settings 中的 picture_color_temperature 已经是小米层枚举值；
        # 只有 JNI 的 g_video__clr_temp 读数才需要从 MTK 枚举转换。
        self.current_vals.update(vals)
        slider_mappings = {
            "picture_backlight": "backlight",
            "xiaomi_picture_backlight": "backlight",
            "picture_brightness": "black_level",
            "picture_contrast": "contrast",
            "picture_saturation": "saturation",
            "picture_hue": "hue",
            "picture_sharpness": "sharpness",
            "picture_red_gain": "red_gain",
            "picture_green_gain": "green_gain",
            "picture_blue_gain": "blue_gain",
            "atmosphere_light_illumination": "atmosphere_illumination",
        }
        for k, name in slider_mappings.items():
            if k in vals and name in self.sliders:
                val = vals[k]
                if isinstance(val, int):
                    display_val = val + 1 if k == "atmosphere_light_illumination" else val
                    slider, label_widget = self.sliders[name]
                    if not slider.isSliderDown():
                        self._sync_slider_value(slider, label_widget, display_val)

        for key, btn_map in self.state_buttons.items():
            if key in vals:
                active_val = vals[key]
                for val, btn in btn_map.items():
                    self._highlight_btn(btn, str(active_val) == str(val))
                if key == "picture_color_temperature":
                    self._update_color_gain_visibility(active_val)
                        
        if "picture_preset_scenario" in vals:
            self._highlight_mode(vals["picture_preset_scenario"])
        elif "picture_mode" in vals:
            self._highlight_mode(vals["picture_mode"])

        if "mitv.tvplayer.hdmi.last.source" in vals:
            sid = vals["mitv.tvplayer.hdmi.last.source"]
            self.source_var_text = self._source_names.get(sid, f"未知 ({sid})")
            self.source_label.setText(self.source_var_text)

    def _apply_polled_jni_values(self, vals):
        self.current_vals.update(vals)
        if "g_disp__disp_back_light" in vals and "backlight" in self.sliders:
            val = vals["g_disp__disp_back_light"]
            slider, label_widget = self.sliders["backlight"]
            if not slider.isSliderDown():
                self._sync_slider_value(slider, label_widget, val)

        for key in ("mode_320", "freesync"):
            if key in vals and key in self.state_buttons:
                active_val = vals[key]
                for val, btn in self.state_buttons[key].items():
                    self._highlight_btn(btn, str(active_val) == str(val))

    # ===== 页面按需加载 =====

    _PAGES_NEED_CONNECTION = {"picturePage", "gamePage", "sourcePage", "lightPage", "remotePage"}

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
        refresh_seq = getattr(self, "_picture_mode_switch_seq", 0) if page_name == "picturePage" else None
        self._page_loading.add(page_name)
        self._mark_adb_busy(4.0)
        self._show_loading_overlay(page_name)
        self.log(f"正在刷新 {page_name} 数据...")

        def do():
            loaded = False
            try:
                cfg = self._page_data_keys[page_name]
                settings_vals = {}
                jni_vals = {}

                # 页面中的多项读取必须来自同一设备快照，避免写操作穿插后混合新旧值。
                with self.adb.transaction():
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
                                        except (TypeError, ValueError): settings_vals[k] = v

                    jni_batch_vals = {}
                    jni_keys = cfg.get("jni", [])
                    if jni_keys:
                        jni_batch_vals = self.adb.jni_batch_get_or_single(jni_keys)

                    # 读取 JNI 背光
                    if "g_disp__disp_back_light" in jni_batch_vals:
                        jni_vals["g_disp__disp_back_light"] = jni_batch_vals["g_disp__disp_back_light"]

                    # 读取 JNI 色域 (覆盖 settings 中的主键和旧 OSD 键)
                    if "g_video__vid_gamut_mapping_mode" in jni_batch_vals:
                        try:
                            gamut_val = int(jni_batch_vals["g_video__vid_gamut_mapping_mode"])
                            # MTK 值和 settings 值一致，直接覆盖
                            settings_vals["tv_picture_advanced_video_color_space"] = gamut_val
                            settings_vals["tv_picture_video_color_space"] = gamut_val
                        except (TypeError, ValueError): pass

                    # 读取 JNI 色温 (MTK 值需转换为小米 settings 枚举值)
                    if "g_video__clr_temp" in jni_batch_vals:
                        try:
                            clr_val = int(jni_batch_vals["g_video__clr_temp"])
                            if clr_val in MTK_TO_XIAOMI_COLOR_TEMP:
                                settings_vals["picture_color_temperature"] = MTK_TO_XIAOMI_COLOR_TEMP[clr_val]
                        except (TypeError, ValueError): pass

                    # 读取 JNI 控光 (覆盖官方主键和旧 OSD 键)
                    if "g_video__vid_local_dimming" in jni_batch_vals:
                        try:
                            dim_val = int(jni_batch_vals["g_video__vid_local_dimming"])
                            settings_vals["picture_local_dimming"] = dim_val
                            settings_vals["tv_picture_video_local_dimming"] = dim_val
                        except (TypeError, ValueError): pass

                    # HDR 色调映射的 OSD 索引与 MTK 底层枚举不同。
                    if "g_video__vid_hdr_tone_mapping_mode" in jni_batch_vals:
                        try:
                            tone_val = int(jni_batch_vals["g_video__vid_hdr_tone_mapping_mode"])
                            ui_val = HDR_TONE_MAPPING_MTK_TO_UI.get(tone_val)
                            settings_vals["picture_hdr_tone_mapping"] = tone_val
                            if ui_val is not None:
                                settings_vals["settings_display_hdr_color_tone"] = ui_val
                        except (TypeError, ValueError): pass

                    # 读取 JNI 模式 (game page)
                    if cfg.get("jni_mode"):
                        src = settings_vals.get("mitv.tvplayer.hdmi.last.source")
                        if src in (29, 30):
                            mode_vals = self.adb.jni_batch_get_or_single([
                                "g_fusion_picture__dp_edid_version",
                                "g_video__dp_adaptive_sync",
                            ])
                            try:
                                jni_vals["mode_320"] = (
                                    1 if int(mode_vals.get("g_fusion_picture__dp_edid_version")) == 3 else 0
                                )
                            except (TypeError, ValueError): pass
                            try:
                                jni_vals["freesync"] = (
                                    1 if int(mode_vals.get("g_video__dp_adaptive_sync")) == 1 else 0
                                )
                            except (TypeError, ValueError): pass
                        else:
                            mode_vals = self.adb.jni_batch_get_or_single([
                                "g_fusion_picture__hdmi_edid_version",
                                "g_video__freesync_switch",
                            ])
                            try:
                                jni_vals["mode_320"] = (
                                    1 if int(mode_vals.get("g_fusion_picture__hdmi_edid_version")) == 6 else 0
                                )
                            except (TypeError, ValueError): pass
                            try:
                                jni_vals["freesync"] = (
                                    1 if int(mode_vals.get("g_video__freesync_switch")) == 3 else 0
                                )
                            except (TypeError, ValueError): pass

                if page_name == "picturePage" and refresh_seq != getattr(self, "_picture_mode_switch_seq", 0):
                    self.log("已丢弃过期的画面设置刷新结果")
                    return

                # 应用数据到 UI
                if settings_vals:
                    self.values_signal.emit(settings_vals)
                if jni_vals:
                    self.jni_values_signal.emit(jni_vals)

                loaded = True
                self.log("页面数据刷新完成")
            except Exception as e:
                self.log(f"页面数据刷新失败: {e}")
                print(f"Page data refresh error: {e}")
            finally:
                self.page_refresh_finished.emit(page_name, loaded)

        async_run(do)

    def _finish_page_refresh(self, page_name, loaded):
        if loaded:
            self._page_loaded.add(page_name)
        self._page_loading.discard(page_name)
        self._hide_loading_overlay(page_name)

    def _show_loading_overlay(self, page_name):
        pages = {
            "picturePage": self.picture_page,
            "gamePage": self.game_page,
            "sourcePage": self.source_page,
        }
        page = pages.get(page_name)
        if not page:
            return
        # 先清理已有的 overlay，防止重复创建导致泄漏
        for child in page.findChildren(QWidget):
            if child.objectName() == "_loading_overlay":
                child.deleteLater()
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

    def _force_refresh_page(self, page_name):
        self._page_loaded.discard(page_name)
        self._refresh_page_data(page_name)

    def _refresh_pages(self, page_names, delay_ms=0, force=False):
        """刷新多个页面数据。

        force=True：立即清缓存并强制回读（用于设置改动后同步真实值，
        立即清缓存可避免延迟窗口内切过去看到旧值）；
        force=False：只加载尚未加载 / 未在加载中的页面（用于连接后预加载，
        不会与当前页已触发的加载重复）。
        """
        if force:
            for page_name in page_names:
                self._page_loaded.discard(page_name)

        def do_refresh():
            if not getattr(self, "adb_connected", False):
                return
            for page_name in page_names:
                if force:
                    self._force_refresh_page(page_name)
                elif page_name not in self._page_loaded and page_name not in self._page_loading:
                    self._refresh_page_data(page_name)

        if delay_ms > 0:
            QTimer.singleShot(delay_ms, do_refresh)
        else:
            do_refresh()


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
            client_socket.deleteLater()
            
    local_server.newConnection.connect(on_new_connection)

    if "--minimized" not in sys.argv:
        w.show()
    sys.exit(app.exec())
