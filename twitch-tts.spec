# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

datas = []
binaries = []
hiddenimports = [
    # uvicorn
    'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on',
    # сервер
    'xtts_api_server', 'xtts_api_server.server', 'xtts_api_server.tts_funcs',
    'fastapi', 'starlette', 'pydantic', 'pydantic_core', 'loguru', 'anyio',
    # tts/torch
    'TTS', 'torch', 'torchaudio', 'transformers', 'tokenizers', 'safetensors',
    'huggingface_hub', 'scipy', 'librosa', 'numba', 'llvmlite',
    'sklearn', 'sklearn.utils._typedefs', 'joblib', 'threadpoolctl',
    'soundfile', 'sounddevice', 'soxr', 'pyrubberband',
    'numpy', 'requests', 'psutil',
    # наши модули
    'core', 'core.error_logger', 'core.message_queue',
    'core.tts_service', 'core.twitch_chat',
    'ui', 'ui.chat_page', 'ui.main_window', 'ui.settings_window', 'ui.splash_window',
]

# ============ ВСЕ фонемизаторы TTS (полный список) ============
# TTS при запуске импортирует ВСЕ эти библиотеки, даже если используются только для RU
ALL_PHONEMIZERS = [
    # GUI + сервер
    'qfluentwidgets', 'xtts_api_server', 'TTS',
    # Английский
    'gruut', 'gruut_lang_en', 'gruut_lang_de', 'gruut_lang_fr', 'gruut_lang_es',
    'g2p-en', 'inflect',
    # Японский
    'MeCab', 'unidic', 'unidic_lite', 'sudachipy',
    # Корейский
    'jamo', 'hangul_romanize', 'g2p-korean',
    # Китайский
    'pypinyin', 'jieba',
    # Другие языки
    'anyascii', 'bangla', 'num2words', 'transliterate', 'g2p-mi', 'g2p-mk',
    # Трансдьюсеры
    'pynini',
]

print(f"[Spec] Собираем {len(ALL_PHONEMIZERS)} пакетов через collect_all...")
for pkg in ALL_PHONEMIZERS:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
        if d or b:
            print(f"  ✓ {pkg}: {len(d)} data-файлов, {len(b)} бинарников")
    except Exception as e:
        print(f"  · {pkg}: пропущен ({type(e).__name__})")

# Метаданные
for meta in ['transformers', 'TTS', 'torch', 'fastapi', 'starlette', 'uvicorn',
             'pydantic', 'numpy', 'tokenizers', 'safetensors', 'xtts-api-server',
             'gruut', 'inflect', 'g2p-en', 'jamo']:
    try:
        datas += copy_metadata(meta)
    except Exception:
        pass

a = Analysis(
    ['launcher.py'],
    pathex=['D:\\Programs\\Projects\\Projects Python\\twitch-tts-reader'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TwitchTTSReader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='TwitchTTSReader',
)