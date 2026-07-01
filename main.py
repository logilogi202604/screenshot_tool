"""Entry point: lives in the system tray, listens for the global hotkey,
and launches the capture overlay.
"""
import os
import sys
import traceback
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QColor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QSystemTrayIcon,
)

from config import CONFIG_DIR, load_config
from hotkey import GlobalHotkey, describe_hotkey
from overlay import ScreenshotOverlay
from single_instance import acquire_single_instance

LOG_PATH = os.path.join(CONFIG_DIR, "app.log")

# Strong reference to the running TrayApp so it is never garbage-collected.
_app_holder = None


def log(msg):
    """Append a timestamped line to the log file (windowed exe has no console)."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except OSError:
        pass


def make_icon():
    """Draw a simple camera icon so we don't ship a binary asset."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(QColor("#007aff"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(6, 14, 52, 40, 8, 8)
    p.setBrush(QColor("#cfe8ff"))
    p.drawRoundedRect(24, 8, 16, 10, 3, 3)
    p.setBrush(QColor("white"))
    p.drawEllipse(22, 26, 20, 20)
    p.setBrush(QColor("#007aff"))
    p.drawEllipse(28, 32, 8, 8)
    p.end()
    return QIcon(pm)


class TrayApp:
    def __init__(self, app, config):
        self.app = app
        self.config = config
        self.overlay = None
        self._pending_capture = False

        hk = config["hotkey"]
        self.combo = describe_hotkey(hk)
        # The backend is chosen per-platform inside `hotkey`; `start` installs
        # whatever event-loop hook it needs (native filter on Windows, listener
        # thread on macOS) and registers the combo.
        self.hotkey = GlobalHotkey.from_config(hk)
        ok = self.hotkey.start(app)
        log(f"hotkey {self.combo} register_ok={ok} "
            f"err={getattr(self.hotkey, 'last_error', '?')}")
        self.hotkey.activated.connect(self._on_hotkey)

        self._build_tray(ok)

    def _on_hotkey(self):
        log("hotkey pressed")
        self.start_capture()

    def _build_tray(self, hotkey_ok):
        self.tray = QSystemTrayIcon(make_icon(), self.app)
        self.tray.setToolTip(f"截图工具 · {self.combo}")

        # Keep a Python reference on self: setContextMenu() does not take ownership,
        # so a local-only QMenu would be garbage-collected and right-click would break.
        self.menu = menu = QMenu()
        cap = QAction(f"截图  ({self.combo})", menu)
        cap.triggered.connect(self.start_capture)
        menu.addAction(cap)

        open_dir = QAction("打开保存目录", menu)
        open_dir.triggered.connect(self.open_save_dir)
        menu.addAction(open_dir)

        menu.addSeparator()
        quit_act = QAction("退出", menu)
        quit_act.triggered.connect(self.quit)
        menu.addAction(quit_act)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        log("tray icon shown")

        if hotkey_ok:
            self.tray.showMessage(
                "截图工具已启动",
                f"按 {self.combo} 截图；单击/右键托盘图标也可截图。",
                make_icon(),
                4000,
            )
        else:
            self.tray.showMessage(
                "热键被占用",
                f"{self.combo} 已被其它程序占用，热键不可用。"
                f"请单击托盘图标截图，或改用其它热键（见 README）。",
                QSystemTrayIcon.Warning,
                8000,
            )

    def _on_tray_activated(self, reason):
        # Trigger(single-click)=3, DoubleClick=2 — use ints to avoid enum-scope
        # differences across PySide6 versions.
        log(f"tray activated reason={int(reason)}")
        if int(reason) in (2, 3):
            self.start_capture()

    def open_save_dir(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        d = self.config["save_dir"]
        os.makedirs(d, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def start_capture(self):
        log(f"start_capture (busy={self.overlay is not None}, pending={self._pending_capture})")
        # Guard against two triggers within the 120ms delay both scheduling a
        # capture (e.g. a fast double Alt+A, or hotkey + tray click).
        if self.overlay is not None or self._pending_capture:
            return
        self._pending_capture = True
        QTimer.singleShot(120, self._do_capture)

    def _do_capture(self):
        self._pending_capture = False
        if self.overlay is not None:
            return
        log("_do_capture begin")
        try:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                log("capture aborted: no primary screen")
                return
            geo = screen.virtualGeometry()
            pixmap = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
            self.overlay = ScreenshotOverlay(pixmap, geo, self.config)
            self.overlay.finished.connect(self._on_overlay_finished)
            self.overlay.saved.connect(self._on_saved)
            self.overlay.show()
            self.overlay.raise_()
            self.overlay.activateWindow()
            log(f"overlay shown geo={geo.width()}x{geo.height()}")
        except Exception:
            log("capture failed:\n" + traceback.format_exc())
            self.overlay = None

    def _on_overlay_finished(self):
        self.overlay = None

    def _on_saved(self, path):
        log(f"saved {path}")
        self.tray.showMessage(
            "已截图（已复制到剪贴板）",
            f"已保存到：\n{path}\n可把该文件拖入终端发给 Claude Code。",
            make_icon(),
            5000,
        )

    def quit(self):
        self.hotkey.unregister()
        self.tray.hide()
        self.app.quit()


def main():
    log("=== startup ===")
    if not acquire_single_instance():
        log("another instance already running; exiting")
        return 0

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Tray can be momentarily unavailable right after a process spawns; don't
    # treat that as fatal — just log it and carry on (the icon appears when ready).
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log("warning: system tray not reported available yet; continuing")

    config = load_config()
    try:
        # Keep a strong reference for the whole app lifetime. Without it the
        # TrayApp is garbage-collected: Qt keeps the tray icon alive on the C++
        # side (so it still shows), but the Python-side native event filter and
        # click handlers die, and Alt+A / tray clicks silently stop working.
        global _app_holder
        _app_holder = TrayApp(app, config)
    except Exception:
        log("TrayApp init failed:\n" + traceback.format_exc())
        raise
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        log("fatal:\n" + traceback.format_exc())
        raise
