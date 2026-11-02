# SPDX-License-Identifier: GPL-3.0-only
"""Real one-shot worker source-contrast regressions (not a second tracer)."""
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from pbnstudio.engine import EngineOptions, run_image

ROOT = Path(__file__).resolve().parents[1]
FOX = ROOT.parent / "images/02-fox-forest.png"

def load(out):
    graph = json.loads((out / "graph.json").read_text())
    labels = np.fromfile(out / "labels.u32le", dtype="<u4").reshape(graph["height"], graph["width"])
    return graph, labels, np.asarray(graph["palette"])[labels, :3]

def test_black_navy_contrast_survives_bounded_auto_palette(tmp_path):
    image = Image.new("RGB", (256,256), (6,33,66))
    draw = ImageDraw.Draw(image)
    # Many broad colors compete with a smaller black accent for quantization.
    for i in range(24):
        x=(i%6)*40; y=(i//6)*40
        draw.rectangle((x,y,x+35,y+35), fill=((i*37)%256,(i*73)%256,(i*101)%256))
    draw.rectangle((120,185,139,224), fill=(5,3,2))
    source=tmp_path/"contrast.png";image.save(source)
    graph, labels, rgb = load(run_image(source,tmp_path/"job",EngineOptions(8,4,1)))
    assert labels[200,125] != labels[200,145], "black accent coalesced into navy"
    assert rgb[200,125].max() <= 24, "dark representative lost to weighted quantization"
    assert np.abs(rgb[200,125].astype(int)-rgb[200,145]).sum() >= 32
    assert len({tuple(c) for c in graph["palette"]}) <= 8

def test_frozen_fox_dark_accents(tmp_path):
    assert hashlib.sha256(FOX.read_bytes()).hexdigest() == "3442d72af18f4dbb61ee8b924ab401f223af8006efd47768a7e1adf0110265e1"
    graph, labels, rgb = load(run_image(FOX,tmp_path/"fox"))
    for x,y in [(440,750),(291,440)]:
        assert labels[y,x] != labels[750,425], "source black/navy ownership lost"
        assert rgb[y,x].max() <= 24
    assert len({tuple(c) for c in graph["palette"]}) <= 24
    navy=int(labels[750,425])
    distant=[e for e in graph["edges"] if navy in (e["left"],e["right"]) and max(p[1] for p in e["lattice"])<200]
    assert distant and any(e["kind"]=="cubic" for e in distant), "local ink protection disabled distant smooth geometry"


def test_connected_thin_ink_survives_speckle_without_noise_cells(tmp_path):
    image=Image.new("RGB",(180,100),(245,240,215));draw=ImageDraw.Draw(image)
    for y,width in [(20,1),(40,2),(60,3)]:
        draw.line((20,y,110,y),fill=(20,18,16),width=width)
    for x in range(5,175,7): draw.point((x,85),fill=(5,3,2))
    source=tmp_path/"ink.png";image.save(source)
    out=run_image(source,tmp_path/"ink")
    graph,labels,rgb=load(out)
    for y in [20,40,60]: assert np.all(rgb[y,20:111].max(axis=1)<130)
    assert np.all(rgb[85].min(axis=1)>180), "isolated noise resurrected"
    assert len(graph["components"])<=6
    info=graph.get("source_features",{})
    assert info.get("mode")=="dark-anchor-local-ink/1"
    assert info["restored_pixels"]>0
    assert json.loads((out/"manifest.json").read_text())["source_features"]==info


def test_fox_mouth_source_corridor_restored(tmp_path):
    graph,labels,rgb=load(run_image(FOX,tmp_path/"fox"))
    evidence=json.loads((ROOT/"reports/feedback-segmentation/mouth-full-metrics.json").read_text())
    ids=np.asarray(evidence["mask_indices"])
    assert (rgb.reshape(-1,3)[ids].max(axis=1)<130).sum()>=94
    # Pale left endpoint is actual source ink, not an invented closure.
    assert rgb[454,303].max()<200
    assert len(graph["components"])<300
    for edge in graph["edges"]:
        if edge["left"]!=0xffffffff and edge["right"]!=0xffffffff:
            assert graph["palette"][edge["left"]]!=graph["palette"][edge["right"]], "artificial same-paint ink partition"


def test_component_budget_warns_instead_of_exploding(tmp_path):
    image=Image.new("RGB",(400,400),(250,245,230));draw=ImageDraw.Draw(image)
    for y in range(10,390,12):
        for x in range(10,390,20): draw.line((x,y,x+9,y),fill=(20,18,16))
    source=tmp_path/"many-lines.png";image.save(source)
    graph,labels,rgb=load(run_image(source,tmp_path/"many"))
    info=graph["source_features"]
    assert info["restored_components"]<=128
    assert info["skipped_components"]>0
    assert "source-ink-component-budget-exceeded" in info["warnings"]
    assert len(graph["components"])<=130
    assert len(set(map(tuple,graph["palette"])))<=24


def test_solid_nonfox_shapes_stay_exact_and_no_restoration(tmp_path):
    image=Image.new("RGB",(120,100),(240,220,180));draw=ImageDraw.Draw(image)
    draw.rectangle((0,20,40,80),fill=(10,90,180))
    draw.rectangle((70,10,110,90),fill=(200,50,30))
    source=tmp_path/"shapes.png";image.save(source)
    graph,labels,rgb=load(run_image(source,tmp_path/"shapes",EngineOptions(24,4,1)))
    assert len(graph["components"])==3
    assert graph["source_features"]["restored_components"]==0
    assert np.array_equal(rgb,np.asarray(image))


def test_restored_ink_joins_existing_same_ink_not_false_partition(tmp_path):
    image=Image.new("RGB",(160,100),(245,240,215));draw=ImageDraw.Draw(image)
    draw.rectangle((100,30,125,70),fill=(5,3,2))
    draw.line((20,50,110,50),fill=(5,3,2),width=1)
    source=tmp_path/"joined.png";image.save(source)
    graph,labels,rgb=load(run_image(source,tmp_path/"joined"))
    assert labels[50,25]==labels[50,110], "restoration invented same-ink partition"
    assert len(graph["components"])==2


def test_thin_source_pixel_region_has_no_fake_closure_and_exact_native_contour(tmp_path):
    image=Image.new("RGB",(140,110),(245,240,215));draw=ImageDraw.Draw(image)
    points=[(20,75),(35,75),(60,60),(90,25),(100,25)]
    # Positive-width 4-connected open staircase, no diagonal-only contacts.
    for (x0,y0),(x1,y1) in zip(points,points[1:]):
        draw.line((x0,y0,x1,y0),fill=(40,35,30),width=1)
        draw.line((x1,y0,x1,y1),fill=(40,35,30),width=1)
    source=tmp_path/"curve.png";image.save(source)
    graph,labels,rgb=load(run_image(source,tmp_path/"curve"))
    source_dark=np.asarray(image).max(2)<130
    assert np.array_equal(rgb.max(2)<130,source_dark)
    assert (labels[55,85]==labels[10,10]), "open curve interior was closed/flood-filled"
    ink_owner=int(labels[75,20])
    for edge in graph["edges"]:
        if ink_owner in (edge["left"],edge["right"]):
            assert edge["kind"]=="polyline"
            assert np.array_equal(edge["geometry"],edge["lattice"])


def test_tight_palette_reports_unpreserved_dark_interface(tmp_path):
    image=Image.new("RGB",(200,100),(250,245,230));draw=ImageDraw.Draw(image)
    draw.rectangle((0,0,95,99),fill=(6,33,66))
    draw.rectangle((30,30,45,65),fill=(5,3,2))
    source=tmp_path/"two.png";image.save(source)
    graph,labels,rgb=load(run_image(source,tmp_path/"two",EngineOptions(2,4,1)))
    assert len(set(map(tuple,graph["palette"])))<=2
    assert graph["source_features"]["requested_colors"]==2
    assert rgb[40,35].max()<=24


def test_ink_representative_comes_from_source_not_invented_black(tmp_path):
    image=Image.new("RGB",(120,70),(245,240,215));draw=ImageDraw.Draw(image)
    draw.line((20,35,100,35),fill=(60,50,40),width=1)
    source=tmp_path/"brown.png";image.save(source)
    graph,labels,rgb=load(run_image(source,tmp_path/"brown"))
    assert np.abs(rgb[35,50].astype(int)-[60,50,40]).max()<=8
