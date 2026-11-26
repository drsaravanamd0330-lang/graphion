; Inno Setup 6 script for the Journal Typesetting Studio installer.
; build.ps1 compiles it with /DAppVersion, /DSourceDir and /DOutputDir.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\..\dist\Journal Typesetting Studio"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\..\dist"
#endif
#define AppName "Journal Typesetting Studio"
#define AppExe "Journal Typesetting Studio.exe"

[Setup]
AppId={{6E3F2C1A-9B7D-4E58-A0C4-3D2B71F5E8A9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Journal Typesetting Studio
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installs for the current user without asking for administrator rights;
; the editor can choose "all users" on the first screen.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=JournalTypesettingStudio-Setup-{#AppVersion}
SetupIconFile=..\branding\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Put an icon on the &Desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

; Articles, journal profiles and criteria live in Documents\Journal Typesetting
; Studio and are deliberately left in place on uninstall.
