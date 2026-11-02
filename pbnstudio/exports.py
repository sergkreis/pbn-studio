# SPDX-License-Identifier: GPL-3.0-only
"""Same SVG model for true vector PDF and explicitly raster PNG."""
from pathlib import Path
import math,os,shutil,tempfile
from xml.etree import ElementTree as ET
from PySide6.QtCore import QByteArray,QRectF,QSizeF,Qt
from PySide6.QtGui import QImage,QPainter,QPdfWriter,QPageSize,QPageLayout
from PySide6.QtSvg import QSvgRenderer
from .geometry import ensure_app,glyph_path,path_svg

def preflight(document):
    document.settings.validate()
    errors=list(document.issues)
    from .document import Issue
    seen=set()
    for l in document.labels:
        if l.component in seen or l.component not in document.components:
            errors.append(Issue("invalid-label",l.component,"duplicate/unknown component"));continue
        seen.add(l.component)
        number=document.palette.index(document.component_colors[l.component])+1
        if l.number!=number or not document.label_fits(l):
            errors.append(Issue("invalid-label",l.component,"invalid number/font/final footprint"))
    for cid in set(document.components)-seen:
        if not any(i.component==cid and i.code in ("label-no-fit","geometry-unmapped","missing-label") for i in errors):
            errors.append(Issue("missing-label",cid,"component has no validated label"))
    if not document.topology_certified and not any(i.code=="topology-uncertified" for i in errors):
        errors.append(Issue("topology-uncertified",None,"global Bezier topology not certified"))
    try:_palette_layout(document)
    except ValueError as exc:errors.append(Issue("palette-layout",None,str(exc)))
    return errors

def page_svg(document,mode,diagnostic):
    s=document.settings;mm=document.mm_per_unit
    inner=document.svg(mode,diagnostic=True)
    node=ET.fromstring(inner)
    # Millimetre page coordinate system preserves physical glyph and pen size.
    body="".join(ET.tostring(child,encoding="unicode") for child in node)
    x=(s.width_mm-document.width*mm)/2;y=(s.height_mm-document.height*mm)/2
    legend="Leader dot marks the target region; number at the other end gives its color."
    description=(document.geometry_notice+" "+legend).strip()
    footer=""
    if mode=="numbers" and any(l.leader for l in document.labels) and s.margin_mm>=6 and s.width_mm>=150:
        footer=f'<text x="{s.margin_mm:g}" y="{s.height_mm-3:g}" font-family="Arial" font-size="{7*25.4/72:.9g}">{legend}</text>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" data-geometry-mode="{document.geometry_mode}" width="{s.width_mm:g}mm" height="{s.height_mm:g}mm" viewBox="0 0 {s.width_mm:g} {s.height_mm:g}"><desc>{description}</desc><rect width="100%" height="100%" fill="white"/><g transform="translate({x:.9g},{y:.9g}) scale({mm:.9g})">{body}</g>{footer}{warning(s) if diagnostic else ""}</svg>'

def warning(settings):
    return '<text x="4" y="8" font-family="Arial" font-size="4" fill="#c00000">DIAGNOSTIC / NOT PRINT READY</text>'

def _palette_layout(document):
    """Measured millimetre cells; never shrink the configured glyph outlines."""
    from PySide6.QtGui import QTransform
    s=document.settings;s.validate()
    glyphs=[glyph_path(i+1,s.font_pt,1,0,0) for i in range(len(document.palette))]
    swatch=8.;pen=.15;gap=2.
    cell_height=max(swatch+pen,max(p.boundingRect().height() for p in glyphs))
    cell_width=swatch+pen+gap+max(p.boundingRect().width() for p in glyphs)
    width=s.width_mm-2*s.margin_mm;height=s.height_mm-2*s.margin_mm
    rows=int((height+gap)/(cell_height+gap))
    cols=math.ceil(len(glyphs)/rows) if rows else 0
    if not rows or cols*cell_width+(cols-1)*gap>width:
        raise ValueError("palette cannot fit page at configured font size; enlarge print size or reduce font size explicitly")
    page=QRectF(s.margin_mm,s.margin_mm,width,height);layout=[]
    for i,ink in enumerate(glyphs):
        x=s.margin_mm+(i//rows)*(cell_width+gap)
        y=s.margin_mm+(i%rows)*(cell_height+gap)
        rect=QRectF(x+pen/2,y+(cell_height-swatch)/2,swatch,swatch)
        bounds=ink.boundingRect()
        ink=QTransform.fromTranslate(x+swatch+pen+gap-bounds.left(),y+cell_height/2-bounds.center().y()).map(ink)
        # Disjoint measured cells prove inter-item separation. Check all ink
        # (including swatch stroke) against its cell and the printable page.
        cell=QRectF(x,y,cell_width,cell_height)
        swatch_bounds=rect.adjusted(-pen/2,-pen/2,pen/2,pen/2)
        tolerance=1e-8  # millimetre floating-point roundoff only
        if not page.adjusted(-tolerance,-tolerance,tolerance,tolerance).contains(cell) or not cell.adjusted(-tolerance,-tolerance,tolerance,tolerance).contains(ink.boundingRect()) or not cell.adjusted(-tolerance,-tolerance,tolerance,tolerance).contains(swatch_bounds) or ink.intersects(swatch_bounds):
            raise ValueError("palette ink overlaps or exceeds printable page; enlarge print size")
        layout.append((rect,ink))
    return layout


def palette_svg(document,diagnostic):
    s=document.settings
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{s.width_mm:g}mm" height="{s.height_mm:g}mm" viewBox="0 0 {s.width_mm:g} {s.height_mm:g}"><rect width="100%" height="100%" fill="white"/>']
    for i,(rgb,(rect,ink)) in enumerate(zip(document.palette,_palette_layout(document))):
        color="#%02x%02x%02x"%rgb
        parts.append(f'<rect data-number="{i+1}" x="{rect.x():.9g}" y="{rect.y():.9g}" width="8" height="8" fill="{color}" stroke="black" stroke-width=".15"/>')
        parts.append(f'<path d="{path_svg(ink)}" fill="black"/>')
    if diagnostic:parts.append(warning(s))
    return "".join(parts)+"</svg>"

def render_files(svg,pdf,png,settings):
    ensure_app();renderer=QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():raise ValueError("invalid generated SVG")
    writer=QPdfWriter(str(pdf));writer.setResolution(300)
    writer.setPageSize(QPageSize(QSizeF(settings.width_mm,settings.height_mm),QPageSize.Millimeter))
    writer.setPageMargins(__import__('PySide6.QtCore',fromlist=['QMarginsF']).QMarginsF(0,0,0,0),QPageLayout.Millimeter)
    writer.setTitle("PBN Studio vector export")
    painter=QPainter(writer)
    if not painter.isActive():raise OSError("cannot open PDF painter")
    renderer.render(painter,QRectF(0,0,writer.width(),writer.height()));painter.end();del writer
    w=round(settings.width_mm/25.4*150);h=round(settings.height_mm/25.4*150)
    if w*h>40_000_000:raise ValueError("PNG page exceeds 40MP limit")
    image=QImage(w,h,QImage.Format_ARGB32_Premultiplied);image.fill(Qt.white)
    image.setDotsPerMeterX(round(150/0.0254));image.setDotsPerMeterY(round(150/0.0254))
    painter=QPainter(image);painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter,QRectF(0,0,w,h));painter.end()
    if not image.save(str(png),"PNG"):raise OSError("cannot write PNG")

def export_bundle(document,destination,diagnostic=False):
    destination=Path(destination)
    if destination.exists():raise FileExistsError(destination)
    errors=preflight(document)
    if errors and not diagnostic:raise ValueError(f"unresolved preflight issues ({len(errors)}); diagnostic export must be explicit")
    destination.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix=".pbn-export-",dir=destination.parent))
    prefix="DIAGNOSTIC-" if diagnostic else ""
    try:
        for name,svg in [("answer",page_svg(document,"color",diagnostic)),("numbered",page_svg(document,"numbers",diagnostic)),("palette",palette_svg(document,diagnostic))]:
            stem=prefix+name
            (staging/(stem+".svg")).write_text(svg,encoding="utf-8")
            render_files(svg,staging/(stem+".pdf"),staging/(stem+".png"),document.settings)
        names=sorted(p.name for p in staging.iterdir())
        if destination.exists():raise FileExistsError(destination)
        os.rename(staging,destination)
        return [destination/name for name in names]
    finally:
        if staging.exists():shutil.rmtree(staging)
