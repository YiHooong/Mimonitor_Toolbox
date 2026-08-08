import unittest


class EntrypointTests(unittest.TestCase):
    """捕获根入口复制实现或导出不同 App/main 对象的回归。"""

    def test_root_entrypoint_reexports_package_app_and_main(self):
        import monitor_controller as entry
        from mimonitor_toolbox import app as app_module
        from mimonitor_toolbox import main_window

        self.assertIs(entry.App, main_window.App)
        self.assertIs(entry.main, app_module.main)

    def test_app_uses_three_domain_mixins(self):
        from mimonitor_toolbox.device_features import DeviceFeaturesMixin
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin
        from mimonitor_toolbox.main_window import App
        from mimonitor_toolbox.pages import PagesMixin

        self.assertEqual(
            App.__bases__[:3],
            (PagesMixin, DisplayFeaturesMixin, DeviceFeaturesMixin),
        )


if __name__ == "__main__":
    unittest.main()
