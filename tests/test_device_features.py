import os
import threading
import time
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import mock

from PyQt6.QtCore import QObject

from mimonitor_toolbox import device_features


class FakeTimer:
    def __init__(self, callback=None):
        self.callback = callback
        self.start_calls = []
        self.stop_calls = 0

    def start(self, interval=None):
        self.start_calls.append(interval)
        if self.callback:
            self.callback()

    def stop(self):
        self.stop_calls += 1


class FakeSignal:
    def __init__(self, callback=None):
        self.callback = callback
        self.events = []

    def emit(self, *args):
        self.events.append(args)
        if self.callback:
            self.callback(*args)


class ReconnectHost(device_features.DeviceFeaturesMixin):
    def __init__(self):
        self.adb = SimpleNamespace(
            ip="192.168.5.205",
            transaction=nullcontext,
        )
        self.adb_connected = False
        self._cleanup_done = False
        self._windows_session_ending = False
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
        self._resume_retry_timer = FakeTimer()
        self._resume_network_timer = FakeTimer()
        self.status_signal = FakeSignal()
        self.logs = []
        self.resume_reconnect_finished = FakeSignal()
        self.connection_recovery_requested = FakeSignal()
        self.reconnect_target_probe_finished = FakeSignal()
        self.resume_discovery_finished = FakeSignal()
        self.devices_signal = FakeSignal()
        self._scan_id = 0
        self.ip_entry = SimpleNamespace(setText=lambda _value: None)

    def log(self, message):
        self.logs.append(message)


class DeviceFeatureContractTests(unittest.TestCase):
    """捕获设备生命周期方法漏迁或散回主窗口。"""

    def test_device_lifecycle_is_owned_by_device_mixin(self):
        from mimonitor_toolbox.device_features import DeviceFeaturesMixin

        expected = {
            "connect",
            "disconnect_adb",
            "scan_net",
            "_finish_scan",
            "_monitor_adb_server",
            "_keep_adb_alive",
            "_check_guardian_status",
            "_refresh_page_data",
        }
        self.assertTrue(expected.issubset(vars(DeviceFeaturesMixin)))

    def test_multiple_mitv_results_leave_device_selector_unselected(self):
        class Combo:
            def __init__(self):
                self.items = []
                self.current_index = None

            def blockSignals(self, _blocked):
                pass

            def clear(self):
                self.items.clear()

            def addItem(self, text):
                self.items.append(text)

            def setCurrentIndex(self, index):
                self.current_index = index

        host = SimpleNamespace(_scan_id=7, dev_combo=Combo())
        devices = [
            ("192.168.5.5", "MiTV-MFFU1"),
            ("192.168.5.6", "MiTV-SECOND"),
        ]

        device_features.DeviceFeaturesMixin._update_scanned_devices(
            host,
            7,
            devices,
        )

        self.assertEqual(host.dev_combo.current_index, -1)


class TerminalLaunchTests(unittest.TestCase):
    def test_adb_cmd_opens_without_connected_device_using_private_server(self):
        messages = []
        host = SimpleNamespace(
            adb=SimpleNamespace(ip=None),
            adb_connected=False,
            log=messages.append,
            _show_message_box=lambda *args: messages.append(args),
        )
        opener = getattr(device_features.DeviceFeaturesMixin, "_open_adb_cmd", None)
        self.assertIsNotNone(opener)

        adb_path = os.path.abspath(os.path.join("test-runtime", "adb.exe"))
        adb_dir = os.path.dirname(adb_path)

        with mock.patch.object(device_features.sys, "platform", "win32"), \
                mock.patch.object(device_features, "ADB", adb_path), \
                mock.patch.object(device_features, "ADB_SERVER_PORT", "5038"), \
                mock.patch.object(device_features.subprocess, "Popen") as popen:
            opener(host)

        popen.assert_called_once_with(
            [
                "cmd.exe",
                "/k",
                "title Mimonitor ADB CMD & doskey adb=adb.exe -P 5038 $*",
            ],
            cwd=adb_dir,
            creationflags=device_features.CREATE_NEW_CONSOLE,
        )
        self.assertEqual(messages, ["正在打开 ADB CMD..."])


class WindowsResumeReconnectTests(unittest.TestCase):
    def test_device_lifecycle_starts_without_an_implicit_reconnect_target(self):
        class Host(device_features.DeviceFeaturesMixin, QObject):
            pass

        host = Host()
        host.initialize_device_features()

        self.assertEqual(host.adb.ip, "")

    def test_duplicate_resume_notifications_start_only_one_cycle(self):
        host = ReconnectHost()
        starts = []
        host._start_resume_reconnect = starts.append

        with mock.patch.object(device_features.time, "monotonic", side_effect=[100.0, 101.0]):
            host._handle_windows_resume()
            host._handle_windows_resume()

        self.assertEqual(starts, ["Windows 唤醒"])

    def test_resume_reconnect_clears_stale_transport_and_recovers(self):
        host = ReconnectHost()
        start = getattr(host, "_start_resume_reconnect", None)
        finish = getattr(host, "_finish_resume_reconnect_attempt", None)
        self.assertIsNotNone(start)
        self.assertIsNotNone(finish)
        host.resume_reconnect_finished.callback = finish
        commands = []

        def fake_adb_run(args, timeout=10, check=False):
            commands.append(args)
            if args[-1] == "getprop ro.product.model":
                return "MiTV-MONITOR"
            return ""

        with mock.patch.object(device_features, "adb_device_state", side_effect=["offline", "device"]), \
                mock.patch.object(device_features, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            start("Windows 唤醒")

        self.assertFalse(host._resume_reconnect_active)
        self.assertEqual(host._resume_reconnect_attempt, 1)
        self.assertEqual(
            commands,
            [
                ["disconnect", "192.168.5.205:5555"],
                ["connect", "192.168.5.205:5555"],
                ["-s", "192.168.5.205:5555", "shell", "getprop ro.product.model"],
            ],
        )
        self.assertEqual(host.status_signal.events[-1], ("已连接: MiTV-MONITOR",))

    def test_resume_reconnect_stops_after_five_failures_and_waits_for_network(self):
        host = ReconnectHost()
        start = getattr(host, "_start_resume_reconnect", None)
        run_attempt = getattr(host, "_run_resume_reconnect_attempt", None)
        finish = getattr(host, "_finish_resume_reconnect_attempt", None)
        self.assertIsNotNone(start)
        self.assertIsNotNone(run_attempt)
        self.assertIsNotNone(finish)
        host.resume_reconnect_finished.callback = finish
        host._resume_retry_timer.callback = run_attempt
        host._network_signature = lambda: ((7, "192.168.5.8", "192.168.5.0/24"),)
        commands = []

        with mock.patch.object(device_features, "adb_device_state", return_value="offline") as get_state, \
                mock.patch.object(device_features, "adb_run", side_effect=lambda args, **kwargs: commands.append(args) or ""), \
                mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            start("Windows 唤醒")

        self.assertEqual(host._resume_reconnect_attempt, 5)
        self.assertFalse(host._resume_reconnect_active)
        self.assertTrue(host._resume_waiting_for_network)
        self.assertEqual(get_state.call_count, 10)
        self.assertEqual(host._resume_retry_timer.start_calls, [3000, 3000, 3000, 3000])
        self.assertEqual(host._resume_network_timer.start_calls, [2000])
        self.assertEqual(
            commands.count(["connect", "192.168.5.205:5555"]),
            5,
        )
        self.assertIn("等待显示器或网络恢复", host.status_signal.events[-1][0])

    def test_fifth_failure_discovers_unique_mitv_and_switches_to_new_ip(self):
        host = ReconnectHost()
        reconnect_reasons = []
        entered_ips = []
        host.ip_entry = SimpleNamespace(setText=entered_ips.append)
        host._start_resume_reconnect = reconnect_reasons.append
        host.resume_discovery_finished.callback = lambda *args: (
            device_features.DeviceFeaturesMixin._finish_resume_discovery(host, *args)
        )

        with mock.patch.object(
            device_features,
            "scan_adb",
            return_value=[("192.168.5.5", "MiTV-MFFU1")],
        ), mock.patch.object(
            device_features,
            "async_run",
            side_effect=lambda fn: fn(),
        ), mock.patch.object(device_features, "update_settings") as save_settings:
            host._finish_resume_reconnect_attempt(0, 5, False, "not found")

        self.assertEqual(host.adb.ip, "192.168.5.5")
        self.assertEqual(entered_ips, ["192.168.5.5"])
        save_settings.assert_called_once_with({"saved_ip": "192.168.5.5"})
        self.assertEqual(reconnect_reasons, ["局域网发现显示器"])

    def test_waiting_recovery_throttles_full_network_discovery_to_thirty_seconds(self):
        host = ReconnectHost()
        host._resume_reconnect_generation = 4
        host._resume_waiting_for_network = True
        host._resume_network_signature = ((7, "192.168.5.8", "192.168.5.0/24"),)
        host._resume_next_discovery_at = 130.0
        host._network_signature = lambda: host._resume_network_signature
        host.reconnect_target_probe_finished.callback = host._finish_reconnect_target_probe
        host.resume_discovery_finished.callback = host._finish_resume_discovery

        with mock.patch.object(
            device_features.time,
            "monotonic",
            side_effect=[129.0, 130.0, 130.0, 130.0],
        ), mock.patch.object(
            device_features,
            "is_tcp_endpoint_open",
            return_value=False,
        ), mock.patch.object(
            device_features,
            "scan_adb",
            return_value=[],
        ) as scan, mock.patch.object(
            device_features,
            "async_run",
            side_effect=lambda fn: fn(),
        ):
            host._check_resume_network_change()
            host._check_resume_network_change()

        scan.assert_called_once()
        self.assertEqual(host._resume_next_discovery_at, 160.0)

    def test_fresh_old_ip_retry_cycle_preserves_existing_full_scan_deadline(self):
        host = ReconnectHost()
        host._resume_waiting_for_network = True
        host._resume_next_discovery_at = 200.0
        host._network_signature = lambda: ((7, "192.168.5.8", "192.168.5.0/24"),)
        host._run_resume_reconnect_attempt = lambda: None

        with mock.patch.object(
            device_features.time,
            "monotonic",
            return_value=100.0,
        ), mock.patch.object(device_features, "scan_adb") as scan:
            host._start_resume_reconnect("旧 IP 端口恢复")
            host._finish_resume_reconnect_attempt(
                host._resume_reconnect_generation,
                5,
                False,
                "not found",
            )

        scan.assert_not_called()
        self.assertEqual(host._resume_next_discovery_at, 200.0)

    def test_due_discovery_waits_for_inflight_old_ip_probe(self):
        host = ReconnectHost()
        host._resume_reconnect_generation = 4
        host._resume_waiting_for_network = True
        host._resume_network_signature = ((7, "192.168.5.8", "192.168.5.0/24"),)
        host._resume_target_probe_checking = True
        host._resume_next_discovery_at = 100.0
        host._network_signature = lambda: host._resume_network_signature

        with mock.patch.object(
            device_features.time,
            "monotonic",
            return_value=100.0,
        ), mock.patch.object(device_features, "scan_adb") as scan:
            host._check_resume_network_change()

        scan.assert_not_called()
        self.assertFalse(host._resume_discovery_checking)

    def test_recovery_discovery_waits_for_manual_scan_to_finish(self):
        host = ReconnectHost()
        host._resume_waiting_for_network = True
        host._scan_running = True

        with mock.patch.object(
            device_features.time,
            "monotonic",
            return_value=100.0,
        ), mock.patch.object(device_features, "scan_adb") as scan:
            host._start_resume_discovery()

        scan.assert_not_called()
        self.assertFalse(host._resume_discovery_checking)
        self.assertEqual(host._resume_next_discovery_at, 130.0)

    def test_manual_scan_is_ignored_while_recovery_discovery_is_running(self):
        host = ReconnectHost()
        host._connection_in_progress = False
        host._scan_running = False
        host._resume_discovery_checking = True
        host.scan_btn = SimpleNamespace(setEnabled=lambda _enabled: None)
        host.dev_combo = SimpleNamespace(clear=lambda: None)
        workers = []

        with mock.patch.object(device_features, "async_run", side_effect=workers.append):
            host.scan_net()

        self.assertEqual(workers, [])
        self.assertEqual(host.logs[-1], "显示器地址扫描已在进行中，忽略重复请求")

    def test_manual_scan_is_ignored_while_old_ip_probe_is_running(self):
        host = ReconnectHost()
        host._connection_in_progress = False
        host._scan_running = False
        host._resume_target_probe_checking = True
        host.scan_btn = SimpleNamespace(setEnabled=lambda _enabled: None)
        host.dev_combo = SimpleNamespace(clear=lambda: None)
        workers = []

        with mock.patch.object(device_features, "async_run", side_effect=workers.append):
            host.scan_net()

        self.assertEqual(workers, [])
        self.assertEqual(host.logs[-1], "旧 IP 状态探测正在进行，忽略内网扫描")

    def test_old_ip_probe_waits_for_manual_scan_to_finish(self):
        host = ReconnectHost()
        host._resume_reconnect_generation = 4
        host._resume_waiting_for_network = True
        host._scan_running = True
        host._resume_network_signature = ((7, "192.168.5.8", "192.168.5.0/24"),)
        host._network_signature = lambda: host._resume_network_signature

        with mock.patch.object(
            device_features,
            "is_tcp_endpoint_open",
        ) as probe, mock.patch.object(
            device_features,
            "async_run",
            side_effect=lambda fn: fn(),
        ):
            host._check_resume_network_change()

        probe.assert_not_called()
        self.assertFalse(host._resume_target_probe_checking)

    def test_recovery_does_not_auto_select_when_multiple_mitv_devices_are_found(self):
        host = ReconnectHost()
        host._resume_reconnect_generation = 4
        host._resume_waiting_for_network = True
        reconnect_reasons = []
        host._start_resume_reconnect = reconnect_reasons.append
        devices = [
            ("192.168.5.5", "MiTV-MFFU1"),
            ("192.168.5.6", "MiTV-SECOND"),
            ("192.168.5.7", "Android TV"),
        ]

        with mock.patch.object(device_features.time, "monotonic", return_value=100.0):
            host._finish_resume_discovery(4, devices, "")

        self.assertEqual(host.adb.ip, "192.168.5.205")
        self.assertEqual(reconnect_reasons, [])
        self.assertEqual(
            host.devices_signal.events,
            [(0, devices[:2])],
        )
        self.assertIn("发现 2 台", host.status_signal.events[-1][0])

    def test_multiple_device_ambiguity_blocks_later_single_result_from_switching_ip(self):
        host = ReconnectHost()
        host._resume_reconnect_generation = 4
        host._resume_waiting_for_network = True
        reconnect_reasons = []
        host._start_resume_reconnect = reconnect_reasons.append

        with mock.patch.object(
            device_features.time,
            "monotonic",
            side_effect=[100.0, 110.0],
        ), mock.patch.object(device_features, "update_settings") as save_settings:
            host._finish_resume_discovery(
                4,
                [
                    ("192.168.5.5", "MiTV-MFFU1"),
                    ("192.168.5.6", "MiTV-SECOND"),
                ],
                "",
            )
            host._finish_resume_discovery(
                4,
                [("192.168.5.5", "MiTV-MFFU1")],
                "",
            )

        self.assertEqual(host.adb.ip, "192.168.5.205")
        self.assertEqual(reconnect_reasons, [])
        save_settings.assert_not_called()

    def test_network_change_starts_a_fresh_five_attempt_cycle(self):
        host = ReconnectHost()
        check_network = getattr(host, "_check_resume_network_change", None)
        self.assertIsNotNone(check_network)
        host._resume_reconnect_generation = 4
        host._resume_reconnect_attempt = 5
        host._resume_waiting_for_network = True
        host._resume_network_signature = ((7, "192.168.5.8", "192.168.5.0/24"),)
        attempts = []
        host._run_resume_reconnect_attempt = lambda: attempts.append("attempt")

        host._network_signature = lambda: host._resume_network_signature
        check_network()
        self.assertEqual(attempts, [])

        host._network_signature = lambda: ((11, "192.168.6.9", "192.168.6.0/24"),)
        check_network()

        self.assertEqual(attempts, ["attempt"])
        self.assertEqual(host._resume_reconnect_generation, 5)
        self.assertEqual(host._resume_reconnect_attempt, 0)
        self.assertFalse(host._resume_waiting_for_network)

    def test_new_reconnect_cycle_releases_an_inflight_target_probe(self):
        host = ReconnectHost()
        host._resume_reconnect_generation = 4
        host._resume_reconnect_attempt = 5
        host._resume_waiting_for_network = True
        host._resume_target_probe_checking = True
        host._resume_network_signature = ((7, "192.168.5.8", "192.168.5.0/24"),)
        host._network_signature = lambda: ((11, "192.168.6.9", "192.168.6.0/24"),)
        host._run_resume_reconnect_attempt = lambda: None

        host._check_resume_network_change()

        self.assertFalse(host._resume_target_probe_checking)

    def test_display_wake_starts_a_fresh_cycle_when_target_port_reopens(self):
        host = ReconnectHost()
        host._resume_reconnect_generation = 4
        host._resume_reconnect_attempt = 5
        host._resume_waiting_for_network = True
        host._resume_network_signature = ((7, "192.168.5.8", "192.168.5.0/24"),)
        host._resume_target_available = None
        host._network_signature = lambda: host._resume_network_signature
        attempts = []
        host._run_resume_reconnect_attempt = lambda: attempts.append("attempt")
        finish_probe = getattr(host, "_finish_reconnect_target_probe", None)
        self.assertIsNotNone(finish_probe)
        host.reconnect_target_probe_finished.callback = finish_probe

        with mock.patch.object(
            device_features,
            "is_tcp_endpoint_open",
            side_effect=[False, True],
            create=True,
        ), mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            host._check_resume_network_change()
            self.assertEqual(attempts, [])
            self.assertFalse(host._resume_target_available)

            host._check_resume_network_change()

        self.assertEqual(attempts, ["attempt"])
        self.assertEqual(host._resume_reconnect_generation, 5)
        self.assertFalse(host._resume_waiting_for_network)

    def test_continuously_open_target_does_not_start_repeated_cycles(self):
        host = ReconnectHost()
        host._resume_reconnect_generation = 4
        host._resume_waiting_for_network = True
        host._resume_target_available = False
        attempts = []
        host._run_resume_reconnect_attempt = lambda: attempts.append("attempt")

        host._finish_reconnect_target_probe(4, True)
        self.assertEqual(attempts, ["attempt"])

        host._resume_reconnect_active = False
        host._resume_waiting_for_network = True
        host._finish_reconnect_target_probe(5, True)
        host._finish_reconnect_target_probe(5, True)

        self.assertEqual(attempts, ["attempt"])

    def test_late_target_probe_result_is_ignored_after_cancellation(self):
        host = ReconnectHost()
        host._resume_reconnect_generation = 4
        host._resume_waiting_for_network = True
        host._resume_target_probe_checking = True
        attempts = []
        host._start_resume_reconnect = attempts.append

        host._cancel_resume_reconnect()
        host._finish_reconnect_target_probe(4, True)

        self.assertEqual(attempts, [])
        self.assertFalse(host._resume_waiting_for_network)
        self.assertIsNone(host._resume_target_available)

    def test_network_signature_keeps_each_physical_adapter_and_original_prefix(self):
        host = ReconnectHost()
        records = [
            SimpleNamespace(
                interface_index=7,
                interface_name="Wi-Fi",
                local_ip="192.168.5.8",
                prefix_length=24,
                metric=20,
                if_type=71,
                oper_status=1,
                hardware_interface=True,
                filter_interface=False,
                media_connected=True,
                endpoint_interface=False,
            ),
            SimpleNamespace(
                interface_index=11,
                interface_name="Ethernet",
                local_ip="192.168.5.9",
                prefix_length=24,
                metric=10,
                if_type=6,
                oper_status=1,
                hardware_interface=True,
                filter_interface=False,
                media_connected=True,
                endpoint_interface=False,
            ),
        ]

        with mock.patch.object(
            device_features,
            "enumerate_windows_adapter_addresses",
            return_value=records,
            create=True,
        ):
            signature = host._network_signature()

        self.assertEqual(
            signature,
            (
                (7, "Wi-Fi", "192.168.5.8", 24, 20, 71, 1, True),
                (11, "Ethernet", "192.168.5.9", 24, 10, 6, 1, True),
            ),
        )

        records[0].prefix_length = 23
        with mock.patch.object(
            device_features,
            "enumerate_windows_adapter_addresses",
            return_value=records,
            create=True,
        ):
            changed_signature = host._network_signature()

        self.assertNotEqual(changed_signature, signature)

    def test_unknown_network_signature_does_not_restart_waiting_cycle(self):
        host = ReconnectHost()
        host._resume_waiting_for_network = True
        host._resume_network_signature = (
            (7, "Wi-Fi", "192.168.5.8", 24, 20, 71, 1, True),
        )
        attempts = []
        host._run_resume_reconnect_attempt = lambda: attempts.append("attempt")
        host._network_signature = lambda: None

        host._check_resume_network_change()

        self.assertEqual(attempts, [])
        self.assertTrue(host._resume_waiting_for_network)

    def test_manual_disconnect_cancels_retries_and_network_waiting(self):
        host = ReconnectHost()
        host._resume_reconnect_active = True
        host._resume_reconnect_checking = True
        host._resume_waiting_for_network = True

        with mock.patch.object(device_features, "adb_run", return_value=""), \
                mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            host.disconnect_adb()

        self.assertEqual(host.adb.ip, "")
        self.assertFalse(host._resume_reconnect_active)
        self.assertFalse(host._resume_reconnect_checking)
        self.assertFalse(host._resume_waiting_for_network)
        self.assertGreater(host._resume_retry_timer.stop_calls, 0)
        self.assertGreater(host._resume_network_timer.stop_calls, 0)

    def test_manual_disconnect_runs_after_an_inflight_resume_attempt(self):
        host = ReconnectHost()
        adb_lock = threading.RLock()

        class LockedAdb:
            ip = "192.168.5.205"

            def transaction(self):
                return adb_lock

        host.adb = LockedAdb()
        host.resume_reconnect_finished.callback = host._finish_resume_reconnect_attempt
        state_check_started = threading.Event()
        allow_resume = threading.Event()
        manual_worker_started = threading.Event()
        allow_manual = threading.Event()
        commands = []
        workers = []

        def fake_async_run(fn):
            worker = threading.Thread(target=fn, name=f"worker-{len(workers) + 1}")
            workers.append(worker)
            worker.start()

        state_calls = 0

        def fake_device_state(serial, timeout=3):
            nonlocal state_calls
            state_calls += 1
            if state_calls == 1:
                state_check_started.set()
                self.assertTrue(allow_resume.wait(2))
            return "offline"

        def fake_adb_run(args, timeout=10, check=False):
            if threading.current_thread().name == "worker-2":
                manual_worker_started.set()
                self.assertTrue(allow_manual.wait(2))
            with adb_lock:
                commands.append(args)
            return ""

        with mock.patch.object(device_features, "adb_device_state", side_effect=fake_device_state), \
                mock.patch.object(device_features, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(device_features, "async_run", side_effect=fake_async_run):
            host._start_resume_reconnect("Windows 唤醒")
            self.assertTrue(state_check_started.wait(2))
            host.disconnect_adb()
            self.assertTrue(manual_worker_started.wait(2))
            ip_while_manual_disconnect_is_pending = host.adb.ip
            allow_manual.set()
            time.sleep(0.05)
            allow_resume.set()
            for worker in workers:
                worker.join(2)
                self.assertFalse(worker.is_alive())

        self.assertEqual(ip_while_manual_disconnect_is_pending, "")
        self.assertEqual(commands[-1], ["disconnect", "192.168.5.205:5555"])

    def test_resume_worker_aborts_when_manual_disconnect_wins_the_race(self):
        host = ReconnectHost()
        test_case = self
        transaction_waiting = threading.Event()
        allow_transaction = threading.Event()
        manual_disconnect_done = threading.Event()
        commands = []
        workers = []

        class GatedTransaction:
            def __enter__(self):
                transaction_waiting.set()
                test_case.assertTrue(allow_transaction.wait(2))

            def __exit__(self, exc_type, exc_value, traceback):
                return False

        host.adb.transaction = GatedTransaction
        host.resume_reconnect_finished.callback = host._finish_resume_reconnect_attempt

        def fake_async_run(fn):
            worker = threading.Thread(target=fn)
            workers.append(worker)
            worker.start()

        def fake_adb_run(args, timeout=10, check=False):
            commands.append(args)
            if args[0] == "disconnect" and len(commands) == 1:
                manual_disconnect_done.set()
            return ""

        with mock.patch.object(device_features, "adb_device_state", return_value="offline"), \
                mock.patch.object(device_features, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(device_features, "async_run", side_effect=fake_async_run):
            host._start_resume_reconnect("Windows 唤醒")
            self.assertTrue(transaction_waiting.wait(2))
            host.disconnect_adb()
            self.assertTrue(manual_disconnect_done.wait(2))
            allow_transaction.set()
            for worker in workers:
                worker.join(2)
                self.assertFalse(worker.is_alive())

        self.assertNotIn(["connect", "192.168.5.205:5555"], commands)

    def test_keepalive_aborts_stale_reconnect_after_manual_disconnect(self):
        host = ReconnectHost()
        host.adb_connected = True
        host._adb_keepalive_checking = False
        host._adb_busy_until = 0.0
        state_check_started = threading.Event()
        allow_state_check = threading.Event()
        manual_disconnect_done = threading.Event()
        commands = []
        workers = []

        def fake_async_run(fn):
            worker = threading.Thread(target=fn)
            workers.append(worker)
            worker.start()

        def fake_device_state(serial, timeout=3):
            state_check_started.set()
            self.assertTrue(allow_state_check.wait(2))
            return "offline"

        def fake_adb_run(args, timeout=10, check=False):
            commands.append(args)
            if args[0] == "disconnect" and threading.current_thread() is not workers[0]:
                manual_disconnect_done.set()
            return ""

        with mock.patch.object(device_features, "adb_device_state", side_effect=fake_device_state), \
                mock.patch.object(device_features, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(device_features, "async_run", side_effect=fake_async_run):
            host._keep_adb_alive()
            self.assertTrue(state_check_started.wait(2))
            host.disconnect_adb()
            self.assertTrue(manual_disconnect_done.wait(2))
            allow_state_check.set()
            for worker in workers:
                worker.join(2)
                self.assertFalse(worker.is_alive())

        self.assertNotIn(["connect", "192.168.5.205:5555"], commands)

    def test_failed_keepalive_requests_display_sleep_recovery(self):
        host = ReconnectHost()
        host.adb_connected = True
        host._adb_keepalive_checking = False
        host._adb_busy_until = 0.0
        requests = []
        host.connection_recovery_requested.callback = lambda *args: requests.append(args)

        with mock.patch.object(device_features, "adb_device_state", side_effect=["offline", "offline"]), \
                mock.patch.object(device_features, "adb_run", return_value=""), \
                mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            host._keep_adb_alive()

        self.assertEqual(
            requests,
            [(0, "192.168.5.205", "显示器休眠或临时断连")],
        )

    def test_delayed_recovery_request_cannot_apply_to_a_new_target(self):
        host = ReconnectHost()
        handler = getattr(host, "_handle_connection_recovery_request", None)
        self.assertIsNotNone(handler)
        starts = []
        host._start_resume_reconnect = starts.append
        host._connection_intent_generation = 8
        host.adb.ip = "192.168.6.206"

        handler(7, "192.168.5.205", "显示器休眠或临时断连")

        self.assertEqual(starts, [])

    def test_keepalive_drops_stale_result_after_target_changes(self):
        host = ReconnectHost()
        host.adb_connected = True
        host._adb_keepalive_checking = False
        host._adb_busy_until = 0.0
        state_calls = 0

        def fake_device_state(_serial, timeout=3):
            nonlocal state_calls
            state_calls += 1
            if state_calls == 2:
                host._invalidate_connection_intent()
                host.adb.ip = "192.168.6.206"
            return "offline"

        with mock.patch.object(device_features, "adb_device_state", side_effect=fake_device_state), \
                mock.patch.object(device_features, "adb_run", return_value=""), \
                mock.patch.object(device_features, "async_run", side_effect=lambda fn: fn()):
            host._keep_adb_alive()

        self.assertEqual(host.connection_recovery_requested.events, [])
        self.assertFalse(any(event[0].startswith("未连接") for event in host.status_signal.events))

    def test_server_monitor_aborts_stale_reconnect_after_manual_disconnect(self):
        host = ReconnectHost()
        host.adb_connected = True
        host._adb_server_monitor_checking = False
        host._adb_server_retry_after = 0.0
        host.adb_server_event = FakeSignal()
        server_probe_started = threading.Event()
        allow_server_probe = threading.Event()
        manual_disconnect_done = threading.Event()
        commands = []
        workers = []
        probe_calls = 0

        def fake_async_run(fn):
            worker = threading.Thread(target=fn)
            workers.append(worker)
            worker.start()

        def fake_server_alive(timeout=0.2):
            nonlocal probe_calls
            probe_calls += 1
            if probe_calls == 1:
                server_probe_started.set()
                self.assertTrue(allow_server_probe.wait(2))
                return False
            return True

        def fake_adb_run(args, timeout=10, check=False):
            commands.append(args)
            if args[0] == "disconnect":
                manual_disconnect_done.set()
            return ""

        with mock.patch.object(device_features, "is_adb_server_alive", side_effect=fake_server_alive), \
                mock.patch.object(device_features, "adb_run", side_effect=fake_adb_run), \
                mock.patch.object(device_features, "async_run", side_effect=fake_async_run):
            host._monitor_adb_server()
            self.assertTrue(server_probe_started.wait(2))
            host.disconnect_adb()
            self.assertTrue(manual_disconnect_done.wait(2))
            allow_server_probe.set()
            for worker in workers:
                worker.join(2)
                self.assertFalse(worker.is_alive())

        self.assertNotIn(["connect", "192.168.5.205:5555"], commands)

    def test_manual_connect_worker_aborts_when_disconnect_finishes_first(self):
        host = ReconnectHost()
        host._connection_in_progress = False
        host._cancel_scan = lambda _reason: False
        host.ip_entry = SimpleNamespace(text=lambda: "192.168.5.205")
        host.message_signal = FakeSignal()
        calls = []
        workers = []
        host.adb.connect = lambda: calls.append("connect") or False

        with mock.patch.object(device_features, "async_run", side_effect=workers.append):
            host.connect()
            host.disconnect_adb()

        self.assertEqual(len(workers), 2)
        workers[1]()
        workers[0]()

        self.assertNotIn("connect", calls)

    def test_startup_connect_worker_aborts_when_disconnect_finishes_first(self):
        host = ReconnectHost()
        host._connection_in_progress = False
        host.ip_entry = SimpleNamespace(setText=lambda _value: None)
        host.auto_scan_signal = FakeSignal()
        calls = []
        workers = []
        host.adb.connect = lambda: calls.append("connect") or False

        with mock.patch.object(device_features, "load_settings", return_value={"saved_ip": "192.168.5.205"}), \
                mock.patch.object(device_features, "async_run", side_effect=workers.append):
            host._auto_connect_on_startup()
            host.disconnect_adb()

        self.assertEqual(len(workers), 2)
        workers[1]()
        workers[0]()

        self.assertNotIn("connect", calls)

    def test_adb_action_worker_aborts_when_disconnect_finishes_first(self):
        host = ReconnectHost()
        host.adb_connected = True
        host.adb_action_finished = FakeSignal(host._finish_adb_action)
        calls = []
        workers = []
        host.adb.ensure_connected = lambda: calls.append("ensure") or (True, "device")

        with mock.patch.object(device_features, "async_run", side_effect=workers.append):
            host._run_adb_action(
                "测试操作",
                lambda: calls.append("operation"),
                on_success=lambda: calls.append("success"),
                on_failure=lambda: calls.append("failure"),
            )
            host.disconnect_adb()

        self.assertEqual(len(workers), 2)
        workers[1]()
        workers[0]()

        self.assertNotIn("ensure", calls)
        self.assertNotIn("operation", calls)
        self.assertNotIn("success", calls)
        self.assertEqual(calls.count("failure"), 1)
        self.assertFalse(any("测试操作失败" in message for message in host.logs))


if __name__ == "__main__":
    unittest.main()
