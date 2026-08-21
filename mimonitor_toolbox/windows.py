"""Windows HDR、原生消息和开机启动辅助。"""

import ctypes
import os
import sys

# Native Windows Hotkey support variables
user32 = None
WM_HOTKEY = 0x0312
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_DISPLAYCHANGE = 0x007E
WM_SETTINGCHANGE = 0x001A
WM_POWERBROADCAST = 0x0218
PBT_APMRESUMEAUTOMATIC = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

if sys.platform == "win32":
    try:
        import ctypes.wintypes
        user32 = ctypes.windll.user32
    except Exception as e:
        print(f"Failed to load user32: {e}")


def dispatch_power_broadcast(message, power_event, on_resume):
    """Dispatch Windows resume notifications and report whether they were handled."""

    if (
        int(message) != WM_POWERBROADCAST
        or int(power_event) != PBT_APMRESUMEAUTOMATIC
    ):
        return False
    on_resume()
    return True


def query_windows_hdr_enabled(window_handle=None):
    """Return True/False for the active Windows HDR color space, or None when unavailable."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes.wintypes as wt
        import uuid

        DXGI_ERROR_NOT_FOUND = 0x887A0002
        DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020 = 12
        MONITOR_DEFAULTTONEAREST = 2

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]

        def make_guid(value):
            return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)


        class RECTL(ctypes.Structure):
            _fields_ = [("left", wt.LONG), ("top", wt.LONG), ("right", wt.LONG), ("bottom", wt.LONG)]

        class DXGI_OUTPUT_DESC1(ctypes.Structure):
            _fields_ = [
                ("DeviceName", wt.WCHAR * 32),
                ("DesktopCoordinates", RECTL),
                ("AttachedToDesktop", wt.BOOL),
                ("Rotation", ctypes.c_int),
                ("Monitor", wt.HMONITOR),
                ("BitsPerColor", wt.UINT),
                ("ColorSpace", ctypes.c_int),
                ("RedPrimary", ctypes.c_float * 2),
                ("GreenPrimary", ctypes.c_float * 2),
                ("BluePrimary", ctypes.c_float * 2),
                ("WhitePoint", ctypes.c_float * 2),
                ("MinLuminance", ctypes.c_float),
                ("MaxLuminance", ctypes.c_float),
                ("MaxFullFrameLuminance", ctypes.c_float),
            ]

        def as_uint(hr):
            return hr & 0xFFFFFFFF

        def release(ptr):
            if not ptr:
                return
            vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            release_fn = ctypes.WINFUNCTYPE(wt.ULONG, ctypes.c_void_p)(vtbl[2])
            release_fn(ptr)

        def method(ptr, index, restype, *argtypes):
            vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])

        target_monitor = None
        if window_handle and user32:
            target_monitor = user32.MonitorFromWindow(wt.HWND(int(window_handle)), MONITOR_DEFAULTTONEAREST)
            try:
                target_monitor = int(target_monitor or 0)
            except Exception:
                target_monitor = None

        dxgi = ctypes.WinDLL("dxgi")
        create_factory = dxgi.CreateDXGIFactory1
        create_factory.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
        create_factory.restype = ctypes.c_long

        iid_factory1 = make_guid("770aae78-f26f-4dba-a829-253c83d1b387")
        iid_output6 = make_guid("068346e8-aaec-4b84-add7-137f513f77a1")
        factory_ptr = ctypes.c_void_p()
        if create_factory(ctypes.byref(iid_factory1), ctypes.byref(factory_ptr)) != 0 or not factory_ptr.value:
            return None

        attached_states = []
        matched_state = None
        factory = factory_ptr.value
        try:
            enum_adapters1 = method(factory, 12, ctypes.c_long, wt.UINT, ctypes.POINTER(ctypes.c_void_p))
            adapter_index = 0
            while True:
                adapter_ptr = ctypes.c_void_p()
                hr = enum_adapters1(factory, adapter_index, ctypes.byref(adapter_ptr))
                if as_uint(hr) == DXGI_ERROR_NOT_FOUND:
                    break
                if hr != 0 or not adapter_ptr.value:
                    break
                adapter = adapter_ptr.value
                try:
                    enum_outputs = method(adapter, 7, ctypes.c_long, wt.UINT, ctypes.POINTER(ctypes.c_void_p))
                    output_index = 0
                    while True:
                        output_ptr = ctypes.c_void_p()
                        hr = enum_outputs(adapter, output_index, ctypes.byref(output_ptr))
                        if as_uint(hr) == DXGI_ERROR_NOT_FOUND:
                            break
                        if hr != 0 or not output_ptr.value:
                            break
                        output = output_ptr.value
                        try:
                            query_interface = method(output, 0, ctypes.c_long, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
                            output6_ptr = ctypes.c_void_p()
                            if query_interface(output, ctypes.byref(iid_output6), ctypes.byref(output6_ptr)) == 0 and output6_ptr.value:
                                output6 = output6_ptr.value
                                try:
                                    desc = DXGI_OUTPUT_DESC1()
                                    get_desc1 = method(output6, 27, ctypes.c_long, ctypes.POINTER(DXGI_OUTPUT_DESC1))
                                    if get_desc1(output6, ctypes.byref(desc)) == 0 and desc.AttachedToDesktop:
                                        is_hdr = desc.ColorSpace == DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020
                                        attached_states.append(is_hdr)
                                        try:
                                            monitor = int(desc.Monitor or 0)
                                        except Exception:
                                            monitor = None
                                        if target_monitor and monitor == target_monitor:
                                            matched_state = is_hdr
                                finally:
                                    release(output6)
                        finally:
                            release(output)
                        output_index += 1
                finally:
                    release(adapter)
                adapter_index += 1
        finally:
            release(factory)

        if matched_state is not None:
            return matched_state
        if attached_states:
            return any(attached_states)
        return None
    except Exception:
        return None

def get_autostart_path():
    startup = os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup",
    )
    return os.path.join(startup, "RedmiToolbox.bat")


def get_executable_path():
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0])


def install_autostart(executable=None):
    executable = executable or get_executable_path()
    path = get_autostart_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as stream:
            stream.write(f'start /min "" "{executable}" --minimized\n')
        return True
    except OSError:
        return False


def remove_autostart():
    path = get_autostart_path()
    try:
        if os.path.exists(path):
            os.remove(path)
        return True
    except OSError:
        return False
