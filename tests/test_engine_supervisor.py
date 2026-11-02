# SPDX-License-Identifier: GPL-3.0-only
"""Supervisor regressions; subprocess checks use real processes."""
from pathlib import Path
import pytest
from PIL import Image
import pbnstudio.engine as engine


def test_oversized_input_rejected_before_any_read(tmp_path, monkeypatch):
    source = tmp_path / "oversized.png"
    source.write_bytes(b"x" * 1025)
    monkeypatch.setattr(engine, "MAX_INPUT_BYTES", 1024, raising=False)
    original = Path.open
    def guarded(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        if path != source:
            return stream
        class Guard:
            def __enter__(self): return self
            def __exit__(self, *args): stream.close()
            def fileno(self): return stream.fileno()
            def read(self, size=-1):
                pytest.fail("oversized input was read before byte-budget rejection")
        return Guard()
    monkeypatch.setattr(Path, "open", guarded)
    with pytest.raises(engine.EngineError, match="byte budget"):
        engine.run_image(source, tmp_path / "job")
    assert not (tmp_path / "job").exists()


def test_read_is_bounded_if_input_grows_after_stat(tmp_path, monkeypatch):
    source = tmp_path / "growing.png"
    source.write_bytes(b"small")
    monkeypatch.setattr(engine, "MAX_INPUT_BYTES", 1024)
    original = Path.open
    reads = []
    def growing(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        if path != source:
            return stream
        class Guard:
            def __enter__(self): return self
            def __exit__(self, *args): stream.close()
            def fileno(self): return stream.fileno()
            def read(self, size=-1):
                reads.append(size)
                assert 0 < size <= 1025, "unbounded input read"
                return b"x" * size
        return Guard()
    monkeypatch.setattr(Path, "open", growing)
    with pytest.raises(engine.EngineError, match="byte budget"):
        engine.run_image(source, tmp_path / "job")
    assert reads == [1025]
    assert not (tmp_path / "job").exists()


def test_native_wall_timeout_kills_child_cleans_job_and_restarts(tmp_path, monkeypatch):
    import subprocess
    import sys
    import time
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 16), "red").save(source)
    monkeypatch.setattr(engine, "NATIVE_TIMEOUT_SECONDS", 0.08, raising=False)
    real_popen = subprocess.Popen
    children = []
    def hanging_child(*args, **kwargs):
        # Real bounded slow process; do not fake worker artifacts/results.
        child = real_popen([sys.executable, "-c", "import time; time.sleep(2)"], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(engine.subprocess, "Popen", hanging_child)
    start = time.monotonic()
    with pytest.raises(engine.EngineError, match="timed out"):
        engine.run_image(source, tmp_path / "timeout")
    assert time.monotonic() - start < 1.5
    assert children[0].poll() is not None and children[0].returncode != 0
    assert not (tmp_path / "timeout").exists()
    monkeypatch.setattr(engine.subprocess, "Popen", real_popen)
    monkeypatch.setattr(engine, "NATIVE_TIMEOUT_SECONDS", 300)
    output = engine.run_image(source, tmp_path / "restart")
    assert (output / "manifest.json").is_file()


def test_frozen_resource_root_runs_actual_bundled_worker(tmp_path, monkeypatch):
    import runpy
    import shutil
    import sys
    bundle = tmp_path / "_internal"
    worker = bundle / "assets/worker/pbn_worker.exe"
    worker.parent.mkdir(parents=True)
    shutil.copy2(engine.WORKER_PATH, worker)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    frozen_engine = runpy.run_path(engine.__file__)
    assert frozen_engine["WORKER_PATH"] == worker
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 16), "red").save(source)
    result = frozen_engine["run_image"](source, tmp_path / "job")
    assert (result / "manifest.json").exists()


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows device regression")
def test_device_input_is_rejected_as_nonregular(tmp_path):
    with pytest.raises(engine.EngineError, match="regular file"):
        engine.run_image("NUL", tmp_path / "job")
    assert not (tmp_path / "job").exists()


@pytest.mark.parametrize("callback", ["progress", "cancel"])
@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_callback_errors_reap_real_native_child_and_remove_job(tmp_path, monkeypatch, callback, error):
    import subprocess
    source = tmp_path / "source.png"
    Image.new("RGB", (600, 600), "red").save(source)
    real_popen = subprocess.Popen
    children = []
    def observe(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(engine.subprocess, "Popen", observe)
    class BrokenCancel:
        def is_set(self):
            if children:
                raise error("callback failure")
            return False
    def progress(update):
        if update["phase"] == "native":
            raise error("callback failure")
    kwargs = {"progress": progress} if callback == "progress" else {"cancel_event": BrokenCancel()}
    expected = engine.EngineError if error is RuntimeError else error
    with pytest.raises(expected, match="callback failure"):
        engine.run_image(source, tmp_path / "job", **kwargs)
    assert children and children[0].poll() is not None
    assert not (tmp_path / "job").exists()


def test_timeout_kills_actual_native_worker(tmp_path, monkeypatch):
    import subprocess
    source = tmp_path / "source.png"
    Image.new("RGB", (600, 600), "red").save(source)
    monkeypatch.setattr(engine, "NATIVE_TIMEOUT_SECONDS", 0.001)
    real_popen = subprocess.Popen
    children = []
    def observe(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(engine.subprocess, "Popen", observe)
    with pytest.raises(engine.EngineError, match="timed out"):
        engine.run_image(source, tmp_path / "job")
    assert children and children[0].poll() is not None and children[0].returncode != 0
    assert not (tmp_path / "job").exists()


def test_pixel_budget_rejects_header_before_decode(tmp_path):
    import struct
    import zlib
    source = tmp_path / "large-header.png"
    Image.new("RGB", (1, 1), "red").save(source)
    data = bytearray(source.read_bytes())
    data[16:24] = struct.pack(">II", 8000, 6000)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
    source.write_bytes(data)
    with pytest.raises(engine.EngineError, match="40 million pixel budget"):
        engine.run_image(source, tmp_path / "job")
    assert not (tmp_path / "job").exists()


def test_input_at_byte_budget_is_accepted(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 16), "red").save(source)
    monkeypatch.setattr(engine, "MAX_INPUT_BYTES", source.stat().st_size)
    assert (engine.run_image(source, tmp_path / "job") / "manifest.json").exists()


@pytest.mark.parametrize("existing_kind", ["directory", "file"])
def test_existing_destination_never_deleted_on_callback_error(tmp_path, existing_kind):
    source = tmp_path / "source.png"
    Image.new("RGB", (16, 16), "red").save(source)
    output = tmp_path / "existing"
    if existing_kind == "directory":
        output.mkdir()
        marker = output / "keep"
    else:
        marker = output
    marker.write_bytes(b"must survive")
    def broken(update):
        raise RuntimeError("callback must not be reached")
    with pytest.raises(engine.EngineError, match="must be new"):
        engine.run_image(source, output, progress=broken)
    assert marker.read_bytes() == b"must survive"
