# SPDX-License-Identifier: GPL-3.0-only
"""Isolated pinned native graph worker supervisor; no Qt dependencies."""
from dataclasses import dataclass, asdict
import math

class EngineError(RuntimeError):
    """Input, worker, or artifact validation failure."""

class EngineCancelled(EngineError):
    """The job was cancelled and its incomplete outputs removed."""

@dataclass(frozen=True)
class EngineOptions:
    colors: int = 24
    speckle: int = 4
    simplify: float = 1.0

    def __post_init__(self):
        if type(self.colors) is not int or not 2 <= self.colors <= 64:
            raise EngineError("colors must be an integer in 2..64")
        if type(self.speckle) is not int or not 0 <= self.speckle <= 32:
            raise EngineError("speckle must be a source-pixel length in 0..32 (area = length squared)")
        if type(self.simplify) not in (int, float) or not math.isfinite(self.simplify) or not 0 <= self.simplify <= 5:
            raise EngineError("simplify must be finite in 0..5 source pixels")

from pathlib import Path
import hashlib
import io
import json
import os
import shutil
import subprocess
import time
import sys
import stat
from PIL import Image, ImageCms, ImageOps

MAX_INPUT_BYTES = 256 * 1024 * 1024
NATIVE_TIMEOUT_SECONDS = 300.0
WORKER_PATH = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets/worker/pbn_worker.exe"
REQUIRED_FILES = ("graph.json", "labels.u32le", "components4.u32le", "preview.svg", "source.png")
PIN = "2500df76b5aea0ac22d15296b22722442f825c5b"

def _sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def _validate_artifacts(output, width, height, *, require_source=True):
    """Validate interchange structure; not a certification of spline topology."""
    import numpy as np
    import xml.etree.ElementTree as ET
    for name in (REQUIRED_FILES if require_source else REQUIRED_FILES[:-1]):
        path = output / name
        if not path.is_file() or path.is_symlink() or not path.stat().st_size:
            raise EngineError(f"Missing or unsafe artifact: {name}")
    def bad_constant(value):
        raise EngineError(f"Nonfinite graph value: {value}")
    graph = json.loads((output / "graph.json").read_text(), parse_constant=bad_constant)
    if graph["schema"] != "native-gate.graph/1" or (graph["width"], graph["height"]) != (width, height) or graph["outside"] != 0xffffffff:
        raise EngineError("Graph schema or dimensions mismatch")
    palette = graph["palette"]
    if not palette or any(len(c) != 4 or any(type(v) is not int or not 0 <= v <= 255 for v in c) for c in palette):
        raise EngineError("Invalid graph palette")
    maps = []
    for name, count in (("labels.u32le", len(palette)), ("components4.u32le", len(graph["components"]))):
        if (output / name).stat().st_size != width * height * 4:
            raise EngineError("Invalid map byte length")
        data = np.fromfile(output / name, dtype="<u4")
        # Normalized RGB is opaque: every source pixel belongs to a component.
        if not count or np.any(data >= count):
            raise EngineError("Map contains an invalid identifier")
        maps.append(data)
    regions = np.asarray([c["region"] for c in graph["components"]], dtype=np.int64)
    if np.any(regions < 0) or np.any(regions >= len(palette)) or not np.array_equal(regions[maps[1]], maps[0]):
        raise EngineError("Component-to-region mismatch")
    areas = np.bincount(maps[1], minlength=len(regions))
    for i, component in enumerate(graph["components"]):
        if component["id"] != i or component["area"] != areas[i] or areas[i] == 0:
            raise EngineError("Component id or area mismatch")
        x0, y0, x1, y1 = component["bbox"]
        if not 0 <= x0 < x1 <= width or not 0 <= y0 < y1 <= height:
            raise EngineError("Invalid component bounds")
    edges = graph["edges"]
    for i, edge in enumerate(edges):
        if edge["id"] != i or edge["kind"] not in ("cubic", "polyline"):
            raise EngineError("Invalid edge")
        coords = np.asarray(edge["geometry"], dtype=float)
        if not coords.size or not np.all(np.isfinite(coords)) or coords.shape[-1] != 2:
            raise EngineError("Invalid edge geometry")
        for side in ("left", "right"):
            if edge[side] != 0xffffffff and not 0 <= edge[side] < len(palette):
                raise EngineError("Invalid edge owner")
    for i, face in enumerate(graph["faces8"]):
        if face["id"] != i or not 0 <= face["region"] < len(palette):
            raise EngineError("Invalid face")
        for contour in face["contours"]:
            for edge, forward in contour:
                if type(edge) is not int or not 0 <= edge < len(edges) or type(forward) is not bool:
                    raise EngineError("Invalid oriented edge reference")
    svg = ET.parse(output / "preview.svg").getroot()
    if svg.tag.rsplit("}", 1)[-1] != "svg":
        raise EngineError("Invalid SVG artifact")
    if require_source:
        with Image.open(output / "source.png") as normalized:
            if normalized.size != (width, height) or normalized.mode != "RGB":
                raise EngineError("Normalized source mismatch")
    return graph

def run_image(input_path, output_dir, options=None, cancel_event=None, progress=None):
    """Run a fresh CLI process; progress is phase-local, never an ETA.

    Untagged inputs are assumed sRGB. EXIF is applied without resizing.
    ICC-tagged inputs are converted to sRGB; alpha is composited on white.
    """
    options = options if options is not None else EngineOptions()
    if type(options) is not EngineOptions:
        raise EngineError("options must be EngineOptions; unknown options are not accepted")
    options.__post_init__()
    output = Path(output_dir)
    try:
        output.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        raise EngineError(f"Output directory must be new and writable: {output}") from exc
    def emit(phase, fraction=None):
        if progress:
            progress({"phase": phase, "fraction": fraction})
    def check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise EngineCancelled("Processing cancelled")
    try:
        check_cancel()
        emit("normalize")
        with Path(input_path).open("rb") as stream:
            input_stat = os.fstat(stream.fileno())
            if not stat.S_ISREG(input_stat.st_mode):
                raise EngineError("Input must be a regular file")
            if input_stat.st_size > MAX_INPUT_BYTES:
                raise EngineError("Input exceeds byte budget (256 MiB)")
            original = stream.read(MAX_INPUT_BYTES + 1)
            if len(original) > MAX_INPUT_BYTES:
                raise EngineError("Input exceeds byte budget (256 MiB)")
        with Image.open(io.BytesIO(original)) as opened:
            if opened.width * opened.height > 40_000_000:
                raise EngineError("Input exceeds 40 million pixel budget; no implicit resizing")
            if getattr(opened, "n_frames", 1) != 1:
                raise EngineError("Animated/multi-frame input is not supported")
            image = ImageOps.exif_transpose(opened)
            icc = opened.info.get("icc_profile")
            if icc:
                try:
                    profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                    alpha = image.convert("RGBA").getchannel("A")
                    color_image = image if image.mode in ("RGB", "CMYK", "LAB", "L") else image.convert("RGB")
                    image = ImageCms.profileToProfile(color_image, profile, ImageCms.createProfile("sRGB"), outputMode="RGB")
                    image.putalpha(alpha)
                except Exception as exc:
                    raise EngineError("ICC profile conversion failed") from exc
            image = image.convert("RGBA")
        source = Image.alpha_composite(Image.new("RGBA", image.size, "white"), image).convert("RGB")
        width, height = source.size
        source.save(output / "source.png", icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
        (output / "input.rgba").write_bytes(source.convert("RGBA").tobytes())
        args = [str(WORKER_PATH), str((output / "input.rgba").resolve()), str(width), str(height), str(output.resolve()), str(options.colors), str(options.speckle), str(options.simplify)]
        check_cancel()
        child_env = os.environ.copy()
        child_env.pop("PYTHONHOME", None); child_env.pop("PYTHONPATH", None)
        with (output / "worker.stdout.log").open("wb") as stdout, (output / "worker.stderr.log").open("wb") as stderr:
            proc = subprocess.Popen(args, stdout=stdout, stderr=stderr, env=child_env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                deadline = time.monotonic() + NATIVE_TIMEOUT_SECONDS
                emit("native")
                while True:
                    check_cancel()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise EngineError(f"Native worker timed out after {NATIVE_TIMEOUT_SECONDS:g} seconds")
                    try:
                        proc.wait(timeout=min(0.04, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pass
                check_cancel()
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
        if proc.returncode:
            raise EngineError(f"Native worker exited {proc.returncode}: " + (output / "worker.stderr.log").read_text(errors="replace")[-3000:])
        check_cancel()
        emit("verify")
        graph = _validate_artifacts(output, width, height)
        (output / "input.rgba").unlink()
        files = [{"path": p.name, "bytes": p.stat().st_size, "sha256": _sha256(p)} for p in sorted(output.iterdir())]
        manifest = {"schema": "studio-artifacts/1", "width": width, "height": height,
                    "original_sha256": hashlib.sha256(original).hexdigest(), "settings": asdict(options),
                    "normalization": {"exif": "transpose", "icc": "converted-to-sRGB" if icc else "assume-sRGB-if-untagged", "alpha": "composite-white", "resize": False},
                    "engine": {"upstream_commit": PIN, "binary_sha256": _sha256(WORKER_PATH), "protocol": "one-shot-cli/1"}, "files": files}
        if graph.get("source_features") is not None:
            manifest["source_features"] = graph["source_features"]
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        stored = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        if stored != manifest:
            raise EngineError("Manifest read-back mismatch")
        for entry in stored["files"]:
            name = entry["path"]
            if Path(name).name != name or "/" in name or "\\" in name or ":" in name or name in (".", ".."):
                raise EngineError("Unsafe artifact path")
            if (output / entry["path"]).stat().st_size != entry["bytes"] or _sha256(output / entry["path"]) != entry["sha256"]:
                raise EngineError("Artifact hash mismatch")
        check_cancel()
        emit("complete", 1.0)
        return output
    except BaseException as exc:
        # Only this freshly created job directory is owned by the operation.
        # Windows virus scanners can briefly retain handles after child exit.
        cleanup_error = None
        for delay in (0, 0.05, 0.1, 0.2, 0.4):
            if delay:
                time.sleep(delay)
            try:
                shutil.rmtree(output)
                cleanup_error = None
                break
            except FileNotFoundError:
                cleanup_error = None
                break
            except OSError as failure:
                cleanup_error = failure
        if cleanup_error is not None:
            exc.add_note(f"Job cleanup incomplete ({type(cleanup_error).__name__}); temporary output retained: {output}")
        if not isinstance(exc, Exception) or isinstance(exc, EngineError):
            raise
        wrapped = EngineError(str(exc))
        for note in getattr(exc, "__notes__", ()):
            wrapped.add_note(note)
        raise wrapped from exc
