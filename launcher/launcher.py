"""
MOGGCraft Launcher 4.0 — Windows-лаунчер (PySide6).

История/архитектура сохранена от 3.1:
  * тот же PyInstaller onedir-формат (MoggCraftLauncher.exe + _internal);
  * тот же каталог пользовательских данных ~/.moggcraft_launcher;
  * тот же манифест модов и тот же канал обновления launcher_version.json;
  * тот же ModSyncWorker -> Minecraft + NeoForge + моды + запуск;
  * тот же PowerShell-апдейтер, но с backup/rollback и SHA-256.

Новое в 4.0:
  * полностью новая визуальная система "Vice City sunset";
  * исправлена причина пропадания кнопки «Играть» при hover;
  * новая иконка во всех местах;
  * проверка update manifest, HTTPS/Host allowlist и SHA-256;
  * красочный диалог обновления, адаптивный UI, лёгкий static background.

Сборка:
  py -3.14 -m PyInstaller MoggCraftLauncher.spec
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote as _url_quote, urlparse

import requests
import minecraft_launcher_lib as mll
from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# ============================== КОНСТАНТЫ ==================================

APP_TITLE = "MOGGCraft Launcher"
APP_VERSION = "4.0"
MINECRAFT_VERSION = "1.21.1"
NEOFORGE_VERSION = "21.1.267"
USER_AGENT = f"MoggCraftLauncher/{APP_VERSION} (MoggCraft)"

MANIFEST_URL = (
    "https://raw.githubusercontent.com/Vimzon/moggcraft-mods/main/mods_manifest.json"
)
LAUNCHER_VERSION_URL = (
    "https://raw.githubusercontent.com/Vimzon/moggcraft-mods/main/launcher_version.json"
)
RELEASE_BASE = "https://github.com/Vimzon/moggcraft-mods/releases/download/launcher/"

SERVER_IP = "x7.joinserver.xyz"
SERVER_PORT = 25931

DEFAULT_RAM_MB = 4096
RAM_OPTIONS = (
    ("Авто (половина ОЗУ)", 0),
    ("2048 МБ", 2048),
    ("3072 МБ", 3072),
    ("4096 МБ", 4096),
    ("6144 МБ", 6144),
    ("8192 МБ", 8192),
    ("12288 МБ", 12288),
)

# Палитра из ТЗ. Все кнопки рисуются с альфой 255 — они не могут «исчезнуть».
C = {
    "pink": "#FF4F9A",
    "neon_pink": "#FF70BD",
    "coral": "#FF776E",
    "sunset": "#FFAD76",
    "peach": "#FFD0A4",
    "teal": "#55E2D0",
    "lavender": "#A991FF",
    "plum": "#21152D",
    "night": "#130F20",
    "fg": "#FFF3F5",
    "dim": "#D6BFD8",
    "ok": "#55E2D0",
    "err": "#FF776E",
    "panel": "#21152DCC",
    "panel_solid": "#21152D",
    "panel_light": "#3A2448E6",
    "border": "#FF70BD55",
    "accent": "#FF4F9A",
    "accent_hover": "#FFAD76",
    "accent_dark": "#B33170",
    "bg": "#130F20",
    "progress_bg": "#3A2448",
    "progress_fg": "#FF70BD",
}

ALLOWED_UPDATE_HOSTS = {
    "raw.githubusercontent.com",
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}

# ============================== ПУТИ И ЛОГИ =================================

APP_DIR = os.path.join(os.path.expanduser("~"), ".moggcraft_launcher")
MC_DIR = os.path.join(APP_DIR, "minecraft")
CONFIG_PATH = os.path.join(APP_DIR, "launcher_config.json")
MODS_DIR = os.path.join(MC_DIR, "mods")
SHADERS_DIR = os.path.join(MC_DIR, "shaderpacks")
EMOTES_DIR = os.path.join(MC_DIR, "emotes")
LOG_PATH = os.path.join(APP_DIR, "launcher.log")

for _d in (APP_DIR, MC_DIR, MODS_DIR, SHADERS_DIR, EMOTES_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except OSError:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        RotatingFileHandler(
            LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("launcher")


def _hook_excepthook(exc_type, exc_value, exc_tb):
    log.critical("Непойманное исключение:\n%s", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))


def _hook_thread_excepthook(args):
    log.critical(
        "Непойманное исключение в потоке %r:\n%s",
        args.thread.name,
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
    )


sys.excepthook = _hook_excepthook
threading.excepthook = _hook_thread_excepthook


# ============================== УТИЛИТЫ =====================================


def resource_path(*parts: str) -> str:
    """Путь к ресурсу и в dev-режиме, и внутри PyInstaller _MEIPASS."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def get_total_ram_mb() -> int:
    if sys.platform == "win32":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        try:
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys / (1024 * 1024))
        except Exception:
            pass
    return 8192


def auto_ram_mb() -> int:
    total = get_total_ram_mb()
    return max(2048, min(int(total * 0.5), 16384))


def parse_ram_value(saved: object | None) -> int:
    """
    Совместимость с конфигом 3.x: там RAM мог сохраняться и как число,
    и как подпись комбобокса («Авто (половина ОЗУ)», «6144 МБ»).
    """
    if isinstance(saved, bool):
        return 0
    if isinstance(saved, int):
        return saved
    try:
        return int(saved)
    except (TypeError, ValueError):
        digits = "".join(c for c in str(saved or "") if c.isdigit())
        return int(digits) if digits else 0


def resolve_ram_mb(saved: object | None) -> int:
    value = parse_ram_value(saved)
    return auto_ram_mb() if value <= 0 else max(512, min(value, 65536))


def open_folder(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        log.warning("Не удалось прочитать конфиг — использую значения по умолчанию")
        return {}


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def offline_uuid(username: str) -> str:
    # bytearray обязателен: digest() возвращает неизменяемые bytes.
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + username).encode("utf-8")).digest())
    digest[6] &= 0x0F
    digest[6] |= 0x30
    digest[8] &= 0x3F
    digest[8] |= 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def is_valid_nickname(name: str) -> bool:
    return 3 <= len(name) <= 16 and all(c.isalnum() or c == "_" for c in name)


def sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def mod_ok(local_path: str, info: dict) -> bool:
    try:
        if not os.path.isfile(local_path):
            return False
        if os.path.getsize(local_path) != int(info.get("size", -1)):
            return False
        expected = str(info.get("sha256", "")).lower()
        return bool(expected) and sha256_of_file(local_path).lower() == expected
    except OSError:
        return False


def bundled_emotes_dir() -> str | None:
    base = getattr(sys, "_MEIPASS", None)
    candidates = []
    if base:
        candidates.append(os.path.join(base, "emotes"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "emotes"))
    candidates.append(os.path.join(os.path.expanduser("~"), "Desktop", "Эмоуты"))
    for c in candidates:
        if not os.path.isdir(c):
            continue
        for root, _dirs, files in os.walk(c):
            if any(f.lower().endswith(".emotecraft") for f in files):
                return c
    return None


def sync_emotes() -> None:
    src = bundled_emotes_dir()
    if not src or not os.path.isdir(src):
        return
    try:
        copied = 0
        os.makedirs(EMOTES_DIR, exist_ok=True)
        for fname in os.listdir(src):
            if not fname.lower().endswith(".emotecraft"):
                continue
            dst = os.path.join(EMOTES_DIR, fname)
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(src, fname), dst)
                copied += 1
        if copied:
            log.info("Скопировано эмоутов: %s", copied)
    except Exception:
        log.exception("Не удалось скопировать эмоуты")


def compare_versions(current: str, latest: str) -> bool:
    def parts(v: str) -> list[int]:
        out = []
        for chunk in str(v).lstrip("vV").split("."):
            digits = "".join(c for c in chunk if c.isdigit())
            out.append(int(digits) if digits else 0)
        return out

    return parts(latest) > parts(current)


def safe_https_url(url: str, *, allow_github: bool = True) -> bool:
    try:
        p = urlparse(str(url))
        if p.scheme != "https" or not p.hostname:
            return False
        host = p.hostname.lower()
        if host in ALLOWED_UPDATE_HOSTS:
            return True
        return allow_github and host == "github.com"
    except Exception:
        return False


def parse_update_manifest(data: dict) -> dict:
    """Проверяем источник обновления до того, как что-то скачивать/запускать."""
    if not isinstance(data, dict):
        raise ValueError("Некорректный manifest обновления")
    version = str(data.get("version", "")).strip()
    url = str(data.get("url", "")).strip()
    if not version or not url:
        raise ValueError("В manifest нет version/url")
    if not safe_https_url(url):
        raise ValueError("Недоверенный URL обновления")
    sha = str(data.get("sha256", "")).strip().lower()
    if sha and not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("Некорректный SHA-256 в manifest")
    notes = data.get("changelog") or data.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    if not isinstance(notes, list):
        notes = []
    return {
        "version": version,
        "url": url,
        "sha256": sha,
        "changelog": [str(n) for n in notes][:12],
        "installer_url": str(data.get("installer_url", "")).strip(),
        "installer_sha256": str(data.get("installer_sha256", "")).strip().lower(),
    }


def _ps_quote(s: str) -> str:
    return str(s).replace("'", "''")


def apply_update_payload(payload_dir: str, tmp_root: str) -> None:
    """
    Безопасная замена работающего лаунчера.

    Создаём backup install-dir, копируем новую версию, перезапускаем.
    При любой ошибке apдейтер восстанавливает backup и запускает старую версию.
    """
    if not getattr(sys, "frozen", False):
        log.warning("apply_update_payload вызван вне frozen-режима — пропускаю")
        return

    exe_path = os.path.realpath(sys.executable)
    launcher_dir = os.path.dirname(exe_path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(
        os.path.expanduser("~"),
        "AppData",
        "Local",
        f"MoggCraftLauncher_backup_{stamp}",
    )
    updater = os.path.join(tempfile.gettempdir(), "moggcraft_updater.ps1")
    log_target = os.path.join(tempfile.gettempdir(), "moggcraft_updater.log")
    marker = os.path.join(tempfile.gettempdir(), "moggcraft_update_ok.txt")

    src = _ps_quote(payload_dir)
    dst = _ps_quote(launcher_dir)
    bak = _ps_quote(backup_dir)
    logp = _ps_quote(log_target)
    exe_name = os.path.basename(exe_path)
    exe_rel = _ps_quote(exe_name)
    tmpq = _ps_quote(tmp_root)
    markq = _ps_quote(marker)

    script = f"""$ErrorActionPreference = 'Stop'
$logPath = '{logp}'
function Log($msg) {{
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $logPath -Value "[$ts] $msg" -ErrorAction SilentlyContinue
}}
Log 'Updater started. Waiting for launcher to exit...'
Start-Sleep -Seconds 2

$src = '{src}'
$dst = '{dst}'
$backup = '{bak}'
$exeName = '{exe_rel}'
$ok = $false

try {{
    if (Test-Path $backup) {{ Remove-Item -Path $backup -Recurse -Force -ErrorAction SilentlyContinue }}
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    Get-ChildItem -LiteralPath $dst -Force | Copy-Item -Destination $backup -Recurse -Force
    Log "Backup created: $backup"

    Get-ChildItem -LiteralPath $src -Force | Copy-Item -Destination $dst -Recurse -Force
    Log 'New version copied'

    $newExe = Join-Path $dst $exeName
    if (-not (Test-Path $newExe)) {{ throw "Updated executable missing: $newExe" }}
    Start-Process -FilePath $newExe -WorkingDirectory $dst
    Set-Content -Path '{markq}' -Value $backup -Encoding UTF8
    $ok = $true
    Log 'Restarted launcher'
}} catch {{
    Log "ERROR: $($_.Exception.Message)"
    try {{
        if (Test-Path $backup) {{
            Get-ChildItem -LiteralPath $dst -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            Get-ChildItem -LiteralPath $backup -Force | Copy-Item -Destination $dst -Recurse -Force
            Log 'Rollback completed'
            Start-Process -FilePath (Join-Path $dst $exeName) -WorkingDirectory $dst
            Log 'Old version restarted'
        }}
    }} catch {{ Log "ROLLBACK FAILED: $($_.Exception.Message)" }}
}} finally {{
    # Backup living until the new launcher starts: _check_and_repair() removes it.
    if (-not $ok) {{ Log 'Current version kept; backup is available for manual rollback' }}
    if (Test-Path '{tmpq}') {{
        Remove-Item -Path '{tmpq}' -Recurse -Force -ErrorAction SilentlyContinue
    }}
    Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
}}
"""

    try:
        with open(updater, "w", encoding="utf-8") as f:
            f.write(script)
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                updater,
            ],
            creationflags=flags,
        )
    except Exception:
        log.exception("Не смог запустить апдейтер")
        raise RuntimeError("Не удалось запустить обновление. Текущая версия не изменена.")


def show_message_box(parent, title: str, text: str, question: bool = False, critical: bool = False) -> bool:
    """Диалог с гарантированно непрозрачным фоном и контрастным текстом."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Question if question else (QMessageBox.Critical if critical else QMessageBox.Information))
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No if question else QMessageBox.Ok)
    box.setWindowOpacity(1.0)
    box.setGraphicsEffect(None)
    box.setAttribute(Qt.WA_StyledBackground, True)
    box.setAutoFillBackground(True)
    box.setStyleSheet(
        f"""
        QMessageBox {{ background-color: {C['panel_solid']}; color: {C['fg']}; }}
        QMessageBox QLabel {{ color: {C['fg']}; background: transparent; font-size: 10pt; }}
        QMessageBox QPushButton {{
            background-color: {C['accent']}; color: {C['fg']}; border: none;
            border-radius: 6px; padding: 6px 20px; font-size: 10pt; min-width: 70px;
        }}
        QMessageBox QPushButton:hover {{ background-color: {C['accent_hover']}; }}
        """
    )
    return box.exec() == QMessageBox.Yes


# ============================== СТИЛИ И ФОН =================================

STYLESHEET = f"""
QMainWindow {{ background: transparent; }}
QWidget {{ color: {C['fg']}; font-family: "Segoe UI"; font-size: 10pt; }}
QLineEdit {{
    background-color: {C['panel_light']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    padding: 11px 14px;
    font-size: 12pt;
    color: {C['fg']};
    selection-background-color: {C['accent']};
}}
QLineEdit:focus {{ border-color: {C['neon_pink']}; }}
QComboBox {{
    background-color: {C['panel_light']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 10pt;
    color: {C['fg']};
    min-width: 190px;
}}
QComboBox QAbstractItemView {{
    background-color: {C['panel_solid']};
    color: {C['fg']};
    selection-background-color: {C['accent']};
    border: 1px solid {C['border']};
}}
QPushButton {{
    background-color: {C['panel_light']};
    color: {C['fg']};
    border: 1px solid {C['border']};
    border-radius: 9px;
    padding: 7px 14px;
    font-size: 9pt;
}}
QPushButton:hover {{ background-color: {C['accent_dark']}; border-color: {C['teal']}; }}
QPushButton:pressed {{ background-color: {C['accent']}; }}
QPushButton:disabled {{ color: {C['dim']}; border-color: #6B5A78; }}
"""


class ViceBackground(QWidget):
    """
    Многослойный статичный фон: закат, солнце, пальмы, неоновый горизонт.
    Рисуется один раз через paintEvent — без бесконечных таймеров и blur.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._logo = QPixmap(resource_path("assets", "app_icon.png"))
        if not self._logo.isNull():
            self._logo = self._logo.scaled(
                84, 84, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        sky = QLinearGradient(0, 0, 0, h)
        sky.setColorAt(0.0, QColor(C["night"]))
        sky.setColorAt(0.42, QColor(C["plum"]))
        sky.setColorAt(0.72, QColor("#6B2B62"))
        sky.setColorAt(1.0, QColor(C["coral"]))
        painter.fillRect(self.rect(), QBrush(sky))

        # Тёплое солнце / свечение за горизонтом.
        sun = QRadialGradient(w * 0.78, h * 0.68, max(80, h * 0.28))
        sun.setColorAt(0.0, QColor(255, 208, 164, 220))
        sun.setColorAt(0.32, QColor(255, 173, 118, 120))
        sun.setColorAt(1.0, QColor(255, 79, 154, 0))
        painter.fillRect(self.rect(), QBrush(sun))

        # Бирюзовый неоновый блик.
        teal = QRadialGradient(w * 0.12, h * 0.82, max(90, h * 0.32))
        teal.setColorAt(0.0, QColor(85, 226, 208, 95))
        teal.setColorAt(1.0, QColor(85, 226, 208, 0))
        painter.fillRect(self.rect(), QBrush(teal))

        # Горизонт и «вода».
        horizon = int(h * 0.76)
        water = QLinearGradient(0, horizon, 0, h)
        water.setColorAt(0.0, QColor(33, 21, 45, 210))
        water.setColorAt(1.0, QColor(19, 15, 32, 245))
        painter.fillRect(QRectF(0, horizon, w, h - horizon), QBrush(water))
        pen = QPen(QColor(255, 208, 164, 75))
        pen.setWidth(1)
        painter.setPen(pen)
        for i in range(4):
            y = horizon + 18 + i * 28
            painter.drawLine(int(w * (0.08 + i * 0.05)), y, int(w * (0.92 - i * 0.05)), y)

        # Силуэты пальм (лёгкие векторные кривые, без картинок).
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(19, 12, 28, 210))
        self._draw_palm(painter, int(w * 0.07), horizon + 6, 1.0)
        self._draw_palm(painter, int(w * 0.93), horizon + 10, 1.18)
        self._draw_palm(painter, int(w * 0.86), horizon + 4, 0.72)

        # Мягкая виньетка для читаемости текста.
        vignette = QRadialGradient(w / 2, h / 2, max(w, h) * 0.82)
        vignette.setColorAt(0.55, QColor(0, 0, 0, 0))
        vignette.setColorAt(1.0, QColor(10, 5, 18, 150))
        painter.fillRect(self.rect(), QBrush(vignette))
        painter.end()

    @staticmethod
    def _draw_palm(painter: QPainter, x: int, y: int, scale: float) -> None:
        trunk_h = int(210 * scale)
        trunk_w = max(5, int(11 * scale))
        path = QPainterPath()
        path.moveTo(x - trunk_w, y)
        path.quadTo(x + 12 * scale, y - trunk_h * 0.55, x + 26 * scale, y - trunk_h)
        path.lineTo(x + 34 * scale, y - trunk_h)
        path.quadTo(x + 18 * scale, y - trunk_h * 0.55, x + trunk_w, y)
        path.closeSubpath()
        painter.drawPath(path)
        top_x, top_y = x + 30 * scale, y - trunk_h
        for angle in (200, 235, 270, 305, 340):
            import math

            rad = angle * math.pi / 180.0
            length = 92 * scale
            end_x = top_x + math.cos(rad) * length
            end_y = top_y + math.sin(rad) * length * 0.55
            leaf = QPainterPath()
            leaf.moveTo(top_x, top_y)
            leaf.quadTo(
                (top_x + end_x) / 2,
                top_y - 34 * scale,
                end_x,
                end_y,
            )
            leaf.quadTo((top_x + end_x) / 2, top_y + 14 * scale, top_x, top_y)
            leaf.closeSubpath()
            painter.drawPath(leaf)


class AnimatedCard(QFrame):
    """Стеклянная панель с лёгким подъёмом при наведении."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._offset = 0.0
        self._target = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WA_StyledBackground, True)

    def enterEvent(self, event):
        self._target = -2.0
        if not self._timer.isActive():
            self._timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._target = 0.0
        if not self._timer.isActive():
            self._timer.start()
        super().leaveEvent(event)

    def _tick(self):
        diff = self._target - self._offset
        if abs(diff) < 0.2:
            self._offset = self._target
            self._timer.stop()
        else:
            self._offset += diff * 0.25
        m = max(14, int(18 + self._offset))
        self.setContentsMargins(m, m, m, m)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        painter.fillPath(path, QColor(33, 21, 45, 214))
        pen = QPen(QColor(255, 112, 189, 95))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)


class Particle:
    def __init__(self, x, y, vx, vy, lifetime, size):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.lifetime, self.age = lifetime, 0
        self.size = size

    def update(self):
        self.x += self.vx
        self.y += self.vy
        self.age += 1

    def get_opacity(self) -> float:
        return max(0.0, 1.0 - self.age / max(1, self.lifetime))

    def is_alive(self) -> bool:
        return self.age < self.lifetime


class ParticleEmitter(QWidget):
    """Только для искр при наведении/нажатии. Таймер останавливается, когда пусто."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.particles: list[Particle] = []
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._update_particles)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def emit_particles(self, x: float, y: float, count: int = 12) -> None:
        import math
        import random

        for _ in range(count):
            angle = random.uniform(0, 2 * math.pi)
            speed = random.uniform(0.7, 2.4)
            self.particles.append(
                Particle(
                    x,
                    y,
                    math.cos(angle) * speed,
                    math.sin(angle) * speed,
                    random.randint(14, 28),
                    random.uniform(2.0, 4.5),
                )
            )
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def _update_particles(self):
        for p in self.particles:
            p.update()
        self.particles = [p for p in self.particles if p.is_alive()]
        if not self.particles:
            self._timer.stop()
        self.update()

    def paintEvent(self, event):
        if not self.particles:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for p in self.particles:
            color = QColor(C["peach"] if p.get_opacity() > 0.5 else C["teal"])
            color.setAlphaF(p.get_opacity() * 0.9)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(int(p.x - p.size), int(p.y - p.size), int(p.size * 2), int(p.size * 2))
        painter.end()


def _mix_color(first: QColor, second: QColor, factor: float) -> QColor:
    """Линейная смесь двух QColor. В PySide6 нет QColor.lerp — делаем сами."""
    t = max(0.0, min(1.0, float(factor)))
    return QColor(
        int(round(first.red() + (second.red() - first.red()) * t)),
        int(round(first.green() + (second.green() - first.green()) * t)),
        int(round(first.blue() + (second.blue() - first.blue()) * t)),
        int(round(first.alpha() + (second.alpha() - first.alpha()) * t)),
    )


class GlowButton(QWidget):
    """
    Кнопка «Играть».

    ПЕРВОПРИЧИНА старого бага: в 3.0 состояние hover анимировало
    QGraphicsOpacityEffect до 0 и/или применяло эффект к родительскому
    виджету, из-за чего кнопка становилась невидимой, оставаясь кликабельной.

    В 4.0 кнопка НЕ имеет QGraphicsOpacityEffect и НЕ меняет alpha.
    При наведении меняются только цвет градиента и радиус свечения.
    Любые состояния рисуются с alpha=255.
    """

    clicked = Signal()

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._label = text
        self._hovered = False
        self._pressed = False
        self._loading = False
        self._hover = 0.0
        self._press = 0.0
        self._phase = 0
        self._phase_dir = 1
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(56)
        self.setMinimumWidth(250)
        self._emitter = ParticleEmitter(self)
        self._timer = QTimer(self)
        self._timer.setInterval(24)
        self._timer.timeout.connect(self._tick)

    def sizeHint(self) -> QSize:
        return QSize(300, 56)

    def text(self) -> str:
        return self._label

    def set_text(self, text: str) -> None:
        self._label = str(text)
        self.update()

    def set_loading(self, loading: bool) -> None:
        self._loading = bool(loading)
        if loading and not self._timer.isActive():
            self._timer.start()
        self.update()

    def is_loading(self) -> bool:
        return self._loading

    def enterEvent(self, event):
        self._hovered = True
        self._emitter.emit_particles(self.width() * 0.5, self.height() * 0.5, 10)
        if not self._timer.isActive():
            self._timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._pressed = False
        if not self._timer.isActive():
            self._timer.start()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled() and not self._loading:
            self._pressed = True
            self._emitter.emit_particles(event.position().x(), event.position().y(), 16)
            if not self._timer.isActive():
                self._timer.start()
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pressed and self.isEnabled() and not self._loading:
            self.clicked.emit()
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.EnabledChange:
            self.update()
        super().changeEvent(event)

    def _tick(self):
        target_hover = 1.0 if self._hovered and self.isEnabled() else 0.0
        target_press = 1.0 if self._pressed else 0.0
        dh = target_hover - self._hover
        dp = target_press - self._press
        if abs(dh) > 0.01:
            self._hover += dh * 0.28
        else:
            self._hover = target_hover
        if abs(dp) > 0.01:
            self._press += dp * 0.35
        else:
            self._press = target_press

        if self._loading:
            self._phase += 8 * self._phase_dir
            if self._phase > 180:
                self._phase_dir = -1
            elif self._phase < 0:
                self._phase_dir = 1

        if (
            abs(dh) <= 0.01
            and abs(dp) <= 0.01
            and not self._loading
            and not self._emitter.particles
        ):
            self._timer.stop()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        rect = QRectF(3, 3, w - 6, h - 6)

        if not self.isEnabled():
            c1, c2 = QColor("#5B4568"), QColor("#40324E")
            glow = QColor(90, 75, 110, 70)
            text_color = QColor(C["fg"])
            text_color.setAlpha(210)
        elif self._loading:
            c1, c2 = QColor(C["coral"]), QColor(C["lavender"])
            glow = QColor(169, 145, 255, 90)
            text_color = QColor(C["fg"])
        else:
            mix = max(self._hover, self._press)
            c1 = _mix_color(QColor(C["accent"]), QColor(C["sunset"]), mix * 0.85)
            c2 = _mix_color(QColor(C["neon_pink"]), QColor(C["coral"]), mix * 0.75)
            if self._press > 0.25:
                c1 = c1.darker(115)
                c2 = c2.darker(115)
            glow = QColor(C["neon_pink"])
            glow.setAlpha(int(60 + 95 * max(self._hover, self._press)))
            text_color = QColor(C["fg"])

        # внешнее свечение (никогда не скрывает саму кнопку)
        for spread in (14, 8):
            gr = QRadialGradient(w / 2, h / 2, max(w, h) * 0.62)
            gr.setColorAt(0.55, glow)
            gr.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setBrush(QBrush(gr))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(
                QRectF(-spread, -spread, w + spread * 2, h + spread * 2),
                18 + spread,
                18 + spread,
            )

        grad = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
        grad.setColorAt(0.0, c1)
        grad.setColorAt(1.0, c2)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        painter.setBrush(QBrush(grad))
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)

        if self._loading:
            shimmer = QLinearGradient(rect.left() + self._phase, 0, rect.right() + self._phase, 0)
            shimmer.setColorAt(0.0, QColor(255, 255, 255, 0))
            shimmer.setColorAt(0.5, QColor(255, 255, 255, 55))
            shimmer.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(QBrush(shimmer))
            painter.drawPath(path)

        painter.setPen(QPen(QColor(255, 243, 245, 220), 1))
        painter.drawPath(path)

        font = QFont("Segoe UI", 12, QFont.Bold)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(self.rect(), Qt.AlignCenter, self._label)
        painter.end()
        self._emitter.setGeometry(self.rect())


class FadeLabel(QLabel):
    """Статусная надпись; эффект прозрачности применяется только к ней."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._animation: QPropertyAnimation | None = None
        self._effect = QGraphicsDropShadowEffect(self)
        self._effect.setBlurRadius(10)
        self._effect.setColor(QColor(19, 15, 32, 200))
        self._effect.setOffset(0, 1)
        self.setGraphicsEffect(self._effect)
        self.setWordWrap(True)

    def fade_to(self, text: str, duration: int = 160) -> None:
        if self._animation and self._animation.state() == QPropertyAnimation.Running:
            self._animation.stop()
        self.setText(str(text))
        self.update()


class AnimatedProgressBar(QProgressBar):
    pass


# ============================== ОБНОВЛЕНИЕ ==================================


class UpdateChecker(QThread):
    update_available = Signal(str, str, str, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manifest = None

    def run(self):
        try:
            r = requests.get(
                LAUNCHER_VERSION_URL,
                timeout=15,
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            data = parse_update_manifest(r.json())
            if compare_versions(APP_VERSION, data["version"]):
                self._manifest = data
                self.update_available.emit(
                    data["version"], data["url"], data["sha256"], data["changelog"]
                )
        except Exception:
            log.debug("Не удалось проверить обновление", exc_info=True)


class UpdateDialog(QDialog):
    """Красивое уведомление об обновлении в стиле 4.0. UI не блокируется: сеть в потоке."""

    update_requested = Signal()

    def __init__(self, current: str, latest: str, notes: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Обновление MOGGCraft Launcher")
        self.setMinimumWidth(480)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(STYLESHEET)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(12)

        title = QLabel("ДОСТУПНО ОБНОВЛЕНИЕ")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color: {C['neon_pink']}; font-size: 18pt; font-weight: 700; background: transparent;"
        )
        layout.addWidget(title)

        version = QLabel(f"Версия {current} → {latest}")
        version.setAlignment(Qt.AlignCenter)
        version.setStyleSheet(f"color: {C['peach']}; font-size: 13pt; background: transparent;")
        layout.addWidget(version)

        desc = QLabel(
            "Для вашего лаунчера доступна новая версия. Обновите приложение, "
            "чтобы получить улучшения и исправления. Настройки, моды и сборка сохранятся."
        )
        desc.setWordWrap(True)
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet(f"color: {C['fg']}; font-size: 10pt; background: transparent;")
        layout.addWidget(desc)

        if notes:
            box = QLabel("• " + "\n• ".join(notes))
            box.setWordWrap(True)
            box.setStyleSheet(f"color: {C['dim']}; font-size: 9pt; background: transparent;")
            layout.addWidget(box)

        self.progress = AnimatedProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(12)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setStyleSheet(f"color: {C['teal']}; font-size: 9pt; background: transparent;")
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(14)
        self.later = QPushButton("ПОЗЖЕ")
        self.later.setFixedHeight(42)
        self.update_button = GlowButton("ОБНОВИТЬ СЕЙЧАЗ")
        self.update_button.setFixedHeight(42)
        self.update_button.setMinimumWidth(210)
        row.addStretch(1)
        row.addWidget(self.later)
        row.addWidget(self.update_button)
        row.addStretch(1)
        layout.addLayout(row)

        self._ready = False
        self.later.clicked.connect(self.reject)
        self.update_button.clicked.connect(self._on_update_clicked)

    @property
    def ready_to_apply(self) -> bool:
        return self._ready

    def _on_update_clicked(self) -> None:
        if self._ready:
            self.accept()
            return
        self.update_button.setEnabled(False)
        self.later.setEnabled(False)
        self.progress.show()
        self.status.setText("Подготовка обновления...")
        self.update_requested.emit()

    def set_progress(self, percent: int, text: str = "") -> None:
        self.progress.show()
        self.progress.setValue(max(0, min(100, int(percent))))
        if text:
            self.status.setText(text)

    def mark_ready(self) -> None:
        self._ready = True
        self.set_progress(100, "Обновление проверено (SHA-256 совпал). Перезапуск...")
        self.update_button.set_text("УСТАНОВИТЬ И ПЕРЕЗАПУСТИТЬ")
        self.update_button.setEnabled(True)
        self.later.setText("ЗАКРЫТЬ")
        self.later.setEnabled(True)

    def mark_error(self, message: str) -> None:
        self._ready = False
        self.status.setText(str(message))
        self.status.setStyleSheet(f"color: {C['err']}; font-size: 9pt; background: transparent;")
        self.update_button.set_text("ПОВТОРИТЬ")
        self.update_button.setEnabled(True)
        self.later.setText("ЗАКРЫТЬ")
        self.later.setEnabled(True)


# ============================== ИГРОВОЙ КОНВЕЙЕР =============================


class ModSyncWorker(QThread):
    sig_status = Signal(str, int)
    sig_done = Signal()
    sig_error = Signal(str)

    def __init__(self, nickname: str, ram_mb: int, parent=None):
        super().__init__(parent)
        self.nickname = nickname
        self.ram_mb = ram_mb

    def run(self):
        try:
            log.info("Начинаю запуск для игрока %s", self.nickname)
            sync_emotes()
            manifest = self._fetch_manifest()
            mc_version = str(manifest.get("minecraft_version") or MINECRAFT_VERSION)
            neoforge_version = str(manifest.get("neoforge_version") or NEOFORGE_VERSION)
            forge_version = str(manifest.get("forge_version") or "")
            self._sync_mods(manifest)
            version_id = self._ensure_minecraft_and_loader(
                mc_version, neoforge_version, forge_version
            )
            self._launch_game(self.nickname, version_id)
            self.sig_done.emit()
        except Exception as exc:
            log.exception("Ошибка в конвейере запуска")
            self.sig_error.emit(str(exc))

    def _report(self, status: str, value: int = 0):
        log.info("Статус: %s", status)
        self.sig_status.emit(status, value)

    def _fetch_manifest(self) -> dict:
        self._report("Загружаю список модов...")
        r = requests.get(MANIFEST_URL, timeout=30, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or not isinstance(data.get("mods"), list):
            raise RuntimeError("Манифест модов повреждён")
        base = str(data.get("base_url", ""))
        if not safe_https_url(base):
            raise RuntimeError("Недоверенный источник модов в манифесте")
        return data

    def _download(self, url: str, dest: str, label: str, attempts: int = 3) -> None:
        if not safe_https_url(url):
            raise RuntimeError(f"Недоверенный URL: {url}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        part = dest + ".part"
        for attempt in range(1, attempts + 1):
            try:
                log.info("Скачиваю %s -> %s (попытка %d)", url, dest, attempt)
                with requests.get(
                    url, stream=True, timeout=120, headers={"User-Agent": USER_AGENT}
                ) as r:
                    r.raise_for_status()
                    with open(part, "wb") as f:
                        for chunk in r.iter_content(1024 * 1024):
                            if chunk:
                                f.write(chunk)
                os.replace(part, dest)
                return
            except Exception as exc:
                log.warning("Ошибка загрузки %s: %s", label, exc)
                try:
                    if os.path.exists(part):
                        os.remove(part)
                except OSError:
                    pass
                if attempt == attempts:
                    raise
                self._report(f"Повторяю загрузку: {label}", 0)

    def _sync_mods(self, manifest: dict) -> None:
        base_url = str(manifest.get("base_url", "")).rstrip("/")
        remote = {
            str(m["filename"]): m
            for m in manifest["mods"]
            if isinstance(m, dict) and m.get("filename")
        }
        if not remote:
            raise RuntimeError("В манифесте нет ни одного мода — проверь список модов")
        os.makedirs(MODS_DIR, exist_ok=True)

        # Удаляем только .jar, которых больше нет в манифесте (инкрементальность).
        for fname in os.listdir(MODS_DIR):
            if fname.lower().endswith(".jar") and fname not in remote:
                try:
                    os.remove(os.path.join(MODS_DIR, fname))
                    log.info("Удалён устаревший мод: %s", fname)
                except OSError:
                    pass

        total = len(remote)
        done = 0
        for fname, info in remote.items():
            local = os.path.join(MODS_DIR, fname)
            if mod_ok(local, info):
                done += 1
                self._report("Проверяю все моды...", round(100 * done / total))
                continue
            if os.path.exists(local):
                try:
                    os.remove(local)
                except OSError:
                    pass
            self._report(f"Скачиваю мод {fname}", round(100 * done / total))
            self._download(base_url + "/" + _url_quote(fname), local, fname)
            if not mod_ok(local, info):
                raise RuntimeError(
                    f"Мод {fname} скачан с ошибкой — контрольная сумма не совпала.\n"
                    "Проверь соединение и попробуй ещё раз."
                )
            done += 1
            self._report("Проверяю все моды...", round(100 * done / total))
        self._report("Все моды на месте", 100)

    def _find_java(self, version_id: str) -> str | None:
        try:
            info = mll.runtime.get_version_runtime_information(version_id, MC_DIR)
            component = getattr(info, "component", None) if info else None
            if component:
                p = mll.runtime.get_executable_path(component, MC_DIR)
                if p and os.path.isfile(p):
                    return p
            path = os.path.join(MC_DIR, "versions", version_id, f"{version_id}.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            component = data.get("javaVersion", {}).get("component")
            if component:
                p = mll.runtime.get_executable_path(component, MC_DIR)
                if p and os.path.isfile(p):
                    return p
        except Exception:
            pass
        return None

    def _ensure_java(self, version_id: str) -> str:
        """Гарантированно возвращает путь к Java (устанавливает рантайм при необходимости)."""
        found = self._find_java(version_id)
        if found:
            return found
        try:
            info = mll.runtime.get_version_runtime_information(version_id, MC_DIR)
            component = getattr(info, "component", None) if info else None
            if component:
                self._report("Устанавливаю Java для Minecraft (один раз)...", 0)
                mll.runtime.install_jvm_runtime(
                    component,
                    MC_DIR,
                    callback={
                        "setStatus": lambda s: self._report(str(s), 0),
                        "setProgress": lambda v: self._report("Устанавливаю Java...", int(v)),
                    },
                )
                p = mll.runtime.get_executable_path(component, MC_DIR)
                if p and os.path.isfile(p):
                    return p
        except Exception as exc:
            log.warning("Не удалось установить рантайм Java: %s", exc)
        for component in mll.runtime.get_installed_jvm_runtimes(MC_DIR):
            p = mll.runtime.get_executable_path(component, MC_DIR)
            if p and os.path.isfile(p):
                return p
        system = shutil.which("java")
        if system:
            return system
        raise RuntimeError(
            "Не найдена Java для запуска Minecraft. Перезапустите лаунчер с интернетом: "
            "он скачает нужный рантайм автоматически."
        )

    def _ensure_minecraft_and_loader(
        self, mc_version: str, neoforge_version: str, forge_version: str
    ) -> str:
        loader_label = (
            f"NeoForge {neoforge_version}" if neoforge_version else f"Forge {forge_version}"
        )
        self._report(f"Проверяю Minecraft {mc_version} + {loader_label}...", 0)

        installed_ids = []
        try:
            installed_ids = [str(v.get("id", "")) for v in mll.utils.get_installed_versions(MC_DIR)]
        except Exception:
            pass

        need_install = mc_version not in installed_ids
        if need_install:
            self._report(f"Устанавливаю Minecraft {mc_version}...", 0)
            for attempt in range(1, 4):
                try:
                    mll.install.install_minecraft_version(
                        mc_version,
                        MC_DIR,
                        callback={
                            "setStatus": lambda s: self._report(str(s), 0),
                            "setProgress": lambda v: self._report(
                                "Устанавливаю Minecraft...", int(v)
                            ),
                        },
                    )
                    break
                except Exception as exc:
                    log.warning("Установка Minecraft, попытка %d: %s", attempt, exc)
                    if attempt == 3:
                        raise
                    self._report(f"Повторяю установку Minecraft ({attempt}/3)...", 0)

        java = self._ensure_java(mc_version)
        if neoforge_version:
            want = mll.mod_loader.Neoforge().get_installed_version(mc_version, neoforge_version)
            if want not in installed_ids:
                self._report("Устанавливаю NeoForge (один раз, может занять пару минут)...", 0)
                for attempt in range(1, 4):
                    try:
                        mll.mod_loader.Neoforge().install(
                            mc_version,
                            MC_DIR,
                            callback={
                                "setStatus": lambda s: self._report(str(s), 0),
                                "setProgress": lambda v: self._report("Устанавливаю NeoForge...", int(v)),
                            },
                            java=java,
                            loader_version=neoforge_version,
                        )
                        installed_ids.append(want)
                        break
                    except Exception as exc:
                        log.warning("Установка NeoForge, попытка %d: %s", attempt, exc)
                        if attempt == 3:
                            raise
                        self._report(f"Повторяю установку NeoForge ({attempt}/3)...", 0)
            return want
        if forge_version:
            forge_full = forge_version if "-" in forge_version else f"{mc_version}-{forge_version}"
            want = forge_full
            if want not in installed_ids:
                self._report("Устанавливаю Forge (один раз, может занять пару минут)...", 0)
                for attempt in range(1, 4):
                    try:
                        mll.forge.install_forge_version(
                            forge_full,
                            MC_DIR,
                            callback={
                                "setStatus": lambda s: self._report(str(s), 0),
                                "setProgress": lambda v: self._report("Устанавливаю Forge...", int(v)),
                            },
                            java=java,
                        )
                        break
                    except Exception as exc:
                        log.warning("Установка Forge, попытка %d: %s", attempt, exc)
                        if attempt == 3:
                            raise
                        self._report(f"Повторяю установку Forge ({attempt}/3)...", 0)
            return want
        raise RuntimeError("Версия загрузчика не указана в манифесте — обратись к администратору сервера")

    def _launch_game(self, nickname: str, version_id: str) -> None:
        ram = self.ram_mb or auto_ram_mb()
        self._report(f"Запускаю игру ({ram} МБ)...", 100)
        options = {
            "username": nickname,
            "uuid": offline_uuid(nickname),
            "token": "0",
            "launcherName": APP_TITLE,
            "launcherVersion": APP_VERSION,
            "gameDirectory": MC_DIR,
            "memory": ram,
            "jvmArguments": [f"-Xmx{ram}M", f"-Xms{min(ram, 2048)}M"],
            "server": SERVER_IP,
            "port": SERVER_PORT,
            "executablePath": self._ensure_java(version_id),
        }
        command = mll.command.get_minecraft_command(version_id, MC_DIR, options)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        log.info("Команда запуска: %s", " ".join(str(x) for x in command))
        subprocess.Popen(command, cwd=MC_DIR, creationflags=flags)
        self._report("Игра запущена", 100)


# ============================== ГЛАВНОЕ ОКНО ================================


class LauncherWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(880, 620)
        self.resize(1020, 720)
        self.setStyleSheet(STYLESHEET)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.cfg = load_config()

        self.progress_queue: queue.Queue = queue.Queue()
        self._worker: ModSyncWorker | None = None
        self._updater: UpdateChecker | None = None
        self._update_info: dict | None = None

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.background = ViceBackground(central)
        root.addWidget(self.background)
        self._build_ui()

        self._center()
        # ВАЖНО: никакого QGraphicsOpacityEffect на окне/central widget.
        # Старый баг hover disappearing originал именно в цепочке opacity-эффектов.
        self.setWindowOpacity(1.0)
        self.show()

        QTimer.singleShot(120, self._poll_queue)
        self._check_for_updates()
        threading.Thread(target=sync_emotes, daemon=True).start()
        self._refresh_build_status()

    def _center(self):
        screen = QApplication.primaryScreen().geometry()
        size = self.frameGeometry()
        size.moveCenter(screen.center())
        self.move(size.topLeft())

    def _build_ui(self):
        overlay = QVBoxLayout(self.background)
        overlay.setContentsMargins(34, 26, 34, 24)
        overlay.setSpacing(14)

        # --- Верх: бренд, версия, MINECRAFT, TM-RPG ---
        header = QHBoxLayout()
        header.setSpacing(12)
        brand_col = QVBoxLayout()
        brand_col.setSpacing(2)
        brand = QLabel("MOGGCRAFT")
        brand.setStyleSheet(
            f"color: {C['fg']}; font-size: 25pt; font-weight: 800; background: transparent;"
        )
        ver = QLabel(f"ЛАУНЧЕР  v{APP_VERSION}")
        ver.setStyleSheet(f"color: {C['teal']}; font-size: 9pt; letter-spacing: 1px; background: transparent;")
        brand_col.addWidget(brand)
        brand_col.addWidget(ver)
        header.addLayout(brand_col)
        header.addStretch(1)

        game_col = QVBoxLayout()
        game_col.setAlignment(Qt.AlignRight)
        game = QLabel("MINECRAFT")
        game.setAlignment(Qt.AlignRight)
        game.setStyleSheet(
            f"color: {C['peach']}; font-size: 27pt; font-weight: 800; background: transparent;"
        )
        tm = QLabel("TM-RPG  •  Vice City Edition")
        tm.setAlignment(Qt.AlignRight)
        tm.setStyleSheet(f"color: {C['neon_pink']}; font-size: 8pt; background: transparent;")
        game_col.addWidget(game)
        game_col.addWidget(tm)
        header.addLayout(game_col)
        overlay.addLayout(header)

        # --- Центр: профиль, ник, RAM, играть, статус ---
        card = AnimatedCard(self.background)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(26, 22, 26, 22)
        card_layout.setSpacing(12)

        profile = QLabel("ИГРОВОЙ ПРОФИЛЬ")
        profile.setStyleSheet(
            f"color: {C['teal']}; font-size: 9pt; font-weight: 700; background: transparent;"
        )
        card_layout.addWidget(profile)

        nick_label = QLabel("Никнейм")
        nick_label.setStyleSheet(f"color: {C['dim']}; font-size: 9pt; background: transparent;")
        card_layout.addWidget(nick_label)

        self.nick_entry = QLineEdit()
        self.nick_entry.setPlaceholderText("Введите ник (3–16 символов)")
        self.nick_entry.setText(str(self.cfg.get("nickname", "")))
        self.nick_entry.setMaxLength(16)
        self.nick_entry.returnPressed.connect(self.on_play_clicked)
        card_layout.addWidget(self.nick_entry)

        self.profile_info = QLabel("")
        self.profile_info.setStyleSheet(f"color: {C['dim']}; font-size: 8pt; background: transparent;")
        self._update_profile_info()
        card_layout.addWidget(self.profile_info)

        ram_row = QHBoxLayout()
        ram_label = QLabel("Оперативная память (RAM)")
        ram_label.setStyleSheet(f"color: {C['dim']}; font-size: 9pt; background: transparent;")
        self.ram_combo = QComboBox()
        self.ram_combo.addItems([label for label, _ in RAM_OPTIONS])
        saved_ram_value = parse_ram_value(self.cfg.get("ram", 0))
        idx = 0
        for i, (_label, value) in enumerate(RAM_OPTIONS):
            if value == saved_ram_value:
                idx = i
                break
        self.ram_combo.setCurrentIndex(idx)
        ram_row.addWidget(ram_label)
        ram_row.addStretch(1)
        ram_row.addWidget(self.ram_combo)
        card_layout.addLayout(ram_row)

        hint = QLabel("Авто — половина оперативной памяти ПК")
        hint.setStyleSheet(f"color: {C['dim']}; font-size: 8pt; background: transparent;")
        card_layout.addWidget(hint)

        self.play_button = GlowButton("ИГРАТЬ")
        self.play_button.clicked.connect(self.on_play_clicked)
        card_layout.addWidget(self.play_button, 0, Qt.AlignCenter)

        self.status_label = FadeLabel("Готов к запуску")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet(
            f"color: {C['fg']}; background: transparent; font-size: 9pt;"
        )
        card_layout.addWidget(self.status_label)

        self.progress_bar = AnimatedProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(10)
        card_layout.addWidget(self.progress_bar)

        self.build_status = QLabel("Сборка: проверяется при запуске")
        self.build_status.setAlignment(Qt.AlignCenter)
        self.build_status.setStyleSheet(f"color: {C['dim']}; font-size: 8pt; background: transparent;")
        card_layout.addWidget(self.build_status)

        self.server_label = QLabel(f"Сервер: {SERVER_IP}:{SERVER_PORT}")
        self.server_label.setAlignment(Qt.AlignCenter)
        self.server_label.setStyleSheet(f"color: {C['neon_pink']}; font-size: 8pt; background: transparent;")
        card_layout.addWidget(self.server_label)

        overlay.addWidget(card, 1)

        # --- Низ:/settings + папки + версия ---
        footer = QHBoxLayout()
        footer.setSpacing(10)
        for text, fn in (
            ("Настройки", self.open_launcher_dir),
            ("Папка лаунчера", self.open_launcher_dir),
            ("Папка модов", self.open_mods_dir),
            ("Шейдеры", self.open_shaders_dir),
        ):
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(fn)
            footer.addWidget(btn)
        footer.addStretch(1)
        ver_small = QLabel(f"MOGGCraft Launcher {APP_VERSION} · MC {MINECRAFT_VERSION}")
        ver_small.setStyleSheet(f"color: {C['dim']}; font-size: 8pt; background: transparent;")
        footer.addWidget(ver_small)
        overlay.addLayout(footer)

    def _update_profile_info(self):
        nick = self.nick_entry.text().strip() if hasattr(self, "nick_entry") else ""
        if nick:
            self.profile_info.setText(f"Профиль: {nick}  ·  UUID {offline_uuid(nick)[:8]}…")
        else:
            self.profile_info.setText("Профиль не выбран")

    def _refresh_build_status(self):
        try:
            mods = [f for f in os.listdir(MODS_DIR) if f.lower().endswith(".jar")]
            self.build_status.setText(
                f"Сборка: MC {MINECRAFT_VERSION} · NeoForge {NEOFORGE_VERSION} · модов: {len(mods)}"
            )
        except OSError:
            pass

    def open_launcher_dir(self):
        open_folder(APP_DIR)

    def open_mods_dir(self):
        open_folder(MODS_DIR)

    def open_shaders_dir(self):
        open_folder(SHADERS_DIR)

    def on_play_clicked(self):
        nickname = self.nick_entry.text().strip()
        if not is_valid_nickname(nickname):
            self.status_label.setText("Ник должен быть 3–16 символов: буквы, цифры, _")
            self.status_label.setStyleSheet(
                f"color: {C['err']}; background: transparent; font-size: 9pt;"
            )
            return
        self.cfg["nickname"] = nickname
        ram_tuple = RAM_OPTIONS[self.ram_combo.currentIndex()][1]
        chosen = auto_ram_mb() if ram_tuple == 0 else ram_tuple
        self.cfg["ram"] = chosen
        save_config(self.cfg)
        self._update_profile_info()
        self.status_label.setText("Готов к запуску")
        self.status_label.setStyleSheet(
            f"color: {C['fg']}; background: transparent; font-size: 9pt;"
        )

        self.play_button.setEnabled(False)
        self.play_button.set_loading(True)
        self.play_button.set_text("ЗАПУСК...")
        self.progress_bar.setValue(0)
        self._worker = ModSyncWorker(nickname, chosen, self)
        self._worker.sig_status.connect(self._on_status)
        self._worker.sig_done.connect(self._on_done)
        self._worker.sig_error.connect(self._on_error)
        self._worker.start()

    def _on_status(self, text: str, value: int):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color: {C['fg']}; background: transparent; font-size: 9pt;"
        )
        self.progress_bar.setValue(int(value))

    def _on_done(self):
        self.status_label.setText("Игра запущена!")
        self.status_label.setStyleSheet(
            f"color: {C['ok']}; background: transparent; font-size: 9pt;"
        )
        self.progress_bar.setValue(100)
        self.play_button.set_loading(False)
        self.play_button.set_text("ИГРАТЬ")
        self.play_button.setEnabled(True)
        self._refresh_build_status()
        QTimer.singleShot(1200, self.close)

    def _on_error(self, msg: str):
        self.status_label.setText("Ошибка запуска")
        self.status_label.setStyleSheet(
            f"color: {C['err']}; background: transparent; font-size: 9pt;"
        )
        self.play_button.set_loading(False)
        self.play_button.set_text("ИГРАТЬ")
        self.play_button.setEnabled(True)
        show_message_box(self, APP_TITLE, f"Не удалось запустить игру:\n{msg}", critical=True)

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.progress_queue.get_nowait()
                if kind == "update_status":
                    self.status_label.setText(str(payload))
                elif kind == "update_progress":
                    self.progress_bar.setValue(int(payload))
                elif kind == "update_dialog_progress":
                    dlg = getattr(self, "_update_dialog", None)
                    if dlg is not None:
                        pct, text = payload
                        dlg.set_progress(pct, text)
                elif kind == "update_ready":
                    dlg = getattr(self, "_update_dialog", None)
                    if dlg is not None:
                        dlg.mark_ready()
                elif kind == "update_apply":
                    payload_dir, tmp_root = payload
                    self.status_label.setText("Устанавливаю обновление...")
                    apply_update_payload(payload_dir, tmp_root)
                    self.close()
                    return
                elif kind == "update_error":
                    dlg = getattr(self, "_update_dialog", None)
                    if dlg is not None:
                        dlg.mark_error(str(payload))
                    self.status_label.setText(f"Ошибка: {payload}")
                    self.play_button.set_loading(False)
                    self.play_button.set_text("ИГРАТЬ")
                    self.play_button.setEnabled(True)
                    show_message_box(
                        dlg if dlg is not None and dlg.isVisible() else self,
                        APP_TITLE,
                        str(payload),
                        critical=True,
                    )
        except queue.Empty:
            pass
        QTimer.singleShot(120, self._poll_queue)

    def _check_for_updates(self):
        self._updater = UpdateChecker(self)
        self._updater.update_available.connect(self._on_update_found)
        self._updater.start()

    def _on_update_found(self, latest: str, url: str, sha: str, notes: list):
        self._update_info = {"version": latest, "url": url, "sha256": sha, "changelog": notes}
        self.play_button.setEnabled(False)
        self.play_button.set_loading(True)
        self.play_button.set_text("ОБНОВЛЕНИЕ...")
        dlg = UpdateDialog(APP_VERSION, latest, notes, self)
        self._update_dialog = dlg
        dlg.update_requested.connect(
            lambda: threading.Thread(
                target=self._download_update, args=(url, sha), daemon=True
            ).start()
        )
        result = dlg.exec()
        if not (result == QDialog.Accepted and dlg.ready_to_apply):
            self._update_info = None
            self._update_dialog = None
            self.play_button.set_loading(False)
            self.play_button.set_text("ИГРАТЬ")
            self.play_button.setEnabled(True)

    def _download_update(self, url: str, expected_sha: str):
        tmp = tempfile.mkdtemp(prefix="moggcraft_update_")
        zpath = os.path.join(tmp, "update.zip")
        try:
            log.info("Скачиваю обновление: %s", url)
            self.progress_queue.put(("update_dialog_progress", (0, "Скачиваю обновление...")))
            with requests.get(
                url, stream=True, timeout=120, headers={"User-Agent": USER_AGENT}
            ) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                with open(zpath, "wb") as f:
                    for chunk in r.iter_content(1024 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        pct = min(99, round(100 * downloaded / total)) if total else 0
                        self.progress_queue.put(("update_progress", pct))
                        mb_done = round(downloaded / 1024 / 1024, 1)
                        mb_total = round(total / 1024 / 1024, 1) if total else "?"
                        self.progress_queue.put(
                            (
                                "update_dialog_progress",
                                (pct, f"Скачиваю: {mb_done}/{mb_total} МБ"),
                            )
                        )

            if expected_sha:
                actual = sha256_of_file(zpath).lower()
                if actual != expected_sha:
                    raise RuntimeError(
                        "SHA-256 обновления не совпал. Загрузка отклонена, текущая версия не изменена."
                    )

            self.progress_queue.put(
                ("update_dialog_progress", (99, "Проверяю архив обновления..."))
            )
            payload = os.path.join(tmp, "payload")
            os.makedirs(payload, exist_ok=True)
            with zipfile.ZipFile(zpath) as zf:
                for member in zf.infolist():
                    target = os.path.realpath(os.path.join(payload, member.filename))
                    if not target.startswith(os.path.realpath(payload)):
                        raise RuntimeError("Архив обновления содержит опасный путь")
                    zf.extract(member, payload)
            inner = os.path.join(payload, "MoggCraftLauncher")
            root = inner if os.path.isdir(inner) else payload
            if not os.path.isfile(os.path.join(root, "MoggCraftLauncher.exe")):
                raise RuntimeError("В архиве обновления нет MoggCraftLauncher.exe")
            self.progress_queue.put(("update_progress", 100))
            self.progress_queue.put(
                ("update_dialog_progress", (100, "SHA-256 совпал. Устанавливаю..."))
            )
            self.progress_queue.put(("update_ready", None))
            self.progress_queue.put(("update_apply", (root, tmp)))
        except Exception as exc:
            log.exception("Ошибка скачивания обновления")
            self.progress_queue.put(("update_error", f"Ошибка обновления: {exc}"))


def _check_and_repair() -> None:
    """Подтверждает удачный апдейт и возвращает лаунчер из temp на место при сбое."""
    if not getattr(sys, "frozen", False):
        return
    # Новый лаунчер успешно стартовал — backup больше не нужен.
    marker = os.path.join(tempfile.gettempdir(), "moggcraft_update_ok.txt")
    try:
        if os.path.isfile(marker):
            with open(marker, "r", encoding="utf-8") as f:
                backup = f.read().strip()
            if backup and os.path.isdir(backup) and "MoggCraftLauncher_backup_" in backup:
                shutil.rmtree(backup, ignore_errors=True)
                log.info("Удалён backup после успешного обновления: %s", backup)
            os.remove(marker)
    except Exception:
        log.debug("Не удалось обработать маркер обновления", exc_info=True)

    exe = os.path.realpath(sys.executable)
    exe_dir = os.path.dirname(exe)
    install_dir = os.path.join(
        os.path.expanduser("~"), "AppData", "Local", "MoggCraftLauncher"
    )
    tmp_root = os.path.realpath(tempfile.gettempdir()) + os.sep
    if not os.path.isdir(install_dir):
        return
    if os.path.realpath(exe_dir).startswith(tmp_root):
        log.info("Лаунчер запущен из %s — копирую в %s", exe_dir, install_dir)
        try:
            for item in os.listdir(exe_dir):
                src = os.path.join(exe_dir, item)
                dst = os.path.join(install_dir, item)
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            subprocess.Popen(
                [os.path.join(install_dir, os.path.basename(exe))],
                cwd=install_dir,
            )
            sys.exit(0)
        except Exception:
            log.exception("Не удалось восстановить лаунчер")


def main() -> None:
    _check_and_repair()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = LauncherWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
