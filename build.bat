@echo off
rem Nuitka onefile 打包。需要 python.org 安装的 Python（Microsoft Store 版缺少链接库，无法编译）。
set PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe
if not exist "%PY%" set PY=python
"%PY%" -m pip install -r requirements-build.txt
"%PY%" -m nuitka ^
  --onefile ^
  --enable-plugin=pyqt6 ^
  --windows-console-mode=disable ^
  --windows-icon-from-ico=assets/app/icon.ico ^
  --include-package=qfluentwidgets ^
  --include-package-data=qfluentwidgets ^
  --include-data-files=assets/runtime/adb.exe=assets/runtime/adb.exe ^
  --include-data-files=assets/runtime/AdbWinApi.dll=assets/runtime/AdbWinApi.dll ^
  --include-data-files=assets/runtime/AdbWinUsbApi.dll=assets/runtime/AdbWinUsbApi.dll ^
  --include-data-files=assets/runtime/MtkDirectTool.jar=assets/runtime/MtkDirectTool.jar ^
  --include-data-files=assets/runtime/ColorfulLedTool.jar=assets/runtime/ColorfulLedTool.jar ^
  --include-data-files=assets/adb_guardian/adbguardian-signed.apk=assets/adb_guardian/adbguardian-signed.apk ^
  --output-filename=MonitorToolbox.exe ^
  --output-dir=dist-nuitka ^
  --assume-yes-for-downloads ^
  monitor_controller.py
echo Done! Check dist-nuitka\MonitorToolbox.exe
pause
