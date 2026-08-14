"""
Быстрая очередь сообщений на базе queue.SimpleQueue.
"""
import queue
import threading


class MessageQueue:
    """Потокобезопасная очередь сообщений чата."""

    __slots__ = ('_queue', '_counter', '_counter_lock')

    def __init__(self):
        self._queue = queue.SimpleQueue()
        self._counter = 0
        self._counter_lock = threading.Lock()

    def add_message(self, username, speak_text, prefix, item_id):
        with self._counter_lock:
            self._counter += 1
        self._queue.put((username, speak_text, prefix, item_id))

    def get_next(self):
        try:
            item = self._queue.get_nowait()
            with self._counter_lock:
                self._counter -= 1
            return item
        except queue.Empty:
            return None

    def is_empty(self):
        with self._counter_lock:
            return self._counter <= 0

    def size(self):
        with self._counter_lock:
            return max(0, self._counter)

    def clear(self):
        with self._counter_lock:
            while True:
                try:
                    self._queue.get_nowait()
                    self._counter -= 1
                except queue.Empty:
                    break
            self._counter = 0

    def trim_to(self, max_size):
        """Выбросить самые старые сообщения, если очередь длиннее max_size."""
        while self.size() > max_size:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            with self._counter_lock:
                self._counter -= 1