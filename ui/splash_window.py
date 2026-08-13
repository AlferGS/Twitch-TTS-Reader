"""Splash окно с анимацией загрузки."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QFrame, QLabel

try:
    from qfluentwidgets import IndeterminateProgressRing
    _HAS_RING = True
except ImportError:
    _HAS_RING = False
    from PyQt5.QtWidgets import QProgressBar

STARTUP_STAGES = [
    ("config", "Конфигурация"),
    ("gpu", "Проверка GPU"),
    ("server_start", "Запуск сервера"),
    ("model_load", "Загрузка модели"),
    ("ui", "Интерфейс"),
]


class SplashWindow(QDialog):
    """Splash окно с анимацией загрузки и списком стадий."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint)
        self.setFixedSize(340, 320)
        self._init_ui()
        self._center_on_screen()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(10)

        title = QLabel("🎙️ Twitch TTS Reader")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        layout.addSpacing(5)

        ring_layout = QHBoxLayout()
        ring_layout.addStretch()
        if _HAS_RING:
            self.progress_ring = IndeterminateProgressRing()
            self.progress_ring.setFixedSize(50, 50)
        else:
            self.progress_ring = QProgressBar()
            self.progress_ring.setRange(0, 0)
            self.progress_ring.setFixedSize(180, 18)
        ring_layout.addWidget(self.progress_ring)
        ring_layout.addStretch()
        layout.addLayout(ring_layout)
        layout.addSpacing(5)

        self.stage_label = QLabel("Инициализация...")
        self.stage_label.setAlignment(Qt.AlignCenter)
        self.stage_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.stage_label.setWordWrap(True)
        layout.addWidget(self.stage_label)
        layout.addSpacing(5)

        self.stages_widget = QFrame()
        self.stages_widget.setStyleSheet(
            "QFrame { background-color: rgba(128, 128, 128, 0.08); border-radius: 8px; }"
        )
        stages_layout = QVBoxLayout(self.stages_widget)
        stages_layout.setContentsMargins(15, 10, 15, 10)
        stages_layout.setSpacing(4)
        self.stage_labels = {}
        for key, text in STARTUP_STAGES:
            label = QLabel(f"○  {text}")
            label.setStyleSheet("color: gray; font-size: 12px;")
            stages_layout.addWidget(label)
            self.stage_labels[key] = label
        layout.addWidget(self.stages_widget)

    def _center_on_screen(self):
        from PyQt5.QtWidgets import QApplication
        screen = QApplication.primaryScreen().availableGeometry()
        x = (screen.width() - self.width()) // 2 + screen.x()
        y = (screen.height() - self.height()) // 2 + screen.y()
        self.move(x, y)

    def set_stage(self, stage_key):
        order = [key for key, _ in STARTUP_STAGES]
        if stage_key not in order:
            return
        current_index = order.index(stage_key)
        for i, key in enumerate(order):
            label = self.stage_labels[key]
            text = label.text()[3:] if len(label.text()) > 3 else label.text()
            if i < current_index:
                label.setText(f"✓  {text}")
                label.setStyleSheet("color: #008800; font-size: 12px;")
            elif i == current_index:
                label.setText(f"◌  {text}")
                label.setStyleSheet("color: #0066CC; font-size: 12px; font-weight: bold;")
            else:
                label.setText(f"○  {text}")
                label.setStyleSheet("color: gray; font-size: 12px;")

    def set_stage_text(self, text):
        if len(text) > 40:
            text = text[:37] + "..."
        self.stage_label.setText(text)

    def set_error(self, error_text):
        self.progress_ring.hide()
        self.stage_label.setText("❌ Ошибка запуска")
        self.stage_label.setStyleSheet("color: #CC0000; font-size: 13px; font-weight: bold;")

    def stop_animation(self):
        try:
            if hasattr(self, 'progress_ring'):
                self.progress_ring.hide()
                if hasattr(self.progress_ring, 'stop'):
                    self.progress_ring.stop()
                elif hasattr(self.progress_ring, 'stopAnimation'):
                    self.progress_ring.stopAnimation()
        except Exception:
            pass

    def closeEvent(self, event):
        self.stop_animation()
        event.accept()