# Runtime Assets

Files in this directory are bundled into the Nuitka executable and used by
MonitorToolbox at runtime.

- `adb.exe`: ADB client used by the desktop app.
- `AdbWinApi.dll` and `AdbWinUsbApi.dll`: Windows ADB runtime libraries.
- `MtkDirectTool.jar`: Helper jar pushed to the display for MTK JNI calls.
- `ColorfulLedTool.jar`: Helper jar used to call MiTV PM2 colorful LED HIDL.

Keep these files in this directory so local builds and GitHub Actions use the
same resource layout.

Source for `MtkDirectTool.jar` is kept in `tools/mtk_direct/`.
Source for `ColorfulLedTool.jar` is kept in `tools/colorful_led/`.
