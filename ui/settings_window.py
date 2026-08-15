"""
Страницы настроек для главного окна.
Сигнал settings_saved передаёт bool: True если нужно переподключиться к чату.
"""
"""
Страницы настроек для главного окна.
Сигнал settings_saved передаёт bool: True если нужно переподключиться к чату.
"""
import time
import requests

from PyQt5.QtCore import pyqtSignal, QThread, QTimer
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame,
    QHeaderView, QTableWidgetItem, QAbstractItemView, QProgressBar
)

from qfluentwidgets import (
    FluentIcon, PrimaryPushButton, PushButton, PlainTextEdit, LineEdit,
    CardWidget, SubtitleLabel, BodyLabel, StrongBodyLabel,
    ComboBox, TableWidget, InfoBar, InfoBarPosition, SwitchButton,
    ScrollArea
)

from core.system_info import (
    get_memory_info_for_config,
    read_runtime_state,
    DEVICE_MODE_AUTO,
    DEVICE_MODE_CPU,
    DEVICE_MODE_CUDA,
)
from core.tts_service import get_output_audio_devices

class ScrollableSettingsPage(QWidget):
    """
    Базовая страница настроек с автоматическим скроллом.

    Все страницы настроек должны наследоваться от этого класса.
    Контент добавляется в self.content_layout.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self._scroll_area = ScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.NoFrame)

        self._content_widget = QWidget()
        self.content_layout = QVBoxLayout(self._content_widget)
        self.content_layout.setContentsMargins(30, 30, 30, 30)
        self.content_layout.setSpacing(20)

        self._scroll_area.setWidget(self._content_widget)
        outer_layout.addWidget(self._scroll_area)

XTTS_API_URL = "http://localhost:8020"
_voices_cache = None
_cache_timestamp = 0
_CACHE_TTL = 300

MESSAGE_LIMIT_PRESETS = ["25", "50", "75", "100", "150"]

NICK_COLOR_OPTIONS = [
    ("Авто", ""),
    ("Синий", "#0066CC"),
    ("Красный", "#CC0000"),
    ("Тёмно-зелёный", "#008800"),
    ("Фиолетовый", "#8800CC"),
    ("Оранжевый", "#CC6600"),
    ("Бирюзовый", "#00AABB"),
    ("Малиновый", "#BB0088"),
    ("Оливковый", "#669900"),
]

THEME_OPTIONS = [
    ("☀️ Светлая", "light"),
    ("🌙 Тёмная", "dark"),
    ("🖥️ Автоматически (системная)", "auto"),
]


def get_available_voices(force_refresh=False, timeout=2):
    global _voices_cache, _cache_timestamp
    now = time.time()
    if not force_refresh and _voices_cache is not None and (now - _cache_timestamp) < _CACHE_TTL:
        return _voices_cache
    try:
        response = requests.get(f"{XTTS_API_URL}/speakers", timeout=timeout)
        if response.status_code == 200:
            voices = []
            for item in response.json():
                if isinstance(item, str):
                    voices.append(item)
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("id") or item.get("speaker")
                    if name:
                        voices.append(str(name))
            _voices_cache = voices
            _cache_timestamp = now
            return voices
    except Exception:
        if _voices_cache is not None:
            return _voices_cache
    return []


class VoicesLoader(QThread):
    voices_loaded = pyqtSignal(list)

    def run(self):
        self.voices_loaded.emit(get_available_voices(force_refresh=True, timeout=30))


class GeneralSettingsPage(ScrollableSettingsPage):
    """Канал, автостарт, лимит сообщений, тема."""

    settings_saved = pyqtSignal(bool)

    def __init__(self, config, save_callback):
        super().__init__()
        self.setObjectName("generalPage")
        self.config = config
        self.save_callback = save_callback
        self._original_channel_url = ""
        self._init_ui()

    def _init_ui(self):
        layout = self.content_layout
        layout.addWidget(SubtitleLabel("Общие настройки"))

        channel_card = CardWidget()
        channel_layout = QVBoxLayout(channel_card)
        channel_layout.setSpacing(10)
        channel_layout.addWidget(StrongBodyLabel("Канал Twitch"))
        channel_layout.addWidget(BodyLabel("URL канала:"))
        self.channel_input = LineEdit()
        self.channel_input.setPlaceholderText("https://www.twitch.tv/popout/username/chat")
        channel_layout.addWidget(self.channel_input)
        layout.addWidget(channel_card)

        auto_card = CardWidget()
        auto_layout = QHBoxLayout(auto_card)
        auto_layout.addWidget(BodyLabel("Автоматический запуск при старте:"))
        auto_layout.addStretch()
        self.auto_start_switch = SwitchButton()
        auto_layout.addWidget(self.auto_start_switch)
        layout.addWidget(auto_card)

        limit_card = CardWidget()
        limit_layout = QVBoxLayout(limit_card)
        limit_layout.setSpacing(10)
        limit_layout.addWidget(StrongBodyLabel("История чата"))
        limit_layout.addWidget(BodyLabel("Максимум сообщений в окне чата:"))
        self.limit_combo = ComboBox()
        self.limit_combo.addItems(MESSAGE_LIMIT_PRESETS)
        limit_layout.addWidget(self.limit_combo)
        limit_info = BodyLabel("💡 Меньше сообщений — меньше потребление памяти и быстрее отрисовка.")
        limit_info.setWordWrap(True)
        limit_info.setStyleSheet("color: gray; font-size: 12px;")
        limit_layout.addWidget(limit_info)
        layout.addWidget(limit_card)

        theme_card = CardWidget()
        theme_layout = QVBoxLayout(theme_card)
        theme_layout.setSpacing(10)
        theme_layout.addWidget(StrongBodyLabel("Тема приложения"))
        theme_layout.addWidget(BodyLabel("Стиль интерфейса:"))
        self.theme_combo = ComboBox()
        for label, _ in THEME_OPTIONS:
            self.theme_combo.addItem(label)
        theme_layout.addWidget(self.theme_combo)
        theme_info = BodyLabel("💡 Тема применяется сразу после сохранения. 'Автоматически' следует за системной темой Windows.")
        theme_info.setWordWrap(True)
        theme_info.setStyleSheet("color: gray; font-size: 12px;")
        theme_layout.addWidget(theme_info)
        layout.addWidget(theme_card)

        easter_card = CardWidget()
        easter_layout = QHBoxLayout(easter_card)

        easter_layout.addWidget(BodyLabel("Пасхалки в озвучке:"))
        easter_layout.addStretch()

        self.easter_egg_switch = SwitchButton()
        easter_layout.addWidget(self.easter_egg_switch)

        layout.addWidget(easter_card)

        easter_hint = BodyLabel(
            "💡 Если включено, к озвучке могут добавляться пасхалки.\n"
            "В чате текст сообщения остаётся без изменений."
        )
        easter_hint.setWordWrap(True)
        easter_hint.setStyleSheet("color: gray; font-size: 12px;")
        layout.addWidget(easter_hint)

        save_btn = PrimaryPushButton("💾 Сохранить общие настройки")
        save_btn.setFixedHeight(40)
        save_btn.setMinimumWidth(200)
        save_btn.clicked.connect(self._on_save)
        layout.addWidget(save_btn)
        layout.addStretch()

    def load(self, config):
        self.channel_input.setText(config.get("channel_url", ""))
        self._original_channel_url = config.get("channel_url", "")
        self.auto_start_switch.setChecked(config.get("auto_start", False))
        limit = config.get("message_limit", 100)
        limit_str = str(limit)
        self.limit_combo.setCurrentText(limit_str if limit_str in MESSAGE_LIMIT_PRESETS else "100")
        theme = config.get("theme", "auto")
        self.theme_combo.setCurrentIndex(self._find_theme_index(theme))
        self.easter_egg_switch.setChecked(bool(config.get("easter_egg_enabled", False)))

    def _find_theme_index(self, theme_str):
        for i, (_, value) in enumerate(THEME_OPTIONS):
            if value == theme_str:
                return i
        return 2

    def save(self, config):
        config["channel_url"] = self.channel_input.text().strip()
        config["auto_start"] = self.auto_start_switch.isChecked()
        try:
            config["message_limit"] = int(self.limit_combo.currentText())
        except ValueError:
            config["message_limit"] = 100
        theme_index = self.theme_combo.currentIndex()
        if 0 <= theme_index < len(THEME_OPTIONS):
            config["theme"] = THEME_OPTIONS[theme_index][1]
        else:
            config["theme"] = "auto"
        config["easter_egg_enabled"] = self.easter_egg_switch.isChecked()

    def _on_save(self):
        self.save(self.config)
        self.save_callback()
        requires_reconnect = self.config.get("channel_url", "") != self._original_channel_url
        self._original_channel_url = self.config.get("channel_url", "")
        self.settings_saved.emit(requires_reconnect)
        InfoBar.success(title="Сохранено", content="Общие настройки сохранены",
                        parent=self, position=InfoBarPosition.TOP, duration=2000)


class VoicesSettingsPage(ScrollableSettingsPage):
    """Голос по умолчанию, режим озвучки, таблица префиксов."""

    settings_saved = pyqtSignal(bool)

    def __init__(self, config, save_callback):
        super().__init__()
        self.setObjectName("voicesPage")
        self.config = config
        self.save_callback = save_callback
        self.voices = []
        self.loader = None
        self._init_ui()
        self._start_load()

    def _init_ui(self):
        layout = self.content_layout

        header = QHBoxLayout()
        header.addWidget(SubtitleLabel("Настройки голосов"))
        header.addStretch()
        self.refresh_btn = PushButton(FluentIcon.SYNC, "Обновить список")
        self.refresh_btn.setFixedHeight(40)
        self.refresh_btn.setMinimumWidth(200)
        self.refresh_btn.clicked.connect(self._start_load)
        header.addWidget(self.refresh_btn)
        layout.addLayout(header)

        self.status_label = BodyLabel("Загрузка списка голосов...")
        self.status_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(self.status_label)

        default_card = CardWidget()
        default_layout = QVBoxLayout(default_card)
        default_layout.setSpacing(10)
        default_layout.addWidget(StrongBodyLabel("Голос по умолчанию"))
        self.prefix_hint = BodyLabel("Используется когда префикс не указан или режим префиксов выключен:")
        self.prefix_hint.setWordWrap(True)
        default_layout.addWidget(self.prefix_hint)
        self.default_combo = ComboBox()
        default_layout.addWidget(self.default_combo)
        layout.addWidget(default_card)

        mode_card = CardWidget()
        mode_layout = QHBoxLayout(mode_card)
        mode_layout.addWidget(BodyLabel("Использовать префиксы и привязки пользователей:"))
        mode_layout.addStretch()
        self.use_prefixes_switch = SwitchButton()
        self.use_prefixes_switch.setChecked(True)
        mode_layout.addWidget(self.use_prefixes_switch)
        layout.addWidget(mode_card)

        self.mode_hint = BodyLabel("")
        self.mode_hint.setStyleSheet("color: gray; font-size: 12px;")
        layout.addWidget(self.mode_hint)
        self.use_prefixes_switch.checkedChanged.connect(self._update_mode_hint)
        self._update_mode_hint()

        prefix_card = CardWidget()
        prefix_layout = QVBoxLayout(prefix_card)
        prefix_layout.setSpacing(10)
        prefix_layout.addWidget(StrongBodyLabel("Привязка префиксов к голосам"))
        self.prefix_example = BodyLabel(
            "💡 Сообщения, начинающиеся с префикса, озвучиваются соответствующим голосом.\n"
            "Пример: '!m привет' — озвучит голосом, привязанным к префиксу !m."
        )
        self.prefix_example.setWordWrap(True)
        prefix_layout.addWidget(self.prefix_example)
        self.prefix_table = TableWidget()
        self.prefix_table.setColumnCount(2)
        self.prefix_table.setHorizontalHeaderLabels(["Префикс", "Голос"])
        self.prefix_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.prefix_table.verticalHeader().setVisible(False)
        self.prefix_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        prefix_layout.addWidget(self.prefix_table)

        btn_layout = QHBoxLayout()
        add_btn = PrimaryPushButton(FluentIcon.ADD, "Добавить префикс")
        add_btn.setFixedHeight(40)
        add_btn.setMinimumWidth(200)
        add_btn.clicked.connect(self._add_prefix_row)
        btn_layout.addWidget(add_btn)
        remove_btn = PushButton(FluentIcon.DELETE, "Удалить выбранное")
        remove_btn.setFixedHeight(40)
        remove_btn.setMinimumWidth(200)
        remove_btn.clicked.connect(self._remove_selected_prefix)
        btn_layout.addWidget(remove_btn)
        btn_layout.addStretch()
        prefix_layout.addLayout(btn_layout)
        layout.addWidget(prefix_card)

        save_btn = PrimaryPushButton("💾 Сохранить голоса")
        save_btn.setFixedHeight(40)
        save_btn.setMinimumWidth(200)
        save_btn.clicked.connect(self._on_save)
        layout.addWidget(save_btn)
        layout.addStretch()

    def _update_mode_hint(self):
        if self.use_prefixes_switch.isChecked():
            self.mode_hint.setText("✓ Озвучка использует привязки пользователей и префиксы из таблицы ниже.")
        else:
            self.mode_hint.setText("✗ Все сообщения озвучиваются голосом по умолчанию.")
        self.mode_hint.setWordWrap(True)

    def _start_load(self):
        self.status_label.setText("⏳ Загрузка списка голосов...")
        self.status_label.setStyleSheet("color: #0066CC; font-style: italic;")
        self.refresh_btn.setEnabled(False)
        cached = get_available_voices(force_refresh=False, timeout=1)
        if cached:
            self._apply_voices(cached)
            self.status_label.setText(f"✓ Загружено {len(cached)} голосов (обновление в фоне...)")
            self.status_label.setWordWrap(True)
        self.loader = VoicesLoader()
        self.loader.voices_loaded.connect(self._on_voices_loaded)
        self.loader.finished.connect(lambda: self.refresh_btn.setEnabled(True))
        self.loader.start()

    def _on_voices_loaded(self, voices):
        self._apply_voices(voices)
        self.status_label.setText(f"✓ Загружено {len(voices)} голосов")
        self.status_label.setStyleSheet("color: #008800;")

    def _apply_voices(self, voices):
        self.voices = voices
        current = self.default_combo.currentText()
        self.default_combo.clear()
        if voices:
            self.default_combo.addItems(voices)
            if current in voices:
                self.default_combo.setCurrentText(current)
            elif self.config.get("default_voice") in voices:
                self.default_combo.setCurrentText(self.config["default_voice"])
        else:
            self.default_combo.addItem("Нет доступных голосов")
        for row in range(self.prefix_table.rowCount()):
            combo = self.prefix_table.cellWidget(row, 1)
            if combo is None:
                continue
            current_voice = combo.currentText()
            combo.clear()
            if voices:
                combo.addItems(voices)
                if current_voice in voices:
                    combo.setCurrentText(current_voice)

    def _add_prefix_row(self, prefix="", voice=""):
        row = self.prefix_table.rowCount()
        self.prefix_table.insertRow(row)
        self.prefix_table.setItem(row, 0, QTableWidgetItem(prefix))
        combo = ComboBox()
        if self.voices:
            combo.addItems(self.voices)
            if voice and voice in self.voices:
                combo.setCurrentText(voice)
        self.prefix_table.setCellWidget(row, 1, combo)

    def _remove_selected_prefix(self):
        rows = sorted(set(idx.row() for idx in self.prefix_table.selectedIndexes()), reverse=True)
        for row in rows:
            self.prefix_table.removeRow(row)

    def load(self, config):
        self.use_prefixes_switch.setChecked(config.get("use_prefixes", True))
        self._update_mode_hint()
        self.prefix_table.setRowCount(0)
        for prefix, voice in config.get("prefix_mappings", {}).items():
            self._add_prefix_row(prefix, voice)

    def save(self, config):
        if self.voices:
            config["default_voice"] = self.default_combo.currentText()
        config["use_prefixes"] = self.use_prefixes_switch.isChecked()
        prefix_mappings = {}
        for row in range(self.prefix_table.rowCount()):
            prefix_item = self.prefix_table.item(row, 0)
            combo = self.prefix_table.cellWidget(row, 1)
            if prefix_item and combo:
                prefix = prefix_item.text().strip()
                voice = combo.currentText()
                if prefix and voice and voice != "Нет доступных голосов":
                    prefix_mappings[prefix] = voice
        config["prefix_mappings"] = prefix_mappings

    def _on_save(self):
        self.save(self.config)
        self.save_callback()
        self.settings_saved.emit(False)
        InfoBar.success(title="Сохранено", content="Настройки голосов сохранены",
                        parent=self, position=InfoBarPosition.TOP, duration=2000)


class UsersSettingsPage(ScrollableSettingsPage):
    """Таблица привязок: никнейм → голос + цвет."""

    settings_saved = pyqtSignal(bool)

    def __init__(self, config, save_callback):
        super().__init__()
        self.setObjectName("usersPage")
        self.config = config
        self.save_callback = save_callback
        self.voices = []
        self._init_ui()
        self._refresh_voices()

    def _init_ui(self):
        layout = self.content_layout

        header = QHBoxLayout()
        header.addWidget(SubtitleLabel("Привязка пользователей"))
        header.addStretch()

        refresh_btn = PushButton(FluentIcon.SYNC, "Обновить голоса")
        refresh_btn.setFixedHeight(36)
        refresh_btn.setMinimumWidth(160)
        refresh_btn.clicked.connect(self._refresh_voices)
        header.addWidget(refresh_btn)

        layout.addLayout(header)

        hint_label = BodyLabel(
            "💡 Пользователи из этого списка всегда озвучиваются выбранным голосом, "
            "игнорируя префиксы. Цвет никнейма в чате тоже можно задать."
        )
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)

        self.table = TableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Никнейм", "Голос", "Цвет ника"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()

        add_btn = PrimaryPushButton(FluentIcon.ADD, "Добавить")
        add_btn.setFixedHeight(36)
        add_btn.setMinimumWidth(130)
        add_btn.clicked.connect(self._add_row)
        btn_layout.addWidget(add_btn)

        remove_btn = PushButton(FluentIcon.DELETE, "Удалить выбранное")
        remove_btn.setFixedHeight(36)
        remove_btn.setMinimumWidth(160)
        remove_btn.clicked.connect(self._remove_selected)
        btn_layout.addWidget(remove_btn)

        btn_layout.addStretch()

        save_btn = PushButton("💾 Сохранить")
        save_btn.setFixedHeight(36)
        save_btn.setMinimumWidth(130)
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)

        layout.addLayout(btn_layout)
        layout.addStretch()

    def _refresh_voices(self):
        self.voices = get_available_voices()

    def _add_row(self, nickname="", voice="", color=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(nickname))

        voice_combo = ComboBox()
        if self.voices:
            voice_combo.addItems(self.voices)
            if voice and voice in self.voices:
                voice_combo.setCurrentText(voice)
        self.table.setCellWidget(row, 1, voice_combo)

        color_combo = ComboBox()
        for label, _ in NICK_COLOR_OPTIONS:
            color_combo.addItem(label)
        color_combo.setCurrentIndex(self._find_color_index(color))
        self.table.setCellWidget(row, 2, color_combo)

    def _find_color_index(self, color_hex):
        if not color_hex:
            return 0
        for i, (_, hex_value) in enumerate(NICK_COLOR_OPTIONS):
            if hex_value == color_hex:
                return i
        return 0

    def _remove_selected(self):
        rows = sorted(set(idx.row() for idx in self.table.selectedIndexes()), reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def load(self, config):
        self._refresh_voices()
        self.table.setRowCount(0)
        user_mappings = config.get("user_mappings", {})
        user_colors = config.get("user_colors", {})
        for nickname in set(user_mappings.keys()) | set(user_colors.keys()):
            self._add_row(nickname, user_mappings.get(nickname, ""), user_colors.get(nickname, ""))

    def save(self, config):
        mappings, colors = {}, {}
        for row in range(self.table.rowCount()):
            nick_item = self.table.item(row, 0)
            voice_combo = self.table.cellWidget(row, 1)
            color_combo = self.table.cellWidget(row, 2)
            if not nick_item:
                continue
            nick = nick_item.text().strip()
            if not nick:
                continue
            if voice_combo:
                voice = voice_combo.currentText()
                if voice and voice != "Нет доступных голосов":
                    mappings[nick] = voice
            if color_combo:
                color_index = color_combo.currentIndex()
                if 0 <= color_index < len(NICK_COLOR_OPTIONS):
                    hex_color = NICK_COLOR_OPTIONS[color_index][1]
                    if hex_color:
                        colors[nick] = hex_color
        config["user_mappings"] = mappings
        config["user_colors"] = colors

    def _on_save(self):
        self.save(self.config)
        self.save_callback()
        self.settings_saved.emit(False)
        InfoBar.success(title="Сохранено", content="Привязки сохранены",
                        parent=self, position=InfoBarPosition.TOP, duration=2000)


class IgnoreWordsPage(ScrollableSettingsPage):
    """Список слов, которые игнорируются при озвучке."""

    settings_saved = pyqtSignal(bool)

    def __init__(self, config, save_callback):
        super().__init__()
        self.setObjectName("ignoreWordsPage")
        self.config = config
        self.save_callback = save_callback
        self._original_words = []
        self._init_ui()

    def _init_ui(self):
        layout = self.content_layout
        layout.addWidget(SubtitleLabel("Игнорируемые слова"))
        self.banned_words_hint = BodyLabel(
            "💡 Слова из этого списка удаляются из текста перед озвучкой.\n"
            "Используйте для смайликов (Kappa, LUL, orkHello и т.д.).\n"
            "Вводите по одному слову на строку."
        )
        self.banned_words_hint.setWordWrap(True)
        layout.addWidget(self.banned_words_hint)
        self.text_edit = PlainTextEdit()
        self.text_edit.setPlaceholderText("Kappa\nLUL\norkHello\nmonkaW")
        self.text_edit.setStyleSheet("font-family: 'Consolas', monospace; font-size: 13px;")
        layout.addWidget(self.text_edit)
        save_btn = PrimaryPushButton("💾 Сохранить список")
        save_btn.setFixedHeight(40)
        save_btn.setMinimumWidth(200)
        save_btn.clicked.connect(self._on_save)
        layout.addWidget(save_btn)

    def load(self, config):
        words = config.get("ignore_words", [])
        self.text_edit.setPlainText('\n'.join(words))
        self._original_words = list(words)

    def save(self, config):
        text = self.text_edit.toPlainText()
        config["ignore_words"] = [w.strip() for w in text.split('\n') if w.strip()]

    def _on_save(self):
        self.save(self.config)
        self.save_callback()
        new_words = self.config.get("ignore_words", [])
        requires_reconnect = set(new_words) != set(self._original_words)
        self._original_words = list(new_words)
        self.settings_saved.emit(requires_reconnect)
        InfoBar.success(title="Сохранено", content=f"Сохранено {len(new_words)} слов",
                        parent=self, position=InfoBarPosition.TOP, duration=2000)


class IgnoreUsersPage(ScrollableSettingsPage):
    """Список пользователей, чьи сообщения не озвучиваются."""

    settings_saved = pyqtSignal(bool)

    def __init__(self, config, save_callback):
        super().__init__()
        self.setObjectName("ignoreUsersPage")
        self.config = config
        self.save_callback = save_callback
        self._init_ui()

    def _init_ui(self):
        layout = self.content_layout
        layout.addWidget(SubtitleLabel("Игнорируемые пользователи"))
        self.bot_names_hint = BodyLabel(
            "💡 Сообщения этих пользователей видны в чате, но НЕ озвучиваются.\n"
            "Используйте для ботов (Nightbot, StreamElements и т.д.).\n"
            "Вводите по одному нику на строку (регистр учитывается)."
        )
        self.bot_names_hint.setWordWrap(True)
        layout.addWidget(self.bot_names_hint)
        self.text_edit = PlainTextEdit()
        self.text_edit.setPlaceholderText("nightbot\nstreamelements\nmoobot\nwizebot")
        self.text_edit.setStyleSheet("font-family: 'Consolas', monospace; font-size: 13px;")
        layout.addWidget(self.text_edit)
        save_btn = PrimaryPushButton("💾 Сохранить список")
        save_btn.setFixedHeight(40)
        save_btn.setMinimumWidth(200)
        save_btn.clicked.connect(self._on_save)
        layout.addWidget(save_btn)

    def load(self, config):
        self.text_edit.setPlainText('\n'.join(config.get("ignore_users", [])))

    def save(self, config):
        text = self.text_edit.toPlainText()
        config["ignore_users"] = [u.strip() for u in text.split('\n') if u.strip()]

    def _on_save(self):
        self.save(self.config)
        self.save_callback()
        self.settings_saved.emit(False)
        InfoBar.success(title="Сохранено",
                        content=f"Сохранено {len(self.config.get('ignore_users', []))} пользователей",
                        parent=self, position=InfoBarPosition.TOP, duration=2000)

DEVICE_OPTIONS = [
    ("Автоматически", DEVICE_MODE_AUTO),
    ("CPU", DEVICE_MODE_CPU),
    ("GPU CUDA", DEVICE_MODE_CUDA),
]


class MemoryInfoWorker(QThread):
    """Фоновый запрос памяти."""

    info_ready = pyqtSignal(dict)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = dict(config or {})

    def run(self):
        try:
            info = get_memory_info_for_config(self.config)
        except Exception as e:
            info = {
                "mode": DEVICE_MODE_CPU,
                "used_mb": 0,
                "total_mb": 0,
                "available_mb": 0,
                "percent": 0,
                "error": f"{type(e).__name__}: {e}"[:200],
            }
        self.info_ready.emit(info)


class PerformanceSettingsPage(ScrollableSettingsPage):
    """Настройки CPU/GPU и lowvram."""

    settings_saved = pyqtSignal(bool)

    def __init__(self, config, save_callback):
        super().__init__()
        self.setObjectName("performancePage")
        self.config = config
        self.save_callback = save_callback
        self._worker = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self._refresh_memory)
        self._init_ui()

    def _init_ui(self):
        from PyQt5.QtCore import QTimer
        layout = self.content_layout

        layout.addWidget(SubtitleLabel("Производительность"))

        # === Текущее состояние ===
        status_card = CardWidget()
        status_layout = QVBoxLayout(status_card)
        status_layout.setSpacing(8)

        status_layout.addWidget(StrongBodyLabel("Текущий режим сервера"))

        self.current_mode_label = BodyLabel("Определяется...")
        status_layout.addWidget(self.current_mode_label)

        self.lowvram_state_label = BodyLabel("Low VRAM: —")
        status_layout.addWidget(self.lowvram_state_label)

        layout.addWidget(status_card)

        # === Настройки ===
        settings_card = CardWidget()
        settings_layout = QVBoxLayout(settings_card)
        settings_layout.setSpacing(10)

        settings_layout.addWidget(StrongBodyLabel("Настройки режима"))

        device_layout = QHBoxLayout()
        device_layout.addWidget(BodyLabel("Режим работы:"))

        self.device_combo = ComboBox()
        for label, _ in DEVICE_OPTIONS:
            self.device_combo.addItem(label)

        device_layout.addWidget(self.device_combo)
        settings_layout.addLayout(device_layout)

        lowvram_layout = QHBoxLayout()
        lowvram_layout.addWidget(BodyLabel("Режим низкой VRAM (только для GPU):"))
        lowvram_layout.addStretch()

        self.lowvram_switch = SwitchButton()
        self.lowvram_switch.setChecked(True)
        lowvram_layout.addWidget(self.lowvram_switch)

        settings_layout.addLayout(lowvram_layout)

        hint = BodyLabel(
            "💡 Режим Low VRAM выгружает модель в RAM когда она не используется.\n"
            "Это снижает потребление VRAM, но немного замедляет генерацию.\n"
            "⚠️ Настройки применяются только после перезапуска приложения!"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 12px;")
        settings_layout.addWidget(hint)

        layout.addWidget(settings_card)

        # === Память ===
        memory_card = CardWidget()
        memory_layout = QVBoxLayout(memory_card)
        memory_layout.setSpacing(10)

        memory_layout.addWidget(StrongBodyLabel("Доступная память устройства"))

        self.memory_mode_label = BodyLabel("Устройство: —")
        memory_layout.addWidget(self.memory_mode_label)

        self.memory_bar = QProgressBar()
        self.memory_bar.setRange(0, 100)
        self.memory_bar.setValue(0)
        memory_layout.addWidget(self.memory_bar)

        self.memory_text_label = BodyLabel("Нажмите «Обновить» для получения данных.")
        self.memory_mode_label.setWordWrap(True)
        memory_layout.addWidget(self.memory_text_label)

        refresh_btn = PushButton(FluentIcon.SYNC, "Обновить")
        refresh_btn.setFixedHeight(40)
        refresh_btn.setMinimumWidth(200)
        refresh_btn.clicked.connect(self._refresh_memory)
        memory_layout.addWidget(refresh_btn)

        layout.addWidget(memory_card)

        audio_card = CardWidget()
        audio_layout = QVBoxLayout(audio_card)
        audio_layout.setSpacing(10)

        audio_layout.addWidget(StrongBodyLabel("Устройство вывода звука"))
        audio_hint = BodyLabel(
            "Закрепляет вывод озвучки на выбранном устройстве, "
            "даже если игра меняет устройство по умолчанию."
        )
        audio_hint.setWordWrap(True)
        audio_layout.addWidget(audio_hint)

        self.audio_device_combo = ComboBox()
        audio_layout.addWidget(self.audio_device_combo)

        update_audio_hint = BodyLabel(
            "💡 Настройка применяется сразу.\n"
            "Если нужного устройства нет в списке, подключи его и нажми «Обновить устройства»."
        )
        update_audio_hint.setWordWrap(True)
        update_audio_hint.setStyleSheet("color: gray; font-size: 12px;")
        audio_layout.addWidget(update_audio_hint)

        refresh_audio_btn = PushButton(FluentIcon.SYNC, "Обновить устройства")
        refresh_audio_btn.setFixedHeight(40)
        refresh_audio_btn.setMinimumWidth(200)
        refresh_audio_btn.clicked.connect(self._refresh_audio_devices)
        audio_layout.addWidget(refresh_audio_btn)

        layout.addWidget(audio_card)

        # === Сохранение ===
        save_btn = PrimaryPushButton("💾 Сохранить настройки производительности")
        save_btn.setFixedHeight(40)
        save_btn.setMinimumWidth(200)
        save_btn.clicked.connect(self._on_save)
        layout.addWidget(save_btn)

        layout.addStretch()

    def _find_device_index(self, value):
        value = str(value or DEVICE_MODE_AUTO).lower()
        for index, (_, key) in enumerate(DEVICE_OPTIONS):
            if key == value:
                return index
        return 0

    def load(self, config):
        self.config = config

        device_mode = config.get("device_mode", DEVICE_MODE_AUTO)
        self.device_combo.setCurrentIndex(self._find_device_index(device_mode))

        self.lowvram_switch.setChecked(bool(config.get("use_lowvram", True)))

        self._refresh_mode_label()
        self._refresh_memory()

        self._load_audio_devices(config.get("audio_device", ""))

    def save(self, config):
        index = self.device_combo.currentIndex()
        if 0 <= index < len(DEVICE_OPTIONS):
            config["device_mode"] = DEVICE_OPTIONS[index][1]
        else:
            config["device_mode"] = DEVICE_MODE_AUTO

        config["use_lowvram"] = self.lowvram_switch.isChecked()

        audio_index = self.audio_device_combo.currentIndex()

        if audio_index <= 0:
            config["audio_device"] = ""
        else:
            config["audio_device"] = self.audio_device_combo.currentText()

    def _on_save(self):
        self.save(self.config)
        self.save_callback()
        self.settings_saved.emit(False)

        InfoBar.warning(
            title="Сохранено",
            content=(
                "Настройки режима GPU/Low VRAM применяются после перезапуска.\n"
                "Устройство вывода звука применяется сразу."
            ),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000
        )

        self._refresh_mode_label()

    def _refresh_mode_label(self):
        state = read_runtime_state()
        mode = state.get("device_mode")

        if mode == DEVICE_MODE_CUDA:
            self.current_mode_label.setText("GPU CUDA")
        elif mode == DEVICE_MODE_CPU:
            self.current_mode_label.setText("CPU")
        else:
            self.current_mode_label.setText("Определяется...")

        lowvram_active = bool(
            state.get("use_lowvram", self.config.get("use_lowvram", True))
        ) and mode == DEVICE_MODE_CUDA

        self.lowvram_state_label.setText(f"Low VRAM: {'вкл' if lowvram_active else 'выкл'}")

    def _refresh_memory(self):
        if self._worker is not None and self._worker.isRunning():
            return

        self._worker = MemoryInfoWorker(self.config, self)
        self._worker.info_ready.connect(self._on_memory_info)
        self._worker.start()

    def _on_memory_info(self, info):
        mode = info.get("mode", DEVICE_MODE_CPU)

        if mode == DEVICE_MODE_CUDA:
            self.memory_mode_label.setText("Устройство: GPU CUDA")
        else:
            self.memory_mode_label.setText("Устройство: CPU (RAM)")

        used_mb = int(info.get("used_mb", 0))
        total_mb = int(info.get("total_mb", 0))
        available_mb = int(info.get("available_mb", 0))
        percent = int(info.get("percent", 0))

        if total_mb > 0:
            self.memory_bar.setEnabled(True)
            self.memory_bar.setValue(percent)
            self.memory_text_label.setText(
                f"Занято: {used_mb} МБ / {total_mb} МБ    Свободно: {available_mb} МБ"
            )
        else:
            self.memory_bar.setEnabled(False)
            self.memory_bar.setValue(0)
            self.memory_text_label.setText("Не удалось получить данные памяти")

        self._refresh_mode_label()

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_mode_label()
        self._refresh_memory()
        self._refresh_timer.start()

    def hideEvent(self, event):
        self._refresh_timer.stop()
        super().hideEvent(event)

    def _load_audio_devices(self, current_value=""):
        """Заполнить список устройств вывода."""
        self.audio_device_combo.clear()
        self.audio_device_combo.addItem("Авто (устройство по умолчанию)")

        devices = get_output_audio_devices()
        current = str(current_value or "").strip()

        # Если в конфиге задана подстрока или кастомное имя,
        # сохраняем его в списке, даже если точного совпадения нет.
        if current and current not in devices:
            devices.append(current)

        selected_index = 0

        for name in devices:
            self.audio_device_combo.addItem(name)

            if current:
                if current == name or current.lower() in name.lower():
                    selected_index = self.audio_device_combo.count() - 1

        self.audio_device_combo.setCurrentIndex(selected_index)

    def _refresh_audio_devices(self):
        """Обновить список устройств."""
        current_text = self.audio_device_combo.currentText()

        if current_text == "Авто (устройство по умолчанию)":
            current_value = self.config.get("audio_device", "")
        else:
            current_value = current_text

        self._load_audio_devices(current_value)