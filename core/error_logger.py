"""
Модуль логирования ошибок в отдельные файлы в папке error_logs/.
Каждая ошибка создаёт новый файл error_logs/error_YYYY-MM-DD_HH-MM-SS.log.
Работает и в обычной сборке, и в PyInstaller exe.
"""
import os
import sys
import traceback
import threading
import glob
from datetime import datetime

_log_lock = threading.Lock()
MAX_LOG_FILES = 50


def get_app_dir():
    """
    Директория приложения.
    Работает и при запуске как скрипт, и как exe (PyInstaller).
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_logs_dir():
    """Путь к папке с логами ошибок."""
    return os.path.join(get_app_dir(), "error_logs")


def get_log_path():
    """
    Путь к последнему созданному файлу лога.
    Используется в диалоге ошибки для показа пользователю.
    Если логов ещё нет — возвращает None.
    """
    logs_dir = get_logs_dir()
    if not os.path.exists(logs_dir):
        return None
    log_files = glob.glob(os.path.join(logs_dir, "error_*.log"))
    if not log_files:
        return None
    return max(log_files)


def _cleanup_old_logs():
    """Удалить старые логи, оставив только последние MAX_LOG_FILES."""
    logs_dir = get_logs_dir()
    if not os.path.exists(logs_dir):
        return
    log_files = sorted(glob.glob(os.path.join(logs_dir, "error_*.log")))
    if len(log_files) > MAX_LOG_FILES:
        for file_path in log_files[:-MAX_LOG_FILES]:
            try:
                os.remove(file_path)
            except Exception:
                pass


def _write_log(content):
    """Потокобезопасная запись в НОВЫЙ лог-файл."""
    try:
        with _log_lock:
            logs_dir = get_logs_dir()
            if not os.path.exists(logs_dir):
                os.makedirs(logs_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            log_file = os.path.join(logs_dir, f"error_{timestamp}.log")

            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(content)

            _cleanup_old_logs()
            return log_file
    except Exception as write_error:
        print(f"[ErrorLogger] Не удалось записать в лог: {write_error}")
        print(content)
        return None


def _get_system_info():
    """Собрать информацию о системе для диагностики."""
    lines = [
        f"Python: {sys.version}",
        f"Platform: {sys.platform}",
        f"Executable: {sys.executable}",
        f"Frozen (exe): {getattr(sys, 'frozen', False)}",
    ]
    try:
        import torch
        if torch.cuda.is_available():
            lines.append(f"GPU: {torch.cuda.get_device_name(0)}")
            lines.append(f"CUDA: {torch.version.cuda}")
        else:
            lines.append("GPU: CPU режим (CUDA недоступна)")
    except ImportError:
        lines.append("GPU: torch не установлен")
    try:
        import psutil
        mem = psutil.virtual_memory()
        lines.append(
            f"RAM: {mem.total // (1024**2)} MB "
            f"(свободно {mem.available // (1024**2)} MB)"
        )
    except ImportError:
        pass
    return "\n".join(lines)


def log_error(error_type, error_value, tb=None, context=None):
    """
    Записать критическую ошибку в отдельный файл.
    Возвращает путь к созданному файлу.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = "=" * 70
    lines = [f"[{timestamp}] КРИТИЧЕСКАЯ ОШИБКА", sep]

    if context:
        lines.append(f"Контекст: {context}")

    lines.append("\n--- Информация о системе ---")
    lines.append(_get_system_info())

    lines.append("\n--- Traceback ---")
    if tb is not None:
        lines.append("".join(traceback.format_exception(error_type, error_value, tb)))
    else:
        lines.append(f"{getattr(error_type, '__name__', str(error_type))}: {error_value}")
    lines.append(sep)

    log_file = _write_log("\n".join(lines) + "\n")
    if log_file:
        print(f"\n[CRITICAL] Ошибка записана в {log_file}")
        print(f"[CRITICAL] {getattr(error_type, '__name__', 'Error')}: {error_value}")
    return log_file


def log_message(message, level="INFO"):
    """Информационное сообщение в консоль (НЕ в файл)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def log_startup_stage(stage_name):
    """Записать стадию запуска (только в консоль)."""
    log_message(f"Стадия запуска: {stage_name}", level="STARTUP")


def log_unhandled_exception(exc_type, exc_value, exc_traceback):
    """Обработчик для sys.excepthook."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    log_error(
        exc_type, exc_value,
        tb=exc_traceback,
        context="Необработанное исключение в главном потоке"
    )


def log_thread_exception(args):
    """Обработчик для threading.excepthook (Python 3.8+)."""
    if args.exc_type is SystemExit:
        return
    log_error(
        args.exc_type, args.exc_value,
        tb=args.exc_traceback,
        context=f"Исключение в потоке: {args.thread.name if args.thread else 'unknown'}"
    )


def install_exception_hooks():
    """Установить глобальные обработчики исключений."""
    sys.excepthook = log_unhandled_exception
    if hasattr(threading, 'excepthook'):
        threading.excepthook = log_thread_exception
    log_message("Обработчики исключений установлены", level="STARTUP")


_DEBUG_LOG_NAME = "debug.log"
_DEBUG_MAX_BYTES = 5 * 1024 * 1024
_debug_lock = threading.Lock()


def get_debug_log_path():
    """Путь к текущему debug-логу."""
    return os.path.join(get_logs_dir(), _DEBUG_LOG_NAME)


def _rotate_debug_log(path):
    """
    Простая ротация:
    debug.log -> debug.log.1
    """
    try:
        if os.path.exists(path) and os.path.getsize(path) > _DEBUG_MAX_BYTES:
            backup = path + ".1"

            if os.path.exists(backup):
                os.remove(backup)

            os.replace(path, backup)
    except OSError:
        # Если ротация не удалась, пытаемся продолжать писать в текущий файл.
        return


def _debug_field_value(value):
    """
    Привести значение поля к безопасной короткой строке.
    Без переносов строк и без бесконечных значений.
    """
    try:
        text = str(value)
    except Exception:
        return "<unprintable>"

    text = text.replace("\n", " ").replace("\r", " ").replace('"', "'")

    if len(text) > 200:
        text = text[:197] + "..."

    return text


def log_debug(source, message, **fields):
    """
    Короткая техническая запись в error_logs/debug.log.

    Пример:
    [2026-06-16 12:00:00.123] [TTS] gen ready | item_id="42" ms="812" status="200"
    """
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        if fields:
            field_text = " ".join(
                f'{key}="{_debug_field_value(fields[key])}"'
                for key in sorted(fields)
            )
            line = f"[{timestamp}] [{source}] {message} | {field_text}"
        else:
            line = f"[{timestamp}] [{source}] {message}"

        with _debug_lock:
            logs_dir = get_logs_dir()
            os.makedirs(logs_dir, exist_ok=True)

            path = get_debug_log_path()
            _rotate_debug_log(path)

            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    except Exception as exc:
        # Логгер не должен ронять приложение.
        # Если не смогли записать в файл, пытаемся сообщить в stderr.
        try:
            if sys.stderr is not None:
                print(
                    f"[debug-log failed] source={source} "
                    f"error={type(exc).__name__}: {exc}",
                    file=sys.stderr
                )
        except Exception:
            return

        return