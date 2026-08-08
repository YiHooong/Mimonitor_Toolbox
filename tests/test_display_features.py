import unittest


class DisplayFeatureTests(unittest.TestCase):
    """捕获画面模式分组在迁移后丢失或映射错误。"""

    def test_picture_mode_group_maps_presets_to_primary_mode(self):
        from mimonitor_toolbox.display_features import DisplayFeaturesMixin

        host = object()
        self.assertEqual(DisplayFeaturesMixin._picture_mode_group_name(host, 64), "标准")
        self.assertEqual(DisplayFeaturesMixin._picture_mode_group_name(host, 25), "游戏")
        self.assertEqual(DisplayFeaturesMixin._picture_mode_group_name(host, 9), "电影")


if __name__ == "__main__":
    unittest.main()
