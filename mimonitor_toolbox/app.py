"""应用启动、单实例通信和 Qt 事件循环。"""

import os
import sys
import threading
import traceback
from datetime import datetime

from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

from .core import cleanup_stale_extract_dirs, get_app_data_dir, load_settings
from .main_window import App


SERVER_NAME = "mitv_gpro27u_controller_single_instance"


def _install_excepthook():
    """记录未捕获异常到日志，替代 PyQt6 默认的 qFatal 终止。

    PyQt6 对 Qt 回调内的未捕获异常默认调用 qFatal 终止进程；
    安装自定义 excepthook 后改为记录日志继续运行。
    """
    def _hook(exc_type, exc_value, exc_tb):
        try:
            log_path = os.path.join(get_app_data_dir(), "logs", "exceptions.log")
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] 未捕获异常:\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
                f.write("\n")
        except Exception:
            pass  # 日志写入失败时静默，不影响程序运行

    sys.excepthook = _hook

    def _thread_hook(args):
        _hook(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = _thread_hook


def main() -> int:
    _install_excepthook()
    # 后台清理 onefile/_MEI 解压残留，不阻塞启动
    threading.Thread(target=cleanup_stale_extract_dirs, daemon=True).start()
    application = QApplication(sys.argv)

    check_socket = QLocalSocket()
    check_socket.connectToServer(SERVER_NAME)
    if check_socket.waitForConnected(500):
        check_socket.write(b"show")
        check_socket.waitForBytesWritten(500)
        return 0

    local_server = QLocalServer()
    local_server.removeServer(SERVER_NAME)
    if not local_server.listen(SERVER_NAME):
        return 1

    settings = load_settings()
    theme_value = settings.get("theme", "dark")
    if theme_value == "auto":
        setTheme(Theme.AUTO)
    elif theme_value == "light":
        setTheme(Theme.LIGHT)
    else:
        setTheme(Theme.DARK)

    window = App()

    def on_new_connection():
        client_socket = local_server.nextPendingConnection()
        if client_socket:
            if client_socket.waitForReadyRead(500):
                message = client_socket.readAll().data().decode("utf-8")
                if message == "show":
                    window.show_and_raise()
            client_socket.close()
            client_socket.deleteLater()

    local_server.newConnection.connect(on_new_connection)

    if "--minimized" not in sys.argv:
        window.show()
    return application.exec()


__all__ = ["main"]
