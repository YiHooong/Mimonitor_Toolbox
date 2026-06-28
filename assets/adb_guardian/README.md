# AdbGuardian Runtime Asset

This directory contains the signed AdbGuardian APK bundled with MonitorToolbox.

AdbGuardian is installed to supported MiTV devices from the Tools page. It keeps
wireless ADB enabled on TCP port 5555 after reboot, standby, and wake cycles.

Source project:

```text
/home/hq/mitv/adb_guardian
```

Bundled APK:

```text
adbguardian-signed.apk
```

Bundled version:

```text
versionCode 5 / versionName 3.2
```

Notes:

- ADB repair checks no longer restart `adbd` on a timer. The guardian only restarts
  `adbd` when ADB settings or TCP port state are wrong.
- Generic accessibility events do not trigger ADB repair checks, so Android's ADB
  authorization dialog is not interrupted by the guardian.
