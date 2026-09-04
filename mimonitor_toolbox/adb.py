"""ADB 子进程、设备协议和扫描识别。"""

import logging
import os
import socket
import subprocess
import sys
import threading
import time

from .core import (
    MTK_BATCH_RESULT_FILE,
    bundled_resource_path,
    get_app_data_dir,
    get_colorful_led_tool_path,
    get_mtk_direct_tool_path,
    is_frozen_build,
)
from .network_scan import (
    WindowsAdapterError,
    build_probe_targets,
    get_windows_scan_networks,
    probe_tcp_targets,
)

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_startup_warnings = []


def drain_startup_warnings():
    warnings = list(_startup_warnings)
    _startup_warnings.clear()
    return warnings

def ensure_persistent_adb_runtime(adb_path):
    if sys.platform != "win32" or not is_frozen_build():
        return adb_path
    try:
        import shutil
        runtime_dir = os.path.join(get_app_data_dir(), "runtime")
        os.makedirs(runtime_dir, exist_ok=True)
        for filename in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
            src = bundled_resource_path("assets", "runtime", filename)
            if not src or not os.path.exists(src):
                continue
            dst = os.path.join(runtime_dir, filename)
            try:
                same_file = os.path.abspath(src).lower() == os.path.abspath(dst).lower()
            except Exception:
                same_file = False
            if same_file:
                continue
            should_copy = not os.path.exists(dst)
            if not should_copy:
                try:
                    should_copy = os.path.getsize(src) != os.path.getsize(dst) or int(os.path.getmtime(src)) > int(os.path.getmtime(dst))
                except Exception:
                    should_copy = True
            if should_copy:
                shutil.copy2(src, dst)
        persistent_adb = os.path.join(runtime_dir, "adb.exe")
        if os.path.exists(persistent_adb):
            return persistent_adb
    except Exception as exc:
        _startup_warnings.append(
            f"ADB 运行时准备失败，已回退到内置路径: "
            f"{type(exc).__name__}: {exc}"
        )
    return adb_path

def get_adb_path():
    adb_names = ["adb.exe"] if sys.platform == "win32" else ["adb"]
    for name in adb_names:
        path = bundled_resource_path("assets", "runtime", name)
        if path:
            return ensure_persistent_adb_runtime(path)
    return "adb"


ADB = get_adb_path()
ADB_SERVER_PORT = os.environ.get("MIMONITOR_ADB_SERVER_PORT", "5038")
ADB_DEVICE_PORT = 5555
ADB_DISCONNECTED_MARKERS = (
    "offline", "unauthorized", "no devices", "not found",
    "cannot", "failed", "error:",
)


def format_adb_serial(ip, port=ADB_DEVICE_PORT):
    ip = str(ip or "").strip()
    return f"{ip}:{port}" if ip else ""


def build_tvservice_app_process_command(jar, args):
    encoded_args = "".join(f"\\${{IFS}}{part}" for part in args)
    return (
        'service call TvService 3 s16 "sh -c eval\\${IFS}'
        f'CLASSPATH={jar}'
        '\\${IFS}/system/bin/app_process'
        '\\${IFS}/data/data/mitv.service/cache'
        f'{encoded_args}"'
    )


def parse_jni_batch_output(output):
    values = {}
    for line in str(output or "").splitlines():
        line = line.strip()
        if not line or line.startswith("__") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        if raw.startswith("ERROR"):
            continue
        try:
            values[key] = int(raw)
        except (TypeError, ValueError):
            values[key] = raw
    return values


def is_adb_server_alive(timeout=0.2):
    """检查本软件独立 ADB Server 的监听端口，不触发 ADB 自动拉起。"""
    sock = None
    try:
        port = int(ADB_SERVER_PORT)
        sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        return True
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

def adb_text_has_disconnected_marker(text):
    lower = str(text or "").lower()
    return any(marker in lower for marker in ADB_DISCONNECTED_MARKERS)

def adb_connect_output_ok(text):
    lower = str(text or "").lower()
    if adb_text_has_disconnected_marker(lower):
        return False
    return "connected to" in lower or "already connected to" in lower

def adb_device_state(serial, timeout=3):
    out = adb_run(["-s", serial, "get-state"], timeout=timeout).strip()
    lower = out.lower()
    if lower in ("device", "offline", "unauthorized"):
        return lower
    if "unauthorized" in lower:
        return "unauthorized"
    if "offline" in lower:
        return "offline"
    if "not found" in lower:
        return "not found"
    if "no devices" in lower:
        return "no devices"
    if "cannot" in lower or "failed" in lower or "error:" in lower:
        return lower or "error"
    return lower or "unknown"


def adb_device_state_label(state):
    lower = str(state or "").lower()
    if "offline" in lower:
        return "设备离线"
    if "unauthorized" in lower:
        return "设备未授权"
    if "not found" in lower or "no devices" in lower:
        return "未找到设备"
    if "cannot" in lower or "failed" in lower or "error" in lower:
        return "连接异常"
    if lower == "unknown" or not lower:
        return "状态未知"
    return str(state)


def disconnected_status_text(detail):
    return f"未连接（{adb_device_state_label(detail)}）"

def is_connected_status_text(text):
    text = str(text or "")
    return (text == "已连接" or text.startswith("已连接:")) and not adb_text_has_disconnected_marker(text)

def normalize_status_text(text):
    text = str(text or "")
    if "已连接" in text and adb_text_has_disconnected_marker(text):
        return disconnected_status_text(text)
    return text

# ===== 日志文件 =====
_log_file = None
_log_path = None
_log_to_file_enabled = False
_adb_processes = set()
_adb_spawn_blocked = False
_adb_command_lock = threading.RLock()
_adb_process_lock = threading.RLock()
_async_error_handler = None
_logger = logging.getLogger(__name__)


def set_async_error_handler(handler):
    global _async_error_handler
    _async_error_handler = handler


def adb_command(args):
    cmd = [ADB]
    if ADB_SERVER_PORT:
        cmd += ["-P", str(ADB_SERVER_PORT)]
    return cmd + args


def adb_command_text(args):
    parts = [f'"{ADB}"']
    if ADB_SERVER_PORT:
        parts += ["-P", str(ADB_SERVER_PORT)]
    return " ".join(parts + args)


def _adb_log(msg):
    """写入 ADB 操作日志到文件。"""
    if _log_file and _log_to_file_enabled:
        try:
            _log_file.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            _log_file.flush()
        except Exception:
            pass


def adb_run(args, timeout=10, check=False):
    proc = None
    with _adb_command_lock:
        try:
            cmd = adb_command(args)
            with _adb_process_lock:
                if _adb_spawn_blocked:
                    _adb_log(f"{adb_command_text(args)} => SKIPPED: shutting down")
                    if check:
                        raise RuntimeError("应用正在退出，ADB 命令已取消")
                    return ""
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=NO_WINDOW,
                    stdin=subprocess.DEVNULL,
                )
                _adb_processes.add(proc)
            stdout, stderr = proc.communicate(timeout=timeout)
            out = stdout.strip()
            if not out and stderr:
                out = stderr.strip()
            _adb_log(f"{adb_command_text(args)} => {out[:200]}")
            if check and proc.returncode not in (0, None):
                raise RuntimeError(out or f"ADB 进程退出码 {proc.returncode}")
            return out
        except subprocess.TimeoutExpired:
            if proc:
                proc.kill()
                try:
                    proc.communicate(timeout=1)
                except Exception:
                    pass
            _adb_log(f"{adb_command_text(args)} => TIMEOUT")
            if check:
                raise RuntimeError(f"ADB 命令超时（{timeout} 秒）") from None
            return ""
        except Exception as exc:
            _adb_log(f"{adb_command_text(args)} => ERROR: {exc}")
            if check:
                raise
            return ""
        finally:
            if proc:
                with _adb_process_lock:
                    _adb_processes.discard(proc)


def cleanup_adb_processes(kill_server=False):
    with _adb_process_lock:
        processes = list(_adb_processes)
        _adb_processes.clear()
    for proc in processes:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass
    if kill_server:
        try:
            subprocess.run(adb_command(["kill-server"]), capture_output=True, text=True, timeout=3,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
        except Exception:
            pass

def block_adb_spawns():
    global _adb_spawn_blocked
    with _adb_process_lock:
        _adb_spawn_blocked = True

def unblock_adb_spawns():
    global _adb_spawn_blocked
    with _adb_process_lock:
        _adb_spawn_blocked = False


class Adb:
    def __init__(self, ip=""): self.ip = ip
    @property
    def serial(self):
        return format_adb_serial(self.ip)
    def transaction(self):
        return _adb_command_lock
    def shell(self, cmd, check=False):
        out = adb_run(["-s", self.serial, "shell", cmd], check=check)
        return out
    def _check_and_heal_remote_jar(self, filename, local_jar, check=False):
        with self.transaction():
            sdcard_jar = f"/sdcard/{filename}"
            cache_jar = f"/data/data/mitv.service/cache/{filename}"
            sd_size_lines = self.shell(
                f"stat -c %s {sdcard_jar} 2>/dev/null || echo 0",
                check=check,
            ).strip().splitlines()
            sd_size_text = sd_size_lines[-1] if sd_size_lines else "0"
            try:
                sd_size = int(sd_size_text)
            except (TypeError, ValueError):
                sd_size = 0
            local_size = 0
            if local_jar:
                try:
                    local_size = os.path.getsize(local_jar)
                except OSError:
                    local_size = 0
            if sd_size < 1000 or (local_size > 0 and sd_size != local_size):
                if local_jar:
                    adb_run(
                        ["-s", self.serial, "push", local_jar, sdcard_jar],
                        check=check,
                    )
                else:
                    _adb_log(f"WARNING: {filename} 本地未找到，无法推送到设备")
                    return False
            self.shell(
                f'service call TvService 3 s16 "cp {sdcard_jar} {cache_jar}"',
                check=check,
            )
            return True

    def check_and_heal_jar(self):
        self._check_and_heal_remote_jar(
            "MtkDirectTool.jar",
            get_mtk_direct_tool_path(),
        )

    def check_and_heal_colorful_led_tool(self, check=False):
        return self._check_and_heal_remote_jar(
            "ColorfulLedTool.jar",
            get_colorful_led_tool_path(),
            check=check,
        )

    def connect(self):
        o = adb_run(["connect", self.serial])
        return adb_connect_output_ok(o) and self.device_state(timeout=3) == "device"
    def ensure_connected(self):
        if not self.ip:
            return False, "unknown"
        state = adb_device_state(self.serial, timeout=2)
        if state == "device":
            return True, state
        adb_run(["connect", self.serial], timeout=5)
        state = adb_device_state(self.serial, timeout=3)
        return state == "device", state
    def device_state(self, timeout=3):
        if not self.ip:
            return "unknown"
        return adb_device_state(self.serial, timeout=timeout)
    def get(self, k, check=False):
        v = self.shell(f"settings get global {k}", check=check)
        _adb_log(f"settings get {k} => {v}")
        return v
    def put(self, k, v, check=False):
        _adb_log(f"settings put {k} = {v}")
        return self.shell(f"settings put global {k} {v}", check=check)
    def key(self, k, check=False):
        _adb_log(f"keyevent {k}")
        return self.shell(f"input keyevent {k}", check=check)
    def colorful_led(self, action, *args, check=False):
        with self.transaction():
            _adb_log(f"colorful_led {action} {' '.join(map(str, args))}")
            if not self.check_and_heal_colorful_led_tool(check=check):
                return ""
            jar = "/data/data/mitv.service/cache/ColorfulLedTool.jar"
            parts = ["ColorfulLedTool", str(action)] + [str(a) for a in args]
            return self.shell(
                build_tvservice_app_process_command(jar, parts),
                check=check,
            )
    def jni_set(self, key, val, upd=3, check=False):
        _adb_log(f"jni_set {key} = {val}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        command = build_tvservice_app_process_command(
            jar, ["MtkDirectTool", "set", key, val, upd]
        )
        return self.shell(command, check=check)
    def hdr_tone_mapping(self, val, upd=3, check=False):
        _adb_log(f"hdr_tone_mapping = {val}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        command = build_tvservice_app_process_command(
            jar, ["MtkDirectTool", "setHdrToneMapping", val, upd]
        )
        return self.shell(command, check=check)
    def jni_set_color_gains(self, red, green, blue, check=False):
        _adb_log(f"jni_set_color_gains r={red} g={green} b={blue}")
        jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
        command = build_tvservice_app_process_command(
            jar, ["MtkDirectTool", "setColorGains", red, green, blue]
        )
        return self.shell(command, check=check)
    def jni_get(self, key, check=False):
        with self.transaction():
            jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            self.shell("logcat -c", check=check)
            command = build_tvservice_app_process_command(
                jar, ["MtkDirectTool", "get", key]
            )
            self.shell(command, check=check)
            time.sleep(0.8)
            log = self.shell(f"logcat -d | grep 'GET {key}' | tail -1", check=check)
        i = log.find("= ")
        if check and i < 0:
            raise RuntimeError(f"未读取到 JNI 值：{key}")
        v = log[i+2:].strip() if i >= 0 else "N/A"
        _adb_log(f"jni_get {key} => {v}")
        return v

    def jni_batch_get(self, keys, check=False):
        safe_keys = []
        for key in keys:
            key = str(key)
            if key and all(ch.isalnum() or ch == "_" for ch in key):
                safe_keys.append(key)
        if not safe_keys:
            return {}

        with self.transaction():
            jar = "/data/data/mitv.service/cache/MtkDirectTool.jar"
            batch_command = build_tvservice_app_process_command(
                jar, ["MtkDirectTool", "batchGet"] + safe_keys
            )
            cmd = (
                f"mkdir -p /sdcard/Download/Mimonitor_Toolbox; "
                f"rm -f {MTK_BATCH_RESULT_FILE}; "
                f"{batch_command} >/dev/null; "
                f"i=0; while [ $i -lt 30 ] && [ ! -f {MTK_BATCH_RESULT_FILE} ]; do sleep 0.1; i=$((i+1)); done; "
                f"cat {MTK_BATCH_RESULT_FILE} 2>/dev/null"
            )
            out = self.shell(cmd, check=check)

        vals = parse_jni_batch_output(out)
        if check and not vals:
            raise RuntimeError("未读取到批量 JNI 值")
        _adb_log(f"jni_batch_get {','.join(safe_keys)} => {vals}")
        return vals

    def jni_batch_get_or_single(self, keys, check=False):
        vals = self.jni_batch_get(keys)
        for key in keys:
            if key in vals:
                continue
            try:
                vals[key] = int(self.jni_get(key, check=check))
            except Exception:
                if check:
                    raise
                pass
        return vals

    def refresh_pq(self, check=False):
        _adb_log("refresh_pq")
        return self.shell("am broadcast -a com.xiaomi.mitv.action.PIC_MODE_CHANGED --ei picmode 7", check=check)
    def get_model(self):
        m = self.shell("getprop ro.product.model")
        _adb_log(f"get_model => {m}")
        return m


def is_mitv_model(model):
    return "mitv" in str(model or "").lower()


def scan_adb(cb=None, log=None, cancel_event=None):
    """扫描全部有效物理网卡，并通过临时 ADB 连接读取设备型号。"""

    cancel_event = cancel_event or threading.Event()
    networks = get_windows_scan_networks(log=log)
    if not networks:
        raise WindowsAdapterError("未找到可扫描的物理网卡")
    targets = build_probe_targets(networks, log=log)
    probe_results = probe_tcp_targets(targets, cancel_event, log=log)
    probe_results.sort(key=lambda item: int(item.ip))

    found = []
    for result in probe_results:
        if cancel_event.is_set():
            break
        ip = str(result.ip)
        serial = format_adb_serial(ip)
        try:
            if log:
                log(f"[扫描] {serial} 开放，正在验证...")
            output = adb_run(["connect", serial], 5)
            if log:
                log(f"[扫描] {ip} adb: {output}")
            if not adb_connect_output_ok(output):
                continue
            if cancel_event.is_set():
                continue
            state = adb_device_state(serial, timeout=3)
            if state != "device":
                if log:
                    log(f"[扫描] {ip} 状态为 {state}，跳过")
                continue
            if cancel_event.is_set():
                continue
            model = adb_run(
                ["-s", serial, "shell", "getprop ro.product.model"], 3
            ).strip()
            if not model or adb_text_has_disconnected_marker(model):
                continue
            found.append((ip, model))
            if cb:
                cb(ip, model)
        except Exception as exc:
            if log:
                log(f"[扫描] 验证 {ip} 失败: {exc}")
        finally:
            adb_run(["disconnect", serial], 3)

    if log:
        mitv_count = sum(1 for _ip, model in found if is_mitv_model(model))
        log(f"[扫描] 完成，发现 {len(found)} 台 ADB 设备，其中 {mitv_count} 台 MiTV")
    return found


def async_run(fn):
    def guarded():
        try:
            fn()
        except Exception as exc:
            task_name = getattr(fn, "__name__", fn.__class__.__name__)
            _adb_log(f"BACKGROUND ERROR [{task_name}]: {exc}")
            handler = _async_error_handler
            if handler is None:
                _logger.exception("后台任务 %s 执行失败", task_name)
                return
            try:
                handler(exc)
            except Exception:
                _logger.exception("后台任务错误处理器执行失败")

    worker = threading.Thread(target=guarded, daemon=True)
    worker.start()
    return worker
