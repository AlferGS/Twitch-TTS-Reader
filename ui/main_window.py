"""
Главное окно приложения Twitch TTS Reader.
Все страницы (чат + настройки) в одном окне через навигацию FluentWindow.
"""
import os
import json
import random
EASTER_MAX_SPEAK_CHARS = 200
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import QWidget, QVBoxLayout
from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon,
    PrimaryPushButton, InfoBar, InfoBarPosition,
    setTheme, Theme
)

from ui.chat_page import ChatPage
from ui.settings_window import (
    GeneralSettingsPage, VoicesSettingsPage, UsersSettingsPage,
    IgnoreWordsPage, IgnoreUsersPage, PerformanceSettingsPage
)
from core.twitch_chat import TwitchChatReader, MAX_SPEAK_CHARS
from core.tts_service import TTSService
from core.message_queue import MessageQueue
from core.error_logger import log_debug

class MainWindow(FluentWindow):
    """Главное окно приложения. Все страницы в одном окне."""

    speech_started = pyqtSignal(int)
    speech_ended = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.config = self._load_config()
        self.message_queue = MessageQueue()
        self.tts_service = TTSService()
        self.twitch_chat = None
        self.tts_enabled = True
        self.max_queue_size = self.config.get("max_queue_size", 10)
        self.tts_service.set_output_device(self.config.get("audio_device", ""))

        self.speech_started.connect(self._do_highlight)
        self.speech_ended.connect(self._do_unhighlight)

        self._init_window()
        self._init_pages()
        self._init_navigation()
        self._connect_signals()
        self._apply_theme()

        self.process_timer = QTimer(self)
        self.process_timer.timeout.connect(self._process_message_queue)
        self.process_timer.start(500)
        log_debug(
            "APP",
            "main window init",
            tts_enabled=self.tts_enabled,
            max_queue_size=self.max_queue_size,
            message_limit=self.config.get("message_limit", 100),
            auto_start=self.config.get("auto_start", False),
        )

        if self.config.get("channel_url") and self.config.get("auto_start", False):
            QTimer.singleShot(500, self.start_chat)

    def closeEvent(self, event):
        """Остановка всех потоков и сервисов перед закрытием."""
        log_debug("APP", "close_event")

        self.process_timer.stop()

        if self.twitch_chat:
            self.twitch_chat.stop()
            self.twitch_chat.wait(2000)
            self.twitch_chat = None

        self.tts_service.stop()

        cleared = self.message_queue.clear()
        if cleared > 0:
            log_debug("APP", "queue cleared on close", cleared=cleared)

        if hasattr(self, '_cleanup_callback') and self._cleanup_callback:
            try:
                self._cleanup_callback()
            except Exception as e:
                log_debug(
                    "APP",
                    "cleanup callback error",
                    error=f"{type(e).__name__}: {e}"[:200],
                )

        event.accept()

        from PyQt5.QtWidgets import QApplication
        QTimer.singleShot(0, QApplication.quit)

    def _load_config(self):
        config_path = "config.json"

        default_config = {
            "channel_url": "",
            "default_voice": "anime_girl",
            "use_prefixes": True,
            "prefix_mappings": {"!m": "game_hero", "!w": "anime_girl"},
            "auto_start": False,
            "user_mappings": {},
            "user_colors": {},
            "ignore_words": [
                "Kappa", "LUL", "PogChamp", "OMEGALUL",
                "monkaW", "monkaS", "PepeLaugh", "Sadge",
                "FeelsGoodMan", "FeelsBadMan"
            ],
            "ignore_users": ["nightbot", "streamelements", "moobot", "wizebot"],
            "message_limit": 100,
            "easter_egg_enabled": True,
            "easter_egg_chance": 0.03,
            "easter_egg_mode": "auto",
            "easter_egg_prefixes": [
                "однажды, ",
            ],
            "easter_egg_postfixes": [
                ", блять",
                ", мяу",
                ". сыкс-сэээвн!",
                ", но это уже совсем другая история ...",
                ", хотя как, бы нихуя себе!"
            ],
            "max_queue_size": 10,
            "audio_device": "",
            "theme": "auto",
            "device_mode": "auto",
            "use_lowvram": True,
        }

        if not os.path.exists(config_path):
            log_debug("APP", "config missing, create default")
            self._write_config(default_config)
            return default_config

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()

                if not content:
                    log_debug("APP", "config empty, use default")
                    return default_config

                config = json.loads(content)
                migrated_config = self._migrate_config(config, default_config)

                log_debug(
                    "APP",
                    "config loaded",
                    has_channel=bool(migrated_config.get("channel_url")),
                    auto_start=migrated_config.get("auto_start", False),
                    message_limit=migrated_config.get("message_limit", 100),
                    max_queue_size=migrated_config.get("max_queue_size", 10),
                    use_prefixes=migrated_config.get("use_prefixes", True),
                )

                return migrated_config

        except Exception as e:
            log_debug(
                "APP",
                "config load error, use default",
                error=f"{type(e).__name__}: {e}"[:200],
            )
            self._write_config(default_config)
            return default_config


    def _migrate_config(self, config, default_config):
        if "male_prefix" in config and "prefix_mappings" not in config:
            prefix_mappings = {}
            male_prefix = config.get("male_prefix", "!m")
            female_prefix = config.get("female_prefix", "!w")
            if male_prefix:
                prefix_mappings[male_prefix] = config.get("male_voice", "game_hero")
            if female_prefix:
                prefix_mappings[female_prefix] = config.get("female_voice", "anime_girl")
            config["prefix_mappings"] = prefix_mappings
            for key in ("male_prefix", "female_prefix", "male_voice", "female_voice"):
                config.pop(key, None)
        for key, value in default_config.items():
            if key not in config:
                config[key] = value
        return config

    def _save_config(self):
        self._write_config(self.config)

    def _write_config(self, config):
        try:
            with open("config.json", 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log_debug(
                "APP",
                "config write error",
                error=f"{type(e).__name__}: {e}"[:200],
            )
            raise

    def _init_window(self):
        self.setWindowTitle("Twitch TTS Reader")
        self.setMinimumWidth(500)
        self.setMinimumHeight(700)
        self.resize(600, 800)

    def _init_pages(self):
        message_limit = self.config.get("message_limit", 100)
        user_colors = self.config.get("user_colors", {})
        self.chat_page = ChatPage(message_limit=message_limit, user_colors=user_colors)
        self.chat_page.setObjectName("chatPage")

        self.general_settings = GeneralSettingsPage(self.config, self._save_config)
        self.voices_settings = VoicesSettingsPage(self.config, self._save_config)
        self.users_settings = UsersSettingsPage(self.config, self._save_config)
        self.ignore_words_settings = IgnoreWordsPage(self.config, self._save_config)
        self.ignore_users_settings = IgnoreUsersPage(self.config, self._save_config)
        self.performance_settings = PerformanceSettingsPage(self.config, self._save_config)

        self.tts_service.on_speech_start = self._on_speech_start
        self.tts_service.on_speech_end = self._on_speech_end

    def _init_navigation(self):
        self.addSubInterface(self.chat_page, FluentIcon.MESSAGE, "Чат", NavigationItemPosition.TOP)
        self.addSubInterface(self.general_settings, FluentIcon.SETTING, "Общие", NavigationItemPosition.TOP)
        self.addSubInterface(self.voices_settings, FluentIcon.MICROPHONE, "Голоса", NavigationItemPosition.TOP)
        self.addSubInterface(self.users_settings, FluentIcon.PEOPLE, "Привязки", NavigationItemPosition.TOP)
        self.addSubInterface(self.ignore_words_settings, FluentIcon.CANCEL, "Стоп-слова", NavigationItemPosition.TOP)
        self.addSubInterface(self.ignore_users_settings, FluentIcon.ROBOT, "Боты", NavigationItemPosition.TOP)

        self.addSubInterface(
            self.performance_settings,
            FluentIcon.SETTING,
            "Производительность",
            NavigationItemPosition.TOP
        )

        save_page = QWidget()
        save_page.setObjectName("saveAllPage")
        save_layout = QVBoxLayout(save_page)
        save_layout.setContentsMargins(30, 30, 30, 30)
        save_layout.addStretch()
        save_btn = PrimaryPushButton("💾 Сохранить все настройки")
        save_btn.setFixedHeight(50)
        save_btn.clicked.connect(self._save_all_settings)
        save_layout.addWidget(save_btn)
        save_layout.addStretch()
        self.addSubInterface(save_page, FluentIcon.SAVE, "Сохранить", NavigationItemPosition.BOTTOM)

        self.general_settings.load(self.config)
        self.voices_settings.load(self.config)
        self.users_settings.load(self.config)
        self.ignore_words_settings.load(self.config)
        self.ignore_users_settings.load(self.config)
        self.performance_settings.load(self.config)

    def _connect_signals(self):
        self.chat_page.start_requested.connect(self.start_chat)
        self.chat_page.stop_requested.connect(self.stop_chat)
        self.chat_page.volume_changed.connect(self.tts_service.set_volume)
        self.chat_page.speed_changed.connect(self.tts_service.set_speed)
        self.chat_page.item_removed.connect(self.tts_service.unregister_item)
        self.chat_page.tts_enabled_changed.connect(self._on_tts_enabled_changed)
        self.general_settings.settings_saved.connect(self._on_any_settings_saved)
        self.voices_settings.settings_saved.connect(self._on_any_settings_saved)
        self.users_settings.settings_saved.connect(self._on_any_settings_saved)
        self.ignore_words_settings.settings_saved.connect(self._on_any_settings_saved)
        self.ignore_users_settings.settings_saved.connect(self._on_any_settings_saved)
        self.performance_settings.settings_saved.connect(self._on_any_settings_saved)

    def _apply_theme(self):
        theme_str = self.config.get("theme", "auto")
        if theme_str == "dark":
            setTheme(Theme.DARK)
        elif theme_str == "light":
            setTheme(Theme.LIGHT)
        else:
            setTheme(Theme.AUTO)
        self.chat_page.refresh_theme()

    def start_chat(self):
        channel_url = self.config.get("channel_url", "")

        if not channel_url:
            log_debug("APP", "start_chat rejected no channel")

            InfoBar.warning(
                title="Настройки не заданы",
                content="Укажите URL канала в настройках",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000
            )
            return

        if self.twitch_chat and self.twitch_chat.is_running():
            log_debug("APP", "start_chat rejected already running")

            InfoBar.info(
                title="Уже запущено",
                content="Чат уже читается",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=2000
            )
            return

        log_debug(
            "APP",
            "start_chat",
            channel_url=channel_url,
            tts_enabled=self.tts_enabled,
        )

        self.chat_page.add_message(
            None,
            f"🚀 Запуск чтения чата: {channel_url}",
            msg_type="system"
        )

        self.twitch_chat = TwitchChatReader(
            channel_url,
            prefix_mappings=self.config.get("prefix_mappings", {}),
            ignore_words=self.config.get("ignore_words", [])
        )

        self.twitch_chat.message_received.connect(self.on_message_received)
        self.twitch_chat.error_occurred.connect(self._on_chat_error)
        self.twitch_chat.start()

        self.chat_page.set_running_state(True)

    def stop_chat(self):
        log_debug("APP", "stop_chat")

        if self.twitch_chat:
            self.twitch_chat.stop()
            self.twitch_chat.wait(2000)
            self.twitch_chat = None

            self.chat_page.add_message(
                None,
                "⏹️ Чтение чата остановлено",
                msg_type="system"
            )

            self.chat_page.set_running_state(False)

        # ВАЖНО: flush() вместо clear_and_stop() —
        # clear_and_stop() ставит _stop_event и навсегда убивает потоки TTS
        self.tts_service.flush()

        cleared = self.message_queue.clear()
        if cleared > 0:
            log_debug("APP", "queue cleared on stop", cleared=cleared)

        self.chat_page.clear_highlight()

    def on_message_received(self, username, display_text, speak_text, prefix, reply_info=None):
        ignore_users = self.config.get("ignore_users", [])

        if username in ignore_users:
            self.chat_page.add_message(
                username,
                display_text,
                msg_type="normal",
                reply_info=reply_info
            )
            return

        msg_id = self.chat_page.add_message(
            username,
            display_text,
            msg_type="normal",
            reply_info=reply_info
        )

        if not speak_text:
            return

        if not self.tts_enabled:
            return

        # Пасхалка применяется только к озвучиваемому тексту.
        # В чате display_text остаётся оригинальным.
        speak_text = self._maybe_apply_easter_egg(speak_text)

        if not speak_text:
            return

        self.tts_service.register_item(msg_id, msg_id)

        self.message_queue.add_message(
            username,
            speak_text,
            prefix,
            msg_id
        )

        self.message_queue.trim_to(self.max_queue_size)

    def _on_chat_error(self, error_message):
        log_debug(
            "APP",
            "chat error",
            error=str(error_message)[:200],
        )

        self.chat_page.add_message(None, error_message, msg_type="error")

        if "Не удалось определить канал" in error_message:
            InfoBar.error(
                title="Ошибка канала",
                content=error_message,
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000
            )
            self.stop_chat()

    def _process_message_queue(self):
        if not self.tts_enabled:
            return

        while not self.message_queue.is_empty():
            message = self.message_queue.get_next()

            if message is None:
                log_debug("APP", "queue counter mismatch empty get")
                break

            username, speak_text, prefix, item_id = message
            voice = self._get_voice_for_user(username, prefix)

            log_debug(
                "APP",
                "dispatch",
                msg_id=item_id,
                user=username,
                voice=voice,
                text_len=len(speak_text),
                prefix=prefix or "",
            )

            self.tts_service.speak(speak_text, voice, item_id)

    def _get_voice_for_user(self, username, prefix):
        if not self.config.get("use_prefixes", True):
            return self.config.get("default_voice", "anime_girl")
        user_mappings = self.config.get("user_mappings", {})
        if username in user_mappings:
            return user_mappings[username]
        if prefix:
            prefix_mappings = self.config.get("prefix_mappings", {})
            if prefix in prefix_mappings:
                return prefix_mappings[prefix]
        return self.config.get("default_voice", "anime_girl")

    @staticmethod
    def _normalize_easter_prefix(prefix):
        """
        Привести префикс пасхалки к нормальному виду.

        Примеры:
        "однажды,"  -> "однажды, "
        "однажды"   -> "однажды "
        "однажды, " -> "однажды, "
        """
        prefix = str(prefix).strip()

        if not prefix:
            return ""

        if prefix.endswith(","):
            return prefix + " "

        if not prefix.endswith(" "):
            return prefix + " "

        return prefix

    @staticmethod
    def _normalize_easter_postfix(postfix):
        """
        Привести постфикс пасхалки к нормальному виду.

        Примеры:
        ",и это начало"       -> ", и это начало"
        ".И тут всё началось" -> ". И тут всё началось"
        "и вот так"           -> "и вот так"
        """
        postfix = str(postfix).strip()

        if not postfix:
            return ""

        if postfix[0] in ",.;:!?":
            if len(postfix) > 1 and not postfix[1].isspace():
                return postfix[0] + " " + postfix[1:]

            return postfix

        return postfix

    @staticmethod
    def _lower_first_letter(text):
        """
        Понизить первую букву в тексте.

        Работает даже если перед буквой есть кавычка, скобка или другой символ.
        """
        for index, char in enumerate(text):
            if char.isalpha():
                return text[:index] + char.lower() + text[index + 1:]

        return text

    @staticmethod
    def _truncate_text_to_limit(text, limit):
        """
        Обрезать текст до лимита, желательно по границе слова.
        """
        if limit <= 0:
            return ""

        text = text.strip()

        if len(text) <= limit:
            return text

        truncated = text[:limit]

        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0]

        return truncated.rstrip()

    def _get_easter_mode(self):
        """Получить режим пасхалки: auto / prefix / postfix."""
        mode = str(self.config.get("easter_egg_mode", "auto")).lower().strip()

        if mode not in ("auto", "prefix", "postfix"):
            return "auto"

        return mode

    def _get_easter_prefixes(self):
        """Получить нормализованный список префиксов из config."""
        raw_prefixes = self.config.get("easter_egg_prefixes", [])

        if isinstance(raw_prefixes, str):
            raw_prefixes = [raw_prefixes]

        if not isinstance(raw_prefixes, list):
            return []

        prefixes = []

        for raw_prefix in raw_prefixes:
            normalized = self._normalize_easter_prefix(raw_prefix)

            if normalized:
                prefixes.append(normalized)

        return prefixes

    def _get_easter_postfixes(self):
        """Получить нормализованный список постфиксов из config."""
        raw_postfixes = self.config.get("easter_egg_postfixes", [])

        if isinstance(raw_postfixes, str):
            raw_postfixes = [raw_postfixes]

        if not isinstance(raw_postfixes, list):
            return []

        postfixes = []

        for raw_postfix in raw_postfixes:
            normalized = self._normalize_easter_postfix(raw_postfix)

            if normalized:
                postfixes.append(normalized)

        return postfixes

    def _apply_easter_prefix(self, text, prefix):
        """Добавить префикс перед сообщением и понизить первую букву."""
        base_text = text.strip()

        if base_text.lower().startswith(prefix.lower()):
            return text

        available_length = EASTER_MAX_SPEAK_CHARS - len(prefix)

        base_text = self._truncate_text_to_limit(
            self._lower_first_letter(base_text),
            available_length
        )

        if not base_text or len(base_text.split()) < 2:
            return text

        return prefix + base_text

    def _apply_easter_postfix(self, text, postfix):
        """Добавить постфикс после сообщения."""
        base_text = text.rstrip()

        if base_text.lower().endswith(postfix.lower()):
            return text

        available_length = EASTER_MAX_SPEAK_CHARS - len(postfix)

        base_text = self._truncate_text_to_limit(base_text, available_length)

        if not base_text or len(base_text.split()) < 2:
            return text

        # Если постфикс начинается с пунктуации, клеим его напрямую:
        # "текст" + ", и это начало"
        if postfix and postfix[0] in ",.;:!?":
            return base_text + postfix

        # Иначе добавляем обычный пробел:
        # "текст" + " " + "и это начало"
        return base_text + " " + postfix

    def _maybe_apply_easter_egg(self, speak_text):
        """
        С шансом из config добавить префикс или постфикс.

        Применяется только к озвучиваемому тексту.
        Текст в чате не меняется.
        """
        if not self.config.get("easter_egg_enabled", False):
            return speak_text
        try:
            chance = float(self.config.get("easter_egg_chance", 0.03) or 0.0)
        except (TypeError, ValueError):
            chance = 0.03

        if chance <= 0.0:
            return speak_text

        if chance > 1.0:
            chance = 1.0

        mode = self._get_easter_mode()

        prefixes = self._get_easter_prefixes()
        postfixes = self._get_easter_postfixes()

        if mode == "prefix":
            if not prefixes:
                return speak_text
        elif mode == "postfix":
            if not postfixes:
                return speak_text
        else:
            if not prefixes and not postfixes:
                return speak_text

        text = (speak_text or "").strip()

        if not text:
            return text

        # Не применять к очень коротким сообщениям.
        # Порог: 2 слова и больше.
        if len(text.split()) < 2:
            return text

        lower_text = text.lower()

        # Если сообщение уже начинается с префикса или заканчивается постфиксом,
        # не применяем повторно.
        for prefix in prefixes:
            if lower_text.startswith(prefix.lower()):
                return text

        for postfix in postfixes:
            if lower_text.endswith(postfix.lower()):
                return text

        if random.random() >= chance:
            return text

        if mode == "prefix":
            use_prefix = True
        elif mode == "postfix":
            use_prefix = False
        else:
            # auto: если есть и префиксы, и постфиксы, выбираем тип 50/50
            if prefixes and postfixes:
                use_prefix = random.random() < 0.5
            else:
                use_prefix = bool(prefixes)

        if use_prefix:
            prefix = random.choice(prefixes)
            modified_text = self._apply_easter_prefix(text, prefix)
        else:
            postfix = random.choice(postfixes)
            modified_text = self._apply_easter_postfix(text, postfix)

        # Финальная страховка от превышения лимита.
        if len(modified_text) > EASTER_MAX_SPEAK_CHARS:
            return text

        return modified_text

    def _on_speech_start(self, item_id):
        self.speech_started.emit(item_id)

    def _on_speech_end(self, item_id):
        self.speech_ended.emit(item_id)

    def _do_highlight(self, item_id):
        self.chat_page.highlight_item(item_id)

    def _do_unhighlight(self, item_id):
        self.chat_page.unhighlight_item(item_id)

    def _save_all_settings(self):
        self.general_settings.save(self.config)
        self.voices_settings.save(self.config)
        self.users_settings.save(self.config)
        self.ignore_words_settings.save(self.config)
        self.ignore_users_settings.save(self.config)
        self.performance_settings.save(self.config)

        self._save_config()

        InfoBar.success(
            title="Сохранено",
            content="Все настройки сохранены",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2000
        )

        InfoBar.warning(
            title="Производительность",
            content="Настройки режима GPU/Low VRAM применяются после перезапуска приложения.",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000
        )

        self._on_any_settings_saved(True)

    def _on_any_settings_saved(self, requires_reconnect):
        new_limit = self.config.get("message_limit", 100)
        self.chat_page.set_message_limit(new_limit)
        self.chat_page.set_user_colors(self.config.get("user_colors", {}))
        self._apply_theme()
        self.tts_service.set_output_device(self.config.get("audio_device", ""))

        if requires_reconnect and self.twitch_chat and self.twitch_chat.is_running():
            log_debug("APP", "settings require reconnect")
            self.stop_chat()
            QTimer.singleShot(500, self.start_chat)

    def _on_tts_enabled_changed(self, enabled):
        """Полное включение/выключение озвучки."""
        log_debug("APP", "tts_enabled_changed", enabled=enabled)

        self.tts_enabled = enabled
        self.tts_service.set_enabled(enabled)

        if not enabled:
            cleared = self.message_queue.clear()

            if cleared > 0:
                log_debug("APP", "queue cleared on disable", cleared=cleared)

            self.chat_page.clear_highlight()