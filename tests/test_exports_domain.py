# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import importlib,xml.etree.ElementTree as ET
import pytest
from test_document_domain import domain,FIXTURES

def exports():
    assert importlib.util.find_spec("pbnstudio.exports") is not None,"exports missing"
    return importlib.import_module("pbnstudio.exports")

def test_vector_bundle_preflight_dimensions_and_no_overwrite(tmp_path):
    e=exports();d=domain().Document.load(FIXTURES/"hole",trusted=True)
    with pytest.raises(ValueError):e.export_bundle(d,tmp_path/"not-ready")
    assert not (tmp_path/"not-ready").exists()
    d.prepare_labels();files=e.export_bundle(d,tmp_path/"ready")
    assert {p.name for p in files}=={f"{name}.{ext}" for name in ("answer","numbered","palette") for ext in ("svg","pdf","png")}
    root=ET.parse(tmp_path/"ready/numbered.svg").getroot()
    assert root.attrib["width"]=="215.9mm" and root.attrib["height"]=="279.4mm"
    assert 'data-number="2"' in (tmp_path/"ready/numbered.svg").read_text()
    pdf=(tmp_path/"ready/numbered.pdf").read_bytes()
    assert pdf.startswith(b"%PDF") and b"/Subtype /Image" not in pdf
    from PIL import Image
    with Image.open(tmp_path/"ready/numbered.png") as im:assert im.size==(1275,1650)
    with pytest.raises(FileExistsError):e.export_bundle(d,tmp_path/"ready")

def test_diagnostic_export_explicit_and_conspicuous(tmp_path):
    e=exports();d=domain().Document.load(FIXTURES/"recraft",trusted=True);d.prepare_labels()
    with pytest.raises(ValueError):e.export_bundle(d,tmp_path/"blocked")
    files=e.export_bundle(d,tmp_path/"diagnostic",diagnostic=True)
    assert all(p.name.startswith("DIAGNOSTIC-") for p in files)
    assert 'NOT PRINT READY' in (tmp_path/"diagnostic/DIAGNOSTIC-numbered.svg").read_text()


def test_atomic_export_cleans_staging_after_io_failure(tmp_path,monkeypatch):
    e=exports();d=domain().Document.load(FIXTURES/"hole",trusted=True);d.prepare_labels()
    def fail(*args):raise OSError("injected disk failure")
    monkeypatch.setattr(e,"render_files",fail)
    with pytest.raises(OSError):e.export_bundle(d,tmp_path/"out")
    assert list(tmp_path.iterdir())==[]

def test_pdf_real_page_dimensions_and_render(tmp_path):
    e=exports();d=domain().Document.load(FIXTURES/"hole",trusted=True);d.prepare_labels()
    e.export_bundle(d,tmp_path/"out")
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtCore import QSize
    pdf=QPdfDocument();assert pdf.load(str(tmp_path/"out/numbered.pdf"))==QPdfDocument.Error.None_
    assert pdf.pageCount()==1
    size=pdf.pagePointSize(0);assert abs(size.width()-612)<.1 and abs(size.height()-792)<.1
    assert not pdf.render(0,QSize(612,792)).isNull()


def test_preflight_rechecks_leader_collision_after_inline_move():
    from dataclasses import replace
    e=exports();m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(m.PrintSettings(width_mm=30,height_mm=30,margin_mm=0,font_pt=38,min_font_pt=38));d.enable_leaders()
    assert not e.preflight(d)
    leader=next(l for l in d.labels if l.leader)
    host=next(l for l in d.labels if not l.leader)
    from pbnstudio.geometry import inflated,leader_path
    candidates=[replace(host,x=leader.x+dx*.1,y=leader.y+dy*.1) for dx in range(-15,16) for dy in range(-15,16)]
    collision=next(l for l in candidates if d.label_fits(l) and (d.glyph_path(l).intersects(inflated(leader_path(leader),.1/d.mm_per_unit)) or d.glyph_path(l).intersects(d.glyph_path(leader))))
    with pytest.raises(ValueError,match="collision"):
        d.move_label(host.component,collision.x,collision.y)
    assert not e.preflight(d)


def test_normal_leader_bundle_retains_route_and_has_no_diagnostic(tmp_path):
    e=exports();m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(m.PrintSettings(width_mm=30,height_mm=30,margin_mm=0,font_pt=38,min_font_pt=38));d.enable_leaders()
    files=e.export_bundle(d,tmp_path/"leaders")
    text=(tmp_path/"leaders/numbered.svg").read_text()
    assert 'data-leader=' in text and 'NOT PRINT READY' not in text
    assert len(files)==9


def test_preflight_one_actionable_issue_per_missing_label():
    e=exports();m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(m.PrintSettings(width_mm=10,height_mm=10,margin_mm=0,font_pt=100,min_font_pt=100))
    issues=e.preflight(d)
    assert [(i.code,i.component) for i in issues if i.component is not None]==[("label-no-fit",0),("label-no-fit",1)]
    assert [i.code for i in issues if i.component is None]==["palette-layout"]


def test_external_leader_marks_its_target_with_dot():
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(m.PrintSettings(width_mm=30,height_mm=30,margin_mm=0,font_pt=38,min_font_pt=38));d.enable_leaders()
    assert 'data-leader-anchor=' in d.svg("numbers")


def test_numbered_page_explains_external_leaders_and_fallback():
    e=exports();m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    d.confirm_lattice_fallback(confirmed=True)
    d.prepare_labels(m.PrintSettings(width_mm=30,height_mm=30,margin_mm=0,font_pt=38,min_font_pt=38));d.enable_leaders()
    text=e.page_svg(d,"numbers",False)
    assert 'data-geometry-mode="lattice"' in text
    assert "dot marks the target region" in text
