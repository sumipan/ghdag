"""Hybrid file watcher: watchdog event-driven with polling fallback."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
    from watchdog.observers.polling import PollingObserver

    _HAS_WATCHDOG = True
except ImportError:
    _HAS_WATCHDOG = False


class _ExecMdHandler:
    """Watchdog handler that sets a threading.Event when exec.md changes."""

    def __init__(self, target_path: str, change_event: threading.Event) -> None:
        self._target = os.path.basename(target_path)
        self._change_event = change_event

    def on_modified(self, event) -> None:
        if not event.is_directory and os.path.basename(event.src_path) == self._target:
            self._change_event.set()

    def on_created(self, event) -> None:
        self.on_modified(event)


if _HAS_WATCHDOG:

    class ExecMdEventHandler(FileSystemEventHandler, _ExecMdHandler):
        def __init__(self, target_path: str, change_event: threading.Event) -> None:
            FileSystemEventHandler.__init__(self)
            _ExecMdHandler.__init__(self, target_path, change_event)


class HybridWatcher:
    """Watch exec.md for changes using watchdog (native or polling fallback).

    Usage:
        watcher = HybridWatcher(exec_md_path)
        watcher.start()
        while True:
            if watcher.wait_for_change(timeout=5.0):
                # exec.md changed
                ...
        watcher.stop()
    """

    def __init__(self, exec_md_path: str | Path, poll_interval: float = 1.0) -> None:
        self._path = str(exec_md_path)
        self._dir = os.path.dirname(os.path.abspath(self._path)) or "."
        self._poll_interval = poll_interval
        self._change_event = threading.Event()
        self._observer = None
        self._use_watchdog = _HAS_WATCHDOG

    def start(self) -> None:
        if not self._use_watchdog:
            logger.info("watchdog not available — polling only")
            return

        handler = ExecMdEventHandler(self._path, self._change_event)

        # Try native observer first, fall back to polling
        try:
            self._observer = Observer()
            self._observer.schedule(handler, self._dir, recursive=False)
            self._observer.start()
            logger.info("watchdog native observer started for %s", self._dir)
        except Exception:
            logger.warning("Native observer failed — falling back to PollingObserver")
            try:
                self._observer = PollingObserver(timeout=self._poll_interval)
                self._observer.schedule(handler, self._dir, recursive=False)
                self._observer.start()
                logger.info("watchdog PollingObserver started for %s", self._dir)
            except Exception:
                logger.warning("PollingObserver also failed — no watchdog")
                self._observer = None
                self._use_watchdog = False

    def wait_for_change(self, timeout: float | None = None) -> bool:
        """Block until a change is detected or timeout expires.

        Returns True if a change was detected, False on timeout.
        """
        if self._use_watchdog and self._observer is not None:
            result = self._change_event.wait(timeout=timeout)
            self._change_event.clear()
            return result
        else:
            # Pure polling fallback
            time.sleep(timeout or self._poll_interval)
            return True  # Always re-check in polling mode

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
