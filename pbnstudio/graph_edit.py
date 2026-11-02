# SPDX-License-Identifier: GPL-3.0-only
"""Exact edited ownership map -> pinned native shared graph (no Qt/source policy).

Palette indices are region IDs, not distinct colors. Output labels preserve every
ID; components4 are raster-order IDs with explicit components[].region ownership.
Disconnected/point-contact pixels may therefore yield multiple cells per input ID.
The returned graph-edit-artifacts/1 directory is deliberately NOT a source-bearing
studio document: consume graph.json/maps with Document._from_data and retain the
original source in the domain. Curve fitting is upstream, not topology-certified.
"""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import time
import numpy as np
from . import engine

MAX_PIXELS = 40_000_000
MAX_REGIONS = 1_000_000


def rebuild_from_map(labels: np.ndarray, palette: list, output_dir,
                     simplify: float = 1.0, *, cancel_event=None, progress=None) -> Path:
    """Rebuild opaque uint32 HxW labels without quantization or color merging.

    RGB or opaque RGBA palette entries are indexed by labels. Unused slots and
    duplicate colors are supported. OUTSIDE, partial alpha, floats and implicit
    integer casts are rejected. Output must not exist; failed jobs are removed.
    """
    def check():
        if cancel_event is not None and cancel_event.is_set():
            raise engine.EngineCancelled("cancelled")
    check()
    engine.EngineOptions(simplify=simplify)
    if not isinstance(labels, np.ndarray) or labels.ndim != 2 or labels.dtype.kind != "u" or labels.dtype.itemsize != 4:
        raise engine.EngineError("labels must be a 2-D uint32 array")
    h, w = labels.shape
    if not h or not w or labels.size > MAX_PIXELS:
        raise engine.EngineError("invalid dimensions/pixel budget")
    if not isinstance(palette, (list, tuple)) or not 1 <= len(palette) <= MAX_REGIONS:
        raise engine.EngineError("invalid palette count/budget")
    colors = []
    for color in palette:
        if not isinstance(color, (list, tuple)) or len(color) not in (3, 4) or any(type(v) is not int or not 0 <= v <= 255 for v in color):
            raise engine.EngineError("palette entries must be RGB/RGBA bytes")
        if len(color) == 4 and color[3] != 255:
            raise engine.EngineError("palette must be opaque")
        colors.append(list(color[:3]) + [255])
    if int(labels.max()) >= len(colors):
        raise engine.EngineError("invalid label identifier")
    # Snapshot caller-owned arrays before starting the process.
    raw = labels.astype("<u4", copy=True).tobytes()
    palette_bytes = bytes(v for c in colors for v in c)
    out = Path(output_dir)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise engine.EngineError("Output directory must be new and writable") from exc
    try:
        (out / "input.u32le").write_bytes(raw)
        (out / "palette.rgba").write_bytes(palette_bytes)
        args = [str(engine.WORKER_PATH), "--map", str((out / "input.u32le").resolve()),
                str(w), str(h), str(out.resolve()), str((out / "palette.rgba").resolve()), str(simplify)]
        env = os.environ.copy()
        env.pop("PYTHONHOME", None); env.pop("PYTHONPATH", None)
        with (out / "worker.stdout.log").open("wb") as stdout, (out / "worker.stderr.log").open("wb") as stderr:
            check()
            if engine.NATIVE_TIMEOUT_SECONDS <= 0:
                raise engine.EngineError("Native map worker timed out")
            child = subprocess.Popen(args, stdout=stdout, stderr=stderr, env=env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            started=time.monotonic()
            try:
                while child.poll() is None:
                    check()
                    if time.monotonic()-started>engine.NATIVE_TIMEOUT_SECONDS:
                        raise engine.EngineError("Native map worker timed out")
                    if progress: progress("native",0,1)
                    time.sleep(.05)
                check()
            finally:
                if child.poll() is None:
                    child.terminate()
                    try: child.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        child.kill();child.wait(timeout=2)
        if child.returncode:
            raise engine.EngineError(f"Native map worker exited {child.returncode}: " + (out / "worker.stderr.log").read_text(errors="replace")[-3000:])
        check()
        graph = engine._validate_artifacts(out, w, h, require_source=False)
        if (out / "labels.u32le").read_bytes() != raw or graph["palette"] != colors:
            raise engine.EngineError("Native worker changed ownership or palette")
        (out / "input.u32le").unlink()
        (out / "palette.rgba").unlink()
        manifest = {"schema": "graph-edit-artifacts/1", "width": w, "height": h,
                    "input_labels_sha256": hashlib.sha256(raw).hexdigest(),
                    "input_palette_rgba_sha256": hashlib.sha256(palette_bytes).hexdigest(),
                    "settings": {"simplify": simplify},
                    "ownership": {"region_ids": "preserved", "components4_ids": "raster-scan", "mapping": "components[].region"},
                    "engine": {"upstream_commit": engine.PIN, "binary_sha256": engine._sha256(engine.WORKER_PATH), "protocol": "exact-map-cli/1"},
                    "files": [{"path": f.name, "bytes": f.stat().st_size, "sha256": engine._sha256(f)} for f in sorted(out.iterdir())]}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf8")
        stored = json.loads((out / "manifest.json").read_text(encoding="utf8"))
        if stored != manifest or any((out / e["path"]).stat().st_size != e["bytes"] or engine._sha256(out / e["path"]) != e["sha256"] for e in stored["files"]):
            raise engine.EngineError("Artifact manifest/hash mismatch")
        check()
        return out
    except BaseException as exc:
        shutil.rmtree(out, ignore_errors=True)
        if not isinstance(exc, Exception) or isinstance(exc, engine.EngineError):
            raise
        raise engine.EngineError(str(exc)) from exc
