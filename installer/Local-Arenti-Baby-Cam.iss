#define AppName "Local Arenti Baby Cam"
#define AppVersion "0.1.0-beta"
#ifndef PayloadDir
  #error PayloadDir must name the allowlisted staging directory created by Build-Installer.ps1
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{4B780B45-2C64-4E9C-9D3D-79B1F7BD04A1}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Tyler Buza
AppPublisherURL=https://github.com/TylerBuza/local-arenti-baby-cam
AppSupportURL=https://github.com/TylerBuza/local-arenti-baby-cam/issues
AppUpdatesURL=https://github.com/TylerBuza/local-arenti-baby-cam/releases
DefaultDirName={localappdata}\Programs\Local Arenti Baby Cam
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
WizardSizePercent=115
DisableWelcomePage=no
DisableProgramGroupPage=yes
LicenseFile={#PayloadDir}\LICENSE
InfoBeforeFile=Setup-Info.txt
SetupIconFile={#PayloadDir}\app.ico
UninstallDisplayIcon={app}\app.ico
UninstallDisplayName={#AppName}
CreateUninstallRegKey=not TestMode
CloseApplications=no
RestartApplications=no
Compression=lzma2
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename=Local-Arenti-Baby-Cam-Windows-Setup-{#AppVersion}
SetupLogging=yes
ExtraDiskSpaceRequired=1600000000
VersionInfoVersion=0.1.0.0
VersionInfoDescription=Local Arenti Baby Cam Setup
VersionInfoCopyright=Copyright (c) 2026 Tyler Buza

[Tasks]
Name: desktopicon; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "Bootstrap-Runtime.ps1"; Flags: dontcopy
Source: "{#PayloadDir}\requirements.txt"; Flags: dontcopy

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\runtime\Scripts\pythonw.exe"; Parameters: """{app}\desktop_app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\runtime\Scripts\pythonw.exe"; Parameters: """{app}\desktop_app.py"""; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\Scripts\pythonw.exe"; Parameters: """{app}\desktop_app.py"""; WorkingDir: "{app}"; Description: "Open {#AppName} and set up your camera"; Flags: nowait postinstall skipifsilent unchecked

[UninstallRun]
Filename: "{app}\runtime\Scripts\python.exe"; Parameters: "-c ""import desktop_autostart as a; a.set_enabled(False) if a.enabled() else None"""; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveOwnedStartup"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\.python"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\gateway\__pycache__"

[Code]
var
  RuntimePage: TOutputProgressWizardPage;
  LastOutput: String;

function TestMode: Boolean;
begin
  { Allows isolated installation/uninstallation tests without a registered app. }
  Result := ExpandConstant('{param:TESTMODE|0}') = '1';
end;

procedure InitializeWizard;
begin
  WizardForm.WelcomeLabel2.Caption :=
    'Install the Windows viewer and phone connector for your local Arenti/D3 camera.' + #13#10#13#10 +
    'Setup downloads a private Python runtime and the application dependencies. You do not need to install Python yourself.' + #13#10#13#10 +
    'An internet connection and approximately 1.6 GB of free disk space are required. Local viewing does not require the vendor cloud.';
  RuntimePage := CreateOutputProgressPage('Preparing the application', 'Downloading and checking your private runtime.');
end;

procedure RuntimeLog(const S: String; const Error, FirstLine: Boolean);
begin
  Log(S);
  if Trim(S) <> '' then begin
    LastOutput := S;
    RuntimePage.SetText('Setting up Local Arenti Baby Cam', S);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Params: String;
begin
  Result := '';
  LastOutput := '';
  ExtractTemporaryFile('Bootstrap-Runtime.ps1');
  ExtractTemporaryFile('requirements.txt');
  Params := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\Bootstrap-Runtime.ps1') + '" -InstallDirectory "' + ExpandConstant('{app}') + '"';
  RuntimePage.SetText('Preparing your private runtime', 'Checking installation location...');
  RuntimePage.SetProgress(0, 0);
  RuntimePage.Show;
  try
    if not ExecAndLogOutput(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Params, ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, ResultCode, @RuntimeLog) then
      Result := 'Could not start Windows PowerShell. Check the setup log and retry.'
    else if ResultCode <> 0 then
      Result := LastOutput + #13#10#13#10 + 'Setup has not completed. Fix the issue above and click Install to retry.';
  finally
    RuntimePage.Hide;
  end;
end;

function InitializeUninstall: Boolean;
var
  ResultCode: Integer;
  Params: String;
begin
  Params := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{app}\Bootstrap-Runtime.ps1') + '" -InstallDirectory "' + ExpandConstant('{app}') + '" -PreflightOnly';
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Params,
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  if not Result then
    SuppressibleMsgBox('Exit Local Arenti Baby Cam from its system tray before uninstalling. If it is already closed, check that this installation has not been moved or modified.', mbError, MB_OK, IDOK);
end;
