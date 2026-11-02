# SPDX-License-Identifier: GPL-3.0-only
"""Native acceptance on the fixed approved Recraft raster, no generated responses."""
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageCms
from pbnstudio.engine import EngineOptions, EngineError, run_image
import pytest

ROOT = Path(__file__).resolve().parents[1]
RECRAFT = ROOT.parent / "exports/recraft-japan-proof/00-original.png"

@pytest.mark.skipif(not RECRAFT.exists(), reason="approved local raster not available")
def test_approved_recraft_full_res(tmp_path):
    assert hashlib.sha256(RECRAFT.read_bytes()).hexdigest() == "fd15b0960284764680bb3b533b6c6c295bce4b239b9c34fda3b3800d93508482"
    output = run_image(RECRAFT, tmp_path / "recraft", EngineOptions(16,4,1.0))
    graph = json.loads((output / "graph.json").read_text())
    assert (graph["width"], graph["height"]) == (1326,1800)
    assert len(graph["components"]) > 1 and len(graph["edges"]) > 1
    preview = (output / "preview.svg").read_bytes()
    upstream = (output / "upstream.svg").read_bytes()
    receipt = json.loads((output / "worker.stdout.log").read_text().splitlines()[-1])
    assert receipt["preview_upstream_identical"] == (preview == upstream)
    assert graph["source_features"]["mode"] == "dark-anchor-local-ink/1"
    assert json.loads((output / "manifest.json").read_text())["source_features"] == graph["source_features"]
    import xml.etree.ElementTree as ET
    assert ET.fromstring(preview).tag.endswith("svg")
    assert ET.fromstring(upstream).tag.endswith("svg")
    assert len({tuple(c) for c in graph["palette"]}) <= 16


def test_exif_and_tagged_srgb_normalization(tmp_path):
    image = Image.new("RGB", (14, 9), (30, 70, 100))
    exif = Image.Exif(); exif[274] = 6
    source = tmp_path / "tagged.png"
    image.save(source, exif=exif, icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    out = run_image(source, tmp_path / "job")
    with Image.open(out / "source.png") as normalized:
        assert normalized.size == (9,14)
        assert normalized.getpixel((3,3)) == (30,70,100)
        assert normalized.info["icc_profile"]
    assert json.loads((out / "manifest.json").read_text())["normalization"]["icc"] == "converted-to-sRGB"


def test_real_worker_failure_cleans_output(tmp_path, monkeypatch):
    import pbnstudio.engine as engine
    import sys
    source = tmp_path / "source.png"; Image.new("RGB", (8,8), "white").save(source)
    monkeypatch.setattr(engine, "WORKER_PATH", Path(sys.executable))
    with pytest.raises(EngineError, match="exited"):
        run_image(source, tmp_path / "job")
    assert not (tmp_path / "job").exists()


def test_native_cli_rejects_unknown_and_invalid_options(tmp_path):
    import subprocess
    from pbnstudio.engine import WORKER_PATH
    rgba = tmp_path / "input.rgba"; rgba.write_bytes(bytes([255,0,0,255])*16)
    for extra in [("4","0","0","--unknown"), ("65","0","0"), ("4","33","0"), ("4","0","NaN"), ("4","0","6")]:
        out = tmp_path / "out"
        p = subprocess.run([str(WORKER_PATH), str(rgba), "4", "4", str(out), *extra], capture_output=True)
        assert p.returncode != 0 and not out.exists()
