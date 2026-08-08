"""应用启动、单实例通信和 Qt 事件循环。"""

import sys

from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

from .core import load_settings
from .main_window import App


SERVER_NAME = "mitv_gpro27u_controller_single_instance"


def main() -> int:
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
