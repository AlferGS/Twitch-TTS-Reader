"""
Страница чата с QTextBrowser и всплывающие слайдеры громкости/скорости.
"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTextBrowser, QDialog, QDialogButtonBox
)
from qfluentwidgets import (
    PrimaryPushButton, PushButton, ToolButton,
    SubtitleLabel, BodyLabel, Slider,
    Flyout, FlyoutViewBase, FluentIcon, SwitchButton, isDarkTheme
)

NICK_COLORS = [
    '#0066CC', '#CC0000', '#008800', '#8800CC',
    '#CC6600', '#00AABB', '#BB0088', '#669900',
]

REPLY_TEXT_MAX_LENGTH = 120

class SliderFlyoutView(FlyoutViewBase):
    """Всплывающий виджет со слайдером."""

    value_changed = pyqtSignal(float)

    def __init__(self, title, min_val, max_val, current_val, format_func, parent=None):
        super().__init__(parent)
        self.format_func = format_func
        self._init_ui(title, min_val, max_val, current_val)

    def _init_ui(self, title, min_val, max_val, current_val):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.title_label = BodyLabel(title)
        self.title_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.title_label)

        self.slider = Slider(Qt.Horizontal)
        self.slider.setMinimum(min_val)
        self.slider.setMaximum(max_val)
        self.slider.setValue(current_val)
        self.slider.setFixedWidth(220)
        layout.addWidget(self.slider)

        self.value_label = BodyLabel(self.format_func(current_val))
        self.value_label.setStyleSheet("color: #0066CC; font-weight: 600;")
        layout.addWidget(self.value_label)

        self.slider.valueChanged.connect(self._on_value_changed)

    def _on_value_changed(self, value):
        self.value_label.setText(self.format_func(value))
        self.value_changed.emit(value)


class ChatPage(QWidget):
    """Страница чата с HTML-рендерингом и подсветкой активного сообщения."""

    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    volume_changed = pyqtSignal(float)
    speed_changed = pyqtSignal(float)
    item_removed = pyqtSignal(int)
    tts_enabled_changed = pyqtSignal(bool)

    def __init__(self, message_limit=100, user_colors=None):
        super().__init__()
        self.messages = []
        self.active_id = None
        self._id_counter = 0
        self.message_limit = message_limit
        self.user_colors = user_colors or {}
        self._current_volume = 80
        self._current_speed = 100
        self._init_ui()

    def set_user_colors(self, user_colors):
        self.user_colors = user_colors or {}
        self._render_html()

    def set_message_limit(self, limit):
        self.message_limit = max(1, limit)
        while len(self.messages) > self.message_limit:
            if self.messages[0]['id'] == self.active_id:
                self.active_id = None
            removed_id = self.messages[0]['id']
            self.messages.pop(0)
            self.item_removed.emit(removed_id)
        self.counter_label.setText(f"Сообщений: {len(self.messages)}")
        self._render_html()

    def refresh_theme(self):
        self._render_html()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title_layout = QHBoxLayout()
        title_layout.addWidget(SubtitleLabel("Чат Twitch"))
        title_layout.addStretch()
        self.counter_label = BodyLabel("Сообщений: 0")
        title_layout.addWidget(self.counter_label)
        layout.addLayout(title_layout)

        control_layout = QHBoxLayout()
        self.start_btn = PrimaryPushButton("▶ Запустить")
        self.start_btn.clicked.connect(self.start_requested.emit)
        control_layout.addWidget(self.start_btn)

        self.stop_btn = PushButton("⏹ Остановить")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        control_layout.addWidget(self.stop_btn)

        self.volume_btn = ToolButton(FluentIcon.VOLUME)
        self.volume_btn.setToolTip("Громкость")
        self.volume_btn.setFixedSize(36, 36)
        self.volume_btn.clicked.connect(self._show_volume_flyout)
        control_layout.addWidget(self.volume_btn)

        self.speed_btn = ToolButton(FluentIcon.SPEED_HIGH)
        self.speed_btn.setToolTip("Скорость озвучки")
        self.speed_btn.setFixedSize(36, 36)
        self.speed_btn.clicked.connect(self._show_speed_flyout)
        control_layout.addWidget(self.speed_btn)

        control_layout.addSpacing(10)
        control_layout.addWidget(BodyLabel("Озвучка:"))
        self.tts_switch = SwitchButton()
        self.tts_switch.setChecked(True)
        self.tts_switch.setToolTip("Включить/выключить озвучку сообщений")
        self.tts_switch.checkedChanged.connect(self._on_tts_toggled)
        control_layout.addWidget(self.tts_switch)

        control_layout.addStretch()
        layout.addLayout(control_layout)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setReadOnly(True)
        self.browser.setStyleSheet(
            "QTextBrowser { border-radius: 8px; border: 1px solid #ddd; }"
        )
        self.browser.setContextMenuPolicy(Qt.NoContextMenu)
        layout.addWidget(self.browser)
        self._render_html()

    def _show_volume_flyout(self):
        try:
            view = SliderFlyoutView(
                "🔊 Громкость", 0, 100, self._current_volume,
                lambda v: f"{v}%"
            )
            view.value_changed.connect(self._on_volume_changed)
            Flyout.make(view, self.volume_btn, self)
        except Exception:
            self._show_fallback_slider(
                "Громкость", 0, 100, self._current_volume,
                self._on_volume_changed, "%"
            )

    def _show_speed_flyout(self):
        try:
            view = SliderFlyoutView(
                "⏩ Скорость озвучки", 50, 200, self._current_speed,
                lambda v: f"{v / 100:.1f}x"
            )
            view.value_changed.connect(self._on_speed_changed)
            Flyout.make(view, self.speed_btn, self)
        except Exception:
            self._show_fallback_slider(
                "Скорость", 50, 200, self._current_speed,
                self._on_speed_changed, "x"
            )

    def _show_fallback_slider(self, title, min_val, max_val, current_val, callback, suffix):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        label = BodyLabel(f"{title}: {current_val}{suffix}")
        layout.addWidget(label)
        slider = Slider(Qt.Horizontal)
        slider.setMinimum(min_val)
        slider.setMaximum(max_val)
        slider.setValue(current_val)
        layout.addWidget(slider)

        def on_change(value):
            label.setText(f"{title}: {value}{suffix}")
            callback(value)

        slider.valueChanged.connect(on_change)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec_()

    def _on_tts_toggled(self, checked):
        """Переключатель озвучки: блокирует ползунки и уведомляет MainWindow."""
        self.volume_btn.setEnabled(checked)
        self.speed_btn.setEnabled(checked)
        self.tts_enabled_changed.emit(checked)

    def _on_volume_changed(self, value):
        self._current_volume = int(value)
        self.volume_changed.emit(value / 100.0)

    def _on_speed_changed(self, value):
        self._current_speed = int(value)
        self.speed_changed.emit(value / 100.0)

    def _get_nick_color(self, username):
        if username in self.user_colors:
            return self.user_colors[username]
        return NICK_COLORS[hash(username) % len(NICK_COLORS)]

    def _html_escape(self, text):
        if text is None:
            return ""
        return (str(text)
                .replace('&', '&amp;').replace('<', '&lt;')
                .replace('>', '&gt;').replace('"', '&quot;')
                .replace('\n', '<br>'))

    def _get_html_style(self):
        """Генерировать CSS в зависимости от темы."""
        dark = isDarkTheme()
        if dark:
            bg = "#1e1e1e"
            text = "#e0e0e0"
            separator_color = "#888"
            system_text = "#aaaaaa"
            error_text = "#ff6666"
            # Контрастные цвета reply для тёмной темы
            reply_border = "#888"
            reply_text_color = "#dddddd"
            reply_user_color = "#eeeeee"
        else:
            bg = "white"
            text = "#222"
            separator_color = "#666"
            system_text = "#666"
            error_text = "#CC3333"
            # Контрастные цвета reply для светлой темы
            reply_border = "#999"
            reply_text_color = "#333333"
            reply_user_color = "#111111"

        return f"""
        <style>
        body {{
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 14px;
            margin: 0;
            padding: 4px;
            background-color: {bg};
            color: {text};
        }}
        .message {{
            padding: 6px 10px;
            margin: 3px 0;
            border-radius: 6px;
            border-left: 3px solid transparent;
            line-height: 1.4;
        }}
        .username {{
            font-weight: 600;
            margin-right: 2px;
        }}
        .separator {{
            color: {separator_color};
        }}
        .text {{
            color: {text};
            word-wrap: break-word;
        }}
        .message.system .text {{ color: {system_text}; font-style: italic; }}
        .message.error .text {{ color: {error_text}; }}
        .reply-quote {{
            padding: 2px 0 2px 8px;
            margin: 0 0 3px 4px;
            border-left: 2px solid {reply_border};
            font-size: 12px;
            line-height: 1.3;
        }}
        .reply-icon {{
            margin-right: 3px;
            font-size: 10px;
            color: {reply_text_color};
        }}
        .reply-user {{
            font-weight: 600;
            margin-right: 4px;
            color: {reply_user_color};
            font-size: 11px;
        }}
        .reply-text {{
            font-style: italic;
            color: {reply_text_color};
            font-size: 11px;
        }}
        </style>
        """

    def _format_reply_html(self, reply_info, block_style=""):
        """Цитата родительского сообщения. block_style — inline-фон при подсветке."""
        if not reply_info:
            return ""

        parent_user = (
            reply_info.get('parent_display_name') or
            reply_info.get('parent_user') or 'unknown'
        )
        parent_text = reply_info.get('parent_text') or ''

        if len(parent_text) > REPLY_TEXT_MAX_LENGTH:
            parent_text = parent_text[:REPLY_TEXT_MAX_LENGTH] + "..."

        if not parent_text.strip():
            return ""

        safe_user = self._html_escape(parent_user)
        safe_text = self._html_escape(parent_text)

        return (
            f'<div class="reply-quote"{block_style}>'
            f'<span class="reply-icon">↩</span>'
            f'<span class="reply-user">{safe_user}</span>'
            f'<span class="separator">:&nbsp;</span>'
            f'<span class="reply-text">{safe_text}</span>'
            f'</div>'
        )

    def _message_to_html(self, msg):
        """Превратить сообщение в HTML блок."""
        msg_id = msg['id']
        is_active = msg_id == self.active_id
        msg_class = msg.get('type', 'normal')

        # Qt не применяет .message.active к вложенным блокам,
        # поэтому фон задаём inline-стилем каждому блоку отдельно
        if is_active:
            active_bg = "#4a4520" if isDarkTheme() else "#FFF59D"
            block_style = f' style="background-color: {active_bg};"'
        else:
            block_style = ""

        # Системные сообщения
        if msg_class == 'system':
            text = f'<span class="text">{self._html_escape(msg["text"])}</span>'
            return f'<div id="msg-{msg_id}" class="message system"{block_style}>{text}</div>'

        # Ошибки
        if msg_class == 'error':
            text = f'<span class="text">⚠ {self._html_escape(msg["text"])}</span>'
            return f'<div id="msg-{msg_id}" class="message error"{block_style}>{text}</div>'

        # Обычное сообщение: цитата (если reply) + текст, каждый блок со своим фоном
        reply_html = self._format_reply_html(msg.get('reply_info'), block_style)

        color = self._get_nick_color(msg['username'])
        username = (
            f'<span class="username" style="color: {color};">'
            f'{self._html_escape(msg["username"])}</span>'
        )
        separator = '<span class="separator">:&nbsp;</span>'
        text = f'<span class="text">{self._html_escape(msg["text"])}</span>'

        return (
            f'<div id="msg-{msg_id}" class="message {msg_class}">'
            f'{reply_html}'
            f'<div{block_style}>{username}{separator}{text}</div>'
            f'</div>'
        )
    
    def _render_html(self, scroll_to_active=False):
        messages_html = '\n'.join(self._message_to_html(m) for m in self.messages)
        full_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">{self._get_html_style()}</head>
<body>{messages_html}</body></html>"""
        scrollbar = self.browser.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 20
        self.browser.setHtml(full_html)
        self.browser.viewport().update()
        if scroll_to_active and self.active_id is not None:
            scrollbar.setValue(scrollbar.maximum())
        elif was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _next_id(self):
        self._id_counter += 1
        return self._id_counter

    def add_message(self, username, text, msg_type="normal", reply_info=None):
        msg_id = self._next_id()
        message = {'id': msg_id, 'username': username, 'text': text, 'type': msg_type}
        if reply_info:
            message['reply_info'] = reply_info
        self.messages.append(message)

        while len(self.messages) > self.message_limit:
            if self.messages[0]['id'] == self.active_id:
                self.active_id = None
            removed_id = self.messages[0]['id']
            self.messages.pop(0)
            self.item_removed.emit(removed_id)

        self.counter_label.setText(f"Сообщений: {len(self.messages)}")
        self._render_html(scroll_to_active=False)
        return msg_id

    def highlight_item(self, msg_id):
        if self.active_id != msg_id:
            self.active_id = msg_id
            self._render_html(scroll_to_active=True)

    def unhighlight_item(self, msg_id):
        if self.active_id == msg_id:
            self.active_id = None
            self._render_html(scroll_to_active=False)

    def clear_highlight(self):
        if self.active_id is not None:
            self.active_id = None
            self._render_html(scroll_to_active=False)

    def set_running_state(self, is_running):
        self.start_btn.setEnabled(not is_running)
        self.stop_btn.setEnabled(is_running)