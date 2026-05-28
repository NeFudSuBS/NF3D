; NF3D Inno Setup installer script
; Requires Inno Setup 6+ from https://jrsoftware.org/isinfo.php
; Build: open this file in Inno Setup and click Compile, or run:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\NF3D_setup.iss
; (run from the 'new claude' parent directory)

#define MyAppName      "NF3D"
#define MyAppVersion   "1.5"
#define MyAppPublisher "NF3D"
#define MyAppURL       ""
#define MyAppExeName   "NF3D.exe"
#define MyAppId        "{{D725AF38-F42B-4488-97A7-D5F61C0C101C}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; Install to Program Files — user can override during install
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output to C:\NF3D\nf3d\
OutputDir=..\..\nf3d
OutputBaseFilename=NF3D_Setup_{#MyAppVersion}
SetupIconFile=..\nf3d_icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Allow user-level install without UAC (elevated auto-granted for Program Files)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
; Uninstall settings
UninstallDisplayIcon={app}\{#MyAppExeName},0
UninstallDisplayName={#MyAppName}
; Minimum Windows version: 10
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";   Description: "Create a &desktop shortcut";    GroupDescription: "Additional icons:"; Flags: unchecked
Name: "startmenuicon"; Description: "Create a &Start Menu shortcut"; GroupDescription: "Additional icons:"

[Files]
; Main application — PyInstaller one-dir bundle
Source: "..\dist\NF3D\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start menu
Name: "{group}\{#MyAppName}";            Filename: "{app}\{#MyAppExeName}"; Tasks: startmenuicon
Name: "{group}\Uninstall {#MyAppName}";  Filename: "{uninstallexe}";        Tasks: startmenuicon
; Desktop
Name: "{autodesktop}\{#MyAppName}";      Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; First-run wizard: checks and installs missing tools, asks for workspace folder
; Runs automatically after install; user can click Skip checks if in a hurry
Filename: "{app}\{#MyAppExeName}"; Parameters: "--setup"; \
  Flags: waituntilterminated skipifsilent; \
  StatusMsg: "Running first-time setup wizard..."
; Offer to launch the app on the Finish page
Filename: "{app}\{#MyAppExeName}"; \
  Description: "Launch {#MyAppName} now"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove the installed application files
Type: filesandordirs; Name: "{app}"

[Code]
// ── Optional user-data cleanup on uninstall ──────────────────────────────────
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ConfigFile, SetupFlag: String;
  Answer: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    ConfigFile := ExpandConstant('{userhome}\nf3d_config.json');
    SetupFlag  := ExpandConstant('{userhome}\.nf3d_setup_ok');
    if FileExists(ConfigFile) or FileExists(SetupFlag) then
    begin
      Answer := MsgBox(
        'Would you like to remove your NF3D user settings?' + #13#10 + #13#10 +
        '  • nf3d_config.json  (tool paths, style settings)' + #13#10 +
        '  • .nf3d_setup_ok   (first-run completion flag)' + #13#10 + #13#10 +
        'Your workspace folder and any subtitle/depth files you created ' +
        'will NOT be deleted regardless of this choice.',
        mbConfirmation, MB_YESNO);
      if Answer = IDYES then
      begin
        DeleteFile(ConfigFile);
        DeleteFile(SetupFlag);
      end;
    end;
  end;
end;

// ── Finish page text ─────────────────────────────────────────────────────────
procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedLabel.Caption :=
      'NF3D has been installed successfully.' + #13#10 + #13#10 +
      'The first-run setup wizard will now open to:' + #13#10 +
      '  • Check for required external tools (ffmpeg, MKVToolNix, Subtitle Edit)' + #13#10 +
      '  • Offer to install any missing tools automatically via winget' + #13#10 +
      '  • Let you choose where NF3D stores your workspace files' + #13#10 + #13#10 +
      'All three tools are free and open source.' + #13#10 +
      'The wizard can install them in one click if winget is available.';
  end;
end;
