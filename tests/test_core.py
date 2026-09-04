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


class CleanupStaleExtractDirsTests(unittest.TestCase):
    """验证 onefile/_MEI 残留清理只删除死实例的解压目录。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_dir(self, name, dll_name="python313.dll"):
        d = self.base / name
        d.mkdir()
        if dll_name:
            (d / dll_name).write_bytes(b"fake dll")
        return d

    def test_removes_dead_extract_dirs(self):
        from mimonitor_toolbox import core

        dead_onefile = self._make_dir("onefile_123_456")
        dead_mei = self._make_dir("_MEI12345")
        self._make_dir("unrelated_dir", dll_name=None)

        core.cleanup_stale_extract_dirs(str(self.base))

        self.assertFalse(dead_onefile.exists())
        self.assertFalse(dead_mei.exists())

    def test_skips_dirs_without_python_dll(self):
        from mimonitor_toolbox import core

        suspicious = self._make_dir("onefile_789", dll_name=None)
        (suspicious / "some.txt").write_text("data", encoding="utf-8")

        core.cleanup_stale_extract_dirs(str(self.base))

        self.assertTrue(suspicious.exists())

    def test_skips_locked_dll_dir(self):
        from mimonitor_toolbox import core

        locked = self._make_dir("onefile_999_1")
        dll_path = locked / "python313.dll"
        original_open = open

        def refusing_open(path, mode="r", *args, **kwargs):
            if str(path) == str(dll_path) and "+" in mode:
                raise PermissionError(13, "denied")
            return original_open(path, mode, *args, **kwargs)

        with mock.patch("builtins.open", side_effect=refusing_open):
            core.cleanup_stale_extract_dirs(str(self.base))

        self.assertTrue(locked.exists())
        self.assertTrue(dll_path.exists())


if __name__ == "__main__":
    unittest.main()
