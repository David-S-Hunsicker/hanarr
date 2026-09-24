#define MyAppName "Hanarr"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#define MyAppPublisher "Hanarr"
#define MyAppExeName "HanarrDesktop.exe"

[Setup]
AppId={{D4A3A6E5-7B52-4A6B-9B57-6C4F5E08D3B1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Hanarr
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=Hanarr-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\HanarrDesktop.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\HanarrBrowser.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Hanarr"; Filename: "{app}\HanarrDesktop.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\Hanarr (Browser)"; Filename: "{app}\HanarrBrowser.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Hanarr"; Filename: "{app}\HanarrDesktop.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a Desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[UninstallDelete]
; Deliberately do not remove {localappdata}\Hanarr. It contains user data.
