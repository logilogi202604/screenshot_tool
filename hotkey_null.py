"""Fallback hotkey backend for platforms without a native implementation.

Registration always fails gracefully, so the app still runs and can be driven
from the tray icon; only the global hotkey is unavailable.
"""
from PySide6.QtCore import QObject, Signal


class GlobalHotkey(QObject):
    activated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.registered = False
        self.last_error = "no global-hotkey backend for this platform"

    @classmethod
    def from_config(cls, hk):  # noqa: ARG003
        return cls()

    def start(self, app):  # noqa: ARG002
        return False

    def register(self):
        return False

    def unregister(self):
        pass
