; Owner installer
#define MyAppName "Sialvi Owner License Manager"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "SialviPrint"
#define MyAppExeName "SialviOwnerLicenseManager.exe"
#define SourceDistDir "..\dist\SialviOwnerLicenseManager"

[Setup]
AppId={{8F2C1A10-3B4D-4E5F-9A1B-AD0000000099}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\SialviPrint\Owner
DefaultGroupName=SialviPrint
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=OwnerSetup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
SetupIconFile=assets\admin_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=admin
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#SourceDistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
