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


if __name__ == "__main__":
    unittest.main()
