# MOGGCraft Launcher 4.0 (Windows)

Исходный код Windows-лаунчера, восстановленный из bytecode 3.1 с сохранением архитектуры:

- `launcher.py` — PySide6 UI, конвейер `ModSyncWorker`, проверка/скачивание модов,
  установка Minecraft 1.21.1 + NeoForge 21.1.267, запуск сервера.
- `MoggCraftLauncher.spec` — PyInstaller **onedir** (обновление заменяет папку).
- `MoggCraftLauncher.iss` — Inno Setup 7: выбор папки, Desktop/Start ярлыки,
  новая иконка, корректная повторная установка.
- `version_info.txt` — ProductVersion/FileVersion `4.0.0.0`.
- `build_release.py` — сборка ZIP + установщика + SHA-256 манифеста.
- `test_launcher.py` — 87 офлайн-проверок (в т.ч. все состояния кнопки «Играть»).

## Пользовательские данные

`%USERPROFILE%\.moggcraft_launcher\` — никнейм, RAM, моды, шейдеры, эмоуты, логи.
Они **outside** папки приложения, поэтому обновление и переустановка их не трогают.

## Релиз

1. `py -3.14 build_release.py`
2. Загрузить `MoggCraftLauncher-4.0.zip` и `MOGGCraft_Launcher_4.0_Setup.exe`
   в релиз `launcher` репозитория `Vimzon/moggcraft-mods`.
3. Скопировать `launcher_version.json` в корень репозитория и запушить.
4. Для будущих версий: workflow `.github/workflows/build-windows-launcher.yml`
   собирает нажатием `workflow_dispatch` или по тегу `launcher-*`.

Лаунчер сам проверяет `launcher_version.json`, показывает «ДОСТУПНО ОБНОВЛЕНИЕ»,
скачивает ZIP в фоне, сверяет SHA-256, делает backup, копирует новую версию
и перезапускается. При ошибке копирования выполняется rollback.
