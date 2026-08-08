import ast
import importlib
import unittest
from pathlib import Path


class ModuleBoundaryTests(unittest.TestCase):
    """防止拆分后重新形成入口反向依赖或隐式循环依赖。"""

    def test_package_modules_are_importable(self):
        module_names = (
            "core",
            "adb",
            "network_scan",
            "windows",
            "widgets",
            "pages",
            "display_features",
            "device_features",
            "main_window",
            "app",
        )

        for name in module_names:
            with self.subTest(name=name):
                importlib.import_module(f"mimonitor_toolbox.{name}")

    def test_package_never_imports_root_entrypoint(self):
        package_dir = Path(__file__).resolve().parents[1] / "mimonitor_toolbox"
        self.assertTrue(package_dir.is_dir())

        for path in package_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported_modules = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported_from_modules = {
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            self.assertNotIn("monitor_controller", imported_modules, path)
            self.assertNotIn("monitor_controller", imported_from_modules, path)


if __name__ == "__main__":
    unittest.main()
