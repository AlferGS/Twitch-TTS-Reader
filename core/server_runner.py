"""
Обёртка для запуска xtts_api_server в DEV-режиме.

Использование:
python -m core.server_runner

Перед запуском сервера применяются:
- режим CPU/CUDA;
- FP16-флаги;
- лимит VRAM.
"""
import sys
import runpy

from core.system_info import apply_server_runtime_settings


def main():
    apply_server_runtime_settings()

    sys.argv = [
        sys.argv[0]
    ] + [
        arg for arg in sys.argv[1:] if arg != "--server"
    ]

    runpy.run_module("xtts_api_server", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()