# SPDX-License-Identifier: GPL-3.0-only
"""Palette ink is checked from actual exported SVG coordinates."""
import re
import xml.etree.ElementTree as ET
import pytest
from test_document_domain import domain, FIXTURES


def palette_ink(svg):
    from PySide6.QtGui import QPainterPath
    from pbnstudio.geometry import inflated
    result=[]
    for element in ET.fromstring(svg):
        if element.tag.endswith("rect") and "data-number" in element.attrib:
            path=QPainterPath();path.addRect(*(float(element.get(k)) for k in ("x","y","width","height")))
            result.append(inflated(path,float(element.get("stroke-width"))/2))
        elif element.tag.endswith("path"):
            tokens=re.findall(r"[MLC]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?",element.get("d"))
            path=QPainterPath();i=0
            while i<len(tokens):
                command=tokens[i];count=6 if command=="C" else 2
                values=list(map(float,tokens[i+1:i+1+count]));i+=count+1
                {"M":path.moveTo,"L":path.lineTo,"C":path.cubicTo}[command](*values)
            result.append(path)
    return result


@pytest.mark.parametrize("font_pt",[7,38,100,200])
def test_palette_exported_ink_has_no_collisions_or_margin_overflow(font_pt):
    from PySide6.QtCore import QRectF
    from pbnstudio.exports import preflight,palette_svg
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(m.PrintSettings(font_pt=font_pt))
    assert not any(i.code=="palette-layout" for i in preflight(d))
    ink=palette_ink(palette_svg(d,False));assert len(ink)==4
    s=d.settings;page=QRectF(s.margin_mm,s.margin_mm,s.width_mm-2*s.margin_mm,s.height_mm-2*s.margin_mm)
    for i,path in enumerate(ink):
        assert not any(path.intersects(other) for other in ink[i+1:]),"palette ink collision"
        assert page.adjusted(-1e-6,-1e-6,1e-6,1e-6).contains(path.boundingRect()),"palette ink outside margins"


@pytest.mark.parametrize("settings",[
    dict(width_mm=50,height_mm=10,margin_mm=2,font_pt=1,min_font_pt=1),
    dict(width_mm=10,height_mm=50,margin_mm=0,font_pt=100),
])
def test_preflight_blocks_palette_that_cannot_fit_page(settings,tmp_path):
    from pbnstudio.exports import preflight,export_bundle,palette_svg
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(m.PrintSettings(**settings))
    issues=[i for i in preflight(d) if i.code=="palette-layout"]
    assert len(issues)==1,"missing actionable palette layout issue"
    assert issues[0].component is None and "enlarge" in issues[0].message
    with pytest.raises(ValueError,match="preflight"):
        export_bundle(d,tmp_path/"blocked")
    assert list(tmp_path.iterdir())==[]
    with pytest.raises(ValueError,match="palette"):
        palette_svg(d,False)


def test_measured_palette_wraps_columns_without_collisions():
    from PySide6.QtCore import QRectF
    from pbnstudio.exports import palette_svg
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(m.PrintSettings(width_mm=120,height_mm=40,margin_mm=2,font_pt=38))
    d.palette=[(i,0,0) for i in range(12)]
    ink=palette_ink(palette_svg(d,False));assert len(ink)==24
    page=QRectF(2,2,116,36)
    for i,path in enumerate(ink):
        assert page.adjusted(-1e-6,-1e-6,1e-6,1e-6).contains(path.boundingRect())
        assert not any(path.intersects(other) for other in ink[i+1:])
