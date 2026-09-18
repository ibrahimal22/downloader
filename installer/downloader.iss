; Inno Setup script — built by build/build.py, which passes:
;   /DAppVersion=x.y.z /DSourceDir=dist\Downloader /DOutputDir=dist /DIconFile=build\out\app.ico

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8C1F6C2E-3E0B-4B7B-9C55-6A2B9E7D4F10}
AppName=Downloader
AppVersion={#AppVersion}
AppPublisher=Downloader
DefaultDirName={autopf}\Downloader
DefaultGroupName=Downloader
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir={#OutputDir}
OutputBaseFilename=Downloader-{#AppVersion}-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Downloader.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=yes
LicenseFile={#SourceDir}\_internal\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start Downloader when I sign in (runs in the tray)"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Downloader"; Filename: "{app}\Downloader.exe"
Name: "{autodesktop}\Downloader"; Filename: "{app}\Downloader.exe"; Tasks: desktopicon

[Registry]
; downloader:// protocol (used by the browser extension when the app isn't running)
Root: HKA; Subkey: "Software\Classes\downloader"; ValueType: string; ValueName: ""; ValueData: "URL:Downloader"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\downloader"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\downloader\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Downloader.exe"" ""%1"""
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Downloader"; ValueData: """{app}\Downloader.exe"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Downloader.exe"; Description: "Launch Downloader"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM Downloader.exe /F"; Flags: runhidden; RunOnceId: "KillApp"

; User data (library database, settings, downloaded components) lives in
; %LOCALAPPDATA%\Downloader and is intentionally kept on uninstall.
