; AniPresence installer (Inno Setup 6).
;
; Build:
;   1. Build the app first:   python -m PyInstaller --noconfirm AniPresence.spec
;      (produces dist\AniPresence\AniPresence.exe + dist\AniPresence\_internal\)
;   2. Compile with ISCC.exe (Inno Setup 6, https://jrsoftware.org/isinfo.php):
;      "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" AniPresence.iss
;
; The version can be overridden from the command line:
;   ISCC.exe /DMyAppVersion=1.2.3 AniPresence.iss
;
; Installs per-user into {localappdata}\Programs\AniPresence so no admin/UAC
; prompt is needed. All user data (config.json, covers, logs) lives in
; %APPDATA%\AniPresence and is intentionally left untouched on uninstall.

#ifndef MyAppVersion
  #define MyAppVersion "1.9.7"
#endif

#define MyAppName "AniPresence"
#define MyAppExeName "AniPresence.exe"

[Setup]
AppId={{AB94BBAC-FC3F-4BFF-91B1-E3F0465FA3C7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=AniPresence
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={localappdata}\Programs\AniPresence
DefaultGroupName=AniPresence
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=AniPresence-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\logo.ico
UninstallDisplayIcon={app}\anipresence-orbit.ico
UninstallDisplayName={#MyAppName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "runapp"; Description: "&Launch {#MyAppName} after finishing"; GroupDescription: "Startup:"; Flags: checkedonce

[Files]
Source: "dist\AniPresence\AniPresence.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\logo.ico"; DestDir: "{app}"; DestName: "anipresence-orbit.ico"; Flags: ignoreversion
Source: "dist\AniPresence\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "browser-extension\*"; DestDir: "{app}\extension"; Excludes: "settings.json"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\anipresence-orbit.ico"; AppUserModelID: "AniPresence.Desktop.Orbit"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\anipresence-orbit.ico"; AppUserModelID: "AniPresence.Desktop.Orbit"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent; Tasks: runapp

[Code]
procedure SHChangeNotify(wEventId, uFlags, dwItem1, dwItem2: Integer);
  external 'SHChangeNotify@shell32.dll stdcall';

procedure CurStepChanged(CurStep: TSetupStep);
var
  Folder: String;
  Entry: TFindRec;
  Shell, Shortcut: Variant;
begin
  if CurStep <> ssPostInstall then Exit;
  { Refresh only pins pointing to this installation; leave other apps alone. }
  Folder := ExpandConstant('{userappdata}\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar\');
  if FindFirst(Folder + '*.lnk', Entry) then begin
    try
      repeat
        try
          Shell := CreateOleObject('WScript.Shell');
          Shortcut := Shell.CreateShortcut(Folder + Entry.Name);
          if CompareText(Shortcut.TargetPath, ExpandConstant('{app}\{#MyAppExeName}')) = 0 then begin
            Shortcut.IconLocation := ExpandConstant('{app}\anipresence-orbit.ico') + ',0';
            Shortcut.Save;
          end;
        except
          Log('Could not refresh a pinned shortcut: ' + Entry.Name);
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  SHChangeNotify($08000000, 0, 0, 0);
end;
