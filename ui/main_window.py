"""
Главное окно приложения Twitch TTS Reader.
Все страницы (чат + настройки) в одном окне через навигацию FluentWindow.
"""
import os
import json
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
    IgnoreWordsPage, IgnoreUsersPage
)
from core.twitch_chat import TwitchChatReader
from core.tts_service import TTSService
from core.message_queue import MessageQueue


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

        if self.config.get("channel_url") and self.config.get("auto_start", False):
            QTimer.singleShot(500, self.start_chat)

    def closeEvent(self, event):
        """Остановка всех потоков и сервисов перед закрытием."""
        self.process_timer.stop()
        if self.twitch_chat:
            self.twitch_chat.stop()
            self.twitch_chat.wait(2000)
            self.twitch_chat = None
        self.tts_service.stop()
        self.message_queue.clear()
        if hasattr(self, '_cleanup_callback') and self._cleanup_callback:
            try:
                self._cleanup_callback()
            except Exception:
                pass
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
            "theme": "auto"
        }
        if not os.path.exists(config_path):
            self._write_config(default_config)
            return default_config
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    return default_config
                config = json.loads(content)
                return self._migrate_config(config, default_config)
        except Exception:
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
        with open("config.json", 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

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

        self.tts_service.on_speech_start = self._on_speech_start
        self.tts_service.on_speech_end = self._on_speech_end

    def _init_navigation(self):
        self.addSubInterface(self.chat_page, FluentIcon.MESSAGE, "Чат", NavigationItemPosition.TOP)
        self.addSubInterface(self.general_settings, FluentIcon.SETTING, "Общие", NavigationItemPosition.TOP)
        self.addSubInterface(self.voices_settings, FluentIcon.MICROPHONE, "Голоса", NavigationItemPosition.TOP)
        self.addSubInterface(self.users_settings, FluentIcon.PEOPLE, "Привязки", NavigationItemPosition.TOP)
        self.addSubInterface(self.ignore_words_settings, FluentIcon.CANCEL, "Стоп-слова", NavigationItemPosition.TOP)
        self.addSubInterface(self.ignore_users_settings, FluentIcon.ROBOT, "Боты", NavigationItemPosition.TOP)

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
            InfoBar.warning(title="Настройки не заданы", content="Укажите URL канала в настройках",
                            parent=self, position=InfoBarPosition.TOP, duration=3000)
            return
        if self.twitch_chat and self.twitch_chat.is_running():
            InfoBar.info(title="Уже запущено", content="Чат уже читается",
                         parent=self, position=InfoBarPosition.TOP, duration=2000)
            return
        self.chat_page.add_message(None, f"🚀 Запуск чтения чата: {channel_url}", msg_type="system")
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
        if self.twitch_chat:
            self.twitch_chat.stop()
            self.twitch_chat.wait(2000)   # дожидаемся смерти старого потока чтения
            self.twitch_chat = None
            self.chat_page.add_message(None, "⏹️ Чтение чата остановлено", msg_type="system")
            self.chat_page.set_running_state(False)
        # ВАЖНО: flush() вместо clear_and_stop() —
        # clear_and_stop() ставит _stop_event и навсегда убивает потоки TTS
        self.tts_service.flush()
        self.message_queue.clear()
        self.chat_page.clear_highlight()

    def on_message_received(self, username, display_text, speak_text, prefix, reply_info=None):
        ignore_users = self.config.get("ignore_users", [])
        if username in ignore_users:
            self.chat_page.add_message(username, display_text, msg_type="normal", reply_info=reply_info)
            return
        msg_id = self.chat_page.add_message(username, display_text, msg_type="normal", reply_info=reply_info)
        if not speak_text:
            return
        if not self.tts_enabled:
            return
        self.tts_service.register_item(msg_id, msg_id)
        self.message_queue.add_message(username, speak_text, prefix, msg_id)

    def _on_chat_error(self, error_message):
        self.chat_page.add_message(None, error_message, msg_type="error")
        if "Не удалось определить канал" in error_message:
            InfoBar.error(title="Ошибка канала", content=error_message,
                          parent=self, position=InfoBarPosition.TOP, duration=5000)
            self.stop_chat()

    def _process_message_queue(self):
        if not self.tts_enabled:
            return
        while not self.message_queue.is_empty():
            message = self.message_queue.get_next()
            if message is None:
                break
            username, speak_text, prefix, item_id = message
            voice = self._get_voice_for_user(username, prefix)
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
        self._save_config()
        InfoBar.success(title="Сохранено", content="Все настройки сохранены",
                        parent=self, position=InfoBarPosition.TOP, duration=2000)
        self._on_any_settings_saved(True)

    def _on_any_settings_saved(self, requires_reconnect):
        new_limit = self.config.get("message_limit", 100)
        self.chat_page.set_message_limit(new_limit)
        self.chat_page.set_user_colors(self.config.get("user_colors", {}))
        self._apply_theme()
        if requires_reconnect and self.twitch_chat and self.twitch_chat.is_running():
            self.stop_chat()
            QTimer.singleShot(500, self.start_chat)

    def _on_tts_enabled_changed(self, enabled):
        """Полное включение/выключение озвучки."""
        self.tts_enabled = enabled
        self.tts_service.set_enabled(enabled)
        if not enabled:
            # Мгновенно глушим всё, что уже набралось
            self.message_queue.clear()
            self.chat_page.clear_highlight()