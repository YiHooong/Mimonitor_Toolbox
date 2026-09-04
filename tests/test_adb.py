import threading
import unittest
from unittest import mock


class AdbModuleTests(unittest.TestCase):
    """捕获私有 server 端口丢失和 MiTV 型号识别失效。"""

    def test_adb_command_uses_private_server_port(self):
        from mimonitor_toolbox import adb as adb_runtime

        with mock.patch.object(adb_runtime, "ADB", "adb-test"), \
                mock.patch.object(adb_runtime, "ADB_SERVER_PORT", "5038"):
            command = adb_runtime.adb_command(["version"])

        self.assertEqual(command, ["adb-test", "-P", "5038", "version"])

    def test_mitv_matching_is_case_insensitive(self):
        from mimonitor_toolbox import adb as adb_runtime

        self.assertTrue(adb_runtime.is_mitv_model("xiaomi mitv pro"))
        self.assertFalse(adb_runtime.is_mitv_model("Android TV"))

    def test_scan_without_physical_network_raises_domain_error(self):
        from mimonitor_toolbox import adb as adb_runtime

        with mock.patch.object(adb_runtime, "get_windows_scan_networks", return_value=[]):
            with self.assertRaises(adb_runtime.WindowsAdapterError):
                adb_runtime.scan_adb()

    def test_adb_target_has_no_personal_default_and_formats_one_serial(self):
        from mimonitor_toolbox import adb as adb_runtime

        self.assertEqual(adb_runtime.Adb().serial, "")
        self.assertEqual(
            adb_runtime.Adb("192.168.1.9").serial,
            "192.168.1.9:5555",
        )

    def test_mtk_command_builder_preserves_tvservice_escaping(self):
        from mimonitor_toolbox import adb as adb_runtime

        command = adb_runtime.build_tvservice_app_process_command(
            "/data/data/mitv.service/cache/MtkDirectTool.jar",
            ["MtkDirectTool", "set", "g_picture_mode", 7, 3],
        )

        self.assertEqual(
            command,
            'service call TvService 3 s16 "sh -c eval\\${IFS}'
            'CLASSPATH=/data/data/mitv.service/cache/MtkDirectTool.jar'
            '\\${IFS}/system/bin/app_process'
            '\\${IFS}/data/data/mitv.service/cache'
            '\\${IFS}MtkDirectTool\\${IFS}set'
            '\\${IFS}g_picture_mode\\${IFS}7\\${IFS}3"',
        )

    def test_jni_batch_parser_ignores_markers_and_error_values(self):
        from mimonitor_toolbox import adb as adb_runtime

        output = """\
__MIMONITOR_BATCH_BEGIN__
picture_mode=7
bad=ERROR: unavailable
label=cinema
malformed
__MIMONITOR_BATCH_END__
"""

        self.assertEqual(
            adb_runtime.parse_jni_batch_output(output),
            {"picture_mode": 7, "label": "cinema"},
        )

    def test_jar_healing_pushes_mismatched_file_and_copies_it(self):
        from mimonitor_toolbox import adb as adb_runtime

        target = adb_runtime.Adb("192.168.1.9")
        shell_calls = []

        def shell(command, check=False):
            shell_calls.append((command, check))
            return "1200" if command.startswith("stat -c") else ""

        with mock.patch.object(target, "shell", side_effect=shell), \
                mock.patch.object(adb_runtime, "get_colorful_led_tool_path", return_value="/tmp/ColorfulLedTool.jar"), \
                mock.patch.object(adb_runtime.os.path, "getsize", return_value=1300), \
                mock.patch.object(adb_runtime, "adb_run") as run:
            healed = target.check_and_heal_colorful_led_tool(check=True)

        self.assertTrue(healed)
        run.assert_called_once_with(
            [
                "-s", "192.168.1.9:5555", "push",
                "/tmp/ColorfulLedTool.jar", "/sdcard/ColorfulLedTool.jar",
            ],
            check=True,
        )
        self.assertEqual(
            shell_calls,
            [
                (
                    "stat -c %s /sdcard/ColorfulLedTool.jar "
                    "2>/dev/null || echo 0",
                    True,
                ),
                (
                    "service call TvService 3 s16 \"cp "
                    "/sdcard/ColorfulLedTool.jar "
                    "/data/data/mitv.service/cache/ColorfulLedTool.jar\"",
                    True,
                ),
            ],
        )

    def test_async_run_reports_uncaught_worker_exception(self):
        from mimonitor_toolbox import adb as adb_runtime

        reported = []
        report_ready = threading.Event()

        def report(error):
            reported.append(error)
            report_ready.set()

        def fail_in_worker():
            raise RuntimeError("worker exploded")

        adb_runtime.set_async_error_handler(report)
        try:
            worker = adb_runtime.async_run(fail_in_worker)
            worker.join(timeout=1)
        finally:
            adb_runtime.set_async_error_handler(None)

        self.assertTrue(report_ready.is_set())
        self.assertEqual(len(reported), 1)
        self.assertIsInstance(reported[0], RuntimeError)
        self.assertEqual(str(reported[0]), "worker exploded")

    def test_persistent_runtime_failure_falls_back_and_records_warning(self):
        from mimonitor_toolbox import adb as adb_runtime

        adb_runtime.drain_startup_warnings()
        with mock.patch.object(adb_runtime.sys, "platform", "win32"), \
                mock.patch.object(adb_runtime.sys, "frozen", True, create=True), \
                mock.patch.object(adb_runtime, "get_app_data_dir", return_value="C:/app-data"), \
                mock.patch.object(adb_runtime.os, "makedirs", side_effect=PermissionError("denied")):
            selected = adb_runtime.ensure_persistent_adb_runtime("C:/bundle/adb.exe")

        self.assertEqual(selected, "C:/bundle/adb.exe")
        warnings = adb_runtime.drain_startup_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertIn("ADB 运行时准备失败", warnings[0])
        self.assertIn("denied", warnings[0])


class ConnectByDeviceStateTests(unittest.TestCase):
    """connect 输出为空（客户端超时被杀）时以 get-state 为唯一裁决。"""

    def _patch_adb_run(self, connect_output, states):
        """states: get-state 依次返回的值列表（每次调用消耗一个）。"""
        from mimonitor_toolbox import adb as adb_runtime
        state_outputs = list(states)
        calls = []

        def fake_adb_run(args, timeout=10, check=False):
            calls.append(list(args))
            if args[0] == "connect":
                return connect_output
            if "get-state" in args:
                return state_outputs.pop(0) if state_outputs else "unknown"
            return ""

        return mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run), calls

    def test_manual_connect_succeeds_on_empty_output_with_device_state(self):
        from mimonitor_toolbox import adb as adb_runtime

        # connect 输出为空 + 首次 get-state 即 device（server 已建好 transport）
        patcher, calls = self._patch_adb_run("", ["device"])
        with patcher:
            ok = adb_runtime.Adb("192.168.1.9").connect()

        self.assertTrue(ok)
        get_state_called = any("get-state" in c for c in calls)
        self.assertTrue(get_state_called)

    def test_manual_connect_succeeds_after_empty_connect_output_then_device(self):
        from mimonitor_toolbox import adb as adb_runtime

        # connect 空输出，首次 get-state 失败、connect 后再查为 device
        patcher, _ = self._patch_adb_run("", ["offline", "device"])
        with patcher:
            ok = adb_runtime.Adb("192.168.1.9").connect()

        self.assertTrue(ok)

    def test_manual_connect_fails_on_empty_output_and_bad_state(self):
        from mimonitor_toolbox import adb as adb_runtime

        patcher, _ = self._patch_adb_run("", ["offline", "offline"])
        with patcher:
            ok = adb_runtime.Adb("192.168.1.9").connect()

        self.assertFalse(ok)

    def _scan_with(self, state_output, model_output):
        import ipaddress

        from mimonitor_toolbox import adb as adb_runtime
        networks = [mock.Mock(ip=ipaddress.ip_address("192.168.1.0"), netmask="255.255.255.0")]
        probe_results = [mock.Mock(ip=ipaddress.ip_address("192.168.1.9"))]

        def fake_adb_run(args, timeout=10, check=False):
            if args[0] == "connect":
                return ""  # 客户端超时被杀，空输出
            if "get-state" in args:
                return state_output
            if "getprop ro.product.model" in args:
                return model_output
            return ""

        with mock.patch.object(adb_runtime, "get_windows_scan_networks", return_value=networks), \
                mock.patch.object(adb_runtime, "build_probe_targets", return_value=probe_results), \
                mock.patch.object(adb_runtime, "probe_tcp_targets", return_value=probe_results), \
                mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run):
            return adb_runtime.scan_adb()

    def test_scan_discovers_device_with_empty_connect_output(self):
        self.assertEqual(
            self._scan_with("device", "xiaomi mitv pro"),
            [("192.168.1.9", "xiaomi mitv pro")],
        )

    def test_scan_skips_device_with_empty_output_and_offline_state(self):
        self.assertEqual(self._scan_with("offline", ""), [])

    def test_reconnect_disconnects_then_ensures_device_state(self):
        from mimonitor_toolbox import adb as adb_runtime

        # reconnect = disconnect 清 stale transport + ensure_connected
        state_outputs = ["offline", "device"]
        calls = []

        def fake_adb_run(args, timeout=10, check=False):
            calls.append(list(args))
            if args[0] == "disconnect":
                return "disconnected"
            if args[0] == "connect":
                return "connected to 192.168.1.9:5555"
            if "get-state" in args:
                return state_outputs.pop(0) if state_outputs else "unknown"
            return ""

        with mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run):
            ok, state = adb_runtime.Adb("192.168.1.9").reconnect()

        self.assertTrue(ok)
        self.assertEqual(state, "device")
        self.assertEqual(calls, [
            ["disconnect", "192.168.1.9:5555"],
            ["-s", "192.168.1.9:5555", "get-state"],
            ["connect", "192.168.1.9:5555"],
            ["-s", "192.168.1.9:5555", "get-state"],
        ])

    def test_reconnect_fails_when_state_stays_offline(self):
        from mimonitor_toolbox import adb as adb_runtime

        def fake_adb_run(args, timeout=10, check=False):
            if args[0] == "disconnect":
                return "disconnected"
            if args[0] == "connect":
                return "connected to 192.168.1.9:5555"
            if "get-state" in args:
                return "offline"
            return ""

        with mock.patch.object(adb_runtime, "adb_run", side_effect=fake_adb_run):
            ok, state = adb_runtime.Adb("192.168.1.9").reconnect()

        self.assertFalse(ok)
        self.assertEqual(state, "offline")


if __name__ == "__main__":
    unittest.main()