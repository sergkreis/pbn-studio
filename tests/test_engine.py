# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import math
import pytest

def test_options_strict_validation():
    assert importlib.util.find_spec("pbnstudio.engine") is not None, "engine implementation missing"
    from pbnstudio.engine import EngineOptions, EngineError
    assert (EngineOptions().colors, EngineOptions().speckle, EngineOptions().simplify) == (24, 4, 1.0)
    for field, values in {"colors": [1, 65, True, 2.5, "24"], "speckle": [-1,33,False,1.5], "simplify": [-1,6,float("nan"),float("inf"),True,"1"]}.items():
        for value in values:
            with pytest.raises(EngineError):
                EngineOptions(**{field:value})
    with pytest.raises(TypeError):
        EngineOptions(unknown=1)


def test_real_worker_full_resolution_manifest(tmp_path):
    import pbnstudio.engine as engine
    import json, hashlib
    from PIL import Image
    assert hasattr(engine, "run_image"), "run_image missing"
    source = tmp_path / "input.png"
    im = Image.new("RGBA", (137, 91), (200, 0, 0, 255))
    im.paste((0, 0, 255, 128), (65, 0, 137, 91)); im.save(source)
    events = []
    out = engine.run_image(source, tmp_path / "job", engine.EngineOptions(colors=4, speckle=0, simplify=0), progress=events.append)
    assert out == tmp_path / "job"
    graph = json.loads((out / "graph.json").read_text())
    manifest = json.loads((out / "manifest.json").read_text())
    assert graph["schema"] == "native-gate.graph/1"
    assert (graph["width"], graph["height"]) == (137, 91)
    assert manifest["schema"] == "studio-artifacts/1"
    assert manifest["width"] == 137 and manifest["height"] == 91
    assert manifest["original_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["settings"] == {"colors": 4, "speckle": 0, "simplify": 0}
    required = {"graph.json", "labels.u32le", "components4.u32le", "preview.svg", "source.png"}
    assert required <= {entry["path"] for entry in manifest["files"]}
    for entry in manifest["files"]:
        assert "/" not in entry["path"] and "\\" not in entry["path"]
        data = (out / entry["path"]).read_bytes()
        assert len(data) == entry["bytes"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    with Image.open(out / "source.png") as normalized:
        assert normalized.mode == "RGB" and normalized.size == (137, 91)
        assert normalized.getpixel((100, 20)) == (127, 127, 255)
    assert (out / "labels.u32le").stat().st_size == 137*91*4
    assert events[0]["phase"] == "normalize"
    assert any(e["phase"] == "native" and e["fraction"] is None for e in events)
    assert events[-1] == {"phase": "complete", "fraction": 1.0}
    assert not (out / "input.rgba").exists()


def test_cancel_hard_kills_real_process_and_next_job_restarts(tmp_path, monkeypatch):
    import threading, subprocess
    from PIL import Image
    import pbnstudio.engine as engine
    source = tmp_path / "input.png"
    Image.new("RGB", (600, 600), "red").save(source)
    event = threading.Event()
    children = []
    real_popen = subprocess.Popen
    def observe(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(engine.subprocess, "Popen", observe)
    phases = []
    def progress(update):
        phases.append(update["phase"])
        if update["phase"] == "native":
            event.set()
    with pytest.raises(engine.EngineCancelled):
        engine.run_image(source, tmp_path / "cancelled", cancel_event=event, progress=progress)
    assert children and children[0].poll() is not None
    assert children[0].returncode != 0
    assert not (tmp_path / "cancelled").exists()
    assert "complete" not in phases
    engine.run_image(source, tmp_path / "restart")
    assert children[-1].pid != children[0].pid and children[-1].returncode == 0


def test_cancel_before_start_never_publishes(tmp_path):
    import threading
    from PIL import Image
    import pbnstudio.engine as engine
    source = tmp_path / "input.png"
    Image.new("RGB", (10, 10), "red").save(source)
    event = threading.Event(); event.set()
    with pytest.raises(engine.EngineCancelled):
        engine.run_image(source, tmp_path / "cancelled", cancel_event=event)
    assert not (tmp_path / "cancelled").exists()


def test_rejects_bad_input_options_existing_output_and_icc(tmp_path):
    from PIL import Image
    import pbnstudio.engine as engine
    source = tmp_path / "input.png"
    Image.new("RGB", (10, 10), "red").save(source)
    existing = tmp_path / "existing"; existing.mkdir()
    (existing / "keep").write_text("safe")
    with pytest.raises(engine.EngineError):
        engine.run_image(source, existing)
    assert (existing / "keep").read_text() == "safe"
    for options in [{"colors": 4, "unknown": 1}, object()]:
        with pytest.raises(engine.EngineError):
            engine.run_image(source, tmp_path / "badoptions", options)
        assert not (tmp_path / "badoptions").exists()
    invalid = tmp_path / "invalid.png"; invalid.write_bytes(b"not an image")
    with pytest.raises(engine.EngineError):
        engine.run_image(invalid, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()
    Image.new("RGB", (10, 10), "red").save(source, icc_profile=b"invalid ICC profile")
    with pytest.raises(engine.EngineError, match="ICC"):
        engine.run_image(source, tmp_path / "badicc")
    assert not (tmp_path / "badicc").exists()


@pytest.mark.parametrize("corruption", ["missing", "maplength", "labelid", "componentid", "graphschema", "dimensions", "nonfinite", "edgeid", "manifesthash"])
def test_worker_artifacts_rejected_before_commit(tmp_path, corruption, monkeypatch):
    import json
    from PIL import Image
    import pbnstudio.engine as engine
    source = tmp_path / "input.png"; Image.new("RGB", (30, 20), "red").save(source)
    out = tmp_path / "job"
    def corrupt(update):
        if update["phase"] != "verify":
            return
        if corruption == "missing":
            (out / "preview.svg").unlink()
        elif corruption == "maplength":
            (out / "labels.u32le").write_bytes(b"bad")
        elif corruption in ("labelid", "componentid"):
            name = "labels.u32le" if corruption == "labelid" else "components4.u32le"
            data = bytearray((out / name).read_bytes()); data[:4] = (999999).to_bytes(4, "little"); (out / name).write_bytes(data)
        elif corruption == "manifesthash":
            # Tamper after hash calculation, when manifest is written.
            original = engine.Path.write_text
            def tamper(path, data, *args, **kwargs):
                result = original(path, data, *args, **kwargs)
                if path.name == "manifest.json":
                    (out / "preview.svg").write_bytes(b"tampered")
                return result
            monkeypatch.setattr(engine.Path, "write_text", tamper)
        else:
            graph = json.loads((out / "graph.json").read_text())
            if corruption == "graphschema": graph["schema"] = "unknown"
            if corruption == "dimensions": graph["width"] += 1
            if corruption == "nonfinite": graph["edges"][0]["geometry"][0][0][0] = float("nan")
            if corruption == "edgeid": graph["faces8"][0]["contours"][0][0][0] = 999999
            (out / "graph.json").write_text(json.dumps(graph))
    with pytest.raises(engine.EngineError):
        engine.run_image(source, out, progress=corrupt)
    assert not out.exists()


def test_native_options_change_effective_config_and_result(tmp_path):
    import json
    from PIL import Image, ImageDraw
    from pbnstudio.engine import EngineOptions, run_image
    image = Image.new("RGB", (100, 100), "white")
    draw = ImageDraw.Draw(image)
    for x, color in enumerate(["red", "green", "blue", "orange", "purple", "cyan"]):
        draw.rectangle((x*16,0,x*16+15,65),fill=color)
    for x in range(4, 96, 6):
        draw.rectangle((x,75,x+1,76),fill="black")
    draw.ellipse((15,20,85,93), fill="yellow")
    source = tmp_path / "source.png"; image.save(source)
    settings = [(2,0,0), (8,0,0), (8,8,0), (8,0,3)]
    outputs = []
    for index, (colors,speckle,simplify) in enumerate(settings):
        out = run_image(source, tmp_path / str(index), EngineOptions(colors,speckle,simplify))
        config = (out / "config.txt").read_text()
        assert f"filter_speckle: {speckle}" in config
        assert f"        {colors}," in config
        graph = json.loads((out / "graph.json").read_text())
        assert graph["curve_passes"] == (1 if simplify else 0)
        outputs.append((out, graph))
    assert len({tuple(c) for c in outputs[0][1]["palette"]}) <= 2
    assert len({tuple(c) for c in outputs[1][1]["palette"]}) > 2
    assert outputs[1][0].joinpath("labels.u32le").read_bytes() != outputs[2][0].joinpath("labels.u32le").read_bytes()
    assert [e["geometry"] for e in outputs[1][1]["edges"]] != [e["geometry"] for e in outputs[3][1]["edges"]]


def test_manifest_itself_is_read_back_and_verified(tmp_path, monkeypatch):
    import json
    from PIL import Image
    import pbnstudio.engine as engine
    source = tmp_path / "source.png"; Image.new("RGB", (8,8), "white").save(source)
    original = engine.Path.write_text
    def tamper(path, data, *args, **kwargs):
        if path.name == "manifest.json":
            manifest = json.loads(data); manifest["files"][0]["path"] = "../outside"; data = json.dumps(manifest)
        return original(path, data, *args, **kwargs)
    monkeypatch.setattr(engine.Path, "write_text", tamper)
    with pytest.raises(engine.EngineError):
        engine.run_image(source, tmp_path / "job")
    assert not (tmp_path / "job").exists()
