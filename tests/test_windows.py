import os
import unittest
from types import SimpleNamespace
from unittest import mock


class WindowsRuntimeTests(unittest.TestCase):
    """捕获 HDR 查询在非 Windows 崩溃和自启动路径漂移。"""

    def test_hdr_query_is_unavailable_off_windows(self):
        from mimonitor_toolbox import windows

        with mock.patch.object(windows.sys, "platform", "linux"):
            self.assertIsNone(windows.query_windows_hdr_enabled())

    def test_repeated_hdr_query_reuses_ctypes_pointer_types(self):
        from mimonitor_toolbox import windows

        class FailingCreateFactory:
            argtypes = None
            restype = None

            def __call__(self, *_args):
                return 1

        fake_dxgi = SimpleNamespace(CreateDXGIFactory1=FailingCreateFactory())
        pointer_cache = windows.ctypes._pointer_type_cache
        initial_size = len(pointer_cache)

        with mock.patch.object(windows.sys, "platform", "win32"), mock.patch.object(
            windows.ctypes,
            "WinDLL",
            return_value=fake_dxgi,
            create=True,
        ):
            for _ in range(4):
                self.assertIsNone(windows.query_windows_hdr_enabled())

        self.assertEqual(len(pointer_cache), initial_size)

    def test_hdr_query_walks_dxgi_vtables_and_releases_interfaces(self):
        from mimonitor_toolbox import windows

        method_calls = []
        release_calls = []

        class CreateFactory:
            argtypes = None
            restype = None

            def __call__(self, _iid, factory_out):
                windows.ctypes.cast(
                    factory_out,
                    windows._VOID_POINTER_POINTER,
                ).contents.value = 1001
                return 0

        def set_void_pointer(pointer_out, value):
            windows.ctypes.cast(
                pointer_out,
                windows._VOID_POINTER_POINTER,
            ).contents.value = value

        def com_method(pointer, index, _prototype):
            method_calls.append((pointer, index))
            if (pointer, index) == (1001, 12):
                def enum_adapters(_this, item_index, adapter_out):
                    if item_index:
                        return windows.DXGI_ERROR_NOT_FOUND
                    set_void_pointer(adapter_out, 2001)
                    return 0

                return enum_adapters
            if (pointer, index) == (2001, 7):
                def enum_outputs(_this, item_index, output_out):
                    if item_index:
                        return windows.DXGI_ERROR_NOT_FOUND
                    set_void_pointer(output_out, 3001)
                    return 0

                return enum_outputs
            if (pointer, index) == (3001, 0):
                def query_interface(_this, _iid, output6_out):
                    set_void_pointer(output6_out, 4001)
                    return 0

                return query_interface
            if (pointer, index) == (4001, 27):
                def get_desc1(_this, desc_out):
                    desc = windows.ctypes.cast(
                        desc_out,
                        windows._DXGI_OUTPUT_DESC1_POINTER,
                    ).contents
                    desc.AttachedToDesktop = 1
                    desc.ColorSpace = (
                        windows.DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020
                    )
                    return 0

                return get_desc1
            self.fail(f"unexpected COM method: {(pointer, index)}")

        fake_dxgi = SimpleNamespace(CreateDXGIFactory1=CreateFactory())
        with mock.patch.object(windows.sys, "platform", "win32"), mock.patch.object(
            windows.ctypes,
            "WinDLL",
            return_value=fake_dxgi,
            create=True,
        ), mock.patch.object(
            windows,
            "_make_guid",
            side_effect=lambda _value: windows._GUID(),
        ), mock.patch.object(windows, "_com_method", side_effect=com_method), mock.patch.object(
            windows,
            "_release_com",
            side_effect=release_calls.append,
        ):
            self.assertTrue(windows.query_windows_hdr_enabled())

        self.assertEqual(
            method_calls,
            [(1001, 12), (2001, 7), (3001, 0), (4001, 27)],
        )
        self.assertEqual(release_calls, [4001, 3001, 2001, 1001])

    def test_autostart_path_uses_windows_startup_folder(self):
        from mimonitor_toolbox import windows

        roaming = r"C:\Users\tester\AppData\Roaming"
        with mock.patch.dict(os.environ, {"APPDATA": roaming}, clear=False):
            path = windows.get_autostart_path()

        self.assertEqual(
            path,
            os.path.join(
                roaming,
                r"Microsoft\Windows\Start Menu\Programs\Startup",
                "RedmiToolbox.bat",
            ),
        )

    def test_power_broadcast_uses_automatic_resume_as_the_single_trigger(self):
        from mimonitor_toolbox import windows

        dispatch = getattr(windows, "dispatch_power_broadcast", None)
        self.assertIsNotNone(dispatch)

        resume_events = []
        self.assertTrue(dispatch(0x0218, 0x0012, lambda: resume_events.append("automatic")))
        self.assertFalse(dispatch(0x0218, 0x0007, lambda: resume_events.append("user-present")))
        self.assertFalse(dispatch(0x0218, 0x0006, lambda: resume_events.append("critical")))
        self.assertFalse(dispatch(0x0218, 0x0004, lambda: resume_events.append("suspend")))
        self.assertFalse(dispatch(0x001A, 0x0012, lambda: resume_events.append("other")))
        self.assertEqual(resume_events, ["automatic"])


if __name__ == "__main__":
    unittest.main()
