# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in removable test journal. Importing performs no I/O."""
from __future__ import annotations
import atexit
import queue
import sys
import json
import math
import re
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time
import uuid
from datetime import datetime, timezone

MAX_BYTES = 2 * 1024 * 1024
FILE_COUNT = 5
QUEUE_SIZE = 2048
_state = None
_lifecycle = threading.RLock()


def _app_directory():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


class _Writer:
    """Only the daemon touches disk after initialization; producers never wait on disk."""
    def __init__(self, path):
        self.path = path
        self.stream = path.open("ab", buffering=0)
        self.size = path.stat().st_size
        self.max_bytes = MAX_BYTES
        self.file_count = FILE_COUNT
        self._prune()

    def _prune(self):
        try:
            paths = sorted(self.path.parent.glob("journal-*.jsonl*"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
            for old in paths[self.file_count:]:
                if old != self.path:
                    try:
                        old.unlink()
                    except OSError:
                        pass  # another running process may hold it open
        except OSError:
            pass

    def write(self, record):
        data = (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(data) > self.max_bytes:
            return
        if self.size + len(data) > self.max_bytes:
            self.stream.close()
            for number in range(self.file_count - 1, 0, -1):
                previous = self.path if number == 1 else Path(str(self.path) + "." + str(number - 1))
                following = Path(str(self.path) + "." + str(number))
                if previous.exists():
                    previous.replace(following)
            self.stream = self.path.open("wb", buffering=0)
            self.size = 0
            self._prune()
        self.stream.write(data)
        self.size += len(data)

    def close(self):
        self.stream.close()


class _Session:
    def __init__(self, path, run_id, writer):
        self.path, self.run_id, self.writer = path, run_id, writer
        self.queue = queue.Queue(maxsize=QUEUE_SIZE)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="pbn-journal-writer", daemon=True)
        self.dropped = 0
        self.old_sys_hook = sys.excepthook
        self.old_thread_hook = threading.excepthook
        self.sys_hook = self._sys_exception
        self.thread_hook = self._thread_exception
        self.qt_hook = None
        self.old_qt_hook = None
        self.timers = set()
        self.timer_lock = threading.Lock()
        try:
            self.watchdog_seconds = float(os.environ.get("PBN_DIAGNOSTICS_WATCHDOG_SECONDS", "0"))
            if not math.isfinite(self.watchdog_seconds) or not .01 <= self.watchdog_seconds <= 86400:
                self.watchdog_seconds = 0
        except ValueError:
            self.watchdog_seconds = 0

    def _sys_exception(self, kind, value, tb):
        _exception("exception.python", value, tb)
        self.old_sys_hook(kind, value, tb)

    def _thread_exception(self, args):
        _exception("exception.thread", args.exc_value, args.exc_traceback)
        self.old_thread_hook(args)

    def watchdog(self, name, started):
        if not self.watchdog_seconds or self.writer is None:
            return None
        def fire():
            try:
                if self.stop.is_set() or _state is not self:
                    return
                # Coordinates only: no local variables, source lines or exception text.
                frames = []
                for ident, frame in list(sys._current_frames().items())[:8]:
                    depth = 0
                    while frame is not None and depth < 3:
                        frames.append(dict(thread_id=ident, file=_label(Path(frame.f_code.co_filename).name),
                                           line=frame.f_lineno, function=_label(frame.f_code.co_name)))
                        frame = frame.f_back
                        depth += 1
                _emit("watchdog.slow", dict(operation=name, duration_ms=round((time.monotonic()-started)*1000, 3), stack_frames=frames))
                if os.environ.get("PBN_DIAGNOSTICS_FAULTHANDLER", "0") == "1":
                    self._fault_snapshot(name)
            except Exception:
                pass
            finally:
                with self.timer_lock:
                    self.timers.discard(timer)
        timer = threading.Timer(self.watchdog_seconds, fire)
        timer.daemon = True
        with self.timer_lock:
            self.timers.add(timer)
        timer.start()
        return timer

    def _fault_snapshot(self, name):
        # Optional native faulthandler snapshot: temporary file removed immediately.
        # Persist only bounded/redacted frame coordinates in rotating JSONL.
        import faulthandler
        import tempfile
        try:
            with tempfile.TemporaryFile(mode="w+b", dir=self.path.parent) as stream:
                faulthandler.dump_traceback(file=stream, all_threads=True)
                stream.seek(0)
                text = stream.read(1024 * 1024).decode("utf-8", "replace")
            frames = []
            for filename, line, function in re.findall(r'File "([^"\n]+)", line (\d+) in ([^\n]+)', text):
                frames.append(dict(file=_label(Path(filename).name), line=int(line), function=_label(function)))
                if len(frames) >= 24:
                    break
            _emit("watchdog.faulthandler", dict(operation=name, stack_frames=frames))
        except Exception:
            event("watchdog.capture_unavailable")

    def _run(self):
        try:
            while not self.stop.is_set() or not self.queue.empty():
                try:
                    record = self.queue.get(timeout=.05)
                except queue.Empty:
                    continue
                try:
                    if self.writer is not None:
                        self.writer.write(record)
                except Exception:
                    # Stop attempting writes after disk failure, no recursive error logging.
                    try:
                        self.writer.close()
                    except Exception:
                        pass
                    self.writer = None
        finally:
            if self.writer is not None:
                try:
                    self.writer.close()
                except Exception:
                    pass


def initialize() -> Path:
    """Return intended JSONL path (even when disabled/unavailable); idempotent."""
    global _state
    with _lifecycle:
        if _state is not None:
            return _state.path
        run_id = uuid.uuid4().hex
        base = Path(os.environ.get("LOCALAPPDATA") or str(_app_directory())) / "PBNStudio" / "logs"
        path = base / ("journal-" + run_id + ".jsonl")
        enabled = os.environ.get("PBN_DIAGNOSTICS", "1") != "0"
        writer = None
        if enabled:
            for directory in (base, _app_directory() / "logs"):
                candidate = directory / path.name
                try:
                    directory.mkdir(parents=True, exist_ok=True)
                    writer = _Writer(candidate)
                    path = candidate
                    break
                except Exception:
                    continue
        state = _Session(path, run_id, writer)
        _state = state
        if writer is not None:
            try:
                state.thread.start()
                sys.excepthook = state.sys_hook
                threading.excepthook = state.thread_hook
            except Exception:
                writer.close()
                state.writer = None
        event("journal.start", available=writer is not None, fallback=path.parent != base)
        return path


_KEY = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,63}$")
_DENY = re.compile(r"password|secret|token|credential|env|content|bytes|payload|path|filename|message", re.I)
_TEXT_FIELDS = {"mode", "state", "status", "action", "operation", "format", "geometry_mode", "reason", "exception_type", "phase", "job", "source", "stage"}


def _label(value, limit=80):
    if type(value) is not str:
        return "redacted"
    value = value[:limit]
    if not re.fullmatch(r"[\w.<> -]+", value, re.UNICODE):
        return "redacted"
    return value


def _safe(fields):
    result = {}
    for key, value in list(fields.items())[:24]:
        if not _KEY.fullmatch(key) or _DENY.search(key):
            continue
        if type(value) in (bool, type(None)):
            result[key] = value
        elif type(value) is int and abs(value) <= 10**15:
            result[key] = value
        elif type(value) is float and math.isfinite(value) and abs(value) <= 10**15:
            result[key] = value
        elif type(value) is str and key in _TEXT_FIELDS:
            result[key] = _label(value)
    return result


def _trace(tb):
    frames = []
    while tb is not None:
        code = tb.tb_frame.f_code
        frames.append(dict(file=_label(Path(code.co_filename).name), line=tb.tb_lineno,
                           function=_label(code.co_name)))
        frames = frames[-12:]
        tb = tb.tb_next
    return frames


def _emit(name, fields):
    if _state is None:
        return
    try:
        state = _state
        if state.writer is None or state.stop.is_set():
            return
        row = dict(event=_label(name), run_id=state.run_id, timestamp=datetime.now(timezone.utc).isoformat(),
                   monotonic=time.monotonic(), pid=os.getpid(), thread_id=threading.get_ident(), fields=fields)
        try:
            state.queue.put_nowait(row)
        except queue.Full:
            state.dropped += 1
    except Exception:
        pass  # diagnostics must never replace the original operation/error


def event(name, **fields):
    try:
        _emit(name, _safe(fields))
    except Exception:
        pass


def _exception(name, exc, tb=None, **fields):
    try:
        clean = _safe(fields)
        clean.update(exception_type=_label(type(exc).__name__), traceback=_trace(tb if tb is not None else exc.__traceback__))
        _emit(name, clean)
    except Exception:
        pass


@contextmanager
def span(name, **fields):
    name = _label(name, 64)
    started = time.monotonic()
    event(name + ".start", **fields)
    state = _state
    timer = None
    try:
        if state is not None:
            timer = state.watchdog(name, started)
    except Exception:
        pass
    try:
        yield
    except BaseException as exc:
        fields["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
        _exception(name + ".error", exc, **fields)
        raise
    else:
        fields["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
        event(name + ".end", **fields)
    finally:
        if timer is not None:
            timer.cancel()
            with state.timer_lock:
                state.timers.discard(timer)


def install_qt_handler():
    """Call immediately after importing QtCore, before importing UI; never imports Qt at startup."""
    state = _state
    if state is None or state.writer is None or state.qt_hook is not None:
        return
    try:
        from PySide6.QtCore import qInstallMessageHandler
        def handler(kind, context, message):
            # Qt messages may embed filenames/content; do not persist their text.
            event("qt.message", severity=int(kind.value), line=getattr(context, "line", 0))
            if state.old_qt_hook is not None:
                state.old_qt_hook(kind, context, message)
            elif sys.stderr is not None:
                # Preserve Qt's stderr reporting, without introducing disk waits.
                try:
                    sys.stderr.write(message + "\n")
                except Exception:
                    pass
        state.qt_hook = handler
        state.old_qt_hook = qInstallMessageHandler(handler)
    except Exception:
        state.qt_hook = None


def shutdown():
    global _state
    with _lifecycle:
        state = _state
        if state is None:
            return
        event("journal.stop", dropped=state.dropped)
        _state = None
        state.stop.set()
        if sys.excepthook is state.sys_hook:
            sys.excepthook = state.old_sys_hook
        if threading.excepthook is state.thread_hook:
            threading.excepthook = state.old_thread_hook
        if state.qt_hook is not None:
            try:
                from PySide6.QtCore import qInstallMessageHandler
                current = qInstallMessageHandler(state.old_qt_hook)
                if current is not state.qt_hook:
                    qInstallMessageHandler(current)
            except Exception:
                pass
        with state.timer_lock:
            for timer in state.timers:
                timer.cancel()
            state.timers.clear()
    if state.thread.ident is not None and state.thread is not threading.current_thread():
        state.thread.join(timeout=.5)


atexit.register(shutdown)
