; Inno Setup script. Built by the GitHub workflow: ISCC /DVersion=1.1.0 packaging\installer.iss
#ifndef Version
  #define Version "1.1.0"
#endif

[Setup]
AppId={{6F1C2B7E-9D4A-4C55-8E21-5B0F3A7D9C10}
AppName=Transcriptrick
AppVersion={#Version}
AppPublisher=Rick Besada
DefaultDirName={localappdata}\Programs\Transcriptrick
DefaultGroupName=Transcriptrick
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\Output
OutputBaseFilename=Transcriptrick-Setup
SetupIconFile=transcriptrick.ico
UninstallDisplayIcon={app}\Transcriptrick.exe
UninstallDisplayName=Transcriptrick
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Add a desktop shortcut"; GroupDescription: "Shortcuts:"

[InstallDelete]
; Clear the previous version's files so an update never mixes old and new parts.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Transcriptrick\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\Transcriptrick"; Filename: "{app}\Transcriptrick.exe"
Name: "{userdesktop}\Transcriptrick"; Filename: "{app}\Transcriptrick.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Transcriptrick.exe"; Description: "Open Transcriptrick"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /im Transcriptrick.exe /f"; Flags: runhidden; RunOnceId: "StopTranscriptrick"
