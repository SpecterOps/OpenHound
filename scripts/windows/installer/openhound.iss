; Compile through scripts/windows/build-installer.ps1.
#ifndef PayloadDir
  #error PayloadDir is required
#endif
#ifndef InstallerOutputDir
  #error InstallerOutputDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef NumericVersion
  #error NumericVersion is required
#endif

[Setup]
AppId={{44B99C67-348E-47B5-9650-CC28421A44B6}
AppName=OpenHound
AppVersion={#AppVersion}
AppPublisher=SpecterOps
AppPublisherURL=https://github.com/SpecterOps/openhound
DefaultDirName={autopf}\OpenHound
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#InstallerOutputDir}
OutputBaseFilename=openhound-{#AppVersion}-windows-x64-setup
VersionInfoVersion={#NumericVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile={#PayloadDir}\LICENSE.md
UsePreviousSetupType=yes
UsePreviousAppDir=yes
AppMutex=Global\SpecterOps.OpenHound.Scheduler
SetupMutex=Global\SpecterOps.OpenHound.Setup
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\python\python.exe

[Types]
Name: "full"; Description: "Runtime and all extensions"
Name: "minimal"; Description: "Runtime only"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

#include PayloadDir + "\installer-components.iss"

[Code]
var
  ManagedPaths: TArrayOfString;
  BackedUp: array of Boolean;
  BackupRoot: String;
  BackupCreated, BackupPrepared, InstallSucceeded: Boolean;
#ifdef TestCancelInstall
  CancellationRequested: Boolean;
#endif

#include PayloadDir + "\installer-managed-paths.iss"

function DeleteManagedPath(const Path: String): Boolean;
begin
  if DirExists(Path) then
    Result := DelTree(Path, True, True, True)
  else if FileExists(Path) then
    Result := DeleteFile(Path)
  else
    Result := True;
end;

procedure DeleteBackup;
begin
  if BackupCreated then begin
    if DelTree(BackupRoot, True, True, True) then
      BackupCreated := False
    else
      Log('Could not remove installation backup: ' + BackupRoot);
  end;
end;

function RestoreBackup: Boolean;
var
  I: Integer;
  OriginalPath, SavedPath: String;
begin
  Result := True;
  for I := 0 to GetArrayLength(ManagedPaths) - 1 do begin
    { Before extraction, restore only entries already moved. After preparation,
      also remove entries which did not exist in the original installation. }
    if BackupPrepared or BackedUp[I] then begin
      OriginalPath := ExpandConstant('{app}\') + ManagedPaths[I];
      SavedPath := BackupRoot + '\' + ManagedPaths[I];
      if not DeleteManagedPath(OriginalPath) then begin
        Log('Could not remove incomplete installation path: ' + OriginalPath);
        Result := False;
      end else if BackedUp[I] then begin
        if RenameFile(SavedPath, OriginalPath) then
          BackedUp[I] := False
        else begin
          Log('Could not restore installation path from: ' + SavedPath);
          Result := False;
        end;
      end;
    end;
  end;
  if Result then begin
    DeleteBackup;
    BackupPrepared := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  I: Integer;
  OriginalPath: String;
begin
  Result := '';
  if CheckForMutexes('Global\SpecterOps.OpenHound.Scheduler') then begin
    Result := 'Stop all OpenHound scheduler instances before changing the installation. ' +
      'Use Ctrl+C or Ctrl+Break and wait for active collections to finish.';
    Exit;
  end;
  if BackupPrepared then Exit;
  GetManagedPaths(ManagedPaths);
  SetArrayLength(BackedUp, GetArrayLength(ManagedPaths));
  BackupRoot := ExpandConstant('{app}\.openhound-backup');
  if DirExists(BackupRoot) or FileExists(BackupRoot) then begin
    Result := 'An earlier installation backup exists at ' + BackupRoot +
      '. Recover that backup before rerunning setup.';
    Exit;
  end;
  for I := 0 to GetArrayLength(ManagedPaths) - 1 do begin
    OriginalPath := ExpandConstant('{app}\') + ManagedPaths[I];
    if DirExists(OriginalPath) or FileExists(OriginalPath) then begin
      if not BackupCreated then begin
        if not CreateDir(BackupRoot) then begin
          Result := 'Could not create installation backup: ' + BackupRoot;
          Exit;
        end;
        BackupCreated := True;
      end;
      if not RenameFile(OriginalPath, BackupRoot + '\' + ManagedPaths[I]) then begin
        Result := 'Could not back up ' + OriginalPath + '. Close processes using OpenHound and retry.';
        if not RestoreBackup then
          Result := Result + ' Recover the previous files from ' + BackupRoot + '.';
        Exit;
      end;
      BackedUp[I] := True;
    end;
  end;
  BackupPrepared := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    InstallSucceeded := True;
end;

procedure DeinitializeSetup;
begin
  if InstallSucceeded then
    DeleteBackup
  else if BackupCreated or BackupPrepared then begin
    if not RestoreBackup then
      SuppressibleMsgBox('Could not restore all previous application files. Recover them from ' +
        BackupRoot + '.', mbCriticalError, MB_OK, IDOK);
  end;
end;

#ifdef TestCancelInstall
{ Only the CI cancellation-test EXE contains these hooks. }
procedure CurInstallProgressChanged(CurProgress, MaxProgress: Integer);
begin
  if (CurProgress > 0) and not CancellationRequested then begin
    CancellationRequested := True;
    PostMessage(WizardForm.Handle, $0010, 0, 0); { WM_CLOSE }
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if CancellationRequested then Confirm := False;
end;
#endif
