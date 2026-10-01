#ifndef PayloadDir
  #error PayloadDir must point to the built Windows runtime.
#endif
#ifndef AppVersion
  #error AppVersion must come from runtime-info.json.
#endif
#ifndef FileVersion
  #error FileVersion must be a numeric Windows file version.
#endif
#ifndef OutputBaseName
  #error OutputBaseName must be set by build-installer.ps1.
#endif

[Setup]
AppId={{C661634B-1D52-4E51-8D5B-7CBDBBC75DE0}
AppName=OpenHound
AppVersion={#AppVersion}
AppPublisher=SpecterOps
DefaultDirName={autopf}\OpenHound
DisableDirPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
OutputBaseFilename={#OutputBaseName}
VersionInfoVersion={#FileVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
UninstallDisplayName=OpenHound

[InstallDelete]
; Remove private dependencies that disappeared between releases. Instance data is outside {app}.
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
