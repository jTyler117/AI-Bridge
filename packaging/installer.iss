; The AI Bridge installer (Inno Setup 6.2+).   Build with:  packaging\build_windows.bat
; - Installs just for you (no admin prompt) into %LOCALAPPDATA%\Programs\AI Bridge.
; - Your chats, keys and settings live in %LOCALAPPDATA%\AI Bridge, which updates never touch.
; - Optionally installs the AI agents (Claude Code, Codex, Ollama) from their makers' official installers.
; - Uninstalling keeps your data unless you tick the box to delete it.

#ifndef AppVersion
  #define AppVersion "0.9.0"
#endif

[Setup]
AppId={{6E2B7B7C-3B5A-4C1E-9F7A-AB12CDEF0425}
AppName=AI Bridge
AppVersion={#AppVersion}
AppVerName=AI Bridge {#AppVersion}
AppPublisher=AI Bridge
DefaultDirName={localappdata}\Programs\AI Bridge
DefaultGroupName=AI Bridge
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=AI-Bridge-Setup-{#AppVersion}
SetupIconFile=..\ui\icon.ico
UninstallDisplayIcon={app}\AIBridge.exe
UninstallDisplayName=AI Bridge
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; GroupDescription: "Shortcuts:"
Name: "claudecode"; Description: "Claude Code  (by Anthropic, needs a paid Claude plan)"; GroupDescription: "Install AI agents for you (downloaded from their makers, you sign in afterwards):"; Check: not AgentInstalled('claude')
Name: "codex"; Description: "Codex  (by OpenAI, for ChatGPT, needs a paid ChatGPT plan)"; GroupDescription: "Install AI agents for you (downloaded from their makers, you sign in afterwards):"; Check: not AgentInstalled('codex')
Name: "ollama"; Description: "Ollama  (free AIs that run on your PC; optional)"; GroupDescription: "Install AI agents for you (downloaded from their makers, you sign in afterwards):"; Flags: unchecked; Check: not AgentInstalled('ollama')

[Files]
Source: "..\dist\AI Bridge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\AI Bridge"; Filename: "{app}\AIBridge.exe"
Name: "{autodesktop}\AI Bridge"; Filename: "{app}\AIBridge.exe"; Tasks: desktopicon

[Run]
; The agents: their own official one-line installers, shown in a window so you can see what's happening.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""irm https://claude.ai/install.ps1 | iex"""; StatusMsg: "Installing Claude Code (from Anthropic)..."; Tasks: claudecode; Flags: waituntilterminated
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""irm https://chatgpt.com/codex/install.ps1 | iex"""; StatusMsg: "Installing Codex (from OpenAI)..."; Tasks: codex; Flags: waituntilterminated
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""$f = Join-Path $env:TEMP 'OllamaSetup.exe'; irm https://ollama.com/download/OllamaSetup.exe -OutFile $f; Start-Process $f -ArgumentList '/SILENT' -Wait"""; StatusMsg: "Installing Ollama..."; Tasks: ollama; Flags: waituntilterminated
Filename: "{app}\AIBridge.exe"; Description: "Open AI Bridge"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebView2Key = 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2KeyUser = 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';

var
  DownloadPage: TDownloadWizardPage;
  NeedWebView2: Boolean;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

{ Is an agent already there? Then its checkbox is hidden. }
function AgentInstalled(Name: String): Boolean;
var
  Home: String;
begin
  Home := GetEnv('USERPROFILE');
  if Name = 'claude' then
    Result := FileExists(Home + '\.local\bin\claude.exe') or FileExists(ExpandConstant('{userappdata}') + '\npm\claude.cmd')
  else if Name = 'codex' then
    Result := FileExists(Home + '\.local\bin\codex.exe') or FileExists(ExpandConstant('{userappdata}') + '\npm\codex.cmd')
      or FileExists(ExpandConstant('{localappdata}') + '\Programs\OpenAI\Codex\bin\codex.exe')
  else if Name = 'ollama' then
    Result := FileExists(ExpandConstant('{localappdata}') + '\Programs\Ollama\ollama.exe')
  else
    Result := False;
end;

{ WebView2 draws the app's window. Windows 10/11 almost always have it; if not, fetch Microsoft's small installer. }
function HasWebView2: Boolean;
var
  V: String;
begin
  Result := (RegQueryStringValue(HKLM, WebView2Key, 'pv', V) and (V <> '') and (V <> '0.0.0.0'))
    or (RegQueryStringValue(HKCU, WebView2KeyUser, 'pv', V) and (V <> '') and (V <> '0.0.0.0'));
end;

procedure InitializeWizard;
begin
  NeedWebView2 := not HasWebView2;
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), 'Getting Microsoft Edge WebView2 (it draws the app window)...', @OnDownloadProgress);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Code: Integer;
begin
  Result := True;
  if (CurPageID = wpReady) and NeedWebView2 then begin
    DownloadPage.Clear;
    DownloadPage.Add(WebView2Url, 'MicrosoftEdgeWebview2Setup.exe', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_SHOW, ewWaitUntilTerminated, Code);
      except
        MsgBox('Couldn''t get WebView2 (' + GetExceptionMessage + '). AI Bridge will open in your browser instead until it''s installed.', mbInformation, MB_OK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

{ Uninstall: stop "start with Windows", and only delete your data if you say so. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'AI Bridge');
  if CurUninstallStep = usPostUninstall then begin
    DataDir := ExpandConstant('{localappdata}\AI Bridge');
    if DirExists(DataDir) then
      if MsgBox('Also delete your AI Bridge data (chats, memory, keys and settings) in' + #13#10 + DataDir + ' ?' + #13#10#13#10 +
                'Choose No to keep it, so reinstalling picks up where you left off.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
