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
    QFileDialog,
    QInputDialog,
    QMenu,
    QSystemTrayIcon,
)

from capture import grab_virtual_desktop
from config import CONFIG_DIR, load_config, save_config
from fileshare import FileShareService
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
            f"suppresses={getattr(self.hotkey, 'suppresses', False)} "
            f"err={getattr(self.hotkey, 'last_error', '?')}")
        self.hotkey.activated.connect(self._on_hotkey)

        self.fileshare = None
        # (name, is_send) -> (done, total) for every in-flight transfer.
        self._transfers = {}
        self._last_tip = None
        self._build_tray(ok)
        # Started after the tray exists so failures can be surfaced as a balloon
        # rather than only reaching app.log.
        self._start_fileshare()

    def _start_fileshare(self):
        if not self.config.get("fileshare_enabled", True):
            log("fileshare disabled by config")
            return
        self.fileshare = FileShareService(self.config, log=log, parent=self.app)
        self.fileshare.received.connect(self._on_file_received)
        self.fileshare.sent.connect(self._on_file_sent)
        self.fileshare.failed.connect(self._on_fileshare_failed)
        self.fileshare.progress.connect(self._on_transfer_progress)
        # Count only, never the names: anyone on the LAN can announce whatever
        # they like, and logging attacker-controlled strings on every change lets
        # a UDP flood grow app.log without bound.
        self.fileshare.peers_changed.connect(
            lambda peers: log(f"fileshare: {len(peers)} peer(s) visible")
        )
        if not self.fileshare.start():
            self.fileshare = None

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

        if self.config.get("fileshare_enabled", True):
            menu.addSeparator()
            # Rebuilt on every open: peers appear and vanish with the discovery
            # broadcasts, so a menu populated once would be stale in seconds.
            # Held on self for the same reason as `self.menu` above.
            self.send_menu = menu.addMenu("发送文件到…")
            self.send_menu.aboutToShow.connect(self._populate_send_menu)

            recv_dir = QAction("打开接收目录", menu)
            recv_dir.triggered.connect(self.open_recv_dir)
            menu.addAction(recv_dir)

            self.pair_menu = menu.addMenu("配对码")
            copy_code = QAction("复制本机配对码", self.pair_menu)
            copy_code.triggered.connect(self.copy_pairing_code)
            self.pair_menu.addAction(copy_code)
            set_code = QAction("输入对方配对码…", self.pair_menu)
            set_code.triggered.connect(self.set_pairing_code)
            self.pair_menu.addAction(set_code)

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
        try:
            os.makedirs(d, exist_ok=True)
        except (OSError, ValueError):
            log(f"open_save_dir: cannot create {d!r}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def open_recv_dir(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        d = self.config["fileshare_recv_dir"]
        try:
            os.makedirs(d, exist_ok=True)
        except (OSError, ValueError):
            log(f"open_recv_dir: cannot create {d!r}")
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _populate_send_menu(self):
        menu = self.send_menu
        menu.clear()
        if self.fileshare is None:
            disabled = QAction("传文件未启动（端口被占用）", menu)
            disabled.setEnabled(False)
            menu.addAction(disabled)
            return

        peers = self.fileshare.peers()
        if not peers:
            disabled = QAction("未发现其它机器", menu)
            disabled.setEnabled(False)
            menu.addAction(disabled)
            return

        for peer in peers:
            act = QAction(f"{peer['name']}   ({peer['host']})", menu)
            # Bind the peer as a default argument: a bare closure would capture
            # the loop variable and send every file to whichever peer is last.
            act.triggered.connect(lambda checked=False, p=peer: self._send_to_peer(p))
            menu.addAction(act)

    def _send_to_peer(self, peer):
        paths, _ = QFileDialog.getOpenFileNames(None, f"发送文件到 {peer['name']}")
        if not paths:
            return
        for path in paths:
            log(f"sending {path} -> {peer['name']} ({peer['host']}:{peer['port']})")
            self.fileshare.send(path, peer)

    def copy_pairing_code(self):
        code = self.config.get("fileshare_token", "")
        QGuiApplication.clipboard().setText(code)
        self.tray.showMessage(
            "配对码已复制",
            f"{code}\n\n到另一台机器的托盘菜单选「配对码 → 输入对方配对码」粘贴。\n"
            f"Deskflow 的剪贴板同步会把它一起带过去。",
            make_icon(),
            8000,
        )

    def set_pairing_code(self):
        code, ok = QInputDialog.getText(
            None, "输入对方配对码", "两台机器的配对码必须完全一致：",
            text=self.config.get("fileshare_token", ""),
        )
        if not ok or not code.strip():
            return
        self.config["fileshare_token"] = code.strip()
        save_config(self.config)
        # Update the live service too, so pairing takes effect without a restart.
        if self.fileshare is not None:
            self.fileshare.token = self.config["fileshare_token"]
        log("pairing code updated")
        self.tray.showMessage("配对码已更新", "两台机器现在可以互传文件了。", make_icon(), 4000)

    def _on_file_received(self, path, sender):
        self.tray.showMessage(
            f"收到文件 · 来自 {sender}",
            f"{os.path.basename(path)}\n已存到：{os.path.dirname(path)}",
            make_icon(),
            6000,
        )

    def _on_file_sent(self, name, peer_name):
        self.tray.showMessage("发送完成", f"{name}  →  {peer_name}", make_icon(), 4000)

    def _on_fileshare_failed(self, msg):
        log(f"fileshare error: {msg}")
        self.tray.showMessage("传文件出错", msg, QSystemTrayIcon.Warning, 8000)

    def _on_transfer_progress(self, name, done, total, is_send):
        # Tooltip only — a balloon per 256KB chunk would be unusable, and Qt has
        # no progress affordance in the tray itself.
        #
        # State is per transfer, not a single shared percentage: sends and
        # receives run concurrently, so one finishing must not clear the tip
        # while others are still going, and two at the same percent must not
        # suppress each other. The final string is still deduped, which is what
        # keeps a fast local transfer from churning the tooltip thousands of times.
        if total <= 0:
            return
        key = (name, bool(is_send))
        if done >= total:
            self._transfers.pop(key, None)
        else:
            self._transfers[key] = (done, total)

        if not self._transfers:
            tip = f"截图工具 · {self.combo}"
        elif len(self._transfers) == 1:
            (only_name, sending), (d, t) = next(iter(self._transfers.items()))
            tip = f"{'发送' if sending else '接收'}中 {only_name} … {int(d * 100 / t)}%"
        else:
            d = sum(v[0] for v in self._transfers.values())
            t = sum(v[1] for v in self._transfers.values())
            tip = f"{len(self._transfers)} 个传输中 … {int(d * 100 / t)}%"

        if tip != self._last_tip:
            self._last_tip = tip
            self.tray.setToolTip(tip)

    def start_capture(self):
        log(f"start_capture (busy={self.overlay is not None}, pending={self._pending_capture})")
        # Safety net: an overlay that is no longer really on screen but never
        # closed would block every future capture. Three known macOS paths get
        # here: app deactivation hides Qt.Tool windows (isVisible() goes
        # False); locking the screen orders the window out behind Qt's back —
        # isVisible() stays True, so only the native check sees it; and a
        # capture fired right after wake-from-sleep can leave the window
        # ordered in but never composited — even NSWindow.isVisible says YES,
        # only occlusionState exposes it (2026-07-08).
        if self.overlay is not None and not getattr(self.overlay, "dialog_open", False):
            on_screen = self.overlay.native_on_screen()
            err = getattr(self.overlay, "native_check_error", None)
            if err is not None:
                log(f"native visibility check failed ({err!r}); "
                    f"falling back to qt_visible={self.overlay.isVisible()}")
            if not on_screen:
                log(f"discarding stale off-screen overlay "
                    f"(qt_visible={self.overlay.isVisible()}, "
                    f"native={getattr(self.overlay, 'native_state', None)})")
                stale, self.overlay = self.overlay, None
                stale.close()
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
            # Composited per screen rather than one grab over virtualGeometry():
            # that rect is mixed-unit on multi-monitor Windows and puts every
            # screen but the first at the wrong offset. See capture.py.
            pixmap, geo = grab_virtual_desktop()
            if pixmap is None:
                log("capture aborted: no screens available")
                return
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
        # Logged because cancels are otherwise invisible in app.log — a capture
        # with no `saved` line is indistinguishable from a stuck overlay.
        log("overlay finished")
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
        if self.fileshare is not None:
            self.fileshare.stop()
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
