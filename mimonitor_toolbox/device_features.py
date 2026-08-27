"""设备连接、扫描、保活、Guardian 和页面数据加载功能。"""

import os
import subprocess
import sys
import threading
import time

from PyQt6.QtCore import QObject, QTimer, Qt
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
    format_adb_serial,
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
from .network_scan import (
    IF_TYPE_ETHERNET_CSMACD,
    IF_TYPE_IEEE80211,
    WindowsAdapterError,
    enumerate_windows_adapter_addresses,
    is_tcp_endpoint_open,
)
from .widgets import InstallProgressDialog, OverlayResizeFilter

_global_overlay_filter = None
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
RESUME_DISCOVERY_INTERVAL_SECONDS = 30.0


class DeviceFeaturesMixin:
    def initialize_device_features(self):
        """初始化设备连接、扫描和页面加载所需的共享状态。"""
        self.adb = Adb("")
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
        self._connection_intent_generation = 0
        self._resume_reconnect_generation = 0
        self._resume_reconnect_attempt = 0
        self._resume_reconnect_active = False
        self._resume_reconnect_checking = False
        self._resume_waiting_for_network = False
        self._resume_network_signature = None
        self._resume_target_probe_checking = False
        self._resume_target_available = None
        self._resume_discovery_checking = False
        self._resume_discovery_cancel_event = None
        self._resume_next_discovery_at = 0.0
        self._resume_manual_selection_required = False
        self._last_windows_resume_at = None
        timer_parent = self if isinstance(self, QObject) else None
        self._resume_retry_timer = QTimer(timer_parent)
        self._resume_retry_timer.setSingleShot(True)
        self._resume_retry_timer.setInterval(3000)
        self._resume_retry_timer.timeout.connect(
            lambda: DeviceFeaturesMixin._run_resume_reconnect_attempt(self)
        )
        self._resume_network_timer = QTimer(timer_parent)
        self._resume_network_timer.setInterval(2000)
        self._resume_network_timer.timeout.connect(
            lambda: DeviceFeaturesMixin._check_resume_network_change(self)
        )

    def _invalidate_connection_intent(self):
        self._connection_intent_generation = (
            getattr(self, "_connection_intent_generation", 0) + 1
        )
        return self._connection_intent_generation

    def _connection_intent_is_current(self, generation, ip):
        return (
            generation == getattr(self, "_connection_intent_generation", 0)
            and str(getattr(self.adb, "ip", "") or "").strip() == ip
            and not getattr(self, "_cleanup_done", False)
            and not getattr(self, "_windows_session_ending", False)
        )

    def _network_signature(self):
        try:
            records = enumerate_windows_adapter_addresses()
        except WindowsAdapterError:
            return None
        except Exception as exc:
            self.log(f"读取 Windows 网卡状态失败: {exc}")
            return None
        signature = [
            (
                record.interface_index,
                record.interface_name,
                str(record.local_ip),
                record.prefix_length,
                record.metric,
                record.if_type,
                record.oper_status,
                record.media_connected,
            )
            for record in records
            if record.hardware_interface
            and not record.filter_interface
            and not record.endpoint_interface
            and record.if_type in (IF_TYPE_ETHERNET_CSMACD, IF_TYPE_IEEE80211)
        ]
        return tuple(sorted(signature))

    def _handle_windows_resume(self):
        now = time.monotonic()
        last_resume = getattr(self, "_last_windows_resume_at", None)
        if last_resume is not None and now - last_resume < 2.0:
            return
        self._last_windows_resume_at = now
        self._start_resume_reconnect("Windows 唤醒")

    def _handle_connection_recovery_request(self, generation, ip, reason):
        if not self._connection_intent_is_current(generation, ip):
            return
        self._start_resume_reconnect(reason)

    def _start_resume_reconnect(self, reason):
        if getattr(self, "_cleanup_done", False) or getattr(self, "_windows_session_ending", False):
            return
        ip = str(getattr(self.adb, "ip", "") or "").strip()
        if not ip:
            return
        if getattr(self, "_resume_reconnect_active", False):
            self.log(f"{reason}: 重连已在进行中")
            return

        discovery_cancel = getattr(self, "_resume_discovery_cancel_event", None)
        if discovery_cancel is not None:
            discovery_cancel.set()

        self._resume_reconnect_generation += 1
        self._resume_reconnect_attempt = 0
        self._resume_reconnect_active = True
        self._resume_reconnect_checking = False
        self._resume_waiting_for_network = False
        self._resume_network_signature = None
        self._resume_target_probe_checking = False
        self._resume_discovery_checking = False
        self._resume_discovery_cancel_event = None
        self._resume_retry_timer.stop()
        self._resume_network_timer.stop()
        self.log(f"{reason}: 开始检查并恢复显示器连接")
        self._run_resume_reconnect_attempt()

    def _run_resume_reconnect_attempt(self):
        if not getattr(self, "_resume_reconnect_active", False):
            return
        if getattr(self, "_cleanup_done", False) or getattr(self, "_windows_session_ending", False):
            self._cancel_resume_reconnect()
            return
        if getattr(self, "_resume_reconnect_checking", False):
            return
        ip = str(getattr(self.adb, "ip", "") or "").strip()
        if not ip:
            self._cancel_resume_reconnect()
            return

        self._resume_reconnect_attempt += 1
        attempt = self._resume_reconnect_attempt
        generation = self._resume_reconnect_generation
        intent_generation = getattr(self, "_connection_intent_generation", 0)
        self._resume_reconnect_checking = True
        self.status_signal.emit(f"连接中...（唤醒后重连，第 {attempt}/5 次）")

        def do():
            ok = False
            detail = "unknown"
            try:
                with self.adb.transaction():
                    if not self._connection_intent_is_current(intent_generation, ip):
                        detail = "连接请求已取消"
                        return
                    serial = format_adb_serial(ip)
                    state = adb_device_state(serial, timeout=3)
                    if state != "device":
                        if not self._connection_intent_is_current(intent_generation, ip):
                            detail = "连接请求已取消"
                            return
                        adb_run(["disconnect", serial], timeout=3)
                        if not self._connection_intent_is_current(intent_generation, ip):
                            detail = "连接请求已取消"
                            return
                        adb_run(["connect", serial], timeout=5)
                        state = adb_device_state(serial, timeout=3)
                    if state == "device":
                        if not self._connection_intent_is_current(intent_generation, ip):
                            detail = "连接请求已取消"
                            return
                        model = adb_run(
                            ["-s", serial, "shell", "getprop ro.product.model"],
                            timeout=3,
                        ).strip()
                        if adb_text_has_disconnected_marker(model):
                            detail = model
                        else:
                            ok = True
                            detail = model or ip
                    else:
                        detail = state
            except Exception as exc:
                detail = str(exc) or "unknown"
            finally:
                self.resume_reconnect_finished.emit(generation, attempt, ok, detail)

        async_run(do)

    def _finish_resume_reconnect_attempt(self, generation, attempt, ok, detail):
        if generation != getattr(self, "_resume_reconnect_generation", -1):
            return
        self._resume_reconnect_checking = False
        if ok:
            self._resume_reconnect_active = False
            self._resume_waiting_for_network = False
            self._resume_manual_selection_required = False
            self._resume_retry_timer.stop()
            self._resume_network_timer.stop()
            self.status_signal.emit(f"已连接: {detail}")
            self.log(f"唤醒后连接已恢复: {detail}")
            return

        if attempt < 5:
            self.log(f"唤醒后第 {attempt}/5 次重连失败: {detail}，3 秒后重试")
            self._resume_retry_timer.start(3000)
            return

        self._resume_reconnect_active = False
        self._resume_waiting_for_network = True
        self._resume_network_signature = self._network_signature()
        self.status_signal.emit("未连接（重连 5 次失败，等待显示器或网络恢复）")
        self.log("重连 5 次仍失败，等待显示器 ADB 端口或 Windows 网络恢复")
        self._resume_network_timer.start(2000)
        self._start_resume_discovery()

    def _start_resume_discovery(self):
        if not getattr(self, "_resume_waiting_for_network", False):
            return
        if getattr(self, "_resume_discovery_checking", False):
            return
        if getattr(self, "_resume_manual_selection_required", False):
            return
        if getattr(self, "_scan_running", False):
            self._resume_next_discovery_at = (
                time.monotonic() + RESUME_DISCOVERY_INTERVAL_SECONDS
            )
            return
        if getattr(self, "_connection_in_progress", False):
            return
        if getattr(self, "_cleanup_done", False) or getattr(self, "_windows_session_ending", False):
            return
        next_discovery_at = getattr(self, "_resume_next_discovery_at", 0.0)
        if next_discovery_at > 0 and time.monotonic() < next_discovery_at:
            return

        generation = self._resume_reconnect_generation
        cancel_event = threading.Event()
        self._resume_discovery_checking = True
        self._resume_discovery_cancel_event = cancel_event
        self.log("旧 IP 重连失败，扫描物理局域网查找显示器的新地址")

        def do():
            devices = []
            detail = ""
            try:
                devices = scan_adb(log=self.log, cancel_event=cancel_event)
            except Exception as exc:
                detail = str(exc) or "扫描异常"
            finally:
                self.resume_discovery_finished.emit(generation, devices, detail)

        async_run(do)

    def _finish_resume_discovery(self, generation, devices, detail):
        if generation != getattr(self, "_resume_reconnect_generation", -1):
            return
        self._resume_discovery_checking = False
        self._resume_discovery_cancel_event = None
        if not getattr(self, "_resume_waiting_for_network", False):
            return
        if getattr(self, "_resume_manual_selection_required", False):
            return
        self._resume_next_discovery_at = (
            time.monotonic() + RESUME_DISCOVERY_INTERVAL_SECONDS
        )
        if detail:
            self.log(f"显示器地址扫描失败: {detail}")
            return

        mitv_devices = [
            (str(ip), model)
            for ip, model in devices
            if is_mitv_model(model)
        ]
        if len(mitv_devices) > 1:
            self._resume_manual_selection_required = True
            self._resume_next_discovery_at = 0.0
            self.devices_signal.emit(
                getattr(self, "_scan_id", 0),
                mitv_devices,
            )
            message = f"局域网发现 {len(mitv_devices)} 台显示器，请手动选择"
            self.status_signal.emit(message)
            self.log(message)
            return
        if not mitv_devices:
            return

        new_ip, model = mitv_devices[0]
        old_ip = str(getattr(self.adb, "ip", "") or "").strip()
        if new_ip != old_ip:
            self._invalidate_connection_intent()
            self.adb.ip = new_ip
            self.ip_entry.setText(new_ip)
            update_settings({"saved_ip": new_ip})
            self.log(f"发现显示器地址已变化: {old_ip} -> {new_ip}")
        else:
            self.log(f"局域网扫描重新发现显示器: {model} ({new_ip})")
        self._start_resume_reconnect("局域网发现显示器")

    def _check_resume_network_change(self):
        if not getattr(self, "_resume_waiting_for_network", False):
            return
        if getattr(self, "_cleanup_done", False) or getattr(self, "_windows_session_ending", False):
            self._cancel_resume_reconnect()
            return
        if not str(getattr(self.adb, "ip", "") or "").strip():
            self._cancel_resume_reconnect()
            return
        if getattr(self, "_scan_running", False):
            return

        signature = self._network_signature()
        if signature is not None:
            if self._resume_network_signature is None:
                self._resume_network_signature = signature
            elif signature != self._resume_network_signature:
                self.log("检测到 Windows 网络状态变化，重新尝试连接显示器")
                self._resume_next_discovery_at = 0.0
                self._start_resume_reconnect("网络状态变化")
                return

        if getattr(self, "_resume_discovery_checking", False):
            return
        if getattr(self, "_resume_target_probe_checking", False):
            return
        next_discovery_at = getattr(self, "_resume_next_discovery_at", 0.0)
        if next_discovery_at > 0 and time.monotonic() >= next_discovery_at:
            self._start_resume_discovery()
            return
        generation = self._resume_reconnect_generation
        ip = str(getattr(self.adb, "ip", "") or "").strip()
        self._resume_target_probe_checking = True

        def do():
            available = is_tcp_endpoint_open(ip, 5555, timeout=0.5)
            self.reconnect_target_probe_finished.emit(generation, available)

        async_run(do)

    def _finish_reconnect_target_probe(self, generation, available):
        if generation != getattr(self, "_resume_reconnect_generation", -1):
            return
        self._resume_target_probe_checking = False
        if not getattr(self, "_resume_waiting_for_network", False):
            return
        previous = getattr(self, "_resume_target_available", None)
        self._resume_target_available = bool(available)
        if not available or previous is True:
            return
        self.log("检测到显示器 ADB 端口恢复，重新尝试连接")
        self._start_resume_reconnect("显示器已唤醒")

    def _cancel_resume_reconnect(self):
        discovery_cancel = getattr(self, "_resume_discovery_cancel_event", None)
        if discovery_cancel is not None:
            discovery_cancel.set()
        self._resume_reconnect_generation = getattr(self, "_resume_reconnect_generation", 0) + 1
        self._resume_reconnect_active = False
        self._resume_reconnect_checking = False
        self._resume_waiting_for_network = False
        self._resume_network_signature = None
        self._resume_target_probe_checking = False
        self._resume_target_available = None
        self._resume_discovery_checking = False
        self._resume_discovery_cancel_event = None
        self._resume_next_discovery_at = 0.0
        self._resume_manual_selection_required = False
        retry_timer = getattr(self, "_resume_retry_timer", None)
        if retry_timer is not None:
            retry_timer.stop()
        network_timer = getattr(self, "_resume_network_timer", None)
        if network_timer is not None:
            network_timer.stop()

    def _monitor_adb_server(self):
        if getattr(self, "_cleanup_done", False) or getattr(self, "_windows_session_ending", False):
            return
        if not getattr(self, "adb_connected", False) or not self.adb.ip:
            return
        if self._adb_server_monitor_checking or time.monotonic() < self._adb_server_retry_after:
            return

        self._adb_server_monitor_checking = True
        ip = self.adb.ip
        intent_generation = getattr(self, "_connection_intent_generation", 0)

        def do():
            if is_adb_server_alive():
                self.adb_server_event.emit("healthy", "")
                return

            self.adb_server_event.emit("restarting", "")
            try:
                with self.adb.transaction():
                    if not self._connection_intent_is_current(intent_generation, ip):
                        self.adb_server_event.emit("cancelled", "")
                        return
                    adb_run(["start-server"], timeout=5, check=True)
                    if not is_adb_server_alive(timeout=0.5):
                        raise RuntimeError("ADB Server 启动后未监听端口")
                    if not self._connection_intent_is_current(intent_generation, ip):
                        self.adb_server_event.emit("cancelled", "")
                        return
                    reconnect_result = adb_run(
                        ["connect", format_adb_serial(ip)], timeout=5
                    )
                self.adb_server_event.emit("recovered", reconnect_result)
            except Exception as e:
                self.adb_server_event.emit("failed", str(e))

        async_run(do)

    def _on_adb_server_event(self, event, detail):
        if getattr(self, "_cleanup_done", False):
            return

        if event in ("healthy", "cancelled"):
            self._adb_server_monitor_checking = False
            if event == "cancelled":
                return
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
        intent_generation = getattr(self, "_connection_intent_generation", 0)

        def do():
            try:
                with self.adb.transaction():
                    if not self._connection_intent_is_current(intent_generation, ip):
                        return
                    serial = format_adb_serial(ip)
                    state = adb_device_state(serial, timeout=3)
                    if state == "device":
                        return
                    if not self._connection_intent_is_current(intent_generation, ip):
                        return

                    # 先 disconnect 清除可能存在的 stale transport，
                    # 否则 adb connect 会返回 "already connected" 但实际 TCP 已断
                    self.status_signal.emit(f"连接中...（{adb_device_state_label(state)}，正在重连）")
                    adb_run(["disconnect", serial], timeout=3)
                    if not self._connection_intent_is_current(intent_generation, ip):
                        return
                    adb_run(["connect", serial], timeout=5)
                    state = adb_device_state(serial, timeout=3)
                    if not self._connection_intent_is_current(intent_generation, ip):
                        return
                    if state != "device":
                        self.status_signal.emit(disconnected_status_text(state))
                        self.connection_recovery_requested.emit(
                            intent_generation,
                            ip,
                            "显示器休眠或临时断连",
                        )
                        return

                    m = adb_run(["-s", serial, "shell", "getprop ro.product.model"], timeout=3).strip()
                    if not self._connection_intent_is_current(intent_generation, ip):
                        return
                    if adb_text_has_disconnected_marker(m):
                        self.status_signal.emit(disconnected_status_text(m))
                        self.connection_recovery_requested.emit(
                            intent_generation,
                            ip,
                            "显示器休眠或临时断连",
                        )
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
        ip = str(getattr(self.adb, "ip", "") or "").strip()
        intent_generation = getattr(self, "_connection_intent_generation", 0)
        context = {
            "label": label,
            "on_success": on_success,
            "on_failure": on_failure,
        }

        def do():
            def finish_cancelled():
                context["cancelled"] = True
                self.adb_action_finished.emit(context, False, "")

            try:
                with self.adb.transaction():
                    if not self._connection_intent_is_current(intent_generation, ip):
                        finish_cancelled()
                        return
                    if getattr(self, "adb_connected", False) and ip:
                        ok, state = self.adb.ensure_connected()
                        if not self._connection_intent_is_current(intent_generation, ip):
                            finish_cancelled()
                            return
                        if not ok:
                            self.status_signal.emit(disconnected_status_text(state))
                            raise RuntimeError(f"ADB 连接已断开（{adb_device_state_label(state)}）")
                    operation()
                    if not self._connection_intent_is_current(intent_generation, ip):
                        finish_cancelled()
                        return
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
        if ok or context.get("cancelled"):
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
        self._invalidate_connection_intent()
        self._cancel_resume_reconnect()
        if self.adb.ip:
            self.log(f"正在断开与 {self.adb.ip} 的连接...")
            ip = self.adb.ip
            self.adb.ip = ""

            def do():
                adb_run(["disconnect", format_adb_serial(ip)])
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
        intent_generation = self._invalidate_connection_intent()
        self.adb.ip = saved_ip
        self._connection_in_progress = True
        self.status_signal.emit("连接中...")
        self.log(f"启动自动连接: 尝试连接上次设备 {saved_ip}")

        def do():
            try:
                with self.adb.transaction():
                    if not self._connection_intent_is_current(intent_generation, saved_ip):
                        return
                    ok = self.adb.connect()
                    if not self._connection_intent_is_current(intent_generation, saved_ip):
                        return
                    if ok:
                        self.status_signal.emit("已连接")
                        self.log(f"启动自动连接成功: {saved_ip}")
                        self.adb.check_and_heal_jar()
                        if not self._connection_intent_is_current(intent_generation, saved_ip):
                            return
                        m = self.adb.get_model().strip()
                        if not self._connection_intent_is_current(intent_generation, saved_ip):
                            return
                        if adb_text_has_disconnected_marker(m):
                            self.status_signal.emit(disconnected_status_text(m))
                            self.log(f"启动自动连接后设备状态异常: {m}")
                            return
                        self.status_signal.emit(f"已连接: {m or saved_ip}")
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
        intent_generation = self._invalidate_connection_intent()
        self._cancel_resume_reconnect()
        self._cancel_scan("手动连接")
        self.adb.ip = ip
        self._connection_in_progress = True
        self.status_signal.emit("连接中...")
        def do():
            try:
                with self.adb.transaction():
                    if not self._connection_intent_is_current(intent_generation, ip):
                        return
                    ok = self.adb.connect()
                    if not self._connection_intent_is_current(intent_generation, ip):
                        return
                    if ok:
                        self.status_signal.emit("已连接")
                        self.log(f"连接成功: {ip}")
                        update_settings({"saved_ip": ip})
                        self.adb.check_and_heal_jar()
                        if not self._connection_intent_is_current(intent_generation, ip):
                            return
                        m = self.adb.get_model().strip()
                        if not self._connection_intent_is_current(intent_generation, ip):
                            return
                        if adb_text_has_disconnected_marker(m):
                            self.status_signal.emit(disconnected_status_text(m))
                            self.log(f"连接后设备状态异常: {m}")
                            return
                        self.status_signal.emit(f"已连接: {m or ip}")
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
        if getattr(self, "_resume_discovery_checking", False):
            self.log("显示器地址扫描已在进行中，忽略重复请求")
            return
        if getattr(self, "_resume_target_probe_checking", False):
            self.log("旧 IP 状态探测正在进行，忽略内网扫描")
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
        mitv_indexes = [
            index
            for index, (_ip, model) in enumerate(dev_list)
            if is_mitv_model(model)
        ]
        if len(mitv_indexes) == 1:
            self._on_dev_sel(mitv_indexes[0])
        elif len(mitv_indexes) > 1:
            if getattr(self, "_resume_waiting_for_network", False):
                self._resume_manual_selection_required = True
                self._resume_next_discovery_at = 0.0
            self.log(f"扫描发现 {len(mitv_indexes)} 台显示器，请手动选择")

    def _update_scanned_devices(self, scan_id, dev_list):
        if scan_id != getattr(self, "_scan_id", -1):
            return
        # Temporarily block signals during combobox population to prevent autoconnect loop
        self.dev_combo.blockSignals(True)
        self.dev_combo.clear()
        for ip, model in dev_list:
            self.dev_combo.addItem(f"{model} ({ip})")
        preferred_index = 0
        mitv_count = 0
        for i, (_ip, model) in enumerate(dev_list):
            if is_mitv_model(model):
                mitv_count += 1
                if mitv_count == 1:
                    preferred_index = i
        if mitv_count > 1:
            self.dev_combo.setCurrentIndex(-1)
        elif dev_list:
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
        shell_args = ["-s", format_adb_serial(self.adb.ip), "shell"]
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
                    serial = format_adb_serial(self.adb.ip)
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
                r = adb_run(
                    ["-s", format_adb_serial(self.adb.ip), "install", "-r", apk_path],
                    timeout=60,
                )
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
