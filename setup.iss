; Chatwisp - Inno Setup installer script
; To build: open this file in Inno Setup Compiler and click Compile, or run:
;   ISCC.exe setup.iss

#define MyAppName "Chatwisp"
#define MyAppVersion "3.0.0"
#define MyAppPublisher "Christmas Child"
#define MyAppURL "https://chatwisp.onrender.com/"
#define MyAppExeName "Chatwisp.exe"

[Setup]
AppId={{7A3C7E5B-1D8F-4A2B-9C0D-6E5F4A3B2C1D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer
OutputBaseFilename=ChatwispSetup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
DisableProgramGroupPage=yes
UninstallDisplayName={#MyAppName} {#MyAppVersion}
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
Source: "{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Register chatwisp:// URI protocol (HKLM so it works for all users)
Root: HKLM; Subkey: "Software\Classes\chatwisp"; ValueType: string; ValueName: ""; ValueData: "URL:Chatwisp Protocol"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\chatwisp"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\Classes\chatwisp\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Chatwisp"; Flags: nowait postinstall skipifsilent

[Code]
var
  AlreadyInstalled: Boolean;

function GetUninstallString: string;
begin
  Result := '';
  if not RegQueryStringValue(HKLM, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1', 'UninstallString', Result) then
    RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#SetupSetting("AppId")}_is1', 'UninstallString', Result);
end;

function IsUpgrade: Boolean;
begin
  Result := (GetUninstallString <> '');
end;

function InitializeSetup: Boolean;
var
  UninstallString: string;
  ResultCode: Integer;
begin
  if IsUpgrade then
  begin
    UninstallString := GetUninstallString;
    UninstallString := RemoveQuotes(UninstallString);
    if Exec(UninstallString, '/SILENT', '', SW_SHOW, ewWaitUntilTerminated, ResultCode) then
    begin
      // Previous version uninstalled successfully
    end;
  end;
  Result := True;
end;
