"""Automatic macOS-port tests: no display, no OS permissions required.

Covers the platform logic added for macOS support that test_smoke.py doesn't:
  A. the mac hotkey backend marshals its off-thread callback onto the Qt main thread
  B. the single-instance flock actually excludes a second *process*
  C. the copy path builds the clipboard payload (image+url+path) and autosaves

Run offscreen (no monitor needed):
    QT_QPA_PLATFORM=offscreen python test_mac.py

POSIX only — the single-instance lock uses fcntl.flock. Skipped on Windows,
where test_hotkey.py / test_null_hotkey.py exercise the native path instead.
"""
import os
import subprocess
import sys
import tempfile
import threading

if sys.platform == "win32":
    print("SKIP: test_mac.py targets macOS/POSIX (Windows uses the native backend)")
    raise SystemExit(0)

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from PySide6.QtCore import QPoint, QRect, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
MAIN_TID = threading.get_ident()


def test_copy_and_autosave():
    from annotations import RectAnnotation
    from config import DEFAULTS
    from overlay import ScreenshotOverlay

    cfg = dict(DEFAULTS)
    cfg["save_dir"] = tempfile.mkdtemp(prefix="shot_")
    cfg["autosave_on_copy"] = True

    pm = QPixmap(200, 100)
    pm.fill(QColor("#3366cc"))
    p = QPainter(pm); p.fillRect(QRect(0, 0, 100, 100), QColor("#cc6633")); p.end()
    pm.setDevicePixelRatio(1.25)

    overlay = ScreenshotOverlay(pm, QRect(0, 0, 160, 80), cfg)
    overlay.selection = QRect(20, 10, 80, 40)
    overlay.has_selection = True
    overlay.annotations.append(RectAnnotation(QColor("#ff0000"), 3, QPoint(25, 15)))
    overlay.annotations[-1].update_end(QPoint(90, 45))

    saved = []
    overlay.saved.connect(saved.append)
    overlay.copy_to_clipboard_and_close()

    md = QApplication.clipboard().mimeData()
    assert md.hasImage(), "clipboard missing image (paste-into-chat path)"
    assert md.hasUrls(), "clipboard missing file URL (drag-into-terminal path)"
    assert md.text().endswith(".png"), f"clipboard text not a png path: {md.text()!r}"
    assert saved and os.path.exists(saved[0]), "autosaved PNG not written"
    assert os.path.getsize(saved[0]) > 0, "autosaved PNG is empty"
    print(f"C copy/autosave OK: saved={os.path.basename(saved[0])} "
          f"clip(image={md.hasImage()},url={md.hasUrls()})")


def test_single_instance_cross_process():
    # Isolate the lockfile from any real running app by pointing CONFIG_DIR at a
    # temp dir (single_instance reads config.CONFIG_DIR at call time).
    import config
    config.CONFIG_DIR = tempfile.mkdtemp(prefix="lock_")
    from single_instance import acquire_single_instance

    child_src = (
        f"import sys,time; sys.path.insert(0,{REPO!r});"
        f"import config; config.CONFIG_DIR={config.CONFIG_DIR!r};"
        "from single_instance import acquire_single_instance;"
        "print('CHILD', acquire_single_instance(), flush=True); time.sleep(6)"
    )
    child = subprocess.Popen([sys.executable, "-c", child_src],
                             stdout=subprocess.PIPE, text=True)
    try:
        line = child.stdout.readline().strip()      # blocks until child locked
        assert line == "CHILD True", f"child failed to acquire: {line!r}"
        assert acquire_single_instance() is False, "lock did not exclude 2nd process"
        print("B single-instance OK: 2nd process refused while 1st holds lock")
    finally:
        child.terminate()
        child.wait(timeout=5)
    assert acquire_single_instance() is True, "lock not released after holder died"
    print("B single-instance OK: lock re-acquirable after holder exits")


def test_hidden_overlay_auto_cancels():
    """macOS hides Qt.Tool windows on app deactivation without a closeEvent.
    The overlay must treat that hide as cancel (emit `finished`), or the tray
    app stays 'busy' forever and the hotkey looks dead. Regression: 2026-07."""
    from config import DEFAULTS
    from overlay import ScreenshotOverlay

    pm = QPixmap(100, 60)
    pm.fill(QColor("#222222"))

    # 1) A hide with dialog_open set (save-dialog flow) must NOT cancel.
    o1 = ScreenshotOverlay(pm, QRect(0, 0, 100, 60), dict(DEFAULTS))
    done1 = []
    o1.finished.connect(lambda: done1.append(True))
    o1.show()
    o1.dialog_open = True
    o1.hide()
    QTimer.singleShot(150, app.quit)
    app.exec()
    assert not done1, "save-dialog hide wrongly cancelled the capture"
    o1.dialog_open = False
    o1.close()

    # 2) A bare hide (what macOS does on deactivate) must auto-cancel.
    o2 = ScreenshotOverlay(pm, QRect(0, 0, 100, 60), dict(DEFAULTS))
    done2 = []
    o2.finished.connect(lambda: done2.append(True))
    o2.show()
    o2.hide()
    QTimer.singleShot(150, app.quit)
    app.exec()
    assert done2, "hidden overlay never emitted finished (tray would stay busy)"
    # `finished` drives TrayApp state; it must fire exactly once even though
    # both the deferred hide-cancel and close() paths touched this overlay.
    QTimer.singleShot(150, app.quit)
    app.exec()
    assert len(done2) == 1, f"finished emitted {len(done2)} times, expected exactly 1"
    print("D hidden-overlay OK: deactivate-hide cancels once, save-dialog hide survives")


def test_trayapp_discards_stale_overlay():
    """TrayApp.start_capture must recover when the overlay got hidden without
    closing (whatever the cause) instead of staying busy forever."""
    import main as main_mod
    from config import DEFAULTS
    from overlay import ScreenshotOverlay
    from PySide6.QtCore import QObject, Signal

    class FakeHotkey(QObject):
        activated = Signal()
        registered = True
        last_error = 0

        @classmethod
        def from_config(cls, hk):
            return cls()

        def start(self, app):  # noqa: ARG002
            return True

        def unregister(self):
            pass

    cfg = dict(DEFAULTS)
    cfg["save_dir"] = tempfile.mkdtemp(prefix="shot_")

    orig_hotkey = main_mod.GlobalHotkey
    main_mod.GlobalHotkey = FakeHotkey
    try:
        tray = main_mod.TrayApp(app, cfg)
    finally:
        main_mod.GlobalHotkey = orig_hotkey
    # Keep the test synchronous: the recovery logic under test lives entirely
    # in start_capture; don't let the 120ms-deferred real capture run later.
    tray._do_capture = lambda: None

    pm = QPixmap(100, 60)
    pm.fill(QColor("#333333"))
    stale = ScreenshotOverlay(pm, QRect(0, 0, 100, 60), cfg)
    stale.finished.connect(tray._on_overlay_finished)  # same wiring as _do_capture
    tray.overlay = stale
    stale.show()
    stale.hide()  # hidden-but-not-closed, before the deferred cancel can run

    tray.start_capture()  # must discard the stale overlay and proceed
    assert tray.overlay is None, "stale hidden overlay not discarded"
    assert tray._pending_capture, "capture not rescheduled after discarding stale overlay"
    assert stale._closed, "stale overlay was dropped without being closed"

    # While a capture is legitimately pending, a second trigger must still be
    # ignored (the original double-trigger guard survives the recovery path).
    tray.start_capture()
    assert tray._pending_capture, "pending flag lost on duplicate trigger"
    tray._pending_capture = False
    print("E trayapp-recovery OK: stale overlay discarded, capture rescheduled")


def test_hotkey_main_thread_delivery():
    from hotkey_mac import GlobalHotkey

    gh = GlobalHotkey.from_config({"win": True, "shift": True, "key": "A"})
    result = {}

    def on_activated():
        result["tid"] = threading.get_ident()
        app.quit()

    gh.activated.connect(on_activated)

    def worker():                      # stand in for the pynput listener thread
        assert threading.get_ident() != MAIN_TID
        gh._on_activate()              # fire from a non-Qt thread

    QTimer.singleShot(0, lambda: threading.Thread(target=worker).start())
    QTimer.singleShot(3000, lambda: (result.setdefault("tid", None), app.quit()))
    app.exec()

    assert result.get("tid") == MAIN_TID, (
        f"hotkey slot ran on {result.get('tid')}, not main thread {MAIN_TID}")
    print("A hotkey thread-hop OK: off-thread callback delivered on Qt main thread")


if __name__ == "__main__":
    test_copy_and_autosave()
    test_single_instance_cross_process()
    test_hidden_overlay_auto_cancels()
    test_trayapp_discards_stale_overlay()
    test_hotkey_main_thread_delivery()   # runs the event loop; keep last
    print("\nALL MAC AUTO-TESTS PASSED")
    sys.stdout.flush()
    # Skip Qt's messy interpreter-shutdown teardown (a known PySide6 segfault when
    # C++ objects finalize after the QApplication singleton). Tests already passed.
    os._exit(0)
