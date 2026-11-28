"""
test_launcher.py — реальные проверки лаунчера 4.0 (PySide6 offscreen, без网).

Запуск:
  py -3.14 test_launcher.py

Проверяет:
  * первопричину бага «Играть»: отсутствие opacity-эффектов и непрозрачность
    кнопки во всех состояниях (normal/hover/pressed/disabled/loading);
  * палитру и класс фона Vice City;
  * диалог обновления (кнопки, прогресс, готовность, ошибка);
  * сравнение версий и валидацию manifest/URL/SHA-256;
  * проверку модов по размеру и SHA-256;
  * генерацию PowerShell-апдейтера с backup и rollback.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QWidget

import launcher

PASS: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL: {name}" + (f" — {detail}" if detail else ""))
    PASS.append(name)
    print(f"  ok: {name}")


def settled_pixel(button) -> QColor:
    button.update()
    QApplication.processEvents()
    for _ in range(8):
        button._tick()
    QApplication.processEvents()
    img: QImage = button.grab().toImage()
    # Не центр: в центре текст. Берём точку на градиенте кнопки.
    return img.pixelColor(max(1, img.width() // 7), img.height() // 2)


def test_palette_and_background(app: QApplication) -> None:
    required = {
        "#FF4F9A", "#FF70BD", "#FF776E", "#FFAD76", "#FFD0A4",
        "#55E2D0", "#A991FF", "#21152D", "#130F20", "#FFF3F5",
    }
    check("палитра Vice City полная", required.issubset(set(launcher.C.values())))
    window = launcher.LauncherWindow()
    window.show()
    app.processEvents()
    check("фон — ViceBackground", isinstance(window.background, launcher.ViceBackground))
    check("окно без graphics effect", window.graphicsEffect() is None)
    check("окно непрозрачно", window.windowOpacity() == 1.0)

    for w in window.findChildren(QWidget) + [window]:
        effect = w.graphicsEffect()
        check(
            f"нет opacity-эффекта: {w.metaObject().className()}",
            effect is None or "Opacity" not in effect.metaObject().className(),
        )
    window.close()


def test_play_button(app: QApplication) -> None:
    window = launcher.LauncherWindow()
    window.show()
    app.processEvents()
    b = window.play_button
    check("кнопка Играть — GlowButton", isinstance(b, launcher.GlowButton))
    check("у кнопки нет graphics effect", b.graphicsEffect() is None)

    states = [
        ("normal", lambda: None),
        ("hover", lambda: setattr(b, "_hovered", True)),
        ("hover+pressed", lambda: (setattr(b, "_hovered", True), setattr(b, "_pressed", True))),
        ("disabled", lambda: b.setEnabled(False)),
        ("loading", lambda: (b.setEnabled(True), b.set_loading(True))),
    ]
    for name, setup in states:
        setup()
        color = settled_pixel(b)
        alpha = color.alpha()
        check(
            f"Играть [{name}] видима (alpha={alpha}, rgb={color.name()})",
            alpha == 255 and color.name() not in ("#000000", "#00000000"),
        )
        check(f"Играть [{name}] текст сохранён", bool(b.text()))

    b.set_loading(False)
    b.setEnabled(True)
    b._hovered = False
    b._pressed = False
    b.set_text("ИГРАТЬ")
    check("кнопка вернулась в ИГРАТЬ", b.text() == "ИГРАТЬ")
    window.close()


def test_config(app, tmp: Path) -> None:
    old = launcher.CONFIG_PATH
    launcher.CONFIG_PATH = str(tmp / "launcher_config.json")
    try:
        cfg = {"nickname": "Vimzon", "ram": 6144, "theme": "vice"}
        launcher.save_config(cfg)
        check("конфиг сохраняется/читается", launcher.load_config() == cfg)
        check("RAM из числа", launcher.parse_ram_value(6144) == 6144)
        check(
            "RAM из подписи 3.x",
            launcher.parse_ram_value("6144 МБ") == 6144
            and launcher.parse_ram_value("Авто (половина ОЗУ)") == 0,
        )
        launcher.save_config({"nickname": "Vimzon", "ram": "Авто (половина ОЗУ)"})
        w = launcher.LauncherWindow()
        app.processEvents()
        check("окно открывается с конфигом 3.x (RAM как строка)", w.ram_combo.currentIndex() == 0)
        check("ник из конфига сохранён", w.nick_entry.text() == "Vimzon")
        w.close()
    finally:
        launcher.CONFIG_PATH = old


def test_versions_and_manifest() -> None:
    check("3.1 -> 4.0 update", launcher.compare_versions("3.1", "4.0") is True)
    check("4.0 -> 4.1 update", launcher.compare_versions("4.0", "4.1") is True)
    check("4.0 -> 4.0 no update", launcher.compare_versions("4.0", "4.0") is False)
    check("4.0 -> 3.1 no update", launcher.compare_versions("4.0", "3.1") is False)

    good = {
        "version": "4.1",
        "url": "https://github.com/Vimzon/moggcraft-mods/releases/download/launcher/MoggCraftLauncher-4.1.zip",
        "sha256": "a" * 64,
        "changelog": ["тест"],
    }
    parsed = launcher.parse_update_manifest(good)
    check("manifest принят", parsed["version"] == "4.1" and parsed["sha256"] == "a" * 64)
    for bad_url in (
        "http://example.com/update.zip",
        "https://evil.example/update.zip",
        "file:///C:/update.zip",
    ):
        try:
            launcher.parse_update_manifest({**good, "url": bad_url})
            check(f"отклонён URL {bad_url}", False)
        except ValueError:
            check(f"отклонён URL {bad_url}", True)
    try:
        launcher.parse_update_manifest({**good, "sha256": "bad"})
        check("отклонён короткий SHA", False)
    except ValueError:
        check("отклонён короткий SHA", True)

    check(
        "release URL разрешён",
        launcher.safe_https_url(
            "https://github.com/Vimzon/moggcraft-mods/releases/download/mods/a.jar"
        ),
    )
    check(
        "raw manifest URL разрешён",
        launcher.safe_https_url(
            "https://raw.githubusercontent.com/Vimzon/moggcraft-mods/main/mods_manifest.json"
        ),
    )
    check(
        "ник валидируется",
        launcher.is_valid_nickname("Vimzon") and not launcher.is_valid_nickname("a b"),
    )
    import uuid as _uuid

    nick_uuid = launcher.offline_uuid("Vimzon")
    try:
        parsed = _uuid.UUID(nick_uuid)
        check(
            "offline UUID стабилен и корректен",
            parsed.version == 3 and nick_uuid == launcher.offline_uuid("Vimzon"),
        )
    except ValueError:
        check("offline UUID корректен", False)


def test_mods(tmp: Path) -> None:
    path = tmp / "test.jar"
    path.write_bytes(b"MOGGCraft test mod")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    good = {"size": path.stat().st_size, "sha256": sha}
    check("мод по SHA принят", launcher.mod_ok(str(path), good))
    check("мод с неверным размером отклонён", not launcher.mod_ok(str(path), {"size": 1, "sha256": sha}))
    check("мод с неверным SHA отклонён", not launcher.mod_ok(str(path), {"size": good["size"], "sha256": "0" * 64}))
    check("отсутствующий мод отклонён", not launcher.mod_ok(str(tmp / "none.jar"), good))


def test_update_dialog(app: QApplication) -> None:
    dlg = launcher.UpdateDialog("4.0", "4.1", ["Тестовое изменение"])
    emitted: list[bool] = []
    dlg.update_requested.connect(lambda: emitted.append(True))
    label = dlg.findChild(launcher.QLabel)
    check("диалог: заголовок обновления", label is not None and "ДОСТУПНО ОБНОВЛЕНИЕ" in label.text())
    check("диалог: кнопка ПОЗЖЕ", dlg.later.text() == "ПОЗЖЕ")
    check("диалог: кнопка ОБНОВИТЬ СЕЙЧАЗ", dlg.update_button.text() == "ОБНОВИТЬ СЕЙЧАЗ")
    dlg._on_update_clicked()
    app.processEvents()
    check("диалог: запрос загрузки", bool(emitted))
    check("диалог: кнопки заблокированы на время загрузки", not dlg.update_button.isEnabled() and not dlg.later.isEnabled())
    dlg.set_progress(55, "Скачиваю: 5/10 МБ")
    check("диалог: прогресс 55", dlg.progress.value() == 55)
    dlg.mark_ready()
    check("диалог: готов к установке", dlg.ready_to_apply and dlg.update_button.text() == "УСТАНОВИТЬ И ПЕРЕЗАПУСТИТЬ")
    dlg.mark_error("тест ошибки")
    check("диалог: состояние ошибки", not dlg.ready_to_apply and dlg.update_button.text() == "ПОВТОРИТЬ")


def test_updater_script() -> None:
    old_frozen = getattr(sys, "frozen", False)
    old_exe = sys.executable
    calls: list[list[str]] = []
    try:
        sys.frozen = True
        sys.executable = r"C:\MoggCraftLauncher\MoggCraftLauncher.exe"
        with mock.patch.object(launcher.subprocess, "Popen", side_effect=lambda cmd, **kw: calls.append(cmd) or mock.Mock()):
            launcher.apply_update_payload(r"C:\payload", r"C:\tmp")
        script_path = Path(tempfile.gettempdir()) / "moggcraft_updater.ps1"
        text = script_path.read_text(encoding="utf-8")
        check("апдейтер запущен", bool(calls))
        check("апдейтер: backup", "MoggCraftLauncher_backup_" in text and "Backup created" in text)
        check("апдейтер: rollback", "Rollback completed" in text)
        check("апдейтер: маркер успешного старта", "moggcraft_update_ok.txt" in text)
        check("апдейтер: перезапуск", "Start-Process -FilePath $newExe" in text)
    finally:
        if old_frozen:
            sys.frozen = True
        elif hasattr(sys, "frozen"):
            del sys.frozen
        sys.executable = old_exe


def main() -> int:
    app = QApplication.instance() or QApplication([])
    # Тест не должен стучаться в сеть и писать реальный конфиг.
    launcher.LauncherWindow._check_for_updates = lambda self: None
    with tempfile.TemporaryDirectory(prefix="mogglauncher_test_") as td:
        tmp = Path(td)
        launcher.CONFIG_PATH = str(tmp / "launcher_config.json")
        test_palette_and_background(app)
        test_play_button(app)
        test_config(app, tmp)
        test_versions_and_manifest()
        test_mods(tmp)
        test_update_dialog(app)
        test_updater_script()
    print(f"\nПРОЙДЕНО ПРОВЕРОК: {len(PASS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
