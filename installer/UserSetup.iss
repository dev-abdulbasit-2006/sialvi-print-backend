; ============================================================
; SialviPrint User (kiosk) — Inno Setup installer script
;
; Build with Inno Setup 6:
;   1. Run PyInstaller first (user/build_user.spec) so
;      dist\SialviPrintUser\ exists.
;   2. iscc installer\UserSetup.iss
;   3. Output: installer\Output\UserSetup.exe
; ============================================================

#define MyAppName "SialviPrint"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "SialviPrint"
#define MyAppExeName "SialviPrintUser.exe"
#define SourceDistDir "..\dist\SialviPrintUser"

[Setup]
AppId={{8F2C1A10-3B4D-4E5F-9A1B-A50000000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\SialviPrint\User
DefaultGroupName=SialviPrint
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=UserSetup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
SetupIconFile=assets\user_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=admin
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce
Name: "autostart"; Description: "Start SialviPrint automatically when Windows starts (recommended for kiosk PCs)"; GroupDescription: "Startup:"; Flags: checkedonce
Name: "firewallrule"; Description: "Allow SialviPrint User print agent through Windows Firewall (required for Admin-to-User printing)"; GroupDescription: "Network:"; Flags: checkedonce

[Files]
Source: "{#SourceDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Offline-LAN firewall repair helper (run as Administrator if the Admin PC cannot print to this PC)
Source: "Fix_LAN_Firewall.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "SialviPrintUser"; ValueData: """{app}\{#MyAppExeName}"""; \
    Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SialviPrint User Print Agent"""; Tasks: firewallrule; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""SialviPrint User Print Agent"" dir=in action=allow protocol=TCP localport=8767 enable=yes profile=any remoteip=LocalSubnet,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16"; Tasks: firewallrule; Flags: runhidden
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe";     Parameters: "advfirewall firewall delete rule name=""SialviPrint User Print Agent""";     Flags: runhidden

[UninstallDelete]
; Does not delete %LOCALAPPDATA%\SialviPrintUser (station config/logs) -
; a reinstall on the same PC should not force re-entering the Admin
; PC's address and connection code.
