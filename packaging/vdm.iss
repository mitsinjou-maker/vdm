; Installateur Windows de VDM (Inno Setup 6).
; Construit par tools/build_installer.py, qui prépare d'abord build\pyinstaller\dist\VDM
; et build\vdm.ico, puis lance :  ISCC /DAppVersion=x.y.z packaging\vdm.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "VDM"
#define AppExe "vdm-gui.exe"

[Setup]
AppId={{8C4E0B2A-5F7D-4E61-9B3A-2D6C1E9F0A47}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=mitsinjou-maker
AppPublisherURL=https://github.com/mitsinjou-maker/vdm
AppSupportURL=https://github.com/mitsinjou-maker/vdm/issues
AppUpdatesURL=https://github.com/mitsinjou-maker/vdm/releases
; sans droits administrateur par défaut (dossier de l'utilisateur),
; « pour tous les utilisateurs » reste possible via la boîte de dialogue
DefaultDirName={autopf}\VDM
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=VDM-Setup-{#AppVersion}
SetupIconFile=..\build\vdm.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; VDM ouvert : l'installateur (ou le désinstalleur) propose de le fermer
AppMutex=VDM_Instance_Mutex
CloseApplications=yes
ChangesEnvironment=yes

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
fr.Options=Options :
en.Options=Options:
fr.StartupTask=Lancer VDM au démarrage de Windows (dans la zone de notification)
en.StartupTask=Start VDM with Windows (in the notification area)
fr.PathTask=Ajouter la commande « vdm » au terminal (PATH)
en.PathTask=Add the "vdm" command to the terminal (PATH)
fr.ExtensionShortcut=Extension navigateur (dossier à charger dans Chrome ou Firefox)
en.ExtensionShortcut=Browser extension (folder to load in Chrome or Firefox)

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "{cm:StartupTask}"; GroupDescription: "{cm:Options}"
Name: "addtopath"; Description: "{cm:PathTask}"; GroupDescription: "{cm:Options}"

[Files]
Source: "..\build\pyinstaller\dist\VDM\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\extension\*"; DestDir: "{app}\extension"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"
Source: "..\README.md"; DestDir: "{app}"

[Icons]
Name: "{autoprograms}\VDM"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\VDM — {cm:ExtensionShortcut}"; Filename: "{app}\extension"
Name: "{autodesktop}\VDM"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
Name: "{userstartup}\VDM"; Filename: "{app}\{#AppExe}"; Parameters: "--tray"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,VDM}"; Flags: nowait postinstall skipifsilent

; Les réglages et la file d'attente (%APPDATA%\vdm) sont conservés à la désinstallation.

[Code]
const
  EnvKey = 'Environment';

function PathHas(Paths, Dir: string): Boolean;
begin
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') > 0;
end;

procedure AddToPath(Dir: string);
var
  Paths: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvKey, 'Path', Paths) then
    Paths := '';
  if PathHas(Paths, Dir) then
    exit;
  if (Paths <> '') and (Paths[Length(Paths)] <> ';') then
    Paths := Paths + ';';
  RegWriteExpandStringValue(HKEY_CURRENT_USER, EnvKey, 'Path', Paths + Dir);
end;

procedure RemoveFromPath(Dir: string);
var
  Paths: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, EnvKey, 'Path', Paths) then
    exit;
  Paths := ';' + Paths + ';';
  P := Pos(';' + Uppercase(Dir) + ';', Uppercase(Paths));
  if P = 0 then
    exit;
  Delete(Paths, P, Length(Dir) + 1);          { retire « ;dossier » }
  Paths := Copy(Paths, 2, Length(Paths) - 2);  { retire les « ; » ajoutés autour }
  RegWriteExpandStringValue(HKEY_CURRENT_USER, EnvKey, 'Path', Paths);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    AddToPath(ExpandConstant('{app}'));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveFromPath(ExpandConstant('{app}'));
end;
