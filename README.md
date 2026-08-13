# Twitch TTS Reader

Читалка Twitch-чата с синтезом речи через XTTS v2. Поддерживает кастомные голоса, ускорение речи, ответы на сообщения (reply), несколько языков и тем оформления.

## 🎯 Возможности

- 🎙️ Синтез речи через XTTS v2 (CPU и CUDA)
- 🎨 Кастомные голоса из WAV-файлов
- ⚡ Ускорение речи без изменения тона (rubberband)
- 💬 Поддержка Twitch reply (ответов на сообщения)
- 🌗 Светлая / тёмная / авто тема
- 🔇 Переключатель озвучки
- 🗑️ Автоматическая очистка кэша аудио

## 📋 Системные требования

- Windows 10/11 (64-bit)
- Python 3.11
- 8+ ГБ RAM (рекомендуется 16 ГБ)
- NVIDIA GPU с драйвером ≥ 525 (опционально, для CUDA-ускорения)
- 4+ ГБ свободного места на диске

## 🚀 Установка

### 1. Клонирование репозитория

```bash
git clone https://github.com/AlferGS/twitch-tts-reader.git
cd twitch-tts-reader
```

### 2. Создание виртуального окружения

```powershell
python -m venv venv
.\venv\Scripts\Activate
```

### 3. Установка torch

**Для CPU:**

```powershell
pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu
```

**Для CUDA (NVIDIA GPU):**

```powershell
pip install torch==2.5.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cu121
```

### 4. Установка остальных зависимостей

```powershell
pip install -r requirements.txt
```

### 5. Скачивание модели XTTS v2

Модель весит 1.8 ГБ и не включена в репозиторий. Скачайте её отдельно:

1. Перейдите на [Hugging Face: coqui/XTTS-v2](https://huggingface.co/coqui/XTTS-v2)
2. Примите лицензию (нужен аккаунт HF)
3. Скачайте файлы модели в папку `xtts_models/v2.0.2/`

Модель будет скачана автоматически при первом запуске `xtts-api-server`, если у вас есть доступ к Hugging Face Hub.

### 6. Установка Rubberband (для ускорения речи)

1. Скачайте [rubberband для Windows](https://breakfastquay.com/rubberband/)
2. Распакуйте в папку `rubberband/` в корне проекта
3. Убедитесь что `rubberband/rubberband.exe` существует

### 7. Добавление голосов

Поместите WAV-файлы голосов (6–10 секунд, mono, 22050+ Hz) в папку `speakers/`.

### 8. Настройка

Скопируйте шаблон конфига:

```powershell
Copy-Item config.example.json config.json
```

Отредактируйте `config.json` — укажите ваш Twitch-канал и настройки.

## ▶️ Запуск

```powershell
python launcher.py
```

При первом запуске XTTS-сервер автоматически скачает модель (~1.8 ГБ), если она ещё не загружена. Загрузка может занять несколько минут в зависимости от скорости интернета.

## 📦 Сборка в exe (опционально)

```powershell
pip install pyinstaller
pyinstaller twitch-tts.spec --noconfirm

# Копирование внешних папок в релиз
Copy-Item -Path xtts_models -Destination dist\TwitchTTSReader\xtts_models -Recurse
Copy-Item -Path speakers   -Destination dist\TwitchTTSReader\speakers   -Recurse
Copy-Item -Path rubberband -Destination dist\TwitchTTSReader\rubberband -Recurse
Copy-Item config.example.json dist\TwitchTTSReader\config.json
```

Готовый релиз будет в папке `dist\TwitchTTSReader\`. Для распространения упакуйте её в zip-архив.

## 🗂️ Структура проекта

```
twitch-tts-reader/
├── launcher.py              # Точка входа, запуск XTTS-сервера и UI
├── requirements.txt         # Зависимости Python
├── twitch-tts.spec          # Spec-файл PyInstaller
├── config.example.json      # Шаблон конфигурации
├── README.md
├── .gitignore
├── core/                    # Бизнес-логика
│   ├── error_logger.py
│   ├── message_queue.py
│   ├── tts_service.py
│   └── twitch_chat.py
├── ui/                      # Интерфейс
│   ├── main_window.py
│   ├── chat_page.py
│   ├── settings_window.py
│   └── splash_window.py
├── xtts_models/             # Модель XTTS (не в git, скачивается отдельно)
├── speakers/                # WAV-файлы голосов
├── rubberband/              # Rubberband CLI (нужен для ускорения)
├── output/                  # Кэш сгенерированного аудио (runtime)
└── error_logs/              # Логи ошибок (runtime)
```

## ⚙️ Конфигурация (`config.json`)

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|--------------|----------|
| `theme` | string | `"auto"` | `"light"` / `"dark"` / `"auto"` |
| `channel_url` | string | — | URL Twitch-канала |
| `voice` | string | `"anime_girl"` | Имя WAV-файла из `speakers/` без расширения |
| `cpu_threads` | int | `0` | Потоки CPU-синтеза (0 = половина ядер) |
| `max_queue_size` | int | `10` | Макс. размер очереди сообщений |

## 🛠️ Разработка

### Требования к голосам

- Формат: WAV (16-bit PCM, 22050 Hz или выше)
- Длительность: 6–10 секунд чистой речи одного спикера
- Без музыки, шумов, эха, фонового разговора
- Имя файла = идентификатор голоса в настройках

### Оптимизация CPU

Для снижения нагрузки на процессор в CPU-режиме:

- Установите `cpu_threads` в `config.json` (по умолчанию половина ядер) (в разработке)
- Уменьшите `max_queue_size` для предотвращения "догонялок" при всплесках сообщений (в разработке)
- На машинах с NVIDIA GPU приложение автоматически переключится на CUDA

## 📜 Лицензия

Проект распространяется под лицензией MIT. См. файл [LICENSE](LICENSE).

## 🙏 Благодарности

- [coqui-ai/TTS](https://github.com/coqui-ai/TTS) — XTTS v2
- [daswer123/xtts-api-server](https://github.com/daswer123/xtts-api-server) — API-сервер
- [zhiyiYo/PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) — UI-компоненты
- [breakfastquay/rubberband](https://breakfastquay.com/rubberband/) — time-stretch аудио

## 📝 Roadmap

- [ ] Поддержка нескольких каналов одновременно
- [ ] Настройка префиксов через UI
- [ ] Экспорт/импорт настроек
- [ ] Поддержка стриминг-режима с чанками
- [ ] Инсталлятор Windows (Inno Setup)