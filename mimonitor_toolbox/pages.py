"""页面构建 mixin。

宿主需提供 settings、ADB/连接状态、状态信号，以及显示器控制、设备连接和
页面刷新槽函数。这里仅创建控件、布局和信号连接，不拥有设备生命周期。
"""

import os
import sys
import time

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    IconWidget,
    LineEdit,
    MessageBox,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SimpleCardWidget,
    Slider,
    SubtitleLabel,
    Theme,
    TitleLabel,
    ToggleButton,
    setTheme,
)

from .core import (
    ADJUSTABLE_HOTKEY_PARAMS,
    HOTKEY_KEYS,
    HOTKEY_MODIFIERS,
    bundled_resource_path,
    load_settings,
    update_settings,
)
from .widgets import PageScrollSlider


class PagesMixin:
    def setup_ui(self):
        self.home_page = self._make_home_page()
        self.picture_page = self._make_picture_page()
        self.game_page = self._make_game_page()
        self.source_page = self._make_source_page()
        self.light_page = self._make_light_page()
        self.tools_page = self._make_tools_page()
        self.remote_page = self._make_remote_page()

        self.home_page.setObjectName("homePage")
        self.picture_page.setObjectName("picturePage")
        self.game_page.setObjectName("gamePage")
        self.source_page.setObjectName("sourcePage")
        self.light_page.setObjectName("lightPage")
        self.tools_page.setObjectName("toolsPage")
        self.remote_page.setObjectName("remotePage")

        # Add routes
        self.addSubInterface(self.home_page, FIF.HOME, "主页 & 连接")
        self.addSubInterface(self.picture_page, FIF.PALETTE, "画面设置")
        self.addSubInterface(self.game_page, FIF.GAME, "游戏模式")
        self.addSubInterface(self.source_page, FIF.SYNC, "信号源切换")
        self.addSubInterface(self.light_page, FIF.BRIGHTNESS, "屏幕灯")
        self.addSubInterface(self.tools_page, FIF.DEVELOPER_TOOLS, "工具与设置")
        self.addSubInterface(self.remote_page, FIF.TILES, "遥控器")

        # Hide return (back) button
        self.navigationInterface.setReturnButtonVisible(False)

    def _make_home_page(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title = TitleLabel("红米 G Pro 27U Toolbox", container)
        title_font = title.font()
        title_font.setPixelSize(28)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = BodyLabel("通过无线 ADB 连接并调优您的 MiniLED 旗舰显示器", container)
        sub_font = subtitle.font()
        sub_font.setPixelSize(14)
        subtitle.setFont(sub_font)
        layout.addWidget(subtitle)

        # Connection Card
        conn_card = SimpleCardWidget(container)
        conn_layout = QVBoxLayout(conn_card)
        conn_layout.setContentsMargins(20, 20, 20, 20)
        conn_layout.setSpacing(15)

        conn_title = SubtitleLabel("连接到显示器", conn_card)
        conn_layout.addWidget(conn_title)

        # IP Row
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        
        ip_label = BodyLabel("显示器 IP:", conn_card)
        row1.addWidget(ip_label)
        
        self.ip_entry = LineEdit(conn_card)
        self.ip_entry.setPlaceholderText("请输入 IP 地址")
        settings = load_settings()
        saved_ip = settings.get("saved_ip", "")
        self.ip_entry.setText(saved_ip)
        self.ip_entry.setFixedWidth(250)
        row1.addWidget(self.ip_entry)

        self.connect_btn = PrimaryPushButton(FIF.WIFI, "开始连接", conn_card)
        self.connect_btn.clicked.connect(self.connect)
        row1.addWidget(self.connect_btn)

        self.scan_btn = PushButton(FIF.SEARCH, "扫描内网", conn_card)
        self.scan_btn.clicked.connect(self.scan_net)
        row1.addWidget(self.scan_btn)

        self.disconnect_btn = PushButton(FIF.CLOSE, "断开连接", conn_card)
        self.disconnect_btn.clicked.connect(self.disconnect_adb)
        row1.addWidget(self.disconnect_btn)
        row1.addStretch(1)
        conn_layout.addLayout(row1)

        # Dropdown and Status
        row2 = QHBoxLayout()
        row2.setSpacing(10)

        dev_label = BodyLabel("已扫描设备:", conn_card)
        row2.addWidget(dev_label)

        self.dev_combo = ComboBox(conn_card)
        self.dev_combo.setPlaceholderText("请选择扫描到的显示器...")
        self.dev_combo.setFixedWidth(250)
        self.dev_combo.currentIndexChanged.connect(self._on_dev_sel)
        row2.addWidget(self.dev_combo)

        status_prefix = BodyLabel("连接状态:", conn_card)
        row2.addWidget(status_prefix)

        self.status_label = BodyLabel("未连接", conn_card)
        self.status_label.setStyleSheet("color: #d83b01; font-weight: bold; font-size: 14px;")
        row2.addWidget(self.status_label)

        row2.addStretch(1)
        conn_layout.addLayout(row2)

        layout.addWidget(conn_card)

        # Log Card
        log_card = SimpleCardWidget(container)
        log_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(15, 15, 15, 15)
        log_layout.setSpacing(12)
        
        log_title = SubtitleLabel("实时操作日志", log_card)
        log_layout.addWidget(log_title)

        self.log_widget = QTextEdit(log_card)
        self.log_widget.setReadOnly(True)
        self.log_widget.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                color: #00ff00;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 4px;
            }
        """)
        self.log_widget.setFixedHeight(220)
        self.log_widget.document().setMaximumBlockCount(500)
        self.log_widget.append("[00:00:00] 系统就绪，等待连接...")
        log_layout.addWidget(self.log_widget)

        log_btn_row = QHBoxLayout()
        self.log_file_toggle = CheckBox("记录到本地文件", log_card)
        self.log_file_toggle.setChecked(False)
        self.log_file_toggle.stateChanged.connect(self._toggle_log_file)
        log_btn_row.addWidget(self.log_file_toggle)
        log_btn_row.addStretch(1)
        export_log_btn = PushButton(FIF.SHARE, "导出日志", log_card)
        export_log_btn.clicked.connect(self._export_log)
        log_btn_row.addWidget(export_log_btn)
        open_log_btn = PushButton(FIF.FOLDER, "打开日志目录", log_card)
        open_log_btn.clicked.connect(self._open_log_dir)
        log_btn_row.addWidget(open_log_btn)
        log_layout.addLayout(log_btn_row)

        layout.addWidget(log_card, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
        return container

    def _make_picture_page(self):
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setObjectName("Container")
        container.setStyleSheet("#Container { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)



        title_row = QHBoxLayout()
        title = SubtitleLabel("画面设置", container)

        title_row.addWidget(title)
        title_row.addStretch(1)
        refresh_pic_btn = PushButton(FIF.UPDATE, "刷新数据", container)
        refresh_pic_btn.clicked.connect(lambda: self._force_refresh_page("picturePage"))
        title_row.addWidget(refresh_pic_btn)
        layout.addLayout(title_row)

        # Mode Selector Card
        lf = SimpleCardWidget(container)
        lf_layout = QVBoxLayout(lf)
        lf_layout.setContentsMargins(15, 15, 15, 15)
        lf_layout.addWidget(BodyLabel("画面模式", lf))
        
        h = QHBoxLayout()
        h.setSpacing(10)
        h.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for val, name in [(14, "标准"), (10, "游戏"), (9, "电影")]:
            b = ToggleButton(name, lf)
            b.setCheckable(True)
            b.setFixedWidth(100)
            b.clicked.connect(lambda checked=False, v=val, n=name: self._set_mode(v, n))
            h.addWidget(b)
            self.mode_btns[val] = b
        h.addSpacing(20)
        reset_mode_btn = PushButton("恢复默认", lf)
        reset_mode_btn.setFixedWidth(100)
        reset_mode_btn.clicked.connect(self._reset_current_mode)
        h.addWidget(reset_mode_btn)
        self.picture_mode_hint_label = BodyLabel("当前场景：未刷新", lf)

        h.addWidget(self.picture_mode_hint_label)
        h.addStretch(1)
        lf_layout.addLayout(h)
        layout.addWidget(lf)

        # Sliders
        self._add_slider(layout, "背光", "backlight", 1, 100, 50, jni_key="g_disp__disp_back_light", settings_keys=["picture_backlight", "xiaomi_picture_backlight"])
        self._add_slider(layout, "黑色级别", "black_level", 0, 100, 50, settings_keys=["picture_brightness"])
        self._add_slider(layout, "对比度", "contrast", 0, 100, 50, settings_keys=["picture_contrast"])
        self._add_slider(layout, "饱和度", "saturation", 0, 100, 50, settings_keys=["picture_saturation"])
        self._add_slider(layout, "色调", "hue", 0, 100, 50, settings_keys=["picture_hue"])
        self._add_slider(layout, "锐度", "sharpness", 0, 100, 1, settings_keys=["picture_sharpness"])

        # Button Groups
        self._btn_section(layout, "色温", [
            ("冷色", 0, lambda _: self._set_color_temp(1, 0, "色温: 冷色")),
            ("标准", 1, lambda _: self._set_color_temp(2, 1, "色温: 标准")),
            ("暖色", 2, lambda _: self._set_color_temp(3, 2, "色温: 暖色")),
            ("原色", 8, lambda _: self._set_color_temp(6, 8, "色温: 原色")),
            ("自定义", 3, lambda _: self._set_color_temp(0, 3, "色温: 自定义")),
        ], state_key="picture_color_temperature")
        self._add_color_gain_slider(layout, "红色增益", "red_gain", "picture_red_gain", "g_video__clr_gain_r")
        self._add_color_gain_slider(layout, "绿色增益", "green_gain", "picture_green_gain", "g_video__clr_gain_g")
        self._add_color_gain_slider(layout, "蓝色增益", "blue_gain", "picture_blue_gain", "g_video__clr_gain_b")

        self._btn_section(layout, "精密控光", [
            ("关", 0, lambda _: self._jni("g_video__vid_local_dimming", 0, "picture_local_dimming", "精密控光: 关", "tv_picture_video_local_dimming")),
            ("低", 1, lambda _: self._jni("g_video__vid_local_dimming", 1, "picture_local_dimming", "精密控光: 低", "tv_picture_video_local_dimming")),
            ("中", 2, lambda _: self._jni("g_video__vid_local_dimming", 2, "picture_local_dimming", "精密控光: 中", "tv_picture_video_local_dimming")),
            ("高", 3, lambda _: self._jni("g_video__vid_local_dimming", 3, "picture_local_dimming", "精密控光: 高", "tv_picture_video_local_dimming")),
        ], state_key="picture_local_dimming")

        self.hdr_tone_mapping_card = self._btn_section(layout, "HDR 色调映射", [
            ("HGiG", 0, lambda _: self._set_hdr_tone_mapping(0, "HGiG")),
            ("层次", 1, lambda _: self._set_hdr_tone_mapping(1, "层次")),
            ("动态", 2, lambda _: self._set_hdr_tone_mapping(2, "动态")),
            ("明亮", 3, lambda _: self._set_hdr_tone_mapping(3, "明亮")),
        ], state_key="settings_display_hdr_color_tone")
        self._update_hdr_tone_mapping_visibility()

        self._btn_section(layout, "动态清晰度", [
            ("关", 0, lambda _: self._jni("g_video__vid_insert_black", 0, "picture_dynamic_definition", "动态清晰度: 关")),
            ("低", 1, lambda _: self._jni("g_video__vid_insert_black", 1, "picture_dynamic_definition", "动态清晰度: 低")),
            ("中", 2, lambda _: self._jni("g_video__vid_insert_black", 2, "picture_dynamic_definition", "动态清晰度: 中")),
            ("高", 3, lambda _: self._jni("g_video__vid_insert_black", 3, "picture_dynamic_definition", "动态清晰度: 高")),
        ], state_key="picture_dynamic_definition")

        self._btn_section(layout, "灰阶响应时间", [
            ("普通", 1, lambda _: self._jni("g_video__vid_od_response_time", 1, "picture_response_time", "响应时间: 普通")),
            ("快速", 2, lambda _: self._jni("g_video__vid_od_response_time", 2, "picture_response_time", "响应时间: 快速")),
            ("高速", 3, lambda _: self._jni("g_video__vid_od_response_time", 3, "picture_response_time", "响应时间: 高速")),
        ], state_key="picture_response_time")

        self._btn_section(layout, "色域", [
            ("自动", 0, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 0, "tv_picture_advanced_video_color_space", "色域: 自动", "tv_picture_video_color_space")),
            ("sRGB", 3, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 3, "tv_picture_advanced_video_color_space", "色域: sRGB", "tv_picture_video_color_space")),
            ("DCI-P3", 6, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 6, "tv_picture_advanced_video_color_space", "色域: DCI-P3", "tv_picture_video_color_space")),
            ("AdobeRGB", 4, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 4, "tv_picture_advanced_video_color_space", "色域: Adobe RGB", "tv_picture_video_color_space")),
            ("BT2020", 5, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 5, "tv_picture_advanced_video_color_space", "色域: BT2020", "tv_picture_video_color_space")),
            ("BT709", 7, lambda _: self._jni("g_video__vid_gamut_mapping_mode", 7, "tv_picture_advanced_video_color_space", "色域: BT709", "tv_picture_video_color_space")),
        ], state_key="tv_picture_advanced_video_color_space")

        scroll.setWidget(container)
        return scroll

    def _make_game_page(self):
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setObjectName("Container")
        container.setStyleSheet("#Container { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)



        title_row = QHBoxLayout()
        title = SubtitleLabel("游戏模式", container)

        title_row.addWidget(title)
        title_row.addStretch(1)
        refresh_game_btn = PushButton(FIF.UPDATE, "刷新数据", container)
        refresh_game_btn.clicked.connect(lambda: self._force_refresh_page("gamePage"))
        title_row.addWidget(refresh_game_btn)
        layout.addLayout(title_row)

        # Game Switches
        self._btn_section(layout, "准星", [("关", 0, lambda _: self._fs(0))]+[(str(i), i, lambda _, v=i: self._fs(v)) for i in range(1,6)], state_key="front_sight_index")
        
        self._btn_section(layout, "动态准星", [
            ("关", 0, lambda _: self._set("mt_game_dynamic_ft", 0, "动态准星: 关")),
            ("开", 1, lambda _: self._set("mt_game_dynamic_ft", 1, "动态准星: 开")),
        ], state_key="mt_game_dynamic_ft")

        self._btn_section(layout, "狙击镜", [
            ("关", 0, lambda _: self._set("mt_game_scope", 0, "狙击镜: 关")),
            ("1.1x", 1, lambda _: self._set("mt_game_scope", 1, "狙击镜: 1.1x")),
            ("1.3x", 3, lambda _: self._set("mt_game_scope", 3, "狙击镜: 1.3x")),
            ("1.5x", 5, lambda _: self._set("mt_game_scope", 5, "狙击镜: 1.5x")),
            ("1.7x", 7, lambda _: self._set("mt_game_scope", 7, "狙击镜: 1.7x")),
            ("2.0x", 10, lambda _: self._set("mt_game_scope", 10, "狙击镜: 2.0x")),
        ], state_key="mt_game_scope")

        self._btn_section(layout, "狙击镜夜视", [
            ("关", 0, lambda _: self._set("mt_game_scope_night", 0, "狙击镜夜视: 关")),
            ("开", 1, lambda _: self._set("mt_game_scope_night", 1, "狙击镜夜视: 开")),
        ], state_key="mt_game_scope_night")

        self._btn_section(layout, "320Hz竞技模式", [
            ("关", 0, lambda _: self._320(False)),
            ("开", 1, lambda _: self._320(True)),
        ], state_key="mode_320")

        self._btn_section(layout, "FreeSync Premium Pro", [
            ("关", 0, lambda _: self._fsync(False)),
            ("开", 1, lambda _: self._fsync(True)),
        ], state_key="freesync")

        self._btn_section(layout, "FPS计数器", [
            ("关", 0, lambda _: self._set("monitor_menu_fps_counter", 0, "FPS: 关")),
            ("刷新率", 1, lambda _: self._set("monitor_menu_fps_counter", 1, "FPS: 刷新率")),
            ("柱状图", 2, lambda _: self._set("monitor_menu_fps_counter", 2, "FPS: 柱状图")),
        ], state_key="monitor_menu_fps_counter")

        self._btn_section(layout, "秒表", [
            ("关", 0, lambda _: self._set("monitor_menu_stopwatch", 0, "秒表: 关")),
            ("开", 1, lambda _: self._set("monitor_menu_stopwatch", 1, "秒表: 开")),
        ], state_key="monitor_menu_stopwatch")

        self._btn_section(layout, "定时器", [
            ("关", 0, lambda _: self._set("monitor_menu_timer", 0, "定时器: 关")),
            ("1分钟", 60, lambda _: self._set("monitor_menu_timer", 60, "定时器: 1分钟")),
            ("5分钟", 300, lambda _: self._set("monitor_menu_timer", 300, "定时器: 5分钟")),
            ("30分钟", 1800, lambda _: self._set("monitor_menu_timer", 1800, "定时器: 30分钟")),
            ("60分钟", 3600, lambda _: self._set("monitor_menu_timer", 3600, "定时器: 60分钟")),
        ], state_key="monitor_menu_timer")

        scroll.setWidget(container)
        return scroll

    def _make_source_page(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title_row = QHBoxLayout()
        title = SubtitleLabel("信号源切换", container)

        title_row.addWidget(title)
        title_row.addStretch(1)
        refresh_source_btn = PushButton(FIF.UPDATE, "刷新数据", container)
        refresh_source_btn.clicked.connect(lambda: self._force_refresh_page("sourcePage"))
        title_row.addWidget(refresh_source_btn)
        layout.addLayout(title_row)

        card = SimpleCardWidget(container)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(15)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if "mitv.tvplayer.hdmi.last.source" not in self.state_buttons:
            self.state_buttons["mitv.tvplayer.hdmi.last.source"] = {}

        for v, n in [(23, "HDMI 1"), (24, "HDMI 2"), (29, "DP"), (30, "USBC")]:
            b = ToggleButton(n, card)
            b.setCheckable(True)
            b.setFixedSize(120, 45)
            b.clicked.connect(lambda checked=False, val=v, name=n: self._set("mitv.tvplayer.hdmi.last.source", val, name))
            btn_layout.addWidget(b)
            self.state_buttons["mitv.tvplayer.hdmi.last.source"][v] = b

        card_layout.addLayout(btn_layout)
        layout.addWidget(card)

        # Active Source Status Card
        status_card = SimpleCardWidget(container)
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(20, 20, 20, 20)
        status_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl = BodyLabel("当前活跃信号源", status_card)

        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(lbl)

        self.source_label = TitleLabel("未知", status_card)
        self.source_label.setStyleSheet("color: #00bcd4; font-size: 32px; font-weight: bold; margin-top: 10px;")
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addWidget(self.source_label)

        layout.addWidget(status_card)
        return container

    def _make_light_page(self):
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setObjectName("Container")
        container.setStyleSheet("#Container { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(15)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        title_row = QHBoxLayout()
        title = SubtitleLabel("屏幕灯", container)

        title_row.addWidget(title)
        title_row.addStretch(1)
        refresh_light_btn = PushButton(FIF.UPDATE, "刷新数据", container)
        refresh_light_btn.clicked.connect(lambda: self._force_refresh_page("lightPage"))
        title_row.addWidget(refresh_light_btn)
        layout.addLayout(title_row)

        self._btn_section(layout, "炫彩灯模式", [
            ("关闭", 4, lambda _: self._set_screen_light_mode(4, "关闭")),
            ("照明", 0, lambda _: self._set_screen_light_mode(0, "照明")),
            ("纯色", 2, lambda _: self._set_screen_light_mode(2, "纯色")),
            ("屏幕同色", 1, lambda _: self._set_screen_light_mode(1, "屏幕同色")),
            ("七彩梦境（循环）", 3, lambda _: self._set_screen_light_mode(3, "七彩梦境（循环）")),
        ], state_key="atmosphere_light_switcher_pm2")

        self._add_light_slider(layout, "亮度挡位", "atmosphere_illumination", 1, 15, 10)

        self._btn_section(layout, "照明色温", [
            ("2700K", 0, lambda _: self._set_screen_light_color_temp(0, "2700K")),
            ("4000K", 1, lambda _: self._set_screen_light_color_temp(1, "4000K")),
            ("6500K", 2, lambda _: self._set_screen_light_color_temp(2, "6500K")),
        ], state_key="atmosphere_light_color_temp")

        self._btn_section(layout, "纯色颜色", [
            ("冰蓝", 0, lambda _: self._set_screen_light_color_value(0, "冰蓝")),
            ("流金", 1, lambda _: self._set_screen_light_color_value(1, "流金")),
            ("天青", 2, lambda _: self._set_screen_light_color_value(2, "天青")),
            ("草地", 3, lambda _: self._set_screen_light_color_value(3, "草地")),
            ("日落", 4, lambda _: self._set_screen_light_color_value(4, "日落")),
        ], state_key="atmosphere_light_color_value")

        scroll.setWidget(container)
        return scroll

    def _make_tools_page(self):
        scroll = ScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        container = QWidget()
        container.setObjectName("Container")
        container.setStyleSheet("#Container { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)



        title = SubtitleLabel("工具与设置", container)

        layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(20)

        # ADB Shell Card
        card1 = SimpleCardWidget(container)
        c1_lay = QVBoxLayout(card1)
        c1_lay.setContentsMargins(20, 20, 20, 20)
        c1_lay.setSpacing(10)
        
        self._add_icon_title(c1_lay, FIF.COMMAND_PROMPT, "打开 ADB Shell", card1)
        
        lbl_c1_desc = BodyLabel("在外部终端中弹出一个交互式的 ADB Shell 会话，供开发人员和高级用户直接调试显示器的 Android 系统参数。", card1)
        lbl_c1_desc.setWordWrap(True)

        c1_lay.addWidget(lbl_c1_desc)

        btn_c1 = PrimaryPushButton(FIF.COMMAND_PROMPT, "启动 Shell 终端", card1)
        btn_c1.clicked.connect(self._open_shell)
        c1_lay.addWidget(btn_c1)
        grid.addWidget(card1, 0, 0)

        # APK Install Card
        card2 = SimpleCardWidget(container)
        c2_lay = QVBoxLayout(card2)
        c2_lay.setContentsMargins(20, 20, 20, 20)
        c2_lay.setSpacing(10)
        
        self._add_icon_title(c2_lay, FIF.APPLICATION, "安装 APK 软件包", card2)
        
        lbl_c2_desc = BodyLabel("通过无线 ADB 安全、静默地向您的显示器安装第三方的 Android APK 应用软件包，支持完整的安装状态回执提示。", card2)
        lbl_c2_desc.setWordWrap(True)

        c2_lay.addWidget(lbl_c2_desc)

        btn_c2 = PrimaryPushButton(FIF.APPLICATION, "选择并安装应用", card2)
        btn_c2.clicked.connect(self._install_apk)
        c2_lay.addWidget(btn_c2)
        grid.addWidget(card2, 0, 1)

        # Software Settings Card
        card3 = SimpleCardWidget(container)
        c3_lay = QVBoxLayout(card3)
        c3_lay.setContentsMargins(20, 20, 20, 20)
        c3_lay.setSpacing(15)
        
        self._add_icon_title(c3_lay, FIF.SETTING, "软件设置", card3)
        
        close_behavior_layout = QHBoxLayout()
        close_behavior_layout.setSpacing(15)
        
        lbl_close_behavior = BodyLabel("窗口关闭行为:", card3)
        lbl_close_behavior.setFixedWidth(120)
        close_behavior_layout.addWidget(lbl_close_behavior)
        
        self.btn_setting_tray = ToggleButton("最小化到托盘", card3)
        self.btn_setting_exit = ToggleButton("直接退出程序", card3)
        
        self.btn_setting_tray.setCheckable(True)
        self.btn_setting_exit.setCheckable(True)
        self.btn_setting_tray.setFixedWidth(120)
        self.btn_setting_exit.setFixedWidth(120)
        
        close_behavior_layout.addWidget(self.btn_setting_tray)
        close_behavior_layout.addWidget(self.btn_setting_exit)
        close_behavior_layout.addStretch()
        
        c3_lay.addLayout(close_behavior_layout)
        
        # Load settings
        settings = load_settings()
        
        # Theme Row
        theme_layout = QHBoxLayout()
        theme_layout.setSpacing(15)
        
        lbl_theme = BodyLabel("应用主题:", card3)
        lbl_theme.setFixedWidth(120)
        theme_layout.addWidget(lbl_theme)
        
        self.theme_combo = ComboBox(card3)
        self.theme_combo.addItems(["跟随系统", "深色模式", "浅色模式"])
        self.theme_combo.setFixedWidth(255)
        theme_layout.addWidget(self.theme_combo)
        theme_layout.addStretch()
        
        c3_lay.addLayout(theme_layout)
        
        # Set initial theme index
        theme_val = settings.get("theme", "dark")
        if theme_val == "auto":
            self.theme_combo.setCurrentIndex(0)
        elif theme_val == "dark":
            self.theme_combo.setCurrentIndex(1)
        else:
            self.theme_combo.setCurrentIndex(2)
            
        def on_theme_changed(index):
            if index == 0:
                theme_str = "auto"
                setTheme(Theme.AUTO)
            elif index == 1:
                theme_str = "dark"
                setTheme(Theme.DARK)
            else:
                theme_str = "light"
                setTheme(Theme.LIGHT)
            
            update_settings({"theme": theme_str})
            
        self.theme_combo.currentIndexChanged.connect(on_theme_changed)
        
        behavior = settings.get("close_behavior", "tray")
        if behavior == "tray":
            self.btn_setting_tray.setChecked(True)
        else:
            self.btn_setting_exit.setChecked(True)
            
        def on_choose_tray():
            self.btn_setting_tray.setChecked(True)
            self.btn_setting_exit.setChecked(False)
            update_settings({"close_behavior": "tray"})
            self._update_hdr_memory_status_label()
            
        def on_choose_exit():
            self.btn_setting_tray.setChecked(False)
            self.btn_setting_exit.setChecked(True)
            update_settings({"close_behavior": "exit"})
            self._update_hdr_memory_status_label()
            
        self.btn_setting_tray.clicked.connect(on_choose_tray)
        self.btn_setting_exit.clicked.connect(on_choose_exit)
        
        # Auto-start minimized
        autostart_layout = QHBoxLayout()
        autostart_layout.setSpacing(15)
        self.chk_autostart = CheckBox("开机自动启动并最小化到托盘", card3)
        self.chk_autostart.setChecked(settings.get("autostart", False))
        self.chk_autostart.stateChanged.connect(self._toggle_autostart)
        autostart_layout.addWidget(self.chk_autostart)
        autostart_layout.addStretch()
        c3_lay.addLayout(autostart_layout)

        lbl_tip = CaptionLabel("* 提示：当选择最小化到托盘时，关闭窗口会使程序在后台默默运行，可通过任务栏右下角的托盘图标随时恢复或退出。", card3)
        lbl_tip.setTextColor(QColor(120, 120, 120), QColor(255, 255, 255, 140))
        c3_lay.addWidget(lbl_tip)

        hdr_memory_layout = QHBoxLayout()
        hdr_memory_layout.setSpacing(15)
        self.chk_hdr_local_dimming_memory = CheckBox("HDR/SDR 分区控光记忆", card3)
        self.chk_hdr_local_dimming_memory.setChecked(settings.get("hdr_sdr_local_dimming_enabled", False))
        self.chk_hdr_local_dimming_memory.stateChanged.connect(self._toggle_hdr_local_dimming_memory)
        hdr_memory_layout.addWidget(self.chk_hdr_local_dimming_memory)
        hdr_memory_layout.addStretch()
        c3_lay.addLayout(hdr_memory_layout)

        self.hdr_memory_status_label = CaptionLabel("当前信号：未检测", card3)
        self.hdr_memory_status_label.setTextColor(QColor(120, 120, 120), QColor(255, 255, 255, 140))
        c3_lay.addWidget(self.hdr_memory_status_label)

        freesync_memory_layout = QHBoxLayout()
        freesync_memory_layout.setSpacing(15)
        self.chk_freesync_mode_memory = CheckBox("FreeSync Pro 模式记忆", card3)
        self.chk_freesync_mode_memory.setChecked(settings.get("freesync_mode_memory_enabled", False))
        self.chk_freesync_mode_memory.stateChanged.connect(self._toggle_freesync_mode_memory)
        freesync_memory_layout.addWidget(self.chk_freesync_mode_memory)
        freesync_memory_layout.addStretch()
        c3_lay.addLayout(freesync_memory_layout)

        self.freesync_memory_status_label = CaptionLabel("FreeSync 模式记忆：未启用", card3)
        self.freesync_memory_status_label.setTextColor(QColor(120, 120, 120), QColor(255, 255, 255, 140))
        c3_lay.addWidget(self.freesync_memory_status_label)

        grid.addWidget(card3, 2, 0, 1, 2)

        # 4K UI Card
        card5 = SimpleCardWidget(container)
        c5_lay = QVBoxLayout(card5)
        c5_lay.setContentsMargins(20, 20, 20, 20)
        c5_lay.setSpacing(10)

        self._add_icon_title(c5_lay, FIF.FIT_PAGE, "4K UI 模式", card5)

        lbl_c5_desc = BodyLabel("将显示器 UI 分辨率提升至 3840×2160，DPI 设为 640。开启或关闭后显示器将自动重启。", card5)
        lbl_c5_desc.setWordWrap(True)

        c5_lay.addWidget(lbl_c5_desc)

        self.chk_4k = CheckBox("启用 4K UI", card5)
        self.chk_4k.stateChanged.connect(self._toggle_4k_ui)
        c5_lay.addWidget(self.chk_4k)

        grid.addWidget(card5, 1, 0, 1, 1)

        # ADB Guardian Card
        card6 = SimpleCardWidget(container)
        c6_lay = QVBoxLayout(card6)
        c6_lay.setContentsMargins(20, 20, 20, 20)
        c6_lay.setSpacing(10)

        self._add_icon_title(c6_lay, FIF.VPN, "ADB 保活守护", card6)

        lbl_c6_desc = BodyLabel("部署电视端 AdbGuardian，重启、待机或唤醒后自动恢复无线 ADB，并保持 5555 端口可用。", card6)
        lbl_c6_desc.setWordWrap(True)

        c6_lay.addWidget(lbl_c6_desc)

        self.guardian_status_label = BodyLabel("状态：未检测", card6)

        c6_lay.addWidget(self.guardian_status_label)

        guardian_btn_row = QHBoxLayout()
        guardian_btn_row.setSpacing(8)
        btn_guardian_check = PushButton(FIF.SEARCH, "检测状态", card6)
        btn_guardian_check.clicked.connect(self._check_guardian_status)
        guardian_btn_row.addWidget(btn_guardian_check)

        btn_guardian_deploy = PrimaryPushButton(FIF.APPLICATION, "部署/修复", card6)
        btn_guardian_deploy.clicked.connect(self._deploy_guardian)
        guardian_btn_row.addWidget(btn_guardian_deploy)

        btn_guardian_start = PushButton(FIF.CONNECT, "启动保活", card6)
        btn_guardian_start.clicked.connect(self._start_guardian)
        guardian_btn_row.addWidget(btn_guardian_start)
        guardian_btn_row.addStretch(1)
        c6_lay.addLayout(guardian_btn_row)

        grid.addWidget(card6, 1, 1, 1, 1)

        # Global Hotkey Settings Card
        card4 = SimpleCardWidget(container)
        c4_lay = QVBoxLayout(card4)
        c4_lay.setContentsMargins(20, 20, 20, 20)
        c4_lay.setSpacing(15)
        
        self._add_icon_title(c4_lay, FIF.TAG, "自定义全局快捷键", card4)
        
        lbl_c4_desc = BodyLabel("为所有带档位切换的功能提供自定义全局快捷键支持。支持后台/游戏中静默控制，设置完成后自动弹出系统原生气泡通知。", card4)
        lbl_c4_desc.setWordWrap(True)

        c4_lay.addWidget(lbl_c4_desc)
        
        self.hotkey_combos = {}
        self.adjust_hotkey_rows = []
        hotkeys_settings = settings.get("hotkeys", {})
        adjust_hotkeys_settings = settings.get("adjust_hotkeys", [])
        
        actions_list = [
            ("picture_mode_cycle", "画面模式 循环切换"),
            ("local_dimming_cycle", "精密控光 循环切换"),
            ("local_dimming_toggle_off", "精密控光 开关切换"),
            ("color_space_cycle", "色域 循环切换"),
            ("color_temp_cycle", "色温 循环切换"),
            ("response_time_cycle", "响应时间 循环切换"),
            ("freesync_toggle", "FreeSync 开关切换"),
            ("input_source_cycle", "信号源 循环切换")
        ]
        
        def add_hotkey_row(row_layout_parent, label_text, action_name):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(15)
            
            lbl = BodyLabel(label_text, card4)
            lbl.setFixedWidth(180)
            row_layout.addWidget(lbl)
            
            mod_combo = ComboBox(card4)
            mod_combo.addItems(HOTKEY_MODIFIERS)
            mod_combo.setFixedWidth(130)
            
            key_combo = ComboBox(card4)
            key_combo.addItems(HOTKEY_KEYS)
            key_combo.setFixedWidth(100)
            
            row_layout.addWidget(mod_combo)
            row_layout.addWidget(key_combo)
            row_layout.addStretch()
            row_layout_parent.addLayout(row_layout)
            
            hk_conf = hotkeys_settings.get(action_name, {"modifier": "无", "key": "无"})
            mod_idx = mod_combo.findText(hk_conf.get("modifier", "无"))
            if mod_idx >= 0: mod_combo.setCurrentIndex(mod_idx)
            key_idx = key_combo.findText(hk_conf.get("key", "无"))
            if key_idx >= 0: key_combo.setCurrentIndex(key_idx)
            
            self.hotkey_combos[action_name] = (mod_combo, key_combo)
            
        for act_name, label_txt in actions_list:
            add_hotkey_row(c4_lay, label_txt, act_name)

        adjust_title = BodyLabel("可调参数快捷键", card4)

        c4_lay.addWidget(adjust_title)

        param_keys = list(ADJUSTABLE_HOTKEY_PARAMS.keys())
        param_labels = [ADJUSTABLE_HOTKEY_PARAMS[k]["label"] for k in param_keys]

        def add_adjust_hotkey_row(rule=None):
            rule = rule or {
                "param": "backlight",
                "direction": "increase",
                "step": ADJUSTABLE_HOTKEY_PARAMS["backlight"]["step"],
                "modifier": "无",
                "key": "无",
            }
            row_widget = QWidget(card4)
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)

            param_combo = ComboBox(row_widget)
            param_combo.addItems(param_labels)
            param_combo.setFixedWidth(120)
            param_idx = param_keys.index(rule.get("param")) if rule.get("param") in param_keys else 0
            param_combo.setCurrentIndex(param_idx)

            direction_combo = ComboBox(row_widget)
            direction_combo.addItems(["增加", "减少"])
            direction_combo.setFixedWidth(70)
            direction_combo.setCurrentIndex(1 if rule.get("direction") == "decrease" else 0)

            step_edit = LineEdit(row_widget)
            step_edit.setFixedWidth(54)
            step_edit.setText(str(rule.get("step", ADJUSTABLE_HOTKEY_PARAMS[param_keys[param_idx]].get("step", 1))))
            step_edit.setPlaceholderText("步进")

            mod_combo = ComboBox(row_widget)
            mod_combo.addItems(HOTKEY_MODIFIERS)
            mod_combo.setFixedWidth(120)
            mod_idx = mod_combo.findText(rule.get("modifier", "无"))
            if mod_idx >= 0:
                mod_combo.setCurrentIndex(mod_idx)

            key_combo = ComboBox(row_widget)
            key_combo.addItems(HOTKEY_KEYS)
            key_combo.setFixedWidth(82)
            key_idx = key_combo.findText(rule.get("key", "无"))
            if key_idx >= 0:
                key_combo.setCurrentIndex(key_idx)

            delete_btn = PushButton("删除", row_widget)
            delete_btn.setFixedWidth(58)

            row_layout.addWidget(param_combo)
            row_layout.addWidget(direction_combo)
            row_layout.addWidget(step_edit)
            row_layout.addWidget(mod_combo)
            row_layout.addWidget(key_combo)
            row_layout.addWidget(delete_btn)
            row_layout.addStretch(1)
            insert_before = getattr(self, "adjust_hotkey_add_button", None)
            insert_index = c4_lay.indexOf(insert_before) if insert_before else -1
            if insert_index >= 0:
                c4_lay.insertWidget(insert_index, row_widget)
            else:
                c4_lay.addWidget(row_widget)

            row_ref = {
                "widget": row_widget,
                "param": param_combo,
                "direction": direction_combo,
                "step": step_edit,
                "modifier": mod_combo,
                "key": key_combo,
            }
            self.adjust_hotkey_rows.append(row_ref)

            def remove_row():
                if row_ref in self.adjust_hotkey_rows:
                    self.adjust_hotkey_rows.remove(row_ref)
                row_widget.setParent(None)
                row_widget.deleteLater()

            delete_btn.clicked.connect(remove_row)

        for rule in adjust_hotkeys_settings:
            if isinstance(rule, dict):
                add_adjust_hotkey_row(rule)

        btn_add_adjust_hotkey = PushButton("新建可调快捷键", card4)
        self.adjust_hotkey_add_button = btn_add_adjust_hotkey
        btn_add_adjust_hotkey.clicked.connect(lambda: add_adjust_hotkey_row())
        c4_lay.addWidget(btn_add_adjust_hotkey)
            
        btn_save_hotkeys = PrimaryPushButton(FIF.TAG, "保存并应用全局快捷键", card4)
        c4_lay.addWidget(btn_save_hotkeys)
        
        def save_and_apply_hotkeys():
            new_hotkeys = {}
            for act_name, (m_combo, k_combo) in self.hotkey_combos.items():
                m_val = m_combo.currentText()
                k_val = k_combo.currentText()
                new_hotkeys[act_name] = {"modifier": m_val, "key": k_val}

            new_adjust_hotkeys = []
            for row in self.adjust_hotkey_rows:
                if not row["widget"].parent():
                    continue
                param_idx = row["param"].currentIndex()
                param_key = param_keys[param_idx] if 0 <= param_idx < len(param_keys) else "backlight"
                cfg = ADJUSTABLE_HOTKEY_PARAMS[param_key]
                try:
                    step_val = abs(int(row["step"].text().strip()))
                except Exception:
                    step_val = cfg.get("step", 1)
                if step_val <= 0:
                    step_val = cfg.get("step", 1)
                new_adjust_hotkeys.append({
                    "param": param_key,
                    "direction": "decrease" if row["direction"].currentText() == "减少" else "increase",
                    "step": step_val,
                    "modifier": row["modifier"].currentText(),
                    "key": row["key"].currentText(),
                })
                
            update_settings({
                "hotkeys": new_hotkeys,
                "adjust_hotkeys": new_adjust_hotkeys,
            })
            
            self.register_global_hotkeys()
            self.log("全局快捷键保存并重新注册成功！")
            
            self.tray_icon.showMessage(
                "红米 G Pro 27U Toolbox",
                "全局快捷键已保存并重新应用！",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
            
        btn_save_hotkeys.clicked.connect(save_and_apply_hotkeys)
        grid.addWidget(card4, 3, 0, 1, 2)

        layout.addLayout(grid)

        github_link = BodyLabel(container)
        github_link.setText('仓库地址：<a href="https://github.com/YiHooong/Mimonitor_Toolbox" style="color: #734EFF;">https://github.com/YiHooong/Mimonitor_Toolbox</a>')
        github_link.setOpenExternalLinks(True)
        github_link.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(github_link)

        scroll.setWidget(container)
        return scroll

    def _make_remote_page(self):
        container = QWidget()
        container.setObjectName("RemoteContainer")
        container.setStyleSheet("#RemoteContainer { background: transparent; }")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)



        title = SubtitleLabel("遥控器", container)

        layout.addWidget(title)

        main_frame = QFrame(container)
        main_frame.setStyleSheet("background: transparent;")
        main_layout = QHBoxLayout(main_frame)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(40)

        # High-End Remote Controller body (Simulated Hardware)
        remote_card = SimpleCardWidget(main_frame)
        remote_card.setFixedSize(300, 520)
        
        rc_layout = QVBoxLayout(remote_card)
        rc_layout.setContentsMargins(25, 25, 25, 25)
        rc_layout.setSpacing(18)
        rc_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Branding top
        logo_label = BodyLabel("G PRO CONTROL", remote_card)
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_label.setStyleSheet("""
            font-size: 11px;
            font-weight: bold;
            letter-spacing: 3px;
            color: #0078d4;
            margin-bottom: 5px;
        """)
        rc_layout.addWidget(logo_label)

        # Top row: Power Button
        row_top = QHBoxLayout()
        row_top.setAlignment(Qt.AlignmentFlag.AlignCenter)
        power_btn = PushButton("⏻", remote_card)
        power_btn.setFixedSize(44, 44)
        power_btn.setStyleSheet("""
            QPushButton {
                background-color: #e81123;
                border: 1px solid #ff4350;
                border-radius: 22px;
                color: white;
                font-size: 18px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #ff2d3d;
                border-color: #ff5f6d;
            }
            QPushButton:pressed {
                background-color: #b30b18;
            }
        """)
        power_btn.clicked.connect(lambda: self._key("KEYCODE_POWER"))
        row_top.addWidget(power_btn)
        rc_layout.addLayout(row_top)

        # Menu buttons row (Home, Menu, Back)
        row_menu = QHBoxLayout()
        row_menu.setSpacing(10)
        
        btn_home = PushButton("主页", remote_card)
        btn_menu = PushButton("菜单", remote_card)
        btn_back = PushButton("返回", remote_card)

        for btn, key in [(btn_home, "KEYCODE_HOME"), (btn_menu, "KEYCODE_MENU"), (btn_back, "KEYCODE_BACK")]:
            btn.setFixedSize(72, 32)
            
            btn.clicked.connect(lambda checked=False, k=key: self._key(k))
            row_menu.addWidget(btn)
            
        rc_layout.addLayout(row_menu)

        # Elegant Circular D-Pad Wheel
        dpad_container = QFrame(remote_card)
        dpad_container.setFixedSize(190, 190)
        dpad_container.setStyleSheet("""
            QFrame {
                background-color: rgba(128, 128, 128, 0.1);
                border: 1px solid rgba(128, 128, 128, 0.2);
                border-radius: 95px;
            }
        """)
        dpad_layout = QGridLayout(dpad_container)
        dpad_layout.setContentsMargins(5, 5, 5, 5)
        dpad_layout.setSpacing(0)

        btn_up = PushButton("▲", dpad_container)
        btn_down = PushButton("▼", dpad_container)
        btn_left = PushButton("◀", dpad_container)
        btn_right = PushButton("▶", dpad_container)
        btn_ok = PrimaryPushButton("OK", dpad_container)

        btn_up.setFixedSize(50, 42)
        btn_down.setFixedSize(50, 42)
        btn_left.setFixedSize(42, 50)
        btn_right.setFixedSize(42, 50)
        btn_ok.setFixedSize(62, 62)

        # Style the D-pad arrow keys
        arrow_style = """
            QPushButton {
                background: transparent;
                border: none;
                color: #888888;
                font-size: 18px;
            }
            QPushButton:hover {
                color: #0078d4;
            }
            QPushButton:pressed {
                color: #005a9e;
            }
        """
        for b in [btn_up, btn_down, btn_left, btn_right]:
            b.setStyleSheet(arrow_style)

        # Style the central circular OK button
        btn_ok.setStyleSheet("""
            QPushButton {
                background-color: #0078d4;
                border: 1px solid #0078d4;
                border-radius: 31px;
                color: white;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #0086f0;
                border-color: #0086f0;
            }
            QPushButton:pressed {
                background-color: #006cc0;
            }
        """)

        btn_up.clicked.connect(lambda: self._key("KEYCODE_DPAD_UP"))
        btn_down.clicked.connect(lambda: self._key("KEYCODE_DPAD_DOWN"))
        btn_left.clicked.connect(lambda: self._key("KEYCODE_DPAD_LEFT"))
        btn_right.clicked.connect(lambda: self._key("KEYCODE_DPAD_RIGHT"))
        btn_ok.clicked.connect(lambda: self._key("KEYCODE_DPAD_CENTER"))

        dpad_layout.addWidget(btn_up, 0, 1, Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(btn_left, 1, 0, Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(btn_ok, 1, 1, Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(btn_right, 1, 2, Qt.AlignmentFlag.AlignCenter)
        dpad_layout.addWidget(btn_down, 2, 1, Qt.AlignmentFlag.AlignCenter)

        # Add D-pad wrapper with alignment to layout
        dpad_wrapper = QHBoxLayout()
        dpad_wrapper.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dpad_wrapper.addWidget(dpad_container)
        rc_layout.addLayout(dpad_wrapper)

        # Vol Row: Pill layout (Vol-, Mute, Vol+)
        vol_layout = QHBoxLayout()
        vol_layout.setSpacing(8)
        vol_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_vol_down = PushButton("🔉 音量-", remote_card)
        btn_mute = PushButton("🔇 静音", remote_card)
        btn_vol_up = PushButton("🔊 音量+", remote_card)

        for btn, key in [(btn_vol_down, "KEYCODE_VOLUME_DOWN"), (btn_mute, "KEYCODE_VOLUME_MUTE"), (btn_vol_up, "KEYCODE_VOLUME_UP")]:
            btn.setFixedSize(74, 34)
            
            btn.clicked.connect(lambda checked=False, k=key: self._key(k))
            vol_layout.addWidget(btn)

        rc_layout.addLayout(vol_layout)
        main_layout.addWidget(remote_card, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(main_frame)
        return container

    def _add_slider(self, parent_layout, title, name, lo, hi, default, jni_key=None, settings_keys=None):
        card = SimpleCardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)

        name_label = BodyLabel(title, card)
        name_label.setFixedWidth(100)
        layout.addWidget(name_label)

        slider = PageScrollSlider(Qt.Orientation.Horizontal, card)
        slider.setRange(lo, hi)
        slider.setValue(default)
        layout.addWidget(slider)

        val_label = BodyLabel(str(default), card)
        val_label.setFixedWidth(40)
        val_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(val_label)

        slider.valueChanged.connect(lambda v: val_label.setText(str(v)))
        self.sliders[name] = (slider, val_label)

        _debounce_timer = QTimer(card)
        _debounce_timer.setSingleShot(True)
        _debounce_timer.setInterval(300)

        def on_commit():
            if not self.check_connection():
                return
            v = slider.value()
            self._mark_adb_busy(2.5)
            def operation():
                with self.adb.transaction():
                    if jni_key:
                        self.adb.jni_set(jni_key, v, check=True)
                        self.adb.refresh_pq(check=True)
                    if settings_keys:
                        for k in settings_keys: self.adb.put(k, str(v), check=True)
            self._run_adb_action(
                title,
                operation,
                lambda: self.log(f"{title}: {v}"),
                lambda: self._force_refresh_page("picturePage"),
            )

        _debounce_timer.timeout.connect(on_commit)
        slider.valueChanged.connect(lambda v: _debounce_timer.start())
        parent_layout.addWidget(card)

    def _add_color_gain_slider(self, parent_layout, title, name, settings_key, jni_key):
        card = SimpleCardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)

        name_label = BodyLabel(title, card)
        name_label.setFixedWidth(100)
        layout.addWidget(name_label)

        slider = PageScrollSlider(Qt.Orientation.Horizontal, card)
        slider.setRange(524, 1524)
        slider.setValue(1024)
        layout.addWidget(slider)

        val_label = BodyLabel("1024", card)
        val_label.setFixedWidth(48)
        val_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(val_label)

        slider.valueChanged.connect(lambda v: val_label.setText(str(v)))
        self.sliders[name] = (slider, val_label)

        _debounce_timer = QTimer(card)
        _debounce_timer.setSingleShot(True)
        _debounce_timer.setInterval(300)

        def on_commit():
            self._set_color_gain(title, settings_key, jni_key, slider.value())

        _debounce_timer.timeout.connect(on_commit)
        slider.valueChanged.connect(lambda _v: _debounce_timer.start())
        parent_layout.addWidget(card)
        self.color_gain_cards.append(card)
        card.setVisible(False)

    def _add_light_slider(self, parent_layout, title, name, lo, hi, default):
        card = SimpleCardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)

        name_label = BodyLabel(title, card)
        name_label.setFixedWidth(100)
        layout.addWidget(name_label)

        slider = PageScrollSlider(Qt.Orientation.Horizontal, card)
        slider.setRange(lo, hi)
        slider.setValue(default)
        layout.addWidget(slider)

        val_label = BodyLabel(str(default), card)
        val_label.setFixedWidth(40)
        val_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(val_label)

        slider.valueChanged.connect(lambda v: val_label.setText(str(v)))
        self.sliders[name] = (slider, val_label)

        _debounce_timer = QTimer(card)
        _debounce_timer.setSingleShot(True)
        _debounce_timer.setInterval(300)
        _debounce_timer.timeout.connect(lambda: self._set_screen_light_illumination(slider.value()))
        slider.valueChanged.connect(lambda v: _debounce_timer.start())
        parent_layout.addWidget(card)

    def _btn_section(self, parent_layout, title, buttons, state_key=None):
        card = SimpleCardWidget(self)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(15, 10, 15, 10)
        
        title_label = BodyLabel(title, card)
        title_label.setFixedWidth(180)
        layout.addWidget(title_label)
        
        btns_layout = QHBoxLayout()
        btns_layout.setSpacing(8)
        btns_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        if state_key:
            if state_key not in self.state_buttons:
                self.state_buttons[state_key] = {}
            for text, val, cmd in buttons:
                b = ToggleButton(text, card)
                b.setCheckable(True)
                b.setMinimumWidth(80)
                b.clicked.connect(cmd)
                btns_layout.addWidget(b)
                self.state_buttons[state_key][val] = b
        else:
            for text, cmd in buttons:
                b = PushButton(text, card)
                b.setMinimumWidth(80)
                b.clicked.connect(cmd)
                btns_layout.addWidget(b)
                
        layout.addLayout(btns_layout)
        parent_layout.addWidget(card)
        return card

    def _add_icon_title(self, layout, icon, text, parent):
        row = QHBoxLayout()
        row.setSpacing(9)
        row.setContentsMargins(0, 0, 0, 0)
        row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        icon_widget = IconWidget(icon, parent)
        icon_widget.setFixedSize(18, 18)
        row.addWidget(icon_widget, 0, Qt.AlignmentFlag.AlignVCenter)
        label = SubtitleLabel(text, parent)
        label.setFixedHeight(26)
        row.addWidget(label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        wrapper = QWidget(parent)
        wrapper.setFixedHeight(26)
        wrapper.setLayout(row)
        layout.addWidget(wrapper)
        return label
