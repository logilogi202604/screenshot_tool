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

The triggering keystroke must not reach the frontmost app: e.g. Cmd+Shift+A is
Chrome's "search tabs" shortcut, and merely delivering the chord closes any open
<select> dropdown — exactly the UI the user is trying to capture. Passing
``darwin_intercept`` to the listener makes pynput create its event tap in
active (``kCGEventTapOptionDefault``) mode, letting us swallow the hotkey chord
system-wide; pynput's own hotkey matching runs *before* the intercept, so the
callback still fires. Active taps need the Accessibility permission (on top of
Input Monitoring); if that tap cannot start we fall back to a listen-only
listener, where the hotkey works but the chord leaks through as before.

The intercept must swallow *exactly* the events pynput activates on, or the two
disagree and the chord half-leaks. pynput's ``HotKey`` ignores modifiers outside
the combo (``Cmd+Shift+Ctrl+A`` still fires ``Cmd+Shift+A``), so we suppress
whenever the *required* modifiers are held rather than demanding an exact set.
And pynput matches character keys by the character the key *produces* under the
active layout (via ``canonical`` → ``KeyCode.from_char(char.lower())``), not by a
fixed keycode, so for letter/digit keys we match the produced Unicode character
too; only layout-stable special keys (F-keys, space, ...) are matched by keycode.

pynput's listener additionally taps ``NSSystemDefined`` events (media keys) and
converts each one with ``NSEvent.eventWithCGEvent_`` *on the listener thread*.
Converting the caps-lock sequence events macOS emits when Caps Lock switches
input sources — the default once a CJK input method is installed — runs
HIToolbox code that asserts it is on the main queue, killing the whole process
with SIGILL. We never consume media keys, so ``_listener_class`` narrows the
tap mask to plain key events; the fatal conversion becomes unreachable.
"""
import time

from PySide6.QtCore import QObject, Signal, Slot, QMetaObject, Qt

# pynput hotkey tokens for keys that are not a single character.
_KEY_TOKENS = {
    "SPACE": "<space>", "ENTER": "<enter>", "RETURN": "<enter>", "TAB": "<tab>",
    "ESC": "<esc>", "ESCAPE": "<esc>",
    "F1": "<f1>", "F2": "<f2>", "F3": "<f3>", "F4": "<f4>", "F5": "<f5>",
    "F6": "<f6>", "F7": "<f7>", "F8": "<f8>", "F9": "<f9>", "F10": "<f10>",
    "F11": "<f11>", "F12": "<f12>",
}

# Keys with no character — matched by keycode, which is layout-independent.
_SPECIAL_KEYS = set(_KEY_TOKENS)


def _key_token(key):
    key = str(key).upper().strip()
    if key in _KEY_TOKENS:
        return _KEY_TOKENS[key]
    # Single letters/digits map to themselves, lower-cased (pynput is case-based).
    return key[:1].lower() if key else "a"


def _listener_class(keyboard):
    """A ``GlobalHotKeys`` subclass whose tap only listens for plain key events.

    Drops ``NSSystemDefined`` (media keys, caps-lock sequences) from pynput's
    event mask so those events never reach its handler — see the module
    docstring for the SIGILL this prevents. Modifier tracking is unaffected:
    Caps Lock state itself arrives as ``flagsChanged``, which we keep. If a
    future pynput stops reading ``_EVENTS`` this override is silently ignored
    and we merely revert to upstream behavior.
    """
    try:
        import Quartz
        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
                | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
    except Exception:
        return keyboard.GlobalHotKeys

    class KeyEventsOnlyHotKeys(keyboard.GlobalHotKeys):
        _EVENTS = mask

    return KeyEventsOnlyHotKeys


# macOS virtual keycodes for the layout-independent special keys. Character keys
# (letters/digits) are intentionally absent: their physical keycode depends on
# the keyboard layout, so the intercept matches those by produced character, the
# same way pynput does — see ``_make_intercept``.
_SPECIAL_VK = {
    "ENTER": 36, "RETURN": 36, "TAB": 48, "SPACE": 49, "ESC": 53, "ESCAPE": 53,
    "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97, "F7": 98,
    "F8": 100, "F9": 101, "F10": 109, "F11": 103, "F12": 111,
}


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

    def __init__(self, spec, hk=None, parent=None):
        super().__init__(parent)
        self._spec = spec
        self._hk = hk or {}
        self._listener = None
        self.registered = False
        self.suppresses = False
        self.last_error = 0

    @classmethod
    def from_config(cls, hk):
        return cls(spec_from_config(hk), hk=hk)

    @Slot()
    def _fire(self):
        # Runs on the Qt main thread; safe to emit into GUI slots.
        self.activated.emit()

    def _on_activate(self):
        # Called from the pynput listener thread — marshal onto the main thread.
        QMetaObject.invokeMethod(self, "_fire", Qt.QueuedConnection)

    def start(self, app):  # noqa: ARG002 - app kept for interface parity with Win
        return self.register()

    def _make_intercept(self):
        """Build the CGEventTap callback that swallows the hotkey chord.

        Returns None when the trigger key cannot be resolved or Quartz is
        unavailable — the caller then registers listen-only, as before.
        """
        key = str(self._hk.get("key", "A")).upper().strip()
        special_vk = _SPECIAL_VK.get(key) if key in _SPECIAL_KEYS else None
        match_char = None
        if special_vk is None:
            # A character key: match on the produced character, lower-cased, so
            # this tracks pynput's layout-derived matching (and its Shift-folding
            # in ``canonical``) instead of a fixed, US-layout keycode.
            match_char = key[:1].lower()
            if not match_char.isalnum():
                return None
        try:
            import Quartz
        except Exception:
            return None

        # Suppress whenever the required modifiers are held, ignoring any extra
        # ones — this mirrors pynput's HotKey, which ignores keys outside the
        # combo, so Cmd+Shift+Ctrl+A both fires the capture and gets swallowed.
        required = 0
        for name, mask in (
            ("ctrl", Quartz.kCGEventFlagMaskControl),
            ("alt", Quartz.kCGEventFlagMaskAlternate),
            ("shift", Quartz.kCGEventFlagMaskShift),
            ("win", Quartz.kCGEventFlagMaskCommand),
        ):
            if self._hk.get(name):
                required |= mask

        key_down = Quartz.kCGEventKeyDown
        key_up = Quartz.kCGEventKeyUp
        get_field = Quartz.CGEventGetIntegerValueField
        keycode_field = Quartz.kCGKeyboardEventKeycode
        get_flags = Quartz.CGEventGetFlags
        get_unicode = Quartz.CGEventKeyboardGetUnicodeString

        def intercept(event_type, event):
            # Runs inside the event-tap callback for every keystroke
            # system-wide: keep it cheap (the produced-character lookup only
            # runs once the modifiers already match, so normal typing skips it)
            # and never raise — an exception here would take the whole tap down.
            try:
                if event_type == key_down or event_type == key_up:
                    flags = get_flags(event)
                    if (flags & required) == required:
                        if special_vk is not None:
                            if get_field(event, keycode_field) == special_vk:
                                return None  # frontmost app never sees it
                        else:
                            _, chars = get_unicode(event, 8, None, None)
                            if chars and chars[:1].lower() == match_char:
                                return None
            except Exception:
                pass
            return event

        return intercept

    def register(self):
        try:
            from pynput import keyboard
        except Exception as e:  # pynput missing or failed to load its backend
            self.last_error = f"pynput unavailable: {e}"
            return False

        listener_cls = _listener_class(keyboard)
        intercept = self._make_intercept()
        variants = ([{"darwin_intercept": intercept}] if intercept else []) + [{}]
        for kwargs in variants:
            try:
                listener = listener_cls(
                    {self._spec: self._on_activate}, **kwargs)
                listener.start()  # spawns the listener thread
                if self._await_listener(listener):
                    self._listener = listener
                    self.registered = True
                    self.suppresses = bool(kwargs)
                    return True
                listener.stop()
                self.last_error = "event tap failed (check Accessibility permission)"
            except Exception as e:  # bad spec, or macOS denied the event tap
                self.last_error = str(e)
        self._listener = None
        return False

    @staticmethod
    def _await_listener(listener, timeout=2.0):
        """Return True once the listener's tap is live, False if it failed.

        Deliberately does *not* call pynput's ``listener.wait()``: that blocks
        on a ready flag that a Quartz failure raising *before* ``_mark_ready``
        never sets, which would freeze app startup (register runs on the Qt main
        thread). Poll for a terminal state instead — ``_ready`` set, or the
        thread dead — capped by ``timeout`` so nothing can hang.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(listener, "_ready", False) or not listener.is_alive():
                break
            time.sleep(0.02)
        # The no-permission path flags ready and *then* the thread exits, so let
        # it finish dying before trusting is_alive() to tell tap-up from tap-gone.
        listener.join(0.2)
        return listener.is_alive()

    def unregister(self):
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        self.registered = False
