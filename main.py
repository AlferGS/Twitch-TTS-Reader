"""Точка входа в приложение (альтернатива launcher.py)."""
import sys
import os
import json
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt


def main(cleanup_callback=None):
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    from qfluentwidgets import setTheme, Theme
    theme_str = "auto"
    if os.path.exists("config.json"):
        try:
            with open("config.json", 'r', encoding='utf-8') as f:
                theme_str = json.load(f).get("theme", "auto")
        except Exception:
            pass

    if theme_str == "dark":
        setTheme(Theme.DARK)
    elif theme_str == "light":
        setTheme(Theme.LIGHT)
    else:
        setTheme(Theme.AUTO)

    from ui.main_window import MainWindow
    window = MainWindow()

    if cleanup_callback is not None:
        app.aboutToQuit.connect(cleanup_callback)
        window._cleanup_callback = cleanup_callback
    else:
        window._cleanup_callback = None

    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()