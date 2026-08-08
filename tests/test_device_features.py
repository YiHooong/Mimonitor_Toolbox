import unittest


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


if __name__ == "__main__":
    unittest.main()
