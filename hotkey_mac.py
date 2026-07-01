"""System-wide hotkey on macOS via ``pynput``.

macOS has no direct equivalent of Win32 ``RegisterHotKey`` that plugs into the
Qt event loop, so we run a ``pynput`` keyboard listener on its own thread. The
listener fires our callback off-thread; we hop back onto the Qt main thread with
``QMetaObject.invokeMethod(..., QueuedConnection)`` before touching any GUI.

Requires a keyboard-monitoring permission (System Settings › Privacy &
Security) for whatever process hosts Python — the Terminal during development,
or the packaged ``ScreenshotTool.app``. Which toggle is needed varies by macOS
version: pynput's docs call for "Accessibility", while Catalina and later often
also require "Input Monitoring", so grant both. Without it the listener starts
but never receives events; the app stays usable via the tray icon.

``pynput`` does not *suppress* the triggering keystroke, so prefer a modifier
combo that produces no text (the default is ``Cmd+Shift+A``).
"""
from PySide6.QtCore import QObject, Signal, Slot, QMetaObject, Qt

# pynput hotkey tokens for keys that are not a single character.
_KEY_TOKENS = {
    "SPACE": "<space>", "ENTER": "<enter>", "RETURN": "<enter>", "TAB": "<tab>",
    "ESC": "<esc>", "ESCAPE": "<esc>",
    "F1": "<f1>", "F2": "<f2>", "F3": "<f3>", "F4": "<f4>", "F5": "<f5>",
    "F6": "<f6>", "F7": "<f7>", "F8": "<f8>", "F9": "<f9>", "F10": "<f10>",
    "F11": "<f11>", "F12": "<f12>",
}


def _key_token(key):
    key = str(key).upper().strip()
    if key in _KEY_TOKENS:
        return _KEY_TOKENS[key]
    # Single letters/digits map to themselves, lower-cased (pynput is case-based).
    return key[:1].lower() if key else "a"


def spec_from_config(hk):
    """Build a pynput ``GlobalHotKeys`` spec such as ``<cmd>+<shift>+a``.

    The shared config schema uses ``win`` for the platform "super" key, which is
    the Command key on macOS; ``alt`` is Option.
    """
    parts = []
    if hk.get("ctrl"):
        parts.append("<ctrl>")
    if hk.get("alt"):
        parts.append("<alt>")
    if hk.get("shift"):
        parts.append("<shift>")
    if hk.get("win"):
        parts.append("<cmd>")
    parts.append(_key_token(hk.get("key", "A")))
    return "+".join(parts)


class GlobalHotkey(QObject):
    activated = Signal()

    def __init__(self, spec, parent=None):
        super().__init__(parent)
        self._spec = spec
        self._listener = None
        self.registered = False
        self.last_error = 0

    @classmethod
    def from_config(cls, hk):
        return cls(spec_from_config(hk))

    @Slot()
    def _fire(self):
        # Runs on the Qt main thread; safe to emit into GUI slots.
        self.activated.emit()

    def _on_activate(self):
        # Called from the pynput listener thread — marshal onto the main thread.
        QMetaObject.invokeMethod(self, "_fire", Qt.QueuedConnection)

    def start(self, app):  # noqa: ARG002 - app kept for interface parity with Win
        return self.register()

    def register(self):
        try:
            from pynput import keyboard
        except Exception as e:  # pynput missing or failed to load its backend
            self.last_error = f"pynput unavailable: {e}"
            return False
        try:
            self._listener = keyboard.GlobalHotKeys({self._spec: self._on_activate})
            self._listener.start()  # spawns the listener thread
            self.registered = True
            return True
        except Exception as e:  # bad spec, or macOS denied the event tap
            self.last_error = str(e)
            self._listener = None
            return False

    def unregister(self):
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self.registered = False
