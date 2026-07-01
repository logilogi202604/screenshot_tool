"""Ensure only one copy of the tool runs at a time, cross-platform.

* Windows: a named kernel mutex (``CreateMutexW``).
* macOS / other POSIX: an advisory ``flock`` on a lockfile under CONFIG_DIR.

The acquired handle is kept alive for the whole process lifetime; releasing it
(process exit) frees the lock automatically.
"""
import os
import sys

# Module-level reference so the lock handle is never garbage-collected while the
# app runs (a closed file object would drop the flock).
_lock_handle = None


def acquire_single_instance():
    """Return True if we got the lock, False if another instance holds it."""
    global _lock_handle

    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.CreateMutexW(None, False, "ScreenshotTool_Singleton_v1")
        return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS

    # POSIX: advisory file lock. flock is released on process exit even if we
    # crash, so a stale lockfile never wedges the next launch.
    try:
        import fcntl

        from config import CONFIG_DIR

        os.makedirs(CONFIG_DIR, exist_ok=True)
        path = os.path.join(CONFIG_DIR, "singleton.lock")
        fh = open(path, "w")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False  # another instance holds the lock
        _lock_handle = fh
        return True
    except Exception:
        # If locking isn't supported for some reason, don't block startup.
        return True
