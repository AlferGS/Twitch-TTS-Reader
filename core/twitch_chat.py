"""
Модуль чтения Twitch IRC чата.
Поддерживает произвольное количество префиксов и парсинг IRC тегов (reply).
Для получения тегов запрашивается capability twitch.tv/tags.
"""
import socket
import random
import time
import re
from PyQt5.QtCore import QThread, pyqtSignal

_RE_CHANNEL = [
    re.compile(r'twitch\.tv/popout/([^/]+)/chat', re.IGNORECASE),
    re.compile(r'twitch\.tv/popout/([^/]+)', re.IGNORECASE),
    re.compile(r'twitch\.tv/([^/]+)', re.IGNORECASE),
]
_RE_MENTION = re.compile(r'@\w+')
_RE_URL = re.compile(r'https?://\S+')
_RE_SPACES = re.compile(r'\s+')
_RE_PUNCT = re.compile(r'^[\s.,!?;:]+|[\s.,!?;:]+$')
_RE_USERNAME = re.compile(r':([^!]+)!')
_RE_PRIVMSG = re.compile(r'PRIVMSG #[^:]+:(.+)$')
_RE_REPEAT_PUNCT = re.compile(r'([!?.…])\s*\1+')

MAX_SPEAK_CHARS = 200

_EXCLUDED_CHANNELS = frozenset({
    'popout', 'videos', 'directory', 'settings',
    'subscriptions', 'turbo', 'prime', 'jobs'
})

_IRC_SERVERS = [
    ("irc.chat.twitch.tv", 6667),
    ("irc.chat.twitch.tv", 443),
]


class TwitchChatReader(QThread):
    """Поток чтения Twitch IRC чата с поддержкой тегов и префиксов."""

    message_received = pyqtSignal(str, str, str, str, object)
    error_occurred = pyqtSignal(str)

    def __init__(self, channel_url, prefix_mappings=None, ignore_words=None):
        super().__init__()
        self.channel_url = channel_url
        self.prefix_mappings = prefix_mappings or {}
        self.sorted_prefixes = sorted(self.prefix_mappings.keys(), key=len, reverse=True)
        self.prefix_patterns = {
            prefix: re.compile(r'^' + re.escape(prefix) + r'\s+')
            for prefix in self.sorted_prefixes
            if prefix.strip()
        }
        self.running = False
        self.sock = None
        self.channel = self._extract_channel_name(channel_url)
        self.ignore_words = [w.strip() for w in (ignore_words or []) if w.strip()]
        self.ignore_patterns = [
            re.compile(r'\b' + re.escape(word) + r'\b', re.IGNORECASE)
            for word in self.ignore_words
        ]
        self._buffer_size = 8192

    def _extract_channel_name(self, url):
        url_clean = url.split('?', 1)[0]
        for pattern in _RE_CHANNEL:
            match = pattern.search(url_clean)
            if match:
                channel = match.group(1).lower()
                if channel not in _EXCLUDED_CHANNELS:
                    return channel
        return None

    def _clean_for_speech(self, text):
        """Нормализация текста под причуды XTTS v2 (пункты 1,2,3,6,9,10)."""
        text = _RE_MENTION.sub('', text)            # @ники
        text = _RE_URL.sub('', text)                # ссылки
        for pattern in self.ignore_patterns:        # стоп-слова/смайлы
            text = pattern.sub('', text)
        text = _RE_SPACES.sub(' ', text)            # (9) пробелы/переносы
        text = _RE_REPEAT_PUNCT.sub(r'\1', text)    # (1) "!!!" -> "!", "..." -> "."
        text = text.replace('…', '.')               # (1) эллипс -> точка
        text = _RE_PUNCT.sub('', text)              # пунктуация по краям
        text = self._fix_caps(text)                 # (6) длинный КАПС -> вниз
        text = text.strip()

        if not text:
            return ''

        if len(text) > MAX_SPEAK_CHARS:
            text = text[:MAX_SPEAK_CHARS]
            text = text.rsplit(' ', 1)[0]

        # (1)+(2) точка в конце — маркер конца фразы (меньше обрывов хвоста),
        # но только для 3+ слов: на коротких модель озвучивает точку как слово
        if len(text.split()) >= 3 and text[-1] not in '.!?':
            text += ' '
        return text

    @staticmethod
    def _fix_caps(text):
        words = []
        for w in text.split(' '):
            if len(w) > 3 and w.isupper():
                w = w.lower()
            words.append(w)
        return ' '.join(words)
    
    def _clean_for_display(self, text):
        return _RE_SPACES.sub(' ', text).strip()

    def _parse_tags(self, line):
        if not line.startswith('@'):
            return {}
        space_idx = line.find(' ')
        if space_idx < 0:
            return {}
        tags = {}
        for tag in line[1:space_idx].split(';'):
            if not tag:
                continue
            if '=' in tag:
                key, value = tag.split('=', 1)
            else:
                key, value = tag, ""
            value = value.replace('\\:', ';').replace('\\s', ' ')
            value = value.replace('\\\\', '\\').replace('\\r', '\r').replace('\\n', '\n')
            tags[key] = value
        return tags

    def _extract_reply_info(self, tags):
        if 'reply-parent-msg-id' not in tags:
            return None
        return {
            'parent_id': tags.get('reply-parent-msg-id', ''),
            'parent_user': tags.get('reply-parent-user-login', ''),
            'parent_display_name': tags.get('reply-parent-display-name', ''),
            'parent_text': tags.get('reply-parent-msg-body', '')
        }

    def run(self):
        self.running = True
        reconnect_delay = 5
        if not self.channel:
            self.error_occurred.emit(f"Не удалось определить канал: {self.channel_url}")
            return
        while self.running:
            try:
                self._connect_and_listen()
            except Exception as e:
                if self.running:
                    self.error_occurred.emit(
                        f"Соединение потеряно: {str(e)}. Переподключение через {reconnect_delay}с..."
                    )
            if not self.running:
                break
            for _ in range(reconnect_delay):
                if not self.running:
                    break
                time.sleep(1)

    def _connect_and_listen(self):
        nickname = f"justinfan{random.randint(10000, 99999)}"
        channel_str = f"#{self.channel}"
        cap_bytes = b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n"
        nick_bytes = f"NICK {nickname}\r\n".encode('utf-8')
        join_bytes = f"JOIN {channel_str}\r\n".encode('utf-8')
        pong_bytes = b"PONG :tmi.twitch.tv\r\n"

        last_error = None
        for server, port in _IRC_SERVERS:
            if not self.running:
                return
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(10)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.sock.connect((server, port))
                self.sock.sendall(cap_bytes)
                self.sock.sendall(nick_bytes)
                self.sock.sendall(join_bytes)
                print(f"[TwitchChat] Подключено к {channel_str}")
                last_error = None
                break
            except (socket.error, socket.timeout) as e:
                last_error = e
                if self.sock:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                    self.sock = None
                continue

        if last_error is not None or self.sock is None:
            raise ConnectionError(f"Не удалось подключиться к IRC: {last_error}")

        sock = self.sock
        buffer = ""
        try:
            while self.running:
                try:
                    data = sock.recv(self._buffer_size)
                except socket.timeout:
                    continue
                except (socket.error, OSError):
                    if self.running:
                        raise
                    break
                if not data:
                    if self.running:
                        raise ConnectionError("Сервер закрыл соединение")
                    break
                buffer += data.decode('utf-8', errors='ignore')
                if 'PING' in buffer:
                    try:
                        sock.sendall(pong_bytes)
                    except socket.error:
                        break
                    idx = buffer.find('\n')
                    buffer = buffer[idx + 1:] if idx >= 0 else ""
                while True:
                    idx = buffer.find('\n')
                    if idx < 0:
                        break
                    line = buffer[:idx].rstrip('\r')
                    buffer = buffer[idx + 1:]
                    if line and 'PRIVMSG' in line:
                        self._parse_message(line)
        finally:
            self._close_socket()

    def _close_socket(self):
        sock = self.sock
        if sock is None:
            return
        self.sock = None
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except (socket.error, OSError):
            pass
        try:
            sock.close()
        except (socket.error, OSError):
            pass

    def _parse_message(self, line):
        try:
            tags = self._parse_tags(line)
            reply_info = self._extract_reply_info(tags)

            if line.startswith('@'):
                space_idx = line.find(' ')
                if space_idx > 0:
                    line = line[space_idx + 1:]

            username_match = _RE_USERNAME.search(line)
            if not username_match:
                return
            username = username_match.group(1)

            message_match = _RE_PRIVMSG.search(line)
            if not message_match:
                return
            raw_text = message_match.group(1).strip()

            prefix_found = None
            text_without_prefix = raw_text
            for prefix in self.sorted_prefixes:
                pattern = self.prefix_patterns.get(prefix)
                if pattern is None:
                    continue
                match = pattern.match(raw_text)
                if match:
                    prefix_found = prefix
                    text_without_prefix = raw_text[match.end():]
                    break

            display_text = self._clean_for_display(text_without_prefix)
            speak_text = self._clean_for_speech(text_without_prefix)

            self.message_received.emit(
                username, display_text, speak_text, prefix_found or "", reply_info
            )
        except Exception:
            pass

    def stop(self):
        self.running = False
        sock = self.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except (socket.error, OSError):
                pass

    def is_running(self):
        return self.running