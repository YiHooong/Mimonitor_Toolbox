import tempfile
import unittest
from pathlib import Path
from unittest import mock


class CoreSettingsTests(unittest.TestCase):
    """捕获配置损坏时崩溃、更新时覆盖其他字段这两类回归。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_invalid_json_falls_back_to_complete_defaults(self):
        from mimonitor_toolbox import core

        self.config_path.write_text("not json", encoding="utf-8")
        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            settings = core.load_settings()

        self.assertEqual(settings["close_behavior"], "tray")
        self.assertEqual(settings["local_dimming_memory"], {"sdr": None, "hdr": None})

    def test_update_settings_preserves_existing_keys(self):
        from mimonitor_toolbox import core

        with mock.patch.object(core, "get_settings_path", return_value=str(self.config_path)):
            self.assertTrue(core.update_settings({"saved_ip": "192.168.1.8"}))
            self.assertTrue(core.update_settings({"close_behavior": "exit"}))
            settings = core.load_settings()

        self.assertEqual(settings["saved_ip"], "192.168.1.8")
        self.assertEqual(settings["close_behavior"], "exit")

    def test_source_mode_base_dir_is_project_root(self):
        from mimonitor_toolbox import core

        expected = Path(__file__).resolve().parents[1]
        with mock.patch.object(core.sys, "frozen", False, create=True):
            actual = Path(core.get_app_base_dir()).resolve()

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
