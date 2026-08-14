"""
Launcher для Twitch TTS Reader.
Запускает XTTS API Server и основное приложение.
Показывает splash окно во время запуска.

В релизном (frozen) режиме этот же exe запускает сервер
вторым процессом с флагом --server.
"""

import sys
import os

# ============ ВАЖНО: проверка frozen ДО любых импортов ============
_IS_FROZEN = hasattr(sys, "frozen") and getattr(sys, "frozen", False)

if _IS_FROZEN:
    print(f"[Launcher] Режим: FROZEN (exe), sys.executable={sys.executable}")
else:
    print(f"[Launcher] Режим: DEV (скрипт), sys.executable={sys.executable}")
# ===================================================================

# ============ ВАЖНО: ДО ВСЕХ ОСТАЛЬНЫХ ИМПОРТОВ ============
# В режиме console=False (windowed) у процесса нет консоли:
# sys.stdout / sys.stderr равны None. Библиотеки (qfluentwidgets)
# падают при импорте. Перенаправляем в devnull.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
    
import time
import json
import subprocess
import faulthandler
import atexit
import requests
from pathlib import Path


from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox

from core.error_logger import (
    log_error, log_message, log_startup_stage,
    install_exception_hooks, get_log_path, get_logs_dir
)

XTTS_HOST = "http://localhost:8020"
HEALTH_CHECK_TIMEOUT = 180
HEALTH_CHECK_INTERVAL = 1

OUTPUT_MAX_FILES = 300
OUTPUT_MAX_SIZE_MB = 200
OUTPUT_MIN_AGE_SEC = 60
CLEANUP_INTERVAL_SEC = 120


def is_frozen():
    """Запущены ли мы как exe (PyInstaller)."""
    return _IS_FROZEN


def app_root():
    """Корневая папка приложения."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def clean_output_folder(app_dir, max_files=OUTPUT_MAX_FILES,
                        max_size_mb=OUTPUT_MAX_SIZE_MB,
                        min_age_seconds=OUTPUT_MIN_AGE_SEC):
    """Держит output/ в рамках лимитов."""
    output_dir = os.path.join(app_dir, "output")
    if not os.path.isdir(output_dir):
        return 0, 0

    now = time.time()
    all_files = []
    for name in os.listdir(output_dir):
        if not name.lower().endswith(".wav"):
            continue
        path = os.path.join(output_dir, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        all_files.append((stat.st_mtime, stat.st_size, path))

    if not all_files:
        return 0, 0

    all_files.sort(key=lambda x: x[0])
    remaining_count = len(all_files)
    remaining_size = sum(f[1] for f in all_files)
    max_bytes = max_size_mb * 1024 * 1024

    if remaining_count <= max_files and remaining_size <= max_bytes:
        return 0, 0

    deleted = 0
    freed = 0
    for mtime, size, path in all_files:
        if remaining_count <= max_files and remaining_size <= max_bytes:
            break
        if now - mtime < min_age_seconds:
            continue
        try:
            os.remove(path)
            deleted += 1
            freed += size
            remaining_count -= 1
            remaining_size -= size
        except OSError:
            pass

    return deleted, freed


def check_cuda():
    """
    Проверка наличия NVIDIA GPU БЕЗ импорта torch.
    Импорт torch в UI-процессе съедал ~0.5-1 ГБ RAM впустую.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def cleanup_server_process(server_process):
    """Завершить процесс сервера и все его потомки."""
    if server_process is None:
        return
    if server_process.poll() is not None:
        return

    log_message(f"Завершение сервера (PID {server_process.pid})", level="SHUTDOWN")

    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(server_process.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
            )
        except Exception:
            try:
                server_process.terminate()
            except Exception:
                pass
    else:
        try:
            import signal
            pgid = os.getpgid(server_process.pid)
            os.killpg(pgid, signal.SIGTERM)
            server_process.wait(timeout=5)
        except Exception:
            try:
                server_process.kill()
            except Exception:
                pass


def show_error_dialog(title, message):
    """Показать модальный диалог с ошибкой."""
    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Critical)
    msg_box.setWindowTitle(title)
    msg_box.setText(message)

    log_path = get_log_path()
    logs_dir = get_logs_dir()

    open_file_btn = None
    if log_path and os.path.exists(log_path):
        msg_box.setInformativeText(f"Подробная информация записана в:\n{log_path}")
        open_file_btn = msg_box.addButton("📄 Открыть файл лога", QMessageBox.ActionRole)
    else:
        msg_box.setInformativeText(f"Логи ошибок сохраняются в:\n{logs_dir}")

    open_folder_btn = msg_box.addButton("📂 Открыть папку с логами", QMessageBox.ActionRole)
    ok_btn = msg_box.addButton(QMessageBox.Ok)
    msg_box.setDefaultButton(ok_btn)
    msg_box.exec_()

    clicked = msg_box.clickedButton()
    try:
        if clicked == open_file_btn and log_path:
            os.startfile(log_path)
        elif clicked == open_folder_btn and os.path.exists(logs_dir):
            subprocess.run(["explorer", logs_dir], check=False)
    except Exception:
        pass


class ServerStartupWorker(QThread):
    """Фоновый поток запуска XTTS сервера."""

    stage_changed = pyqtSignal(str)
    stage_text_changed = pyqtSignal(str)
    server_ready = pyqtSignal(object)
    error_occurred = pyqtSignal(str)

    def __init__(self, cpu_threads=8):
        super().__init__()
        self.cpu_threads = cpu_threads

    def run(self):
        try:
            self.stage_changed.emit("config")
            self.stage_text_changed.emit("Проверка конфигурации...")
            log_startup_stage("Проверка конфигурации")
            time.sleep(0.3)

            self.stage_changed.emit("gpu")
            self.stage_text_changed.emit("Проверка GPU...")
            log_startup_stage("Проверка GPU")
            has_cuda = check_cuda()
            os.environ['TTS_DEVICE_MODE'] = 'cuda' if has_cuda else 'cpu'
            log_message(f"GPU: {'CUDA доступна' if has_cuda else 'CPU режим'}", level="STARTUP")
            time.sleep(0.2)

            try:
                deleted, freed = clean_output_folder(str(app_root()))
                if deleted:
                    log_message(
                        f"Очистка output при старте: удалено {deleted} файлов, "
                        f"освобождено {freed // (1024 * 1024)} МБ",
                        level="STARTUP"
                    )
            except Exception as e:
                log_message(f"Ошибка очистки output: {e}", level="WARNING")

            self.stage_changed.emit("server_start")
            log_startup_stage("Запуск XTTS сервера")

            # ============ ВАЖНО: команда запуска сервера ============
            frozen = is_frozen()
            print(f"[ServerStartupWorker] is_frozen()={frozen}")
            
            if frozen:
                server_cmd = [sys.executable, "--server"]
            else:
                server_cmd = [sys.executable, "-m", "xtts_api_server"]

            if has_cuda:
                server_cmd += ["--device", "cuda"]
                self.stage_text_changed.emit("Запуск XTTS сервера (CUDA)...")
            else:
                self.stage_text_changed.emit("Запуск XTTS сервера (CPU)...")
            # ========================================================

            kwargs = {}
            env = os.environ.copy()

            if not has_cuda:
                # Ограничиваем потоки CPU-синтеза: меньше троттлинга,
                # система остаётся отзывчивой, скорость почти та же
                threads = str(self.cpu_threads)
                env["OMP_NUM_THREADS"] = threads
                env["MKL_NUM_THREADS"] = threads
                env["NUMEXPR_NUM_THREADS"] = threads

            if sys.platform == "win32":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP
                if not has_cuda:
                    flags |= 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
                kwargs["creationflags"] = flags

            log_message(f"Команда запуска сервера: {' '.join(server_cmd)}", level="STARTUP")
            server_process = subprocess.Popen(server_cmd, text=True, env=env, **kwargs)
            log_message(f"Сервер запущен (PID {server_process.pid})", level="STARTUP")
            time.sleep(0.3)

            self.stage_changed.emit("model_load")
            log_startup_stage("Ожидание загрузки модели")
            start_time = time.time()
            while time.time() - start_time < HEALTH_CHECK_TIMEOUT:
                try:
                    response = requests.get(f"{XTTS_HOST}/speakers", timeout=2)
                    if response.status_code == 200:
                        elapsed = int(time.time() - start_time)
                        log_message(f"Сервер готов за {elapsed} секунд", level="STARTUP")
                        self.stage_text_changed.emit("Сервер готов!")
                        self.server_ready.emit(server_process)
                        return
                except requests.exceptions.RequestException:
                    pass
                elapsed = int(time.time() - start_time)
                self.stage_text_changed.emit(f"Загрузка модели голосов... ({elapsed} сек)")
                time.sleep(HEALTH_CHECK_INTERVAL)

            error_msg = f"Сервер не запустился за {HEALTH_CHECK_TIMEOUT} секунд"
            log_error(TimeoutError, error_msg, context="Ожидание готовности сервера")
            self.error_occurred.emit(error_msg)

        except Exception as e:
            log_error(type(e), e, tb=e.__traceback__, context="Запуск XTTS сервера")
            self.error_occurred.emit(f"Ошибка запуска сервера: {str(e)}")


def main():
    if is_frozen():
        os.chdir(os.path.dirname(sys.executable))

    faulthandler.enable()
    install_exception_hooks()
    log_message("=" * 50, level="STARTUP")
    log_message("Запуск Twitch TTS Reader", level="STARTUP")

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)

    theme_str = "auto"
    if os.path.exists("config.json"):
        try:
            with open("config.json", 'r', encoding='utf-8') as f:
                theme_str = json.load(f).get("theme", "auto")
        except Exception:
            pass

    config = {}
    if os.path.exists("config.json"):
        try:
            with open("config.json", 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception:
            config = {}
    theme_str = config.get("theme", "auto")

    # Потоки CPU-синтеза: по умолчанию половина логических ядер
    auto_threads = max(4, (os.cpu_count() or 8) // 2)
    cpu_threads = int(config.get("cpu_threads", 0) or auto_threads)

    from qfluentwidgets import setTheme, Theme
    if theme_str == "dark":
        setTheme(Theme.DARK)
    elif theme_str == "light":
        setTheme(Theme.LIGHT)
    else:
        setTheme(Theme.AUTO)

    from ui.splash_window import SplashWindow
    splash = SplashWindow()
    splash.show()
    app.processEvents()

    server_process_holder = {"process": None}
    splash_closed = {"value": False}

    def close_splash():
        if not splash_closed["value"]:
            splash_closed["value"] = True
            try:
                if hasattr(splash, 'progress_ring'):
                    splash.progress_ring.hide()
                    if hasattr(splash.progress_ring, 'stop'):
                        splash.progress_ring.stop()
                splash.close()
                splash.deleteLater()
                app.processEvents()
            except Exception:
                pass

    def cleanup_server():
        cleanup_server_process(server_process_holder["process"])

    atexit.register(cleanup_server)

    def show_error_and_quit(error_message):
        try:
            close_splash()
        except Exception:
            pass
        try:
            cleanup_server()
        except Exception:
            pass
        log_message("Приложение завершено из-за ошибки", level="SHUTDOWN")
        for _ in range(5):
            app.processEvents()
            time.sleep(0.05)
        try:
            show_error_dialog("Критическая ошибка", str(error_message))
        except Exception:
            pass
        app.quit()

    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        log_error(exc_type, exc_value, tb=exc_traceback, context="Необработанное исключение")
        try:
            close_splash()
            app.processEvents()
        except Exception:
            pass
        try:
            cleanup_server()
        except Exception:
            pass
        try:
            show_error_and_quit(f"{exc_type.__name__}: {exc_value}")
        except Exception:
            pass
        try:
            app.quit()
        except Exception:
            pass

    sys.excepthook = handle_exception

    worker = ServerStartupWorker(cpu_threads=cpu_threads)

    def on_stage_changed(stage_key):
        splash.set_stage(stage_key)
        app.processEvents()

    def on_stage_text_changed(text):
        splash.set_stage_text(text)
        app.processEvents()

    def on_server_ready(server_process):
        server_process_holder["process"] = server_process
        splash.set_stage("ui")
        splash.set_stage_text("Запуск интерфейса...")
        log_startup_stage("Запуск интерфейса")
        app.processEvents()
        try:
            root = app_root()
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from ui.main_window import MainWindow
            window = MainWindow()
            window._cleanup_callback = cleanup_server
            app.aboutToQuit.connect(cleanup_server)
            close_splash()
            window.show()
            log_message("Приложение запущено успешно", level="STARTUP")
        except Exception as e:
            log_error(type(e), e, tb=e.__traceback__, context="Создание MainWindow")
            error_msg = str(e)
            error_type_name = type(e).__name__

            def _show_error():
                show_error_and_quit(
                    f"Не удалось запустить приложение.\n\n"
                    f"Тип ошибки: {error_type_name}\n"
                    f"Сообщение: {error_msg}\n\n"
                    f"Подробности в error_logs"
                )
            QTimer.singleShot(100, _show_error)

    def on_error(error_message):
        log_error(RuntimeError, error_message, context="Запуск сервера")
        splash.set_error(error_message)
        app.processEvents()
        error_msg = error_message
        QTimer.singleShot(500, lambda msg=error_msg: show_error_and_quit(msg))

    worker.stage_changed.connect(on_stage_changed)
    worker.stage_text_changed.connect(on_stage_text_changed)
    worker.server_ready.connect(on_server_ready)
    worker.error_occurred.connect(on_error)
    worker.start()

    def _periodic_cleanup():
        try:
            deleted, freed = clean_output_folder(str(app_root()))
            if deleted:
                log_message(
                    f"Очистка output: удалено {deleted} файлов, "
                    f"освобождено {freed // (1024 * 1024)} МБ",
                    level="INFO"
                )
        except Exception as e:
            log_message(f"Ошибка очистки output: {e}", level="WARNING")

    cleanup_timer = QTimer()
    cleanup_timer.timeout.connect(_periodic_cleanup)
    cleanup_timer.start(CLEANUP_INTERVAL_SEC * 1000)

    exit_code = app.exec_()
    log_message("Приложение завершено", level="SHUTDOWN")
    os._exit(exit_code if exit_code is not None else 0)


def run_server_mode():
    """Релизный режим: запустить XTTS сервер в этом же exe."""
    import inspect
    _original_getsource = inspect.getsource
    
    def _safe_getsource(obj):
        try:
            return _original_getsource(obj)
        except OSError:
            return ""
    
    inspect.getsource = _safe_getsource
    
    import runpy
    sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:] if a != "--server"]
    runpy.run_module("xtts_api_server", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()

    # Серверный режим — ДО main()
    if is_frozen() and "--server" in sys.argv:
        run_server_mode()
        sys.exit(0)

    try:
        main()
    except KeyboardInterrupt:
        os._exit(0)
    except Exception as e:
        log_error(type(e), e, tb=e.__traceback__, context="Главная функция launcher")
        os._exit(1)