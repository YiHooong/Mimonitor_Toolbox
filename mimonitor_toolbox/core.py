"""配置、资源定位和跨功能常量。"""

import json
import os
import sys
import tempfile
import threading

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
def is_frozen_build():
    """打包态检测：PyInstaller 设置 sys.frozen，Nuitka 注入 __compiled__。"""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()

def get_app_base_dir():
    if is_frozen_build():
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def bundled_resource_path(*parts):
    if hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, *parts)
        if os.path.exists(p):
            return p
    if "__compiled__" in globals():
        # Nuitka 打包：__file__ 位于解包目录的 mimonitor_toolbox/ 包内
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), *parts)
        if os.path.exists(p):
            return p
    p = os.path.join(get_app_base_dir(), *parts)
    if os.path.exists(p):
        return p
    return None
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
GAME_FEATURE_KEYS = (
    "front_sight_index",
    "mt_game_dynamic_ft",
    "mt_game_scope",
    "mt_game_scope_night",
)
GAME_PICTURE_MODES = frozenset(PICTURE_MODE_GROUPS[10]) | {4, 15, 19}


def is_game_picture_mode(value):
    try:
        return int(value) in GAME_PICTURE_MODES
    except (TypeError, ValueError):
        return False


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
