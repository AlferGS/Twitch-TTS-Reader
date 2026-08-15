"""
Информация о режиме работы и памяти (CPU/GPU).
"""
import os
import sys
import json
import subprocess
from pathlib import Path

RUNTIME_STATE_FILENAME = "runtime_state.json"

DEVICE_MODE_AUTO = "auto"
DEVICE_MODE_CPU = "cpu"
DEVICE_MODE_CUDA = "cuda"


def is_frozen():
    """Запущены ли мы как PyInstaller exe."""
    return hasattr(sys, "frozen") and getattr(sys, "frozen", False)


def app_root():
    """Корневая папка приложения."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _run_command(cmd, timeout=3):
    """
    Безопасно выполнить команду и вернуть (returncode, stdout).
    """
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "timeout": timeout,
        "text": True,
        "encoding": "utf-8",
        "errors": "ignore",
    }

    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000

    try:
        completed = subprocess.run(cmd, **kwargs)
        return completed.returncode, completed.stdout or ""
    except OSError:
        return -1, ""
    except subprocess.TimeoutExpired:
        return -2, ""
    except Exception:
        return -3, ""

def detect_cuda_available(timeout=3):
    """Проверка NVIDIA GPU через nvidia-smi."""
    code, _ = _run_command(["nvidia-smi", "-L"], timeout=timeout)
    return code == 0


def query_gpu_memory_mb():
    """Возвращает память GPU в МБ."""
    code, output = _run_command(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
        timeout=3,
    )

    if code != 0 or not output.strip():
        return None

    used_total = 0.0
    total_total = 0.0

    for line in output.strip().splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2:
            continue

        try:
            used = float(parts[0])
            total = float(parts[1])
        except ValueError:
            continue

        used_total += used
        total_total += total

    if total_total <= 0:
        return None

    return {
        "used_mb": int(used_total),
        "total_mb": int(total_total),
        "available_mb": int(max(0, total_total - used_total)),
        "percent": min(100, int((used_total / total_total) * 100)),
    }


def query_cpu_memory_mb():
    """Возвращает память RAM через psutil."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        return {
            "used_mb": int(mem.used // (1024 * 1024)),
            "total_mb": int(mem.total // (1024 * 1024)),
            "available_mb": int(mem.available // (1024 * 1024)),
            "percent": int(mem.percent),
        }
    except Exception:
        return None


def resolve_runtime_mode(config, cuda_available=None):
    """Определить режим сервера."""
    choice = str((config or {}).get("device_mode", DEVICE_MODE_AUTO)).lower()

    if choice == DEVICE_MODE_CPU:
        return DEVICE_MODE_CPU

    if cuda_available is None:
        cuda_available = detect_cuda_available()

    if choice == DEVICE_MODE_CUDA:
        return DEVICE_MODE_CUDA if cuda_available else DEVICE_MODE_CPU

    return DEVICE_MODE_CUDA if cuda_available else DEVICE_MODE_CPU


def read_runtime_state():
    """Прочитать runtime_state.json."""
    path = app_root() / RUNTIME_STATE_FILENAME
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def write_runtime_state(state):
    """Записать runtime_state.json."""
    path = app_root() / RUNTIME_STATE_FILENAME
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def load_basic_config():
    """Прочитать config.json."""
    path = app_root() / "config.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_expected_mode(config):
    """Вернуть фактический режим."""
    state = read_runtime_state()
    state_mode = state.get("device_mode")
    if state_mode in (DEVICE_MODE_CPU, DEVICE_MODE_CUDA):
        return state_mode
    return resolve_runtime_mode(config or {})


def get_memory_info(mode):
    """Получить память для режима."""
    if mode == DEVICE_MODE_CUDA:
        info = query_gpu_memory_mb()
        if info is not None:
            info["mode"] = DEVICE_MODE_CUDA
            return info

    info = query_cpu_memory_mb()
    if info is None:
        return {
            "mode": DEVICE_MODE_CPU,
            "used_mb": 0,
            "total_mb": 0,
            "available_mb": 0,
            "percent": 0,
            "error": "memory unavailable",
        }
    info["mode"] = DEVICE_MODE_CPU
    return info


def get_memory_info_for_config(config):
    """Получить память для текущего режима."""
    mode = get_expected_mode(config or {})
    return get_memory_info(mode)