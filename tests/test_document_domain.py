# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import importlib, json, shutil
import pytest

FIXTURES=Path(__file__).resolve().parents[2]/"research/stage1/native-gate/outputs"

def domain():
    spec=importlib.util.find_spec("pbnstudio.document")
    assert spec is not None, "Document module not implemented"
    return importlib.import_module("pbnstudio.document")

def test_load_requires_manifest_and_explicit_trust():
    m=domain()
    with pytest.raises(ValueError,match="manifest"):
        m.Document.load(FIXTURES/"hole")
    d=m.Document.load(FIXTURES/"hole",trusted=True)
    assert (d.width,d.height)==(9,9)
    assert len(d.palette)==2
    assert d.sample_component(4,4)==1
    assert d.sample_component(-1,0) is None
    assert d.sample_component(9,0) is None
    assert d.face_components=={0:[0],1:[1]}

def test_reject_invalid_graph_and_maps(tmp_path):
    m=domain(); shutil.copytree(FIXTURES/"hole",tmp_path/"job")
    p=tmp_path/"job/graph.json"; g=json.loads(p.read_text())
    g["edges"][0]["geometry"][0][0]=float("nan");p.write_text(json.dumps(g))
    with pytest.raises(ValueError): m.Document.load(p.parent,trusted=True)
    shutil.copy(FIXTURES/"hole/graph.json",p)
    (p.parent/"components4.u32le").write_bytes(b"bad")
    with pytest.raises(ValueError): m.Document.load(p.parent,trusted=True)


def test_shared_curves_holes_and_components():
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    assert hasattr(d,"svg"), "shared renderer missing"
    from PySide6.QtCore import QPointF
    assert d.component_path(0).contains(QPointF(1,1))
    assert not d.component_path(0).contains(QPointF(4,4))
    assert d.svg("contours").count('data-edge=')==len(d.graph["edges"])
    d=m.Document.load(FIXTURES/"checkerboard",trusted=True)
    assert len(d.components)==4 and len(d.face_components)==2
    assert sorted(len(v) for v in d.face_components.values())==[2,2]
    for cid in d.components:
        x0,y0,x1,y1=d.components[cid]["bbox"]
        assert d.component_path(cid).contains(QPointF((x0+x1)/2,(y0+y1)/2))
        assert d.component_path(cid).boundingRect().width()<=1.001


def test_cubic_reversal_preserves_control_points():
    domain()
    from pbnstudio.geometry import edge_path
    e={"kind":"cubic","geometry":[[[0,0],[1,0],[2,3],[4,4]]]}
    a=edge_path(e);b=edge_path(e,False)
    assert (b.elementAt(0).x,b.elementAt(0).y)==(4,4)
    assert (b.elementAt(1).x,b.elementAt(1).y)==(2,3)
    assert (b.elementAt(2).x,b.elementAt(2).y)==(1,0)
    assert (b.elementAt(3).x,b.elementAt(3).y)==(0,0)


def test_physical_labels_glyph_fit_no_shrink_and_recraft():
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    assert hasattr(d,"prepare_labels"), "physical label preparation missing"
    assert d.prepare_labels()==[]
    assert len(d.labels)==2
    assert all(l.font_pt==7 for l in d.labels)
    assert all(d.label_fits(l) for l in d.labels)
    assert d.svg("numbers").count('data-label=')==2
    d.prepare_labels(m.PrintSettings(width_mm=10,height_mm=10,margin_mm=0,font_pt=100,min_font_pt=100))
    assert d.issues and not d.labels
    d=m.Document.load(FIXTURES/"recraft",trusted=True);d.prepare_labels()
    assert len(d.palette)<len(d.graph["palette"])
    assert all(d.settings.min_font_pt<=l.font_pt<=7 and d.label_fits(l) for l in d.labels)
    assert {l.component for l in d.labels}|{i.component for i in d.issues if i.component is not None}==set(d.components)
    assert d.issues, "smoothed topology is not globally certified"


def test_recolor_move_undo_redo():
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True);d.prepare_labels()
    old=d.svg("color");label=d.labels[1]
    assert hasattr(d,"recolor"), "edit commands missing"
    d.recolor(1,(255,0,0));assert len(d.palette)==1
    assert all(l.number==1 for l in d.labels)
    assert d.dirty and d.revision==1
    assert d.undo() and d.svg("color")==old
    assert d.redo() and len(d.palette)==1
    with pytest.raises(ValueError):d.move_label(1,0,0)
    d.move_label(1,4.5,4.5)
    assert next(l for l in d.labels if l.component==1).x==4.5
    assert d.undo() and d.redo()


@pytest.mark.parametrize("case",["hole","transparent-hole","nested","checkerboard","disjoint","junction","point-contact","recraft"])
def test_real_native_corpus(case):
    d=domain().Document.load(FIXTURES/case,trusted=True);d.prepare_labels()
    assert set(d.component_paths)==set(d.components)
    assert sorted(cid for ids in d.face_components.values() for cid in ids)==sorted(d.components)
    assert all(d.label_fits(l) for l in d.labels)
    assert 'viewBox=' in d.svg("numbers")

def make_manifest_job(tmp_path):
    import hashlib
    from PIL import Image
    job=tmp_path/"job";job.mkdir()
    for name in ("graph.json","labels.u32le","components4.u32le","preview.svg"):shutil.copy(FIXTURES/"hole"/name,job/name)
    Image.new("RGB",(9,9)).save(job/"source.png")
    manifest={"schema":"studio-artifacts/1","width":9,"height":9,"files":[]}
    for p in job.iterdir():manifest["files"].append({"path":p.name,"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()})
    (job/"manifest.json").write_text(json.dumps(manifest));return job,manifest

def test_manifest_hash_paths_dimensions(tmp_path):
    m=domain();job,manifest=make_manifest_job(tmp_path)
    d=m.Document.load(job);assert d.source_bytes
    manifest["files"][0]["path"]="../escape"
    (job/"manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):m.Document.load(job)

def test_lattice_owner_and_adjacency_references_rejected(tmp_path):
    m=domain();job=tmp_path/"bad";shutil.copytree(FIXTURES/"hole",job)
    p=job/"graph.json";original=json.loads(p.read_text());g=json.loads(p.read_text())
    g["adjacency"][0]["edges"]=[9999];p.write_text(json.dumps(g))
    with pytest.raises(ValueError):m.Document.load(job,trusted=True)
    g=original;g["edges"][0]["lattice"][1]=[5,5];p.write_text(json.dumps(g))
    with pytest.raises(ValueError):m.Document.load(job,trusted=True)

def test_changed_print_settings_are_undoable():
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True);d.prepare_labels()
    d.prepare_labels(m.PrintSettings(font_pt=9))
    assert d.dirty and d.can_undo
    assert d.undo() and d.settings.font_pt==7


def test_modified_polyline_cannot_be_print_certified(tmp_path):
    m=domain();job=tmp_path/"job";shutil.copytree(FIXTURES/"hole",job)
    p=job/"graph.json";g=json.loads(p.read_text());g["edges"][0]["geometry"][1]=[8,8];p.write_text(json.dumps(g))
    d=m.Document.load(job,trusted=True);d.prepare_labels()
    assert any(i.code=="topology-uncertified" for i in d.issues)

def test_duplicate_face_rejected(tmp_path):
    m=domain();job=tmp_path/"job";shutil.copytree(FIXTURES/"hole",job)
    p=job/"graph.json";g=json.loads(p.read_text());g["faces8"][0]["contours"].append(g["faces8"][0]["contours"][0]);p.write_text(json.dumps(g))
    with pytest.raises(ValueError):m.Document.load(job,trusted=True)


def test_invalid_manifest_shape_and_hash_raise_valueerror(tmp_path):
    m=domain();job,manifest=make_manifest_job(tmp_path)
    p=job/"manifest.json";p.write_text("[]")
    with pytest.raises(ValueError):m.Document.load(job)
    manifest["files"][0]["sha256"]="0"*64;p.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):m.Document.load(job)

def test_diagnostic_marks_each_unlabelled_component():
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(m.PrintSettings(width_mm=10,height_mm=10,margin_mm=0,font_pt=100,min_font_pt=100))
    assert d.svg("numbers",diagnostic=True).count('data-issue=')==2


def test_explicit_lattice_fallback_preserves_master_and_certifies_holes():
    import copy
    m=domain();d=m.Document.load(FIXTURES/"recraft",trusted=True)
    original=copy.deepcopy(d.graph)
    assert hasattr(d,"confirm_lattice_fallback"), "explicit checked fallback missing"
    with pytest.raises(ValueError,match="confirm"):
        d.confirm_lattice_fallback()
    d.confirm_lattice_fallback(confirmed=True)
    assert d.geometry_mode=="lattice" and d.topology_certified
    assert d.graph==original and d.geometry_notice
    assert set(d.component_paths)==set(d.components)
    d.prepare_labels()
    assert not any(i.code=="topology-uncertified" for i in d.issues)
    assert d.undo() and d.geometry_mode=="native" and not d.topology_certified
    assert d.redo() and d.topology_certified


def test_lattice_coverage_detects_missing_hole():
    from PySide6.QtGui import QPainterPath
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    bad=QPainterPath();bad.addRect(0,0,9,9)
    d.component_paths[0]=bad
    assert not d.topology_certified, "a filled hole is not valid coverage"


def test_external_leader_rescues_small_region_without_shrinking_or_recolor():
    from dataclasses import replace
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    before=dict(d.component_colors)
    d.prepare_labels(m.PrintSettings(width_mm=30,height_mm=30,margin_mm=0,font_pt=38,min_font_pt=38))
    assert hasattr(d,"enable_leaders"), "explicit collision-checked leader layout missing"
    d.enable_leaders()
    assert not d.issues and len(d.labels)==2
    leader=next(l for l in d.labels if l.leader)
    assert leader.font_pt==38 and d.label_fits(leader)
    assert d.sample_component(*leader.leader[0])==leader.component
    assert not d.label_fits(replace(leader,leader=((0.,0.),(leader.x,leader.y))))
    assert 'data-leader=' in d.svg("numbers")
    assert 'data-leader=' not in d.svg("color")
    assert d.component_colors==before
    assert d.undo() and not d.leaders_enabled


def test_certification_rechecks_shared_edge_geometry_after_mutation():
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    assert d.topology_certified
    e=d.graph["edges"][0]
    e["geometry"]=e["lattice"]=[[0,0],[1,0]]
    assert not d.topology_certified


def test_leader_recolor_keeps_all_target_geometry():
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(m.PrintSettings(width_mm=30,height_mm=30,margin_mm=0,font_pt=38,min_font_pt=38));d.enable_leaders()
    from pbnstudio.geometry import path_svg
    paths={cid:path_svg(p) for cid,p in d.component_paths.items()}
    d.recolor(0,(10,20,30))
    assert paths=={cid:path_svg(p) for cid,p in d.component_paths.items()}
    assert all(d.label_fits(l) for l in d.labels)
    assert d.undo() and d.redo()
