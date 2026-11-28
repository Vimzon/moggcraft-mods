"""
build_release.py — воспроизводимая сборка релиза MOGGCraft Launcher 4.0.

Что делает:
  1. PyInstaller onedir -> dist/MoggCraftLauncher (совместимо с апдейтером).
  2. ZIP -> MoggCraftLauncher-4.0.zip (тот же формат, что скачивает лаунчер).
  3. Inno Setup -> Output/MOGGCraft_Launcher_4.0_Setup.exe.
  4. SHA-256 обоих ассетов -> launcher_version.json.

Запуск:
  py -3.14 build_release.py            # полная сборка
  py -3.14 build_release.py --skip-installer
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Имена файлов остаются 4.0 (ссылки и установщик уже у игроков),
# а версия в manifest увеличена до 4.0.1, чтобы 4.0 получил хотфикс через автообновление.
VERSION = "4.0"
RELEASE_VERSION = "4.0.1"
MC_VERSION = "1.21.1"
NEOFORGE_VERSION = "21.1.267"

BUNDLE = ROOT / "dist" / "MoggCraftLauncher"
ZIP_PATH = ROOT / f"MoggCraftLauncher-{VERSION}.zip"
ISS_PATH = ROOT / "MoggCraftLauncher.iss"
SETUP_PATH = ROOT / "Output" / f"MOGGCraft_Launcher_{VERSION}_Setup.exe"
MANIFEST_PATH = ROOT / "launcher_version.json"

RELEASE_BASE = "https://github.com/Vimzon/moggcraft-mods/releases/download/launcher/"
CHANGELOG = [
    "Хотфикс 4.0.1: исправлен запуск игры (порт сервера передавался числом вместо строки).",
    "Новый дизайн Vice City: закат, пальмы, стеклянные панели, бирюзовые акценты.",
    "Исправлена кнопка «Играть»: больше не пропадает при наведении и нажатии.",
    "Новая иконка MOGG CRAFT в лаунчере, установщике и ярлыках.",
    "Автообновление с SHA-256, backup/rollback и прогрессом без зависания UI.",
    "Сохранены никнейм, RAM, моды, загрузка сборки и запуск Minecraft 1.21.1.",
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    print(">", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run([str(x) for x in cmd], cwd=str(cwd or ROOT), check=True)


def make_zip() -> None:
    if not BUNDLE.is_dir():
        raise SystemExit(f"Нет папки сборки: {BUNDLE}")
    # PyInstaller кладёт ассеты в _internal, но установщик/ярлыки ссылаются
    # на {app}\assets\app_icon.ico — дублируем иконку в корень onedir-папки.
    root_assets = BUNDLE / "assets"
    root_assets.mkdir(exist_ok=True)
    for name in ("app_icon.ico", "app_icon.png"):
        src = ROOT / "assets" / name
        if src.is_file():
            (root_assets / name).write_bytes(src.read_bytes())
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Внутри архива сохраняем папку MoggCraftLauncher — так же, как в 3.1.
        for path in sorted(BUNDLE.rglob("*")):
            if path.is_file():
                zf.write(path, Path("MoggCraftLauncher") / path.relative_to(BUNDLE))
    print(f"ZIP: {ZIP_PATH} ({ZIP_PATH.stat().st_size} bytes)", flush=True)


def find_iscc() -> Path:
    env = os.environ.get("ISCC")
    if env and Path(env).is_file():
        return Path(env)
    candidates = [
        ROOT / "tools" / "ISCC.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "InnoSetup7" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 7" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 7" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise SystemExit("ISCC.exe не найден. Укажи путь через переменную ISCC.")


def make_installer() -> None:
    iscc = find_iscc()
    run([iscc, "/Qp", str(ISS_PATH)], cwd=ROOT)
    if not SETUP_PATH.is_file():
        raise SystemExit(f"Установщик не создан: {SETUP_PATH}")
    print(f"SETUP: {SETUP_PATH} ({SETUP_PATH.stat().st_size} bytes)", flush=True)


def write_manifest(zip_hash: str, setup_hash: str) -> None:
    manifest = {
        "version": RELEASE_VERSION,
        "minecraft_version": MC_VERSION,
        "neoforge_version": NEOFORGE_VERSION,
        "url": RELEASE_BASE + ZIP_PATH.name,
        "sha256": zip_hash,
        "installer_url": RELEASE_BASE + SETUP_PATH.name,
        "installer_sha256": setup_hash,
        "changelog": CHANGELOG,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"MANIFEST: {MANIFEST_PATH}", flush=True)
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-pyinstaller", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    args = parser.parse_args()

    if not args.skip_pyinstaller:
        run([sys.executable, "-m", "PyInstaller", "--noconfirm", "MoggCraftLauncher.spec"])
    make_zip()
    zip_hash = sha256(ZIP_PATH)
    print(f"ZIP SHA256: {zip_hash}", flush=True)

    setup_hash = ""
    if not args.skip_installer:
        make_installer()
        setup_hash = sha256(SETUP_PATH)
        print(f"SETUP SHA256: {setup_hash}", flush=True)

    write_manifest(zip_hash, setup_hash)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
