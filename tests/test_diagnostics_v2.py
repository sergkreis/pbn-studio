# SPDX-License-Identifier: GPL-3.0-only
import importlib
import json
import sys
from pathlib import Path


def test_journal_idempotent_metadata_and_utf8(tmp_path, monkeypatch):
    assert importlib.util.find_spec("pbnstudio.diagnostics") is not None, "journal module not implemented"
    d = importlib.import_module("pbnstudio.diagnostics")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PBN_DIAGNOSTICS", raising=False)
    try:
        path = d.initialize()
        assert isinstance(path, Path)
        assert path.parent == tmp_path / "PBNStudio" / "logs"
        assert d.initialize() == path
        d.event("test.action", width=800, height=600, count=3, revision=2, mode="проверка")
    finally:
        d.shutdown()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["event"] for r in rows].count("journal.start") == 1
    row = next(r for r in rows if r["event"] == "test.action")
    assert row["fields"]["mode"] == "проверка"
    assert row["fields"]["width"] == 800
    assert all(row[k] for k in ("run_id", "timestamp", "monotonic", "pid", "thread_id"))


def test_payloads_bounded_redacted_and_span_preserves_error(tmp_path, monkeypatch):
    from pbnstudio import diagnostics as d
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    class Hostile:
        def __str__(self): raise AssertionError("must not stringify payload")
        def __repr__(self): raise AssertionError("must not repr payload")
    path = d.initialize()
    marker = "PRIVATE_PAYLOAD_7f938"
    error = ValueError(marker)
    try:
        d.event("safe.settings", width=800, password=marker, image_bytes=marker.encode(),
                project_contents={"secret": marker}, env=marker, unknown=marker, mode="x" * 4000,
                settings=Hostile(), nan=float("nan"), huge=10**500)
        try:
            with d.span("job.simplify", revision=4):
                raise error
        except ValueError as caught:
            assert caught is error
        with d.span("job.success", count=3):
            pass
    finally:
        d.shutdown()
    raw = path.read_text(encoding="utf-8")
    assert marker not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    assert len(raw) < 16000
    assert all(len(line.encode("utf-8")) < 8000 for line in raw.splitlines())
    failure = next(r for r in rows if r["event"] == "job.simplify.error")
    assert failure["fields"]["exception_type"] == "ValueError"
    assert failure["fields"]["duration_ms"] >= 0
    assert failure["fields"]["traceback"][-1]["function"] == "test_payloads_bounded_redacted_and_span_preserves_error"
    end = next(r for r in rows if r["event"] == "job.success.end")
    assert end["fields"]["duration_ms"] >= 0


def test_rotation_multithread_retention_and_disabled(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pbnstudio import diagnostics as d
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(d, "MAX_BYTES", 4096, raising=False)
    monkeypatch.setattr(d, "FILE_COUNT", 3, raising=False)
    path = d.initialize()
    try:
        def produce(worker):
            for index in range(100):
                d.event("thread.action", worker=worker, index=index)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(produce, range(6)))
    finally:
        d.shutdown()
    files = list(path.parent.glob("*.jsonl*"))
    assert 1 < len(files) <= 3
    assert all(p.stat().st_size <= 4096 for p in files)
    rows = [json.loads(line) for p in files for line in p.read_text(encoding="utf-8").splitlines()]
    assert all(r["run_id"] == rows[0]["run_id"] for r in rows)
    monkeypatch.setenv("PBN_DIAGNOSTICS", "0")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "disabled"))
    before = sys.excepthook
    assert isinstance(d.initialize(), Path)
    d.event("ignored")
    with d.span("ignored"):
        pass
    d.shutdown()
    assert sys.excepthook is before
    assert not (tmp_path / "disabled").exists()


def test_async_writer_failure_and_fallback(tmp_path, monkeypatch):
    from pbnstudio import diagnostics as d
    import threading
    import time
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    fallback = tmp_path / "fallback"
    monkeypatch.setenv("LOCALAPPDATA", str(blocked))
    monkeypatch.setattr(d, "_app_directory", lambda: fallback, raising=False)
    path = d.initialize()
    try:
        assert path.parent == fallback / "logs"
        entered = threading.Event()
        release = threading.Event()
        original = d._Writer.write
        def stall_then_fail(self, record):
            entered.set()
            release.wait(2)
            raise OSError("disk unavailable")
        monkeypatch.setattr(d._Writer, "write", stall_then_fail)
        d.event("disk.test")
        assert entered.wait(1)
        start = time.monotonic()
        for i in range(10000):
            d.event("while.disk.blocked", count=i)
        assert time.monotonic() - start < 1.5
        release.set()
    finally:
        d.shutdown()
    assert path.exists()
    # Both destinations unavailable must not prevent operation or hooks.
    monkeypatch.setattr(d, "_app_directory", lambda: blocked)
    d.initialize()
    with d.span("still.works"):
        pass
    d.shutdown()


def test_exception_hooks_chain_and_qt_messages_redacted(tmp_path, monkeypatch):
    from pbnstudio import diagnostics as d
    import threading
    from types import SimpleNamespace
    from PySide6.QtCore import qInstallMessageHandler, qWarning
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    calls = []
    def original_sys(*args): calls.append("sys")
    def original_thread(args): calls.append("thread")
    monkeypatch.setattr(sys, "excepthook", original_sys)
    monkeypatch.setattr(threading, "excepthook", original_thread)
    old_qt = qInstallMessageHandler(lambda *args: calls.append("qt"))
    path = d.initialize()
    try:
        d.install_qt_handler()
        d.install_qt_handler()
        try:
            raise RuntimeError("SECRET_EXCEPTION_CONTENT")
        except RuntimeError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
            threading.excepthook(SimpleNamespace(exc_type=type(exc), exc_value=exc, exc_traceback=exc.__traceback__, thread=threading.current_thread()))
        qWarning("SECRET_QT_CONTENT")
    finally:
        d.shutdown()
        restored_qt = qInstallMessageHandler(old_qt)
    assert calls == ["sys", "thread", "qt"]
    assert sys.excepthook is original_sys
    assert threading.excepthook is original_thread
    raw = path.read_text(encoding="utf-8")
    assert "SECRET_" not in raw
    names = [json.loads(line)["event"] for line in raw.splitlines()]
    assert {"exception.python", "exception.thread", "qt.message"} <= set(names)


def test_one_shot_watchdog_stacks_and_short_job_cancellation(tmp_path, monkeypatch):
    from pbnstudio import diagnostics as d
    import time
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PBN_DIAGNOSTICS_WATCHDOG_SECONDS", "0.05")
    path = d.initialize()
    try:
        with d.span("job.long", width=50):
            time.sleep(.20)
        with d.span("job.short"):
            pass
        time.sleep(.1)
    finally:
        d.shutdown()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    slow = [r for r in rows if r["event"] == "watchdog.slow"]
    assert len(slow) == 1
    assert slow[0]["fields"]["operation"] == "job.long"
    assert slow[0]["fields"]["stack_frames"]
    assert len(json.dumps(slow)) < 12000
    assert not list(path.parent.glob("*.tmp"))


def _child(tmp_path, source, **extra_env):
    import os
    import subprocess
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env.pop("PBN_DIAGNOSTICS", None)
    env["LOCALAPPDATA"] = str(tmp_path)
    env.update(extra_env)
    return subprocess.run([sys.executable, "-c", source], cwd=Path(__file__).resolve().parents[1],
                          env=env, capture_output=True, text=True, timeout=15)


def test_real_unhandled_python_and_thread_errors(tmp_path):
    result = _child(tmp_path, """
from pbnstudio import diagnostics as d
import threading
d.initialize()
def fail():
    raise LookupError('PRIVATE_THREAD_MESSAGE')
t = threading.Thread(target=fail)
t.start()
t.join()
raise ValueError('PRIVATE_MAIN_MESSAGE')
""")
    assert result.returncode != 0
    assert "PRIVATE_MAIN_MESSAGE" in result.stderr
    assert "PRIVATE_THREAD_MESSAGE" in result.stderr
    files = list((tmp_path / "PBNStudio" / "logs").glob("*.jsonl"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    assert "PRIVATE_" not in raw
    assert {"exception.python", "exception.thread"} <= {json.loads(line)["event"] for line in raw.splitlines()}


def test_import_is_quiet_and_entrypoint_initializes_before_heavy_imports(tmp_path):
    imported = _child(tmp_path / "import", """
import sys
import pbnstudio.diagnostics
import pbnstudio.__main__
assert 'PySide6' not in sys.modules
assert 'numpy' not in sys.modules
""")
    assert imported.returncode == 0, imported.stderr
    assert not (tmp_path / "import").exists()
    started = _child(tmp_path / "main", """
from pbnstudio.__main__ import main
main(['--help'])
""")
    assert started.returncode == 0, started.stderr
    assert list((tmp_path / "main" / "PBNStudio" / "logs").glob("*.jsonl"))


def test_opt_in_faulthandler_snapshot_is_bounded_and_private(tmp_path):
    result = _child(tmp_path, """
from pbnstudio import diagnostics as d
import time
d.initialize()
with d.span('job.native'):
    time.sleep(.25)
d.shutdown()
""", PBN_DIAGNOSTICS_WATCHDOG_SECONDS="0.05", PBN_DIAGNOSTICS_FAULTHANDLER="1")
    assert result.returncode == 0, result.stderr
    directory = tmp_path / "PBNStudio" / "logs"
    files = list(directory.iterdir())
    assert all(p.suffix == ".jsonl" for p in files)
    raw = files[0].read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines()]
    native = [row for row in rows if row["event"] == "watchdog.faulthandler"]
    assert len(native) == 1
    assert native[0]["fields"]["stack_frames"]
    assert str(tmp_path) not in raw


def test_parallel_all_records_and_session_retention(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pbnstudio import diagnostics as d
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = d.initialize()
    try:
        def produce(worker):
            for index in range(80):
                d.event("parallel.record", worker=worker, index=index)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(produce, range(8)))
    finally:
        d.shutdown()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    pairs = {(r["fields"]["worker"], r["fields"]["index"]) for r in rows if r["event"] == "parallel.record"}
    assert pairs == {(w, i) for w in range(8) for i in range(80)}
    for _ in range(8):
        d.initialize()
        d.event("next.session")
        d.shutdown()
    assert len(list(path.parent.glob("journal-*.jsonl*"))) <= d.FILE_COUNT


def test_thread_start_failure_does_not_break_startup(tmp_path, monkeypatch):
    from pbnstudio import diagnostics as d
    import threading
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    before = sys.excepthook
    def fail(*args, **kwargs): raise RuntimeError("thread resource unavailable")
    monkeypatch.setattr(threading.Thread, "start", fail)
    try:
        assert isinstance(d.initialize(), Path)
        with d.span("application.still_works"):
            pass
    finally:
        d.shutdown()
    assert sys.excepthook is before
