import unittest
from types import SimpleNamespace
from unittest import mock

from mimonitor_toolbox import device_features


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

        with mock.patch.object(device_features.sys, "platform", "win32"), \
                mock.patch.object(device_features, "ADB", "/opt/mimonitor/adb.exe"), \
                mock.patch.object(device_features, "ADB_SERVER_PORT", "5038"), \
                mock.patch.object(device_features.subprocess, "Popen") as popen:
            opener(host)

        popen.assert_called_once_with(
            [
                "cmd.exe",
                "/k",
                "title Mimonitor ADB CMD & doskey adb=adb.exe -P 5038 $*",
            ],
            cwd="/opt/mimonitor",
            creationflags=device_features.CREATE_NEW_CONSOLE,
        )
        self.assertEqual(messages, ["正在打开 ADB CMD..."])


if __name__ == "__main__":
    unittest.main()
