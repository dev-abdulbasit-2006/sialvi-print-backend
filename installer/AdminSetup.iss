; ============================================================
; SialviPrint Admin — Inno Setup installer script
;
; Build with Inno Setup 6 (https://jrsoftware.org/isinfo.php):
;   1. Run PyInstaller first (admin/build_admin.spec) so
;      dist\SialviPrintAdmin\ exists.
;   2. Open this file in Inno Setup Compiler, or run:
;        iscc installer\AdminSetup.iss
;   3. Output: installer\Output\AdminSetup.exe
;
; This installer needs NO Python, NO pip, NO VS Code on the target
; machine - dist\SialviPrintAdmin\ already contains everything
; PyInstaller bundled.
; ============================================================

#define MyAppName "SialviPrint Admin"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "SialviPrint"
#define MyAppExeName "SialviPrintAdmin.exe"
#define SourceDistDir "..\dist\SialviPrintAdmin"

[Setup]
AppId={{8F2C1A10-3B4D-4E5F-9A1B-AD0000000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\SialviPrint\Admin
DefaultGroupName=SialviPrint
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=AdminSetup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
SetupIconFile=assets\admin_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; Requires admin rights - the Admin app binds a LAN port and writes
; to ProgramData, both of which are cleanest with elevated install.
PrivilegesRequired=admin
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "firewallrule"; Description: "Allow this app through Windows Firewall (required for User PCs to connect)"; GroupDescription: "Network:"; Flags: checkedonce
Name: "autostart"; Description: "Start SialviPrint Admin automatically when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Dirs]
; The Admin application runs as the logged-in Windows user, not as a service.
; Explicitly grant that runtime account the inherited modify permission it needs
; for SQLite/WAL, uploads, logs and config under ProgramData. This also fixes
; the first-run/after-install WinError 183 + WinError 5 combination where the
; elevated installer-created folders exist but the normal user cannot access them.
Name: "{commonappdata}\\SialviPrint"; Permissions: users-modify
Name: "{commonappdata}\\SialviPrint\\data"; Permissions: users-modify
Name: "{commonappdata}\\SialviPrint\\uploads"; Permissions: users-modify
Name: "{commonappdata}\\SialviPrint\\tmp"; Permissions: users-modify
Name: "{commonappdata}\\SialviPrint\\logs"; Permissions: users-modify

[Files]
; Everything PyInstaller collected - the whole onedir tree
Source: "{#SourceDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Offline-LAN firewall repair helper (run as Administrator if User PCs cannot connect)
Source: "Fix_LAN_Firewall.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Optional auto-start on login, only if the user opted in
Root: HKLM; Subkey: "SOFTWARE\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "SialviPrintAdmin"; ValueData: """{app}\{#MyAppExeName}"""; \
    Tasks: autostart; Flags: uninsdeletevalue

[Run]
; Add a Windows Firewall inbound rule for the LAN API port so User PCs
; can reach it without the operator having to find Windows Firewall
; settings manually. netsh ships with every supported Windows version.
; Offline wired LAN: rules apply to ALL profiles (a wired network with no internet is
; classed "Public" by Windows) but ONLY to local/private source addresses.
; Old rules are deleted first so upgrades replace them instead of duplicating.
Filename: "{sys}\netsh.exe"; \
    Parameters: "advfirewall firewall delete rule name=""SialviPrint Admin"""; \
    Tasks: firewallrule; Flags: runhidden
Filename: "{sys}\netsh.exe"; \
    Parameters: "advfirewall firewall delete rule name=""SialviPrint Admin API"""; \
    Tasks: firewallrule; Flags: runhidden
Filename: "{sys}\netsh.exe"; \
    Parameters: "advfirewall firewall delete rule name=""SialviPrint Admin Discovery"""; \
    Tasks: firewallrule; Flags: runhidden
Filename: "{sys}\netsh.exe"; \
    Parameters: "advfirewall firewall add rule name=""SialviPrint Admin"" dir=in action=allow program=""{app}\{#MyAppExeName}"" enable=yes profile=any remoteip=LocalSubnet,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16"; \
    Tasks: firewallrule; Flags: runhidden
Filename: "{sys}\netsh.exe"; \
    Parameters: "advfirewall firewall add rule name=""SialviPrint Admin API"" dir=in action=allow protocol=TCP localport=8765 enable=yes profile=any remoteip=LocalSubnet,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16"; \
    Tasks: firewallrule; Flags: runhidden
Filename: "{sys}\netsh.exe"; \
    Parameters: "advfirewall firewall add rule name=""SialviPrint Admin Discovery"" dir=in action=allow protocol=UDP localport=8766 enable=yes profile=any remoteip=LocalSubnet,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16"; \
    Tasks: firewallrule; Flags: runhidden

Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; \
    Parameters: "advfirewall firewall delete rule name=""SialviPrint Admin"""; \
    Flags: runhidden
Filename: "{sys}\netsh.exe"; \
    Parameters: "advfirewall firewall delete rule name=""SialviPrint Admin Discovery"""; \
    Flags: runhidden
Filename: "{sys}\netsh.exe"; \
    Parameters: "advfirewall firewall delete rule name=""SialviPrint Admin API"""; \
    Flags: runhidden

[UninstallDelete]
; Deliberately does NOT touch %PROGRAMDATA%\SialviPrint (the database,
; uploads, and logs). Uninstalling the app must never silently delete
; a shop's order history and earnings records. If a full wipe is ever
; wanted, that should be an explicit, separately-confirmed action.
