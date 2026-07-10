; Inno Setup script — builds a one-click Windows installer for the POS desktop app.
; Compile with:  ISCC.exe installer.iss   (output: installer_output\POS-Setup.exe)
; Prerequisite:  build dist\POS first  (see docs/BUILD_EXE.md)

#define AppName "Wholesale POS System"
#define AppVer "1.0.0"
#define AppPublisher "Wholesale POS System"
#define AppExe "POS.exe"

[Setup]
AppId={{A7F2C9E1-POS0-4B3A-9D5E-POSSYSTEM0001}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\Wholesale POS System
DefaultGroupName=Wholesale POS System
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=POS-Setup
SetupIconFile=app_icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; A POS terminal is per-machine software:
PrivilegesRequired=admin

[Languages]
Name: "arabic"; MessagesFile: "compiler:Languages\Arabic.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; Ship the entire PyInstaller onedir output — but NEVER the runtime data\ folder
; (that's the customer's own database/media, created fresh on first launch).
Source: "dist\POS\*"; DestDir: "{app}"; Excludes: "data\*,data,*.log"; Flags: recursesubdirs createallsubdirs ignoreversion

[Dirs]
; The app writes its database/media here; keep it on uninstall (don't delete customer data).
Name: "{app}\data"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove only the program; the customer's data\ folder is intentionally preserved.
Type: filesandordirs; Name: "{app}\_internal"
