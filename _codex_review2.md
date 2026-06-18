**Fix Verification**

1. CONFIRMED `hotkey.py:93-104` - `nativeEventFilter` accepts both `b"windows_generic_MSG"` and `b"windows_dispatcher_MSG"` at `hotkey.py:97`.

2. CONFIRMED `main.py:101-103` - tray menu is stored as `self.menu = menu = QMenu()`.

3. CONFIRMED `main.py:75`, `main.py:153-164` - `_pending_capture` is initialized, checked before scheduling, set before `singleShot`, and cleared in `_do_capture`.

4. CONFIRMED `config.py:40-47` - `load_config` rebuilds `hotkey` from defaults, updates only if user value is a dict, and restores a valid key if missing/malformed.

5. CONFIRMED `overlay.py:677-684`, `overlay.py:695-700` - `save_to_file` catches `os.makedirs` failure with fallback, closes only on `result.save(path)` success, otherwise restores overlay and warns.

**Remaining Defects**

[med] `toolbar.py:90-97`, `toolbar.py:165-171` - Current tool cannot actually be toggled off because the `QButtonGroup` is exclusive; Qt exclusive groups do not let the checked button be unchecked by clicking it, so the `if not checked` path is effectively unreachable. This leaves users stuck in drawing mode after selecting a tool. - Fix: manage the active tool manually with a non-exclusive group, or temporarily disable exclusivity before clearing the currently checked button and emitting `tool_changed(None)`.

[med] `config.py:33`, `main.py:149-150`, `overlay.py:663`, `overlay.py:678` - malformed user config such as `"save_dir": null` or a non-string value can still crash save/open flows because `os.makedirs()` raises `TypeError`, not `OSError`. - Fix: validate `save_dir` in `load_config` as a non-empty string path and fall back to `DEFAULTS["save_dir"]`; optionally catch `(OSError, TypeError, ValueError)` at call sites.

I did not modify files. I attempted to run tests read-only, but the sandbox policy rejected the command.