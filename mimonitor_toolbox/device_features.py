"""设备连接、扫描、保活、Guardian 和页面数据加载功能。"""

import os
import subprocess
import sys
import threading
import time

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QFileDialog, QSystemTrayIcon, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel

from .adb import (
    ADB,
    ADB_SERVER_PORT,
    Adb,
    adb_command,
    adb_command_text,
    adb_device_state,
    adb_device_state_label,
    adb_run,
    adb_text_has_disconnected_marker,
    async_run,
    disconnected_status_text,
    is_adb_server_alive,
    is_mitv_model,
    scan_adb,
)
from .core import (
    GAME_FEATURE_KEYS,
    GUARDIAN_ACCESSIBILITY,
    GUARDIAN_MAIN_ACTIVITY,
    GUARDIAN_PACKAGE,
    HDR_TONE_MAPPING_MTK_TO_UI,
    MTK_TO_XIAOMI_COLOR_TEMP,
    get_guardian_apk_path,
    load_settings,
    update_settings,
)
from .network_scan import WindowsAdapterError
from .widgets import InstallProgressDialog, OverlayResizeFilter

_global_overlay_filter = None
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)


class DeviceFeaturesMixin:
    def initialize_device_features(self):
        """初始化设备连接、扫描和页面加载所需的共享状态。"""
        self.adb = Adb()
        self._source_names = {
            23: "HDMI 1", 24: "HDMI 2", 29: "DP", 30: "USBC",
            "23": "HDMI 1", "24": "HDMI 2", "29": "DP", "30": "USBC",
        }
        self.source_var_text = "未知"
        self._page_loaded = set()
        self._page_loading = set()
        self._page_data_keys = {
            "picturePage": {
                "settings": [
                    "picture_mode", "picture_backlight", "xiaomi_picture_backlight",
                    "picture_preset_scenario", "picture_brightness", "picture_contrast",
                    "picture_saturation", "picture_hue", "picture_sharpness",
                    "picture_color_temperature", "picture_red_gain", "picture_green_gain",
                    "picture_blue_gain", "picture_local_dimming",
                    "tv_picture_video_local_dimming", "picture_hdr_tone_mapping",
                    "settings_display_hdr_color_tone", "picture_dynamic_definition",
                    "picture_response_time", "tv_picture_advanced_video_color_space",
                    "tv_picture_video_color_space",
                ],
                "jni": [
                    "g_disp__disp_back_light", "g_video__vid_gamut_mapping_mode",
                    "g_video__clr_temp", "g_video__vid_local_dimming",
                    "g_video__vid_hdr_tone_mapping_mode",
                ],
            },
            "gamePage": {
                "settings": [
                    "picture_mode", "picture_preset_scenario",
                    "front_sight_index", "mt_game_dynamic_ft", "mt_game_scope",
                    "mt_game_scope_night", "monitor_menu_fps_counter",
                    "monitor_menu_stopwatch", "monitor_menu_timer",
                    "mitv.tvplayer.hdmi.last.source",
                ],
                "jni_mode": True,
            },
            "sourcePage": {"settings": ["mitv.tvplayer.hdmi.last.source"]},
            "lightPage": {
                "settings": [
                    "atmosphere_light_switcher_pm2", "atmosphere_light_illumination",
                    "atmosphere_light_color_temp", "atmosphere_light_color_value",
                ],
            },
        }
        self.adb_connected = False
        self._scan_id = 0
        self._scan_running = False
        self._scan_cancel_event = None
        self._connection_in_progress = False
        self._adb_busy_until = 0.0
        self._adb_keepalive_checking = False
        self._adb_server_monitor_checking = False
        self._adb_server_down_notified = False
        self._adb_server_failure_notified = False
        self._adb_server_retry_after = 0.0

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
        self._connection_in_progress = True
        self.status_signal.emit("连接中...")
        self.log(f"启动自动连接: 尝试连接上次设备 {saved_ip}")

        def do():
            try:
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
            finally:
                self._connection_in_progress = False

        async_run(do)

    def connect(self):
        if getattr(self, "_connection_in_progress", False):
            self.log("设备连接已在进行中，忽略重复请求")
            return
        ip = self.ip_entry.text().strip()
        if not ip:
            return
        self._cancel_scan("手动连接")
        self.adb.ip = ip
        self._connection_in_progress = True
        self.status_signal.emit("连接中...")
        def do():
            try:
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
            finally:
                self._connection_in_progress = False
        async_run(do)

    def _cancel_scan(self, reason):
        if not getattr(self, "_scan_running", False):
            return False
        event = getattr(self, "_scan_cancel_event", None)
        if event is not None:
            event.set()
        self._scan_id = getattr(self, "_scan_id", 0) + 1
        self._scan_running = False
        self._scan_cancel_event = None
        button = getattr(self, "scan_btn", None)
        if button is not None:
            button.setEnabled(True)
        self.log(f"取消内网扫描: {reason}")
        return True

    def scan_net(self, auto=False):
        if getattr(self, "adb_connected", False):
            self.log("已连接显示器，跳过自动扫描" if auto else "已连接显示器，如需重新扫描请先断开连接")
            return
        if getattr(self, "_connection_in_progress", False):
            self.log("设备正在连接，跳过内网扫描")
            return
        if getattr(self, "_scan_running", False):
            self.log("内网扫描已在进行中，忽略重复请求")
            return

        self._scan_id = getattr(self, "_scan_id", 0) + 1
        scan_id = self._scan_id
        cancel_event = threading.Event()
        self._scan_cancel_event = cancel_event
        self._scan_running = True
        self.scan_btn.setEnabled(False)
        self.status_signal.emit("扫描中...")
        self.dev_combo.clear()
        self.log("启动自动扫描物理局域网" if auto else "扫描全部物理局域网")

        found_devices = []
        def do():
            outcome = "completed"
            detail = ""
            def cb(ip, model):
                if cancel_event.is_set():
                    return
                found_devices.append((ip, model))
                self.devices_signal.emit(scan_id, list(found_devices))

            try:
                scan_adb(cb=cb, log=self.log, cancel_event=cancel_event)
                if cancel_event.is_set():
                    outcome = "cancelled"
            except WindowsAdapterError as exc:
                outcome = "failed"
                detail = str(exc)
            except Exception as exc:
                outcome = "failed"
                detail = f"扫描异常: {exc}"
                self.log(detail)
            self.scan_finished_signal.emit(
                scan_id, outcome, list(found_devices), detail
            )
        async_run(do)

    def _finish_scan(self, scan_id, outcome, dev_list, detail):
        if scan_id != getattr(self, "_scan_id", -1):
            return
        self._scan_running = False
        self._scan_cancel_event = None
        button = getattr(self, "scan_btn", None)
        if button is not None:
            button.setEnabled(True)

        if outcome == "cancelled":
            self.log("内网扫描已取消")
            return
        if outcome == "failed":
            message = detail or "未知错误"
            self.log(f"内网扫描失败: {message}")
            self.status_signal.emit(f"扫描失败: {message}")
            return

        self._update_scanned_devices(scan_id, dev_list)
        self.status_signal.emit(f"扫描完成: {len(dev_list)}台")
        if getattr(self, "adb_connected", False):
            return
        for index, (_ip, model) in enumerate(dev_list):
            if is_mitv_model(model):
                self._on_dev_sel(index)
                break

    def _update_scanned_devices(self, scan_id, dev_list):
        if scan_id != getattr(self, "_scan_id", -1):
            return
        # Temporarily block signals during combobox population to prevent autoconnect loop
        self.dev_combo.blockSignals(True)
        self.dev_combo.clear()
        for ip, model in dev_list:
            self.dev_combo.addItem(f"{model} ({ip})")
        preferred_index = 0
        for i, (_ip, model) in enumerate(dev_list):
            if is_mitv_model(model):
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

    def _open_adb_cmd(self):
        if sys.platform != "win32":
            self._show_message_box("error", "错误", "ADB CMD 仅支持 Windows。")
            return
        self.log("正在打开 ADB CMD...")
        adb_path = os.path.abspath(ADB)
        command = (
            "title Mimonitor ADB CMD & "
            f"doskey adb=adb.exe -P {ADB_SERVER_PORT} $*"
        )
        try:
            subprocess.Popen(
                ["cmd.exe", "/k", command],
                cwd=os.path.dirname(adb_path),
                creationflags=CREATE_NEW_CONSOLE,
            )
        except OSError as exc:
            self._show_message_box("error", "错误", f"无法打开 ADB CMD：{exc}")

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

        update_game_hint = getattr(self, "_update_game_mode_hint", None)
        if callable(update_game_hint):
            update_game_hint()

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

                    if page_name == "gamePage":
                        for key in GAME_FEATURE_KEYS:
                            settings_vals.setdefault(key, 0)

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
        
        # Centering using layout
        lay = QVBoxLayout(overlay)
        label = BodyLabel("正在刷新数据...", overlay)
        label.setStyleSheet("color: white; font-size: 16px; background: transparent;")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(label, 0, Qt.AlignmentFlag.AlignCenter)
        
        global _global_overlay_filter
        if _global_overlay_filter is None:
            _global_overlay_filter = OverlayResizeFilter()
        page.installEventFilter(_global_overlay_filter)
        
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
