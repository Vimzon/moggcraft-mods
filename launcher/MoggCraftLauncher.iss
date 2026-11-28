; MOGGCraft Launcher 4.0 — установщик Windows (Inno Setup 7)
; Собирается из onedir-папки PyInstaller dist\MoggCraftLauncher.

#define AppName "MOGGCraft Launcher"
#define AppVersion "4.0"

[Setup]
AppId={{FCB45B53-DB73-435A-AEAE-7AAC72A3495F}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Vimzon
AppPublisherURL=https://github.com/Vimzon/moggcraft-mods
AppSupportURL=https://t.me/mggcraft
AppUpdatesURL=https://github.com/Vimzon/moggcraft-mods/releases/tag/launcher
; Тот же AppId, что у 3.1: повторная установка/обновление перезаписывает файлы,
; не создавая вторую копию приложения.
DefaultDirName={localappdata}\MoggCraftLauncher
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=MOGGCraft_Launcher_4.0_Setup
SetupIconFile=assets\app_icon.ico
UninstallDisplayIcon={app}\MoggCraftLauncher.exe
Compression=lzma2/max
SolidCompression=yes
DisableDirPage=no
DisableProgramGroupPage=no
AllowNoIcons=no
AlwaysShowDirOnReadyPage=yes
WizardStyle=modern
VersionInfoDescription=MOGGCraft Launcher — лаунчер сборки MOGGCraft (Minecraft 1.21.1)
VersionInfoVersion=4.0.0
VersionInfoProductName={#AppName}
VersionInfoProductVersion=4.0.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DirExistsWarning=no

[Tasks]
Name: desktopicon; Description: "Создать ярлык на Рабочем столе"; GroupDescription: "Ярлыки:"
Name: startmenuicon; Description: "Добавить в меню «Пуск»"; GroupDescription: "Ярлыки:"

[Files]
; Пользовательские данные (~\.moggcraft_launcher:Nick, RAM, моды, миры) НЕ трогаются.
Source: "dist\MoggCraftLauncher\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\MoggCraftLauncher.exe"; IconFilename: "{app}\assets\app_icon.ico"; Tasks: desktopicon
Name: "{group}\{#AppName}"; Filename: "{app}\MoggCraftLauncher.exe"; IconFilename: "{app}\assets\app_icon.ico"; Tasks: startmenuicon
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"; IconFilename: "{app}\assets\app_icon.ico"; Tasks: startmenuicon

[Run]
Filename: "{app}\MoggCraftLauncher.exe"; Description: "Запустить {#AppName}"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=Установщик обновит или установит {#AppName} {#AppVersion}.%n%nНикнейм, настройки, моды и миры хранятся в отдельной папке пользователя и сохранятся при обновлении.
