import ctypes
import ctypes.wintypes
import json
import ipaddress
import os
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import monitor_controller as app
from mimonitor_toolbox import adb as adb_runtime
from mimonitor_toolbox import core
from mimonitor_toolbox import device_features
from mimonitor_toolbox import display_features
from mimonitor_toolbox import main_window
from mimonitor_toolbox.network_scan import ProbeResult


class FakePopen:
    active = 0
    max_active = 0
    created = 0
    state_lock = threading.Lock()
    return_code = 0
    stderr = ""

    @classmethod
    def reset(cls):
        with cls.state_lock:
            cls.active = 0
            cls.max_active = 0
            cls.created = 0
            cls.return_code = 0
            cls.stderr = ""

    def __init__(self, *args, **kwargs):
        self.returncode = None
        with self.state_lock:
            type(self).created += 1
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)

    def communicate(self, timeout=None):
        time.sleep(0.01)
        with self.state_lock:
            type(self).active -= 1
        self.returncode = type(self).return_code
        return ("ok" if self.returncode == 0 else "", type(self).stderr)

    def kill(self):
        self.returncode = -9

    def poll(self):
        return self.returncode


class AdbRuntimeTests(unittest.TestCase):
    def setUp(self):
        FakePopen.reset()
        adb_runtime.unblock_adb_spawns()
        with adb_runtime._adb_process_lock:
            adb_runtime._adb_processes.clear()

    def tearDown(self):
        adb_runtime.unblock_adb_spawns()

    def test_adb_processes_are_serialized(self):
        with mock.patch.object(adb_runtime.subprocess, "Popen", FakePopen):
            workers = [threading.Thread(target=adb_runtime.adb_run, args=(["version"],)) for _ in range(12)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())

        self.assertEqual(FakePopen.created, 12)
        self.assertEqual(FakePopen.max_active, 1)

    def test_nested_adb_transaction_is_reentrant(self):
        with mock.patch.object(adb_runtime.subprocess, "Popen", FakePopen):
            with adb_runtime.Adb("127.0.0.1").transaction():
                self.assertEqual(adb_runtime.adb_run(["version"], check=True), "ok")

    def test_shutdown_block_prevents_new_process(self):
        with mock.patch.object(adb_runtime.subprocess, "Popen", FakePopen):
            adb_runtime.block_adb_spawns()
            with self.assertRaisesRegex(RuntimeError, "正在退出"):
                adb_runtime.adb_run(["version"], check=True)
        self.assertEqual(FakePopen.created, 0)

    def test_strict_adb_failure_raises(self):
        FakePopen.return_code = 7
        FakePopen.stderr = "device offline"
        with mock.patch.object(adb_runtime.subprocess, "Popen", FakePopen):
            with self.assertRaisesRegex(RuntimeError, "device offline"):
                adb_runtime.adb_run(["version"], check=True)

    def test_background_error_is_forwarded_to_thread_safe_log_signal(self):
        emitted = []

        class Signal:
            def emit(self, text):
                emitted.append(text)

        fake_app = type("FakeApp", (), {"log_signal": Signal()})()

        main_window.App._report_background_error(
            fake_app,
            RuntimeError("device command failed"),
        )

        self.assertEqual(
            emitted,
            ["后台任务异常: RuntimeError: device command failed"],
        )

    def test_adb_connect_rejects_offline_device_state(self):
        calls = []

        def fake_adb_run(args, timeout=10, check=False):
            calls.append(args)
            if args[0] == "connect":
                return "already connected to 192.168.5.205:5555"
            if args[-1] == "get-state":
                return "offline"
            return ""

        with mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run):
            self.assertFalse(adb_runtime.Adb("192.168.5.205").connect())

        self.assertEqual(calls, [
            ["connect", "192.168.5.205:5555"],
            ["-s", "192.168.5.205:5555", "get-state"],
        ])

    def test_adb_ensure_connected_reconnects_stale_device(self):
        calls = []

        def fake_adb_run(args, timeout=10, check=False):
            calls.append((args, timeout, check))
            if args[0] == "connect":
                return "connected to 192.168.5.205:5555"
            if args[-1] == "get-state":
                return "offline" if len([c for c in calls if c[0][-1] == "get-state"]) == 1 else "device"
            return ""

        with mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run):
            self.assertEqual(adb_runtime.Adb("192.168.5.205").ensure_connected(), (True, "device"))

        self.assertEqual(calls, [
            (["-s", "192.168.5.205:5555", "get-state"], 2, False),
            (["connect", "192.168.5.205:5555"], 5, False),
            (["-s", "192.168.5.205:5555", "get-state"], 3, False),
        ])

    def test_connected_status_with_adb_error_is_normalized(self):
        text = "已连接: adb.exe: device offline"
        self.assertFalse(adb_runtime.is_connected_status_text(text))
        self.assertEqual(adb_runtime.normalize_status_text(text), "未连接（设备离线）")

    def test_keepalive_marks_offline_device_disconnected(self):
        events = []
        commands = []

        class FakeSignal:
            def emit(self, *args):
                events.append(args)

        class FakeApp:
            _cleanup_done = False
            _windows_session_ending = False
            _connection_intent_generation = 0
            _connection_intent_is_current = (
                device_features.DeviceFeaturesMixin._connection_intent_is_current
            )
            adb_connected = True
            adb = type(
                "FakeAdb",
                (),
                {"ip": "192.168.5.205", "transaction": staticmethod(nullcontext)},
            )()
            _adb_keepalive_checking = False
            _adb_busy_until = 0.0
            status_signal = FakeSignal()
            connection_recovery_requested = mock.Mock()

        def fake_adb_run(args, timeout=10, check=False):
            commands.append(args)
            return "failed to connect"

        fake = FakeApp()
        with mock.patch.object(device_features, "adb_device_state", side_effect=["offline", "offline"]), \
                mock.patch.object(device_features, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            app.App._keep_adb_alive(fake)

        self.assertFalse(fake._adb_keepalive_checking)
        self.assertEqual(events, [
            ("连接中...（设备离线，正在重连）",),
            ("未连接（设备离线）",),
        ])
        self.assertEqual(commands, [
            ["disconnect", "192.168.5.205:5555"],
            ["connect", "192.168.5.205:5555"]
        ])

    def test_adb_server_probe_does_not_start_adb(self):
        fake_socket = mock.Mock()
        with mock.patch.object(adb_runtime.socket, "create_connection", return_value=fake_socket) as connect:
            self.assertTrue(adb_runtime.is_adb_server_alive())
        connect.assert_called_once_with(("127.0.0.1", int(adb_runtime.ADB_SERVER_PORT)), timeout=0.2)
        fake_socket.close.assert_called_once_with()

        with mock.patch.object(adb_runtime.socket, "create_connection", side_effect=ConnectionRefusedError):
            self.assertFalse(adb_runtime.is_adb_server_alive())

    def test_dead_adb_server_is_restarted_and_device_reconnected(self):
        events = []
        commands = []

        class FakeSignal:
            def emit(self, *args):
                events.append(args)

        class FakeApp:
            _cleanup_done = False
            _windows_session_ending = False
            _connection_intent_generation = 0
            _connection_intent_is_current = (
                device_features.DeviceFeaturesMixin._connection_intent_is_current
            )
            adb_connected = True
            adb = type(
                "FakeAdb",
                (),
                {"ip": "192.168.5.205", "transaction": staticmethod(nullcontext)},
            )()
            _adb_server_monitor_checking = False
            _adb_server_retry_after = 0.0
            adb_server_event = FakeSignal()

        def fake_adb_run(args, timeout=10, check=False):
            commands.append((args, timeout, check))
            return "connected to 192.168.5.205:5555"

        fake = FakeApp()
        with mock.patch.object(device_features, "is_adb_server_alive", side_effect=[False, True]), \
                mock.patch.object(device_features, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            app.App._monitor_adb_server(fake)

        self.assertTrue(fake._adb_server_monitor_checking)
        self.assertEqual(events, [
            ("restarting", ""),
            ("recovered", "connected to 192.168.5.205:5555"),
        ])
        self.assertEqual(commands[0], (["start-server"], 5, True))
        self.assertEqual(commands[1], (["connect", "192.168.5.205:5555"], 5, False))

    def test_adb_restart_notification_is_not_repeated(self):
        messages = []
        hud = []
        logs = []

        class FakeTray:
            def showMessage(self, *args):
                messages.append(args)

        class FakeOsd:
            def show_hud(self, *args):
                hud.append(args)

        class FakeApp:
            _cleanup_done = False
            _adb_server_monitor_checking = True
            _adb_server_down_notified = False
            _adb_server_failure_notified = False
            _adb_server_retry_after = 0.0
            tray_icon = FakeTray()
            osd = FakeOsd()

            def log(self, message):
                logs.append(message)

        fake = FakeApp()
        app.App._on_adb_server_event(fake, "restarting", "")
        app.App._on_adb_server_event(fake, "restarting", "")

        self.assertEqual(len(messages), 1)
        self.assertEqual(hud, [("ADB 进程", "正在重启")])
        self.assertEqual(logs, ["检测到 ADB 进程被杀死，正在重启"])

    def test_adb_action_self_heals_before_operation(self):
        events = []
        operation_calls = []

        class FakeSignal:
            def emit(self, *args):
                events.append(args)

        class FakeAdb:
            ip = "192.168.5.205"

            def transaction(self):
                return nullcontext()

            def ensure_connected(self):
                events.append(("ensure",))
                return True, "device"

        class FakeApp:
            _cleanup_done = False
            _windows_session_ending = False
            _connection_intent_generation = 0
            _connection_intent_is_current = (
                device_features.DeviceFeaturesMixin._connection_intent_is_current
            )
            adb_connected = True
            adb = FakeAdb()
            adb_action_finished = FakeSignal()
            status_signal = FakeSignal()

        def operation():
            operation_calls.append("run")

        with mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            app.App._run_adb_action(FakeApp(), "测试操作", operation)

        self.assertEqual(operation_calls, ["run"])
        self.assertEqual(events, [("ensure",), ({"label": "测试操作", "on_success": None, "on_failure": None}, True, "")])

    def test_adb_action_reports_disconnected_when_self_heal_fails(self):
        events = []
        operation_calls = []

        class FakeSignal:
            def emit(self, *args):
                events.append(args)

        class FakeAdb:
            ip = "192.168.5.205"

            def transaction(self):
                return nullcontext()

            def ensure_connected(self):
                return False, "offline"

        class FakeApp:
            _cleanup_done = False
            _windows_session_ending = False
            _connection_intent_generation = 0
            _connection_intent_is_current = (
                device_features.DeviceFeaturesMixin._connection_intent_is_current
            )
            adb_connected = True
            adb = FakeAdb()
            adb_action_finished = FakeSignal()
            status_signal = FakeSignal()

        def operation():
            operation_calls.append("run")

        with mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            app.App._run_adb_action(FakeApp(), "测试操作", operation)

        self.assertEqual(operation_calls, [])
        self.assertEqual(events[0], ("未连接（设备离线）",))
        self.assertFalse(events[1][1])

    def test_scan_adb_sorts_devices_and_cleans_temporary_transports(self):
        source_ip = ipaddress.IPv4Address("192.168.5.2")
        probe_results = [
            ProbeResult(ipaddress.IPv4Address("192.168.5.30"), source_ip),
            ProbeResult(ipaddress.IPv4Address("192.168.5.10"), source_ip),
            ProbeResult(ipaddress.IPv4Address("192.168.5.20"), source_ip),
            ProbeResult(ipaddress.IPv4Address("192.168.5.40"), source_ip),
            ProbeResult(ipaddress.IPv4Address("192.168.5.50"), source_ip),
        ]
        models = {
            "192.168.5.10:5555": "MiTV-A",
            "192.168.5.20:5555": "mitv-B",
            "192.168.5.30:5555": "Pixel 9",
        }
        states = {
            "192.168.5.10:5555": "device",
            "192.168.5.20:5555": "device",
            "192.168.5.30:5555": "device",
            "192.168.5.40:5555": "unauthorized",
            "192.168.5.50:5555": "offline",
        }
        commands = []
        callbacks = []

        def fake_adb_run(args, timeout=10, check=False):
            commands.append(args)
            if args[0] == "connect":
                return f"connected to {args[1]}"
            if args[0] == "disconnect":
                return f"disconnected {args[1]}"
            if args[-1] == "getprop ro.product.model":
                return models[args[1]]
            return ""

        with mock.patch.object(adb_runtime, "get_windows_scan_networks", return_value=[object()]), \
                mock.patch.object(adb_runtime, "build_probe_targets", return_value=[object()]), \
                mock.patch.object(adb_runtime, "probe_tcp_targets", return_value=probe_results), \
                mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(adb_runtime, "adb_device_state", side_effect=lambda serial, timeout=3: states[serial]):
            found = adb_runtime.scan_adb(
                cb=lambda ip, model: callbacks.append((ip, model)),
                cancel_event=threading.Event(),
            )

        expected = [
            ("192.168.5.10", "MiTV-A"),
            ("192.168.5.20", "mitv-B"),
            ("192.168.5.30", "Pixel 9"),
        ]
        self.assertEqual(found, expected)
        self.assertEqual(callbacks, expected)
        self.assertTrue(adb_runtime.is_mitv_model("MiTV-A"))
        self.assertTrue(adb_runtime.is_mitv_model("mitv-B"))
        self.assertFalse(adb_runtime.is_mitv_model("Pixel 9"))
        self.assertEqual(
            [args[1] for args in commands if args[0] == "disconnect"],
            [
                "192.168.5.10:5555",
                "192.168.5.20:5555",
                "192.168.5.30:5555",
                "192.168.5.40:5555",
                "192.168.5.50:5555",
            ],
        )


class SettingsTests(unittest.TestCase):
    def test_concurrent_updates_are_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            config_path = os.path.join(folder, "config.json")
            with mock.patch.object(core, "get_settings_path", return_value=config_path):
                results = []

                def update(index):
                    results.append(core.update_settings({f"key_{index}": index}))

                workers = [threading.Thread(target=update, args=(index,)) for index in range(40)]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join(timeout=3)
                    self.assertFalse(worker.is_alive())

                self.assertTrue(all(results))
                settings = core.load_settings()
                for index in range(40):
                    self.assertEqual(settings[f"key_{index}"], index)

                with open(config_path, "r", encoding="utf-8") as stream:
                    json.load(stream)
                self.assertFalse([name for name in os.listdir(folder) if name.endswith(".tmp")])


class ScanLifecycleTests(unittest.TestCase):
    def test_finish_scan_requires_manual_choice_when_multiple_mitv_devices_exist(self):
        emitted = []
        selected = []
        updated = []

        class FakeSignal:
            def emit(self, *args):
                emitted.append(args)

        class FakeButton:
            enabled = False

            def setEnabled(self, enabled):
                self.enabled = enabled

        class FakeApp:
            _scan_id = 7
            _scan_running = True
            _scan_cancel_event = threading.Event()
            adb_connected = False
            status_signal = FakeSignal()
            scan_btn = FakeButton()

            def _update_scanned_devices(self, scan_id, devices):
                updated.append((scan_id, list(devices)))

            def _on_dev_sel(self, index):
                selected.append(index)

            def log(self, message):
                pass

        devices = [
            ("192.168.5.2", "Pixel 9"),
            ("192.168.5.10", "MiTV-A"),
            ("192.168.5.20", "mitv-B"),
        ]

        fake = FakeApp()
        app.App._finish_scan(fake, 7, "completed", devices, "")

        self.assertFalse(fake._scan_running)
        self.assertIsNone(fake._scan_cancel_event)
        self.assertTrue(fake.scan_btn.enabled)
        self.assertEqual(updated, [(7, devices)])
        self.assertEqual(emitted, [("扫描完成: 3台",)])
        self.assertEqual(selected, [])

    def test_finish_scan_auto_connects_when_exactly_one_mitv_exists(self):
        selected = []

        class Signal:
            def emit(self, *_args):
                pass

        class Button:
            def setEnabled(self, _enabled):
                pass

        fake = SimpleNamespace(
            _scan_id=7,
            _scan_running=True,
            _scan_cancel_event=threading.Event(),
            adb_connected=False,
            status_signal=Signal(),
            scan_btn=Button(),
            _update_scanned_devices=lambda _scan_id, _devices: None,
            _on_dev_sel=selected.append,
            log=lambda _message: None,
        )
        devices = [
            ("192.168.5.2", "Pixel 9"),
            ("192.168.5.5", "MiTV-MFFU1"),
        ]

        app.App._finish_scan(fake, 7, "completed", devices, "")

        self.assertEqual(selected, [1])

    def test_stale_scan_completion_is_ignored(self):
        class FakeApp:
            _scan_id = 8

            def _update_scanned_devices(self, scan_id, devices):
                raise AssertionError("旧扫描不应更新设备列表")

            def _on_dev_sel(self, index):
                raise AssertionError("旧扫描不应触发连接")

        app.App._finish_scan(FakeApp(), 7, "completed", [("192.168.5.10", "MiTV")], "")

    def test_cancel_scan_invalidates_worker_and_restores_button(self):
        logs = []

        class FakeButton:
            enabled = False

            def setEnabled(self, enabled):
                self.enabled = enabled

        class FakeApp:
            _scan_id = 3
            _scan_running = True
            _scan_cancel_event = threading.Event()
            scan_btn = FakeButton()

            def log(self, message):
                logs.append(message)

        fake = FakeApp()
        original_event = fake._scan_cancel_event

        self.assertTrue(app.App._cancel_scan(fake, "手动连接"))
        self.assertTrue(original_event.is_set())
        self.assertEqual(fake._scan_id, 4)
        self.assertFalse(fake._scan_running)
        self.assertTrue(fake.scan_btn.enabled)
        self.assertEqual(logs, ["取消内网扫描: 手动连接"])

    def test_scan_status_does_not_clear_connected_state(self):
        class FakeLabel:
            def setText(self, text):
                self.text = text

            def setStyleSheet(self, style):
                self.style = style

        class FakeApp:
            adb_connected = True
            status_label = FakeLabel()

            def setWindowTitle(self, title):
                self.title = title

        fake = FakeApp()
        app.App._on_status(fake, "扫描完成: 1台")

        self.assertTrue(fake.adb_connected)
        self.assertEqual(fake.status_label.text, "扫描完成: 1台")

    def test_duplicate_scan_request_is_ignored(self):
        logs = []

        class FakeApp:
            adb_connected = False
            _scan_running = True

            def log(self, message):
                logs.append(message)

        app.App.scan_net(FakeApp())

        self.assertEqual(logs, ["内网扫描已在进行中，忽略重复请求"])

    def test_scan_request_is_ignored_while_connection_is_in_progress(self):
        logs = []

        class FakeApp:
            adb_connected = False
            _connection_in_progress = True
            _scan_running = False

            def log(self, message):
                logs.append(message)

        app.App.scan_net(FakeApp())

        self.assertEqual(logs, ["设备正在连接，跳过内网扫描"])


class StateMachineTests(unittest.TestCase):
    def test_native_power_resume_starts_reconnect_cycle(self):
        qapp = QApplication.instance() or QApplication([])
        window = main_window.App.__new__(main_window.App)
        calls = []
        window._handle_windows_resume = lambda: calls.append("resume")
        message = ctypes.wintypes.MSG()
        message.message = 0x0218
        message.wParam = 0x0012

        with mock.patch.object(main_window.sys, "platform", "win32"), \
                mock.patch.object(main_window, "user32", object()), \
                mock.patch.object(main_window.FluentWindow, "nativeEvent", return_value=(False, 0)):
            result = window.nativeEvent(
                b"windows_generic_MSG",
                ctypes.addressof(message),
            )

        self.assertEqual(result, (True, 1))
        self.assertEqual(calls, ["resume"])
        self.assertIsNotNone(qapp)

    def test_non_game_feature_cancel_restores_memory_without_adb_action(self):
        dialogs = []
        highlights = []
        actions = []

        class FakeDialog:
            def __init__(self, title, content, parent):
                dialogs.append((title, content))

            def exec(self):
                return False

            def deleteLater(self):
                pass

        class FakeApp:
            current_vals = {"picture_mode": 14, "mt_game_scope": 3}

            def check_connection(self):
                return True

            def _optimistic_highlight(self, key, value):
                highlights.append((key, value))

            def _run_adb_action(self, *args):
                actions.append(args)

        setter = getattr(display_features.DisplayFeaturesMixin, "_set_game_feature", None)
        self.assertIsNotNone(setter)
        with mock.patch.object(display_features, "MessageBox", FakeDialog):
            setter(FakeApp(), "mt_game_scope", 5, "狙击镜: 1.5x")

        self.assertEqual(len(dialogs), 1)
        self.assertIn("当前不是游戏模式", dialogs[0][1])
        self.assertEqual(highlights, [("mt_game_scope", 3)])
        self.assertEqual(actions, [])

    def test_non_game_feature_off_updates_memory_without_switch_prompt(self):
        commands = []
        dialog_count = []

        class FakeDialog:
            def __init__(self, *args):
                dialog_count.append(1)

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def put(self, key, value, check=False):
                commands.append(("put", key, value, check))

            def refresh_pq(self, check=False):
                commands.append(("refresh", check))

        class FakeApp:
            adb = FakeAdb()
            current_vals = {"picture_mode": 14, "front_sight_index": 2}

            def check_connection(self):
                return True

            def _mark_adb_busy(self, duration):
                pass

            def _run_adb_action(self, label, operation, success, failure):
                operation()
                success()

            def _optimistic_highlight(self, key, value):
                pass

            def log(self, message):
                pass

        setter = getattr(display_features.DisplayFeaturesMixin, "_set_game_feature", None)
        self.assertIsNotNone(setter)
        with mock.patch.object(display_features, "MessageBox", FakeDialog):
            fake = FakeApp()
            setter(fake, "front_sight_index", 0, "准星: 关", retrigger_game_mode=True)

        self.assertEqual(dialog_count, [])
        self.assertEqual(commands, [
            ("put", "front_sight_index", "0", True),
            ("refresh", True),
        ])
        self.assertEqual(fake.current_vals["picture_mode"], 14)
        self.assertEqual(fake.current_vals["front_sight_index"], 0)

    def test_non_game_feature_confirm_writes_value_before_switching_mode(self):
        commands = []
        logs = []

        class FakeDialog:
            def __init__(self, title, content, parent):
                pass

            def exec(self):
                return True

            def deleteLater(self):
                pass

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def put(self, key, value, check=False):
                commands.append((key, value, check))

        class FakeApp:
            adb = FakeAdb()
            current_vals = {"picture_mode": 14, "mt_game_scope": 0}
            _picture_mode_switch_seq = 0

            def check_connection(self):
                return True

            def _mark_adb_busy(self, duration):
                pass

            def _run_adb_action(self, label, operation, success, failure):
                operation()
                success()

            def _optimistic_highlight(self, key, value):
                pass

            def _highlight_mode(self, value):
                pass

            def _update_game_mode_hint(self):
                pass

            def _refresh_pages(self, *args, **kwargs):
                pass

            def log(self, message):
                logs.append(message)

        setter = getattr(display_features.DisplayFeaturesMixin, "_set_game_feature", None)
        self.assertIsNotNone(setter)
        with mock.patch.object(display_features, "MessageBox", FakeDialog):
            fake = FakeApp()
            setter(fake, "mt_game_scope", 5, "狙击镜: 1.5x")

        self.assertEqual(commands, [
            ("mt_game_scope", "5", True),
            ("picture_mode", "10", True),
        ])
        self.assertEqual(fake.current_vals["mt_game_scope"], 5)
        self.assertEqual(fake.current_vals["picture_mode"], 10)
        self.assertEqual(fake._picture_mode_switch_seq, 1)
        self.assertIn("已切换到游戏模式", logs)

    def test_game_feature_dependencies_are_enabled_before_requested_feature(self):
        cases = (
            (
                "mt_game_dynamic_ft", "动态准星: 开",
                {"front_sight_index": 0, "mt_game_dynamic_ft": 0},
                "front_sight_index", "准星 1",
            ),
            (
                "mt_game_scope_night", "狙击镜夜视: 开",
                {"mt_game_scope": 0, "mt_game_scope_night": 0},
                "mt_game_scope", "狙击镜 1.1x",
            ),
        )

        for key, message, feature_values, dependency_key, dependency_name in cases:
            with self.subTest(key=key):
                commands = []
                dialogs = []

                class FakeDialog:
                    def __init__(self, title, content, parent):
                        dialogs.append((title, content))

                    def exec(self):
                        return True

                    def deleteLater(self):
                        pass

                class FakeAdb:
                    def transaction(self):
                        return nullcontext()

                    def put(self, setting, value, check=False):
                        commands.append(("put", setting, value, check))

                    def refresh_pq(self, check=False):
                        commands.append(("refresh", check))

                class FakeApp:
                    adb = FakeAdb()
                    current_vals = {"picture_mode": 10, **feature_values}

                    def check_connection(self):
                        return True

                    def _mark_adb_busy(self, duration):
                        pass

                    def _run_adb_action(self, label, operation, success, failure):
                        operation()
                        success()

                    def _optimistic_highlight(self, state_key, value):
                        pass

                    def log(self, text):
                        pass

                with mock.patch.object(display_features, "MessageBox", FakeDialog):
                    fake = FakeApp()
                    display_features.DisplayFeaturesMixin._set_game_feature(
                        fake, key, 1, message,
                    )

                self.assertEqual(len(dialogs), 1)
                self.assertIn(dependency_name, dialogs[0][1])
                self.assertEqual(commands, [
                    ("put", dependency_key, "1", True),
                    ("put", key, "1", True),
                    ("refresh", True),
                ])
                self.assertEqual(fake.current_vals[dependency_key], 1)
                self.assertEqual(fake.current_vals[key], 1)

    def test_game_feature_dependency_cancel_runs_no_action(self):
        actions = []
        highlights = []

        class FakeDialog:
            def __init__(self, title, content, parent):
                pass

            def exec(self):
                return False

            def deleteLater(self):
                pass

        class FakeApp:
            current_vals = {
                "picture_mode": 10,
                "front_sight_index": 0,
                "mt_game_dynamic_ft": 0,
            }

            def check_connection(self):
                return True

            def _mark_adb_busy(self, duration):
                pass

            def _run_adb_action(self, *args):
                actions.append(args)

            def _optimistic_highlight(self, key, value):
                highlights.append((key, value))

        with mock.patch.object(display_features, "MessageBox", FakeDialog):
            display_features.DisplayFeaturesMixin._set_game_feature(
                FakeApp(), "mt_game_dynamic_ft", 1, "动态准星: 开",
            )

        self.assertEqual(actions, [])
        self.assertEqual(highlights, [("mt_game_dynamic_ft", 0)])

    def test_game_page_refresh_reads_mode_and_defaults_missing_features_to_off(self):
        emitted = []

        class FakeSignal:
            def __init__(self, name):
                self.name = name

            def emit(self, *args):
                emitted.append((self.name, args))

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def shell(self, command):
                rows = []
                if " picture_mode " in command:
                    rows.append("picture_mode=14")
                if " picture_preset_scenario " in command:
                    rows.append("picture_preset_scenario=14")
                rows.extend([
                    "front_sight_index=null",
                    "mt_game_dynamic_ft=null",
                    "mt_game_scope=null",
                    "mt_game_scope_night=null",
                    "mitv.tvplayer.hdmi.last.source=23",
                ])
                return "\n".join(rows)

            def jni_batch_get_or_single(self, keys):
                return {}

        class FakeApp:
            adb_connected = True
            _page_loading = set()
            adb = FakeAdb()
            values_signal = FakeSignal("values")
            jni_values_signal = FakeSignal("jni")
            page_refresh_finished = FakeSignal("finished")

            def _mark_adb_busy(self, duration):
                pass

            def _show_loading_overlay(self, page_name):
                pass

            def log(self, message):
                pass

        fake = FakeApp()
        device_features.DeviceFeaturesMixin.initialize_device_features(fake)
        fake.adb_connected = True
        fake.adb = FakeAdb()
        fake.values_signal = FakeSignal("values")
        fake.jni_values_signal = FakeSignal("jni")
        fake.page_refresh_finished = FakeSignal("finished")

        with mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            app.App._refresh_page_data(fake, "gamePage")

        snapshots = [args[0] for name, args in emitted if name == "values"]
        self.assertEqual(snapshots, [{
            "picture_mode": 14,
            "picture_preset_scenario": 14,
            "front_sight_index": 0,
            "mt_game_dynamic_ft": 0,
            "mt_game_scope": 0,
            "mt_game_scope_night": 0,
            "mitv.tvplayer.hdmi.last.source": 23,
        }])

    def test_disabling_4k_ui_cancel_restores_checkbox_without_adb_action(self):
        dialogs = []
        actions = []

        class FakeDialog:
            def __init__(self, title, content, parent):
                dialogs.append((title, content, parent))

            def exec(self):
                return False

            def deleteLater(self):
                pass

        class FakeCheckBox:
            checked = False

            def blockSignals(self, blocked):
                pass

            def setChecked(self, checked):
                self.checked = checked

        class FakeApp:
            chk_4k = FakeCheckBox()

            def _run_adb_action(self, *args):
                actions.append(args)

        fake = FakeApp()
        with mock.patch.object(main_window, "MessageBox", FakeDialog):
            app.App._toggle_4k_ui(fake, 0)

        self.assertEqual(len(dialogs), 1)
        self.assertIn("关闭 4K UI", dialogs[0][1])
        self.assertIn("1920×1080、DPI 320", dialogs[0][1])
        self.assertTrue(fake.chk_4k.checked)
        self.assertEqual(actions, [])

    def test_disabling_4k_ui_confirm_restores_1080p_and_reboots(self):
        commands = []
        logs = []

        class FakeDialog:
            def __init__(self, title, content, parent):
                pass

            def exec(self):
                return True

            def deleteLater(self):
                pass

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def shell(self, command, check=False):
                commands.append((command, check))

        class FakeApp:
            adb = FakeAdb()

            def _run_adb_action(self, label, operation, success, failure):
                operation()
                success()

            def log(self, message):
                logs.append(message)

        with mock.patch.object(main_window, "MessageBox", FakeDialog):
            app.App._toggle_4k_ui(FakeApp(), 0)

        self.assertEqual(commands, [
            ("wm size 1920x1080", True),
            ("wm density 320", True),
            ("reboot", True),
        ])
        self.assertEqual(logs, [
            "已恢复 1080p UI (1920×1080 / DPI 320)",
            "正在重启显示器...",
        ])

    def test_hdr_tone_mapping_values_and_setter(self):
        self.assertEqual(core.HDR_TONE_MAPPING_UI_TO_MTK, {0: 5, 1: 0, 2: 2, 3: 1})
        calls = []

        class FakeAdb:
            def transaction(self):
                return nullcontext()

            def jni_set(self, key, value, check=False):
                calls.append(("jni", key, value, check))

            def check_and_heal_jar(self):
                calls.append(("heal_jar",))

            def hdr_tone_mapping(self, value, check=False):
                calls.append(("hdr_tone", value, check))

            def put(self, key, value, check=False):
                calls.append(("put", key, value, check))

            def refresh_pq(self, check=False):
                calls.append(("refresh", check))

        class FakeApp:
            adb = FakeAdb()
            current_vals = {}

            def check_connection(self):
                return True

            def _mark_adb_busy(self, duration):
                pass

            def _take_control_previous(self, key):
                return 0

            def _run_adb_action(self, label, operation, success, failure):
                operation()
                success()

            def _optimistic_highlight(self, key, value):
                calls.append(("highlight", key, value))

            def log(self, message):
                calls.append(("log", message))

        fake = FakeApp()
        app.App._set_hdr_tone_mapping(fake, 2, "动态")

        self.assertIn(("heal_jar",), calls)
        self.assertIn(("hdr_tone", 2, True), calls)
        self.assertIn(("put", "picture_hdr_tone_mapping", "2", True), calls)
        self.assertIn(("put", "settings_display_hdr_color_tone", "2", True), calls)
        self.assertEqual(fake.current_vals["settings_display_hdr_color_tone"], 2)

    def test_hdr_tone_mapping_visibility_follows_picture_scene(self):
        self.assertFalse(core.is_hdr_tone_mapping_picture_mode(14))
        self.assertTrue(core.is_hdr_tone_mapping_picture_mode(18))

        class FakeCard:
            visible = None

            def setVisible(self, visible):
                self.visible = visible

        class FakeApp:
            def __init__(self, current_vals):
                self.current_vals = current_vals
                self.hdr_tone_mapping_card = FakeCard()

        sdr = FakeApp({"picture_mode": 14, "picture_preset_scenario": 14})
        app.App._update_hdr_tone_mapping_visibility(sdr)
        self.assertFalse(sdr.hdr_tone_mapping_card.visible)

        hdr = FakeApp({"picture_mode": 14, "picture_preset_scenario": 18})
        app.App._update_hdr_tone_mapping_visibility(hdr)
        self.assertTrue(hdr.hdr_tone_mapping_card.visible)

    def test_polled_local_dimming_does_not_update_memory(self):
        class FakeApp:
            def _hdr_memory_enabled(self):
                return True

            def _save_local_dimming_memory(self, memory):
                raise AssertionError("automatic refresh must not save memory")

        app.App._remember_local_dimming_value(FakeApp(), 3, log_change=False)

    def test_hdr_transition_applies_memory_before_refresh(self):
        calls = []

        class FakeApp:
            _hdr_windows_state = True
            _hdr_last_state = False

            def _update_hdr_memory_status_label(self, source=None):
                calls.append(("status", source))

            def _hdr_memory_enabled(self):
                return True

            def _schedule_hdr_memory_apply(self, delay_ms=250):
                calls.append(("apply", delay_ms))

            def _schedule_picture_refresh_after_hdr_change(self, state, initial_delay_ms=0, is_switch=True):
                calls.append(("refresh", state, initial_delay_ms, is_switch))

        fake = FakeApp()
        app.App._reconcile_hdr_memory_state(fake, "Windows HDR")
        self.assertEqual(fake._hdr_last_state, True)
        # 真实切换(SDR->HDR)：应用记忆在前、刷新在后，且标记为切换
        self.assertEqual(calls[1:], [("apply", 120), ("refresh", True, 300, True)])

    def test_page_refresh_cleanup_updates_state_in_slot(self):
        hidden = []

        class FakeApp:
            _page_loaded = set()
            _page_loading = {"picturePage"}

            def _hide_loading_overlay(self, page_name):
                hidden.append(page_name)

        fake = FakeApp()
        app.App._finish_page_refresh(fake, "picturePage", True)
        self.assertIn("picturePage", fake._page_loaded)
        self.assertNotIn("picturePage", fake._page_loading)
        self.assertEqual(hidden, ["picturePage"])

    def test_page_refresh_reads_one_adb_transaction(self):
        emitted = []

        class FakeSignal:
            def __init__(self, name):
                self.name = name

            def emit(self, *args):
                emitted.append((self.name, args))

        class FakeTransaction:
            def __init__(self, adb):
                self.adb = adb

            def __enter__(self):
                self.adb.depth += 1

            def __exit__(self, exc_type, exc_value, traceback):
                self.adb.depth -= 1

        class FakeAdb:
            depth = 0

            def transaction(self):
                return FakeTransaction(self)

            def shell(self, cmd):
                if self.depth != 1:
                    raise AssertionError("settings read escaped the page transaction")
                return "picture_mode=2"

            def jni_get(self, key):
                if self.depth != 1:
                    raise AssertionError("JNI read escaped the page transaction")
                return "40"

            def jni_batch_get_or_single(self, keys):
                if self.depth != 1:
                    raise AssertionError("JNI read escaped the page transaction")
                return {key: 40 for key in keys}

        class FakeApp:
            adb_connected = True
            _picture_mode_switch_seq = 0
            _page_loading = set()
            _page_data_keys = {
                "picturePage": {
                    "settings": ["picture_mode"],
                    "jni": ["g_disp__disp_back_light"],
                }
            }
            adb = FakeAdb()
            values_signal = FakeSignal("values")
            jni_values_signal = FakeSignal("jni")
            page_refresh_finished = FakeSignal("finished")

            def _mark_adb_busy(self, duration):
                pass

            def _show_loading_overlay(self, page_name):
                pass

            def log(self, message):
                pass

        with mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            app.App._refresh_page_data(FakeApp(), "picturePage")

        self.assertEqual(FakeApp.adb.depth, 0)
        self.assertIn(("values", ({"picture_mode": 2},)), emitted)
        self.assertIn(("jni", ({"g_disp__disp_back_light": 40},)), emitted)
        self.assertIn(("finished", ("picturePage", True)), emitted)

    def test_jni_batch_get_falls_back_for_missing_keys(self):
        calls = []

        class FakeAdb:
            def jni_batch_get(self, keys):
                calls.append(("batch", tuple(keys)))
                return {"a": 1}

            def jni_get(self, key, check=False):
                calls.append(("single", key, check))
                return "2"

        fake = FakeAdb()
        vals = adb_runtime.Adb.jni_batch_get_or_single(fake, ["a", "b"])
        self.assertEqual(vals, {"a": 1, "b": 2})
        self.assertEqual(calls, [("batch", ("a", "b")), ("single", "b", False)])

    def test_tray_click_toggles_visible_window_to_tray(self):
        calls = []

        class FakeApp:
            def windowState(self):
                return Qt.WindowState.WindowNoState

            def isVisible(self):
                return True

            def hide(self):
                calls.append("hide")

            def show_and_raise(self):
                calls.append("show")

        app.App.on_tray_activated(FakeApp(), QSystemTrayIcon.ActivationReason.Trigger)
        self.assertEqual(calls, ["hide"])

    def test_tray_click_restores_hidden_window(self):
        calls = []

        class FakeApp:
            def windowState(self):
                return Qt.WindowState.WindowNoState

            def isVisible(self):
                return False

            def hide(self):
                calls.append("hide")

            def show_and_raise(self):
                calls.append("show")

        app.App.on_tray_activated(FakeApp(), QSystemTrayIcon.ActivationReason.Trigger)
        self.assertEqual(calls, ["show"])

    def test_restore_checks_adb_when_connected(self):
        calls = []

        class FakeApp:
            adb_connected = True
            adb = type("FakeAdb", (), {"ip": "192.168.5.205"})()

            def _keep_adb_alive(self):
                calls.append("keepalive")

        app.App._check_adb_when_restored(FakeApp())
        self.assertEqual(calls, ["keepalive"])

    def test_restore_does_not_check_adb_when_disconnected(self):
        calls = []

        class FakeApp:
            adb_connected = False
            adb = type("FakeAdb", (), {"ip": "192.168.5.205"})()

            def _keep_adb_alive(self):
                calls.append("keepalive")

        app.App._check_adb_when_restored(FakeApp())
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
