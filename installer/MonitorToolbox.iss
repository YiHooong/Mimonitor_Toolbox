; MonitorToolbox Inno Setup 安装器
; 本地构建: build_installer.bat（先产出 standalone 目录再编译）
; CI 构建: ISCC /DMyAppVersion=<tag> installer\MonitorToolbox.iss

#ifndef MyAppVersion
#define MyAppVersion "0.0.0-dev"
#endif

#define MyAppName "红米 G Pro 27U Toolbox"
#define MyAppExeName "MonitorToolbox.exe"

[Setup]
AppId={{ABB559AF-03B2-4BA3-B1CD-8595E7E4A9F0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Mimonitor Toolbox
DefaultDirName={autopf}\MonitorToolbox
DefaultGroupName={#MyAppName}
UninstallDisplayName={#MyAppName}
OutputDir=..\dist-installer
OutputBaseFilename=MonitorToolbox-Setup
SetupIconFile=..\assets\app\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist-nuitka-standalone\monitor_controller.dist\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
