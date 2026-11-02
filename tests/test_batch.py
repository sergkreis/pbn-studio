# SPDX-License-Identifier: GPL-3.0-only
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from test_ui import app
from PIL import Image


def test_batch_real_native_exports_each_image_without_overwrite(app, tmp_path):
    import pbnstudio
    import importlib.util
    assert importlib.util.find_spec("pbnstudio.batch") is not None, "offline batch service missing"
    from pbnstudio.batch import run_batch
    from pbnstudio.engine import EngineOptions
    inputs = []
    for folder, color in [("a", "red"), ("b", "blue")]:
        source = tmp_path/folder/"same.png"; source.parent.mkdir()
        Image.new("RGB", (24, 16), color).save(source); inputs.append(source)
    output = tmp_path/"out"; progress = []
    result = run_batch(inputs, output, EngineOptions(simplify=0), progress=progress.append, diagnostic=True)
    assert len(result.items) == 2 and not result.cancelled
    assert all(item.status == "exported" for item in result.items), result
    targets = [item.destination for item in result.items]
    assert len(set(targets)) == 2
    for target in targets:
        assert len(list(target.iterdir())) == 9
        assert (target/"DIAGNOSTIC-numbered.pdf").read_bytes().startswith(b"%PDF")
    original = [(target/"DIAGNOSTIC-numbered.svg").read_bytes() for target in targets]
    again = run_batch(inputs, output, EngineOptions(simplify=0), diagnostic=True)
    assert not set(targets) & {item.destination for item in again.items}
    assert original == [(target/"DIAGNOSTIC-numbered.svg").read_bytes() for target in targets]
    assert progress[-1]["fraction"] == 1
    assert result.report_path.is_file()


def test_batch_errors_preflight_and_cancellation_are_per_file(app, tmp_path):
    from pbnstudio.batch import run_batch
    from pbnstudio.engine import EngineOptions
    from threading import Event
    import json
    source = tmp_path/"good.png"; Image.new("RGB", (24,16), "red").save(source)
    bad = tmp_path/"bad.png"; bad.write_bytes(b"broken image")
    from pbnstudio.document import PrintSettings
    result = run_batch([bad, source], tmp_path/"out", EngineOptions(simplify=0), settings=PrintSettings(width_mm=20,height_mm=20,margin_mm=8,font_pt=100))
    assert [item.status for item in result.items] == ["error", "blocked"]
    assert result.items[1].issues and result.items[1].destination is None
    assert not list((tmp_path/"out").glob("*/numbered.pdf"))
    assert len(json.loads(result.report_path.read_text(encoding="utf-8"))["items"]) == 2
    stop = Event()
    def progress(payload):
        if payload.get("phase") == "prepared": stop.set()
    result = run_batch([source, source], tmp_path/"cancel", diagnostic=True,
                       cancel_event=stop, progress=progress)
    assert result.cancelled and [item.status for item in result.items] == ["cancelled", "cancelled"]
    assert not list((tmp_path/"cancel").glob("*/DIAGNOSTIC-numbered.pdf"))
    assert not list((tmp_path/"cancel").glob(".pbn-batch-*"))


def test_batch_auto_simplify_is_never_silently_ignored(app, tmp_path):
    from pbnstudio.batch import run_batch
    from pbnstudio.document import Document
    import pytest
    if callable(getattr(Document, "simplify_for_print", None)):
        pytest.skip("real domain auto-simplify is covered by the integration test")
    source = tmp_path/"good.png"; Image.new("RGB", (24,16), "red").save(source)
    with pytest.raises(ValueError, match="недоступ"):
        run_batch([source], tmp_path/"out", auto_simplify=True)
    assert not (tmp_path/"out").exists()


def test_batch_normal_export_uses_real_smooth_validation(app, tmp_path):
    from pbnstudio.batch import run_batch
    from pbnstudio.engine import EngineOptions
    source = tmp_path/"normal.png"; Image.new("RGB", (24,16), "red").save(source)
    result = run_batch([source], tmp_path/"normal-out", EngineOptions(simplify=0))
    assert result.items[0].status == "exported", result.items
    target = result.items[0].destination
    assert (target/"numbered.pdf").is_file()
    assert "DIAGNOSTIC" not in (target/"numbered.svg").read_text(encoding="utf-8")


def test_batch_cancel_keeps_previous_published_file_and_reports_every_input(app, tmp_path):
    from pbnstudio.batch import run_batch
    from threading import Event
    import json
    source = tmp_path/"good.png"; Image.new("RGB", (24,16), "red").save(source)
    stop = Event()
    def progress(payload):
        if payload.get("phase") == "batch" and payload.get("index") == 2: stop.set()
    result = run_batch([source, source, source], tmp_path/"out", cancel_event=stop, progress=progress)
    assert [item.status for item in result.items] == ["exported", "cancelled", "cancelled"]
    assert (result.items[0].destination/"numbered.pdf").is_file()
    data = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert len(data["items"]) == 3 and data["cancelled"]
    assert data["options"]["colors"] == 24
    assert data["diagnostic"] is False and data["auto_simplify"] is False


def test_batch_progress_is_monotonic_across_native_prepare_and_export(app, tmp_path):
    from pbnstudio.batch import run_batch
    source = tmp_path/"good.png"; Image.new("RGB", (24,16), "red").save(source)
    values = []
    run_batch([source,source], tmp_path/"out", progress=lambda p: values.append(p["fraction"]))
    assert values == sorted(values)
    assert values[-1] == 1
