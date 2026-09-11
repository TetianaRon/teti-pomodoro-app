; Inno Setup script for Pomodoro Guardian.
;
; Builds a real install wizard and a registered Add/Remove Programs entry
; (with uninstaller) around the PyInstaller build in dist/. Compile with:
;   ISCC.exe pomodoro-guardian.iss
; dist\pomodoro-guardian.exe must already exist — build it first via
; `pyinstaller --name pomodoro-guardian --onefile --windowed
;  --icon assets/pomodoro.ico --collect-data tzdata --paths . entrypoint.py`
;
; Installs per-user (no admin prompt, no UAC) since this runs on a single
; personal machine — same reasoning as the app's own venv/shortcut setup
; needing nothing elevated. AppMutex matches runtime.MUTEX_NAME, so Setup
; itself detects a running copy and asks to close it rather than failing
; to overwrite a locked exe.

#define MyAppName "Pomodoro Guardian"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Tetiana Ron"
#define MyAppExeName "pomodoro-guardian.exe"

[Setup]
AppId={{6E54203D-C5B8-4890-B361-A283AD4D2DA8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=PomodoroGuardian-Setup-{#MyAppVersion}
SetupIconFile=assets\pomodoro.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
AppMutex=PomodoroGuardian_SingleInstance

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Same path the app's own runtime.ensure_start_menu_shortcut() would use
; (no subfolder) — landing here first just means that code sees the
; shortcut already exists on first run and leaves it alone.
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The exe writes nothing next to itself, but a belt-and-suspenders clean
; of the install folder in case a future version ever does.
Type: filesandordirs; Name: "{app}"
