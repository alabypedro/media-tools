; Instalador do Universal Media Tools para Windows (Inno Setup 6).
; Nao rode direto: use  python build.py --installer
; (gera o dist\UniversalMediaTools\ e depois compila este script, passando a versao).
; Resultado: dist\UniversalMediaTools-Setup-<versao>.exe

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "Universal Media Tools"
#define AppExe "UniversalMediaTools.exe"

[Setup]
AppId={{C4E7A1D2-6B3F-4E8A-9D5C-2F1B7E0A4C93}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Pedro
SourceDir=..
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=assets\icon.ico
OutputDir=dist
OutputBaseFilename=UniversalMediaTools-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; O .exe e 64 bits (Python + Qt 6) e exige Windows 10/11.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; O app nao precisa de Administrador: por padrao instala so para o usuario atual
; (%LOCALAPPDATA%\Programs); o assistente oferece instalar para todos os usuarios.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes
ChangesEnvironment=yes

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ptbr.AddToPath=Adicionar o comando "umd" ao PATH (usar "umd" e "umd convert" em qualquer terminal)
en.AddToPath=Add the "umd" command to PATH (use "umd" and "umd convert" from any terminal)
ptbr.CommandLine=Linha de comando:
en.CommandLine=Command line:

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "addtopath"; Description: "{cm:AddToPath}"; GroupDescription: "{cm:CommandLine}"; Flags: unchecked

[Files]
Source: "dist\UniversalMediaTools\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; Atualizacao pelo proprio programa (/SILENT /RELAUNCH=1): reabre o programa no fim,
; como o usuario que o estava usando (sem herdar a elevacao do instalador).
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: RelaunchRequested

; Historico, biblioteca, configuracoes e engines atualizadas ficam em %LOCALAPPDATA% (fora da
; pasta do programa) e sao mantidos na desinstalacao, assim como os arquivos baixados.

[Code]
const
  UserEnvKey = 'Environment';
  SystemEnvKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';
  SYNCHRONIZE = $00100000;

function OpenProcess(dwDesiredAccess: DWORD; bInheritHandle: BOOL; dwProcessId: DWORD): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(hHandle: THandle; dwMilliseconds: DWORD): DWORD;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

{ Atualizacao pelo proprio programa: ele passa /WAITPID=<pid> e fecha em seguida.
  Espera (ate 60 s) esse processo terminar antes de mexer nos arquivos. }
function InitializeSetup: Boolean;
var
  Pid: Integer;
  Handle: THandle;
begin
  Pid := StrToIntDef(ExpandConstant('{param:WAITPID|0}'), 0);
  if Pid > 0 then
  begin
    Handle := OpenProcess(SYNCHRONIZE, False, Pid);
    if Handle <> 0 then
    begin
      WaitForSingleObject(Handle, 60000);
      CloseHandle(Handle);
    end;
  end;
  Result := True;
end;

function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function EnvRoot: Integer;
begin
  if IsAdminInstallMode then Result := HKEY_LOCAL_MACHINE else Result := HKEY_CURRENT_USER;
end;

function EnvKey: String;
begin
  if IsAdminInstallMode then Result := SystemEnvKey else Result := UserEnvKey;
end;

procedure AddToPath(Dir: String);
var
  Paths: String;
begin
  if not RegQueryStringValue(EnvRoot, EnvKey, 'Path', Paths) then Paths := '';
  if Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') > 0 then Exit;
  if (Paths <> '') and (Paths[Length(Paths)] <> ';') then Paths := Paths + ';';
  RegWriteExpandStringValue(EnvRoot, EnvKey, 'Path', Paths + Dir);
end;

procedure RemoveFromPath(Dir: String);
var
  Paths: String;
  P: Integer;
begin
  if not RegQueryStringValue(EnvRoot, EnvKey, 'Path', Paths) then Exit;
  P := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';');
  if P = 0 then Exit;
  if P > 1 then
    Delete(Paths, P - 1, Length(Dir) + 1)   { tira o ";" anterior e a pasta }
  else
    Delete(Paths, P, Length(Dir) + 1);      { primeira entrada: tira a pasta e o ";" seguinte }
  RegWriteExpandStringValue(EnvRoot, EnvKey, 'Path', Paths);
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
