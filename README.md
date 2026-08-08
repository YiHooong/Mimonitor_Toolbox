# Mimonitor Toolbox

Redmi G Pro 27U 2026显示器 ADB 控制工具
测试机器系统版本号：HyperOS 3.0.112.0  
不确定小米是否后续会对该行为进行阻止 且用且珍惜吧 尚不知2025是否可用  
摸索了很久 有点地方写的也不怎么样 有问题欢迎issue  
如果觉得本项目对你有帮助，欢迎点亮一颗 ⭐ (Star) 支持一下！或者，您也可以通过赞助 Sponsor 请我喝杯快乐水。开源不易，感谢认可！🙌  

<img src="assets/e41e5c8458c34a6e2f7c46b92be05381.png" width="300"> <img src="assets/116be2f82c8a3f88e57f95cf4f11c0a9.jpg" width="300">

## 软件截图

<img src="assets/Screensettings.png" width="500">
<img src="assets/Gamesettings.png" width="500">

## 实现原理

通过无线 ADB 连接到显示器内置的 Android 系统，利用 `settings` 命令和 MTK 平台 JNI 接口（`MtkDirectTool.jar`）直接读写硬件寄存器，实现对显示器各项参数的精确控制。

### 通信架构

```
PC (MonitorToolbox.exe)
  │
  ├─ ADB Wireless ──► 显示器 Android 系统 (port 5555)
  │
  ├─ settings get/put ──► 读写 Android Global Settings
  │   (picture_mode, picture_backlight, picture_contrast, ...)
  │
  └─ MtkDirectTool.jar ──► MTK JNI 直写硬件寄存器
      (背光 g_disp__disp_back_light)
      (色温 g_video__clr_temp)
      (色域 g_video__vid_gamut_mapping_mode)
      (精密控光 g_video__vid_local_dimming)
      (320Hz g_fusion_picture__hdmi_edid_version)
      (FreeSync g_video__freesync_switch)
      (恢复默认 g_fusion_picture__pic_reset_def_bypicmode)

  └─ ColorfulLedTool.jar ──► MiTV PM2 炫彩灯 HIDL 接口
      (炫彩灯模式)
      (照明色温)
      (纯色颜色)
      (亮度)
```

### JNI 调用方式

通过 `service call TvService` 调用系统服务，以 `app_process` 执行 jar 包中的 Java 类：

```bash
# 读取寄存器
service call TvService 3 s16 "sh -c eval\${IFS}CLASSPATH=/data/data/mitv.service/cache/MtkDirectTool.jar\${IFS}/system/bin/app_process\${IFS}/data/data/mitv.service/cache\${IFS}MtkDirectTool\${IFS}get\${IFS}g_disp__disp_back_light"

# 写入寄存器
service call TvService 3 s16 "sh -c eval\${IFS}CLASSPATH=...\${IFS}MtkDirectTool\${IFS}set\${IFS}g_disp__disp_back_light\${IFS}50\${IFS}3"
```

单项兼容读取仍可通过 `logcat` 获取结果；页面刷新使用 `batchGet` 一次读取多个寄存器，
结果写入 `/sdcard/Download/Mimonitor_Toolbox/.mtk_batch_result.txt` 后由桌面端解析。

### 数据加载策略

采用按需加载，不持续轮询：

1. **首次进入页面** — 读取该页面所有 settings key + JNI 寄存器，显示 loading 遮罩
2. **再次进入** — 直接使用缓存数据，不重新读取
3. **手动刷新** — 点击"刷新数据"按钮强制重新读取
4. **模式切换** — 自动刷新当前页面数据

### Jar 自动部署

首次连接时自动检测并补齐 `MtkDirectTool.jar` / `ColorfulLedTool.jar`：

1. 检查设备 `/sdcard/` 中的 jar 大小是否与本地一致
2. 缺失或大小不一致时从本地 push 到 `/sdcard/`
3. 从 `/sdcard/` 复制到 `/data/data/mitv.service/cache/`

打包后 jar 会从 `assets/runtime/` 嵌入 exe 中（PyInstaller `--add-binary`）。

## 功能

- 无线 ADB 连接，内网设备自动扫描
- 画面设置：模式 / 背光 / 黑色级别 / 对比度 / 饱和度 / 色调 / 锐度 / 色温 / 精密控光 / 动态清晰度 / 响应时间 / 色域
- 游戏模式：准星 / 动态准星 / 狙击镜 / 夜视 / 320Hz / FreeSync / FPS 计数器 / 秒表 / 定时器
- 信号源切换（HDMI 1/2 / DP / USBC）
- 屏幕灯：炫彩灯模式 / 亮度挡位 / 纯色颜色 / 照明色温
- 虚拟遥控器
- 全局快捷键（Windows）+ OSD 悬浮通知
- 开机自启动最小化
- 4K UI 模式（3840×2160 / DPI 640，需重启显示器）
- ADB 保活守护部署与状态检测（内置 `assets/adb_guardian/adbguardian-signed.apk`）
- 操作日志记录与导出

## 项目资源结构

```text
Mimonitor_Toolbox/
├─ monitor_controller.py          # 源码运行入口，转发到应用包
├─ MonitorToolbox.spec            # PyInstaller 打包配置和资源清单
├─ build.bat                      # Windows 一键打包脚本
├─ requirements-build.txt         # 锁定的运行与构建依赖
├─ mimonitor_toolbox/             # 主程序包
│  ├─ app.py                      # Qt 启动、单实例通信和事件循环
│  ├─ main_window.py              # 主窗口状态、托盘、快捷键和应用生命周期
│  ├─ pages.py                    # 各功能页面及控件布局
│  ├─ device_features.py          # 设备连接、扫描、保活和页面数据刷新
│  ├─ display_features.py         # 画面、游戏、信号源和灯效控制逻辑
│  ├─ adb.py                      # 私有 ADB 服务、命令执行和设备通信
│  ├─ network_scan.py             # Windows 物理网卡筛选和内网端口扫描
│  ├─ windows.py                  # Windows HDR、自启动和系统接口
│  ├─ widgets.py                  # OSD、弹窗、加载动画等自定义控件
│  ├─ core.py                     # 路径、设置、常量和模式映射
│  └─ __init__.py                 # Python 包标识
├─ assets/                        # 打包进程序的运行资源
│  ├─ app/
│  │  └─ icon.ico                 # 程序图标
│  ├─ runtime/
│  │  ├─ adb.exe                  # Windows ADB 客户端
│  │  ├─ AdbWinApi.dll            # ADB Windows 运行库
│  │  ├─ AdbWinUsbApi.dll         # ADB Windows USB 运行库
│  │  ├─ MtkDirectTool.jar        # MTK 寄存器读写 helper
│  │  └─ ColorfulLedTool.jar      # 屏幕灯控制 helper
│  └─ adb_guardian/
│     └─ adbguardian-signed.apk   # 显示器端 ADB 保活应用
├─ tools/                         # 设备端 helper 源码及说明
│  ├─ mtk_direct/
│  │  ├─ MtkDirectTool.java
│  │  └─ README.md
│  └─ colorful_led/
│     ├─ ColorfulLedTool.java
│     └─ README.md
└─ tests/                         # 按模块划分的单元测试与运行状态测试
   ├─ test_adb.py
   ├─ test_network_scan.py
   ├─ test_device_features.py
   ├─ test_display_features.py
   ├─ test_pages.py
   ├─ test_runtime.py
   └─ ...
```

源码入口只负责启动应用；窗口、页面、设备生命周期和显示器控制逻辑均拆分在 `mimonitor_toolbox/` 中。`assets/` 保存发布版本运行时必须携带的二进制资源，`tools/` 保留对应 helper 的可读源码，`tests/` 用于验证模块边界、设备状态机和 Windows 专属行为。

## 打包

```bash
# 安装锁定的构建依赖
python -m pip install -r requirements-build.txt

# 使用与 build.bat、GitHub Actions 相同的资源清单打包
python -m PyInstaller --clean --noconfirm MonitorToolbox.spec
```

## 依赖

- Python 3.10+
- PyQt6（版本见 `requirements-build.txt`）
- PyQt-Fluent-Widgets（版本见 `requirements-build.txt`）
- ADB（打包进 exe，无需额外安装）

## 感谢认可！🙌
