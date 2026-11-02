# SPDX-License-Identifier: GPL-3.0-only
"""One canonical curve renderer; no tracing or alternate smoothing."""
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QPainterPath


# A task-local context propagates cancellation through nested geometry helpers.
# Cache lifetime is one operation: mutable documents cannot reuse stale certificates.
from contextvars import ContextVar
from contextlib import contextmanager
_JOB = ContextVar("geometry_job", default=None)

@contextmanager
def job_scope(cancel_event=None, progress=None):
    current = _JOB.get()
    if current is not None:
        checkpoint()
        yield current
        return
    state = {"cancel": cancel_event, "progress": progress, "cache": {}}
    token = _JOB.set(state)
    try:
        checkpoint()
        yield state
        checkpoint()
    finally:
        _JOB.reset(token)

def checkpoint(stage=None, completed=0, total=0):
    state = _JOB.get()
    if state is None:
        return
    if state["cancel"] is not None and state["cancel"].is_set():
        from .engine import EngineCancelled
        raise EngineCancelled("cancelled")
    if stage is not None and state["progress"] is not None:
        state["progress"](stage, completed, total)
        if state["cancel"] is not None and state["cancel"].is_set():
            from .engine import EngineCancelled
            raise EngineCancelled("cancelled")

def font_sizes(settings):
    """Descending half-point trials, including a non-grid exact minimum.

    Validated settings bound this to at most 399 sizes.
    """
    import math
    settings.validate()
    count=math.ceil((settings.font_pt-settings.min_font_pt)/.5)
    return tuple(settings.font_pt-.5*i for i in range(count))+(settings.min_font_pt,)


def cached_ink(document,label,clearance=0.):
    state=_JOB.get()
    if state is None:
        path=document.glyph_path(label)
        return inflated(path,clearance) if clearance else path
    # Font outline/stroking is translation-invariant: build once per physical
    # number/size/clearance, rather than once per speculative anchor.
    from PySide6.QtGui import QTransform
    key=("template",label.number,label.font_pt,document.mm_per_unit,clearance)
    cache=state["cache"]
    if key not in cache:
        path=glyph_path(label.number,label.font_pt,document.mm_per_unit,0,0)
        cache[key]=inflated(path,clearance) if clearance else path
    return QTransform.fromTranslate(label.x,label.y).map(cache[key])


def edge_path(edge,forward=True):
    path=QPainterPath();g=edge["geometry"]
    if edge["kind"]=="polyline":
        points=g if forward else list(reversed(g))
        path.moveTo(*points[0])
        for point in points[1:]: path.lineTo(*point)
    else:
        segments=g if forward else [list(reversed(s)) for s in reversed(g)]
        path.moveTo(*segments[0][0])
        for a,b,c,d in segments:
            if abs(path.currentPosition().x()-a[0])+abs(path.currentPosition().y()-a[1])>1e-5: raise ValueError("discontinuous cubic")
            path.cubicTo(*b,*c,*d)
    return path

def contour_rings(graph,contour):
    # Decompose repeated graph junctions (point contacts) into separate cycles.
    pending=[];vertices=[];rings=[]
    for eid,forward in contour:
        path=edge_path(graph["edges"][eid],forward)
        start=(path.elementAt(0).x,path.elementAt(0).y)
        end=(path.currentPosition().x(),path.currentPosition().y())
        if not vertices: vertices=[start]
        if abs(vertices[-1][0]-start[0])+abs(vertices[-1][1]-start[1])>1e-5: raise ValueError("discontinuous contour")
        pending.append(path)
        if end in vertices:
            i=vertices.index(end);ring=QPainterPath()
            for part in pending[i:]:
                if ring.isEmpty(): ring=QPainterPath(part)
                else: ring.connectPath(part)
            ring.closeSubpath();ring.setFillRule(Qt.OddEvenFill);rings.append(ring)
            pending=pending[:i];vertices=vertices[:i+1]
        else: vertices.append(end)
    if pending: raise ValueError("open contour")
    return rings

def build_paths(document):
    import numpy as np
    face_paths={};component_paths={};mapping={}
    graph=document.render_graph
    for face in graph["faces8"]:
        checkpoint()
        rings=[r for c in face["contours"] for r in contour_rings(graph,c)]
        full=QPainterPath();full.setFillRule(Qt.OddEvenFill)
        for r in rings: full.addPath(r)
        face_paths[face["id"]]=full;mapping[face["id"]]=[]
        for cid,c in document.components.items():
            if c["region"]!=face["region"]: continue
            x0,y0,x1,y1=c["bbox"]
            ys,xs=np.where(document.component_map[y0:y1,x0:x1]==cid)
            # Interior sample robust to smoothing at a raster corner.
            candidates=np.linspace(0,len(xs)-1,min(128,len(xs)),dtype=int)
            seed=next((QPointF(float(xs[i]+x0)+.5,float(ys[i]+y0)+.5) for i in candidates if full.contains(QPointF(float(xs[i]+x0)+.5,float(ys[i]+y0)+.5))),None)
            if seed is None: continue
            containing=[r for r in rings if r.contains(seed)]
            if not containing: continue
            outer=min(containing,key=lambda r:r.boundingRect().width()*r.boundingRect().height())
            shape=QPainterPath(outer);shape.setFillRule(Qt.OddEvenFill)
            for r in rings:
                if r is outer: continue
                if outer.contains(r.boundingRect()) and not r.contains(seed): shape.addPath(r)
            if cid in component_paths:
                # Ambiguous component/face association must not be certified.
                raise ValueError("ambiguous face/component geometry")
            component_paths[cid]=shape;mapping[face["id"]].append(cid)
    if document.geometry_mode=="lattice":
        # Derive component (not palette-region) cycles directly from verified
        # shared unit edges. This preserves holes touching an outer bbox.
        import math
        from collections import defaultdict
        outgoing={cid:defaultdict(list) for cid in document.components}
        cells=document.component_map;h,w=cells.shape
        for e in graph["edges"]:
            checkpoint()
            for av,bv in zip(e["lattice"],e["lattice"][1:]):
                a,b=tuple(av),tuple(bv);dx,dy=b[0]-a[0],b[1]-a[1]
                for sign in (-1,1):
                    x=math.floor((a[0]+b[0])/2-sign*dy*.5);y=math.floor((a[1]+b[1])/2+sign*dx*.5)
                    cid=int(cells[y,x]) if 0<=x<w and 0<=y<h else 4294967295
                    if cid in outgoing:
                        start,end=(a,b) if sign==1 else (b,a)
                        outgoing[cid][start].append(end)
        component_paths={}
        for cid,links in outgoing.items():
            shape=QPainterPath();shape.setFillRule(Qt.OddEvenFill)
            while links:
                start=next(iter(links));at=start;shape.moveTo(*start)
                while True:
                    if at not in links:raise ValueError("open component lattice boundary")
                    end=links[at].pop()
                    if not links[at]:del links[at]
                    shape.lineTo(*end);at=end
                    if at==start:break
                shape.closeSubpath()
            component_paths[cid]=shape
    return face_paths,component_paths,mapping

def path_svg(path):
    values=[];i=0
    while i<path.elementCount():
        e=path.elementAt(i)
        if e.isMoveTo(): values.append(f"M{e.x:.9g},{e.y:.9g}")
        elif e.isLineTo(): values.append(f"L{e.x:.9g},{e.y:.9g}")
        else:
            b=path.elementAt(i+1);c=path.elementAt(i+2)
            values.append(f"C{e.x:.9g},{e.y:.9g} {b.x:.9g},{b.y:.9g} {c.x:.9g},{c.y:.9g}");i+=2
        i+=1
    return " ".join(values)


_APP=None

def ensure_app():
    global _APP
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None: _APP=QApplication([])
    return QApplication.instance()

def glyph_path(number,font_pt,mm_per_unit,x,y):
    from PySide6.QtGui import QFont,QFontInfo,QTransform,QFontDatabase
    from pathlib import Path
    import os
    ensure_app()
    if "Arial" not in QFontDatabase.families():
        font_file=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts/arial.ttf"
        if not font_file.is_file() or QFontDatabase.addApplicationFont(str(font_file))<0:
            raise ValueError("required Arial font unavailable; install licensed system font")
    font=QFont("Arial");font.setPixelSize(1000)
    if QFontInfo(font).family().lower()!="arial": raise ValueError("required Arial font unavailable")
    p=QPainterPath();p.addText(0,0,font,str(number))
    scale=font_pt*25.4/72/mm_per_unit/1000
    p=QTransform.fromScale(scale,scale).map(p)
    b=p.boundingRect();return QTransform.fromTranslate(x-b.center().x(),y-b.center().y()).map(p)

def inflated(path,clearance):
    from PySide6.QtGui import QPainterPathStroker
    stroke=QPainterPathStroker();stroke.setWidth(2*clearance)
    return path.united(stroke.createStroke(path))


def certify_lattice(document):
    """Exact combinatorial coverage, with 1e-8 source-unit roundoff only.

    Unit axis-aligned integer segments cannot cross except at lattice vertices.
    Each component's odd-even path must contain precisely its raster boundary,
    once each: this checks all holes/islands and prevents overlap/filled holes.
    No curve flattening, pixel sampling or smoothing tolerance is asserted.
    """
    import numpy as np
    graph=document.render_graph
    if not all(e["kind"]=="polyline" and e["geometry"]==e["lattice"] for e in graph["edges"]): return False
    if set(document.component_paths)!=set(document.components):return False
    cells=document.component_map;h,w=cells.shape;outside=4294967295
    region=document.region_map;shared=set()
    for edge in graph["edges"]:
        lattice=edge["lattice"]
        if len(lattice)<2:return False
        for a,b in zip(lattice,lattice[1:]):
            if any(type(v) is not int for v in a+b):return False
            dx,dy=b[0]-a[0],b[1]-a[1]
            if abs(dx)+abs(dy)!=1 or not(0<=a[0]<=w and 0<=b[0]<=w and 0<=a[1]<=h and 0<=b[1]<=h):return False
            key=tuple(sorted((tuple(a),tuple(b))))
            if key in shared:return False
            shared.add(key)
            for side,sign in (("left",-1),("right",1)):
                x=int(np.floor((a[0]+b[0])/2-sign*dy*.5));y=int(np.floor((a[1]+b[1])/2+sign*dx*.5))
                owner=int(region[y,x]) if 0<=x<w and 0<=y<h else outside
                if edge[side]!=owner:return False
    rp=np.pad(region,1,constant_values=outside)
    if len(shared)!=np.count_nonzero(rp[:,1:]!=rp[:,:-1])+np.count_nonzero(rp[1:,:]!=rp[:-1,:]):return False
    padded=np.pad(cells,1,constant_values=outside)
    counts=np.zeros(len(document.components),dtype=np.int64)
    for a,b in ((padded[:,1:],padded[:,:-1]),(padded[1:,:],padded[:-1,:])):
        mask=a!=b
        for owners in (a[mask],b[mask]):
            owners=owners[owners!=outside]
            counts+=np.bincount(owners.astype(np.int64),minlength=len(counts))
    for cid,path in document.component_paths.items():
        checkpoint()
        if path.fillRule()!=Qt.OddEvenFill:return False
        seen=set();prev=None;origin=None
        for i in range(path.elementCount()):
            if i%256==0:checkpoint()
            e=path.elementAt(i)
            if not (e.isMoveTo() or e.isLineTo()):return False
            point=(round(e.x),round(e.y))
            if abs(e.x-point[0])>1e-8 or abs(e.y-point[1])>1e-8:return False
            if e.isMoveTo():
                if prev is not None and prev!=origin:return False
                origin=prev=point;continue
            a=prev;b=point;prev=point
            if a==b:continue
            dx,dy=b[0]-a[0],b[1]-a[1]
            if abs(dx)+abs(dy)!=1:return False
            if not(0<=a[0]<=w and 0<=b[0]<=w and 0<=a[1]<=h and 0<=b[1]<=h):return False
            key=tuple(sorted((a,b)))
            if key in seen:return False
            seen.add(key)
            owners=[]
            for sign in (-1,1):
                x=int(np.floor((a[0]+b[0])/2-sign*dy*.5));y=int(np.floor((a[1]+b[1])/2+sign*dx*.5))
                owners.append(int(cells[y,x]) if 0<=x<w and 0<=y<h else outside)
            if sum(v==cid for v in owners)!=1:return False
        if prev!=origin or len(seen)!=counts[cid]:return False
    return True


def leader_path(label):
    path=QPainterPath()
    if label.leader:
        path.moveTo(*label.leader[0])
        for p in label.leader[1:]:path.lineTo(*p)
    return path


def _bounded_leader_segment(document,a,b):
    """Reject untrusted routes before any pixel-grid enumeration."""
    import math
    for point in (a,b):
        if len(point)!=2 or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in point):return False
        if not (0<=point[0]<document.width and 0<=point[1]<document.height):return False
    mm=document.mm_per_unit
    return math.isfinite(mm) and mm>0 and math.hypot(b[0]-a[0],b[1]-a[1])*mm<=18


def segment_owners(document,a,b):
    """Exact ownership intervals along a straight line, split at pixel edges."""
    import math
    if not _bounded_leader_segment(document,a,b):return []
    dx,dy=b[0]-a[0],b[1]-a[1]
    # Fail closed even for a very long, thin image at tiny physical scale.
    crossings=sum(max(0,math.ceil(max(start,end))-math.floor(min(start,end))-1)
                  for start,end in zip(a,b))
    if crossings>65536:return []
    ts={0.,1.}
    for start,delta,end in ((a[0],dx,b[0]),(a[1],dy,b[1])):
        if abs(delta)>1e-12:
            for k in range(math.floor(min(start,end))+1,math.ceil(max(start,end))):
                t=(k-start)/delta
                if 0<t<1:ts.add(t)
    ts=sorted(ts);owners=[]
    for lo,hi in zip(ts,ts[1:]):
        t=(lo+hi)/2;cid=document.sample_component(a[0]+dx*t,a[1]+dy*t)
        if not owners or owners[-1]!=cid:owners.append(cid)
    return owners


def leader_fits(document,label):
    """Supported route: one straight segment, target -> one adjacent host.

    No third region, re-entry, other label or other leader is allowed. Anchor
    is strictly in the target and a 0.12mm pen is contained in the two shapes.
    Glyph stays at the configured font size and wholly within the host.
    """
    import math
    from dataclasses import replace
    if len(label.leader)!=2 or any(len(p)!=2 or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in p) for p in label.leader):return False
    a,b=label.leader
    if not _bounded_leader_segment(document,a,b):return False
    if document.sample_component(*a)!=label.component or not document.component_path(label.component).contains(QPointF(*a)):return False
    host=document.sample_component(label.x,label.y)
    if host is None or host==label.component:return False
    if not document.label_fits(replace(label,component=host,leader=())):return False
    if segment_owners(document,a,b)!=[label.component,host]:return False
    mm=document.mm_per_unit
    dot=QPainterPath();dot.addEllipse(QPointF(*a),.12/mm,.12/mm)
    if not document.component_path(label.component).contains(dot):return False
    route=leader_path(label);ink=cached_ink(document,label)
    # Endpoint must be beside this glyph, not an arbitrary point in the host.
    if ink.intersects(inflated(route,.18/mm)):return False
    if not inflated(ink,.8/mm).contains(QPointF(*b)):return False
    corridor=inflated(route,.06/mm).united(dot)
    if not document.component_path(label.component).united(document.component_path(host)).contains(corridor):return False
    footprint=cached_ink(document,label,.25/mm)
    for other in document.labels:
        checkpoint()
        if other.component==label.component:continue
        other_ink=cached_ink(document,other,.25/mm)
        bounds=other_ink.boundingRect()
        if (footprint.boundingRect().intersects(bounds) and footprint.intersects(other_ink)) or (corridor.boundingRect().intersects(bounds) and corridor.intersects(other_ink)):return False
        if other.leader:
            other_route=inflated(leader_path(other),.18/mm)
            if other_route.intersects(corridor) or other_route.intersects(footprint):return False
    return True


def place_leaders(document,old):
    import math
    import numpy as np
    from scipy import ndimage
    from .document import Label
    mm=document.mm_per_unit
    missing=[i.component for i in document.issues if i.code=="label-no-fit"]
    # Smallest first: avoid spending the available clear space on easy regions.
    for index,cid in enumerate(sorted(missing,key=lambda c:document.components[c]["area"])):
        checkpoint("leaders",index,len(missing))
        c=document.components[cid];x0,y0,x1,y1=c["bbox"]
        mask=document.component_map[y0:y1,x0:x1]==cid
        dist=ndimage.distance_transform_edt(np.pad(mask,1))[1:-1,1:-1]
        yy,xx=np.where((dist==ndimage.maximum_filter(dist,3))&mask)
        order=np.argsort(-dist[yy,xx],kind="stable")
        # Spatially diverse anchors along thin components, not just one maximum.
        selected=list(order[:4])+list(order[np.linspace(0,len(order)-1,min(8,len(order)),dtype=int)])
        anchors=list(dict.fromkeys((float(xx[i]+x0)+.5,float(yy[i]+y0)+.5) for i in selected))
        number=document.palette.index(document.component_colors[cid])+1
        chosen=None
        previous=old.get(cid)
        if previous and previous.leader:
            from dataclasses import replace
            candidate=replace(previous,number=number)
            if document.label_fits(candidate):chosen=candidate
        if chosen is None:
            for size in font_sizes(document.settings):
                checkpoint()
                base=glyph_path(number,size,mm,0,0).boundingRect()
                for radius_mm in (2,3,4,6,9,13,17):
                    for a in anchors:
                        for angle in range(0,360,30):
                            checkpoint()
                            dx,dy=math.cos(math.radians(angle)),math.sin(math.radians(angle))
                            x,y=a[0]+dx*radius_mm/mm,a[1]+dy*radius_mm/mm
                            # Stop .35mm before the rectangular glyph envelope.
                            half=min((base.width()/2+.35/mm)/max(abs(dx),1e-12),(base.height()/2+.35/mm)/max(abs(dy),1e-12))
                            b=(x-dx*half,y-dy*half)
                            if (b[0]-a[0])*dx+(b[1]-a[1])*dy<=0:continue
                            candidate=Label(cid,number,x,y,size,(a,b))
                            if document.label_fits(candidate):chosen=candidate;break
                        if chosen:break
                    if chosen:break
                if chosen:break
        if chosen:
            document.labels.append(chosen)
            document.issues=[i for i in document.issues if not(i.code=="label-no-fit" and i.component==cid)]
    from dataclasses import replace
    document.issues=[replace(i,reason="inline-and-leader-no-fit-after-minimum",
        message=f"Number for component {i.component} has no certified inline or adjacent leader fit after minimum {document.settings.min_font_pt:g} pt; bounded search exhausted. Move label or enlarge print size",
        metadata={**i.metadata,"leaders_attempted":True,"leader_anchor_limit":12,
                  "leader_candidates_per_size_limit":1008}) if i.code=="label-no-fit" else i for i in document.issues]
    checkpoint("leaders",len(missing),len(missing))
    document.labels.sort(key=lambda l:l.component)


def adaptive_flatten(edge,tolerance=.01):
    """De Casteljau: convex-hull distance bounds every cubic/chord error."""
    import math
    if edge["kind"]=="polyline":return [tuple(p) for p in edge["geometry"]]
    result=[]
    def flatten(p,depth=0):
        a,b,c,d=p;dx,dy=d[0]-a[0],d[1]-a[1];den=dx*dx+dy*dy
        def distance(q):
            t=max(0,min(1,((q[0]-a[0])*dx+(q[1]-a[1])*dy)/den)) if den else 0
            return math.hypot(q[0]-a[0]-t*dx,q[1]-a[1]-t*dy)
        if max(distance(b),distance(c))<=tolerance:
            result.append(tuple(d));return
        if depth>=20 or len(result)>200000:raise ValueError("curve flattening budget exceeded")
        def mid(x,y):return [(x[0]+y[0])/2,(x[1]+y[1])/2]
        ab,bc,cd=mid(a,b),mid(b,c),mid(c,d);abc,bcd=mid(ab,bc),mid(bc,cd);m=mid(abc,bcd)
        flatten([a,ab,abc,m],depth+1);flatten([m,bcd,cd,d],depth+1)
    for segment in edge["geometry"]:
        if not result:result.append(tuple(segment[0]))
        elif result[-1]!=tuple(segment[0]):raise ValueError("discontinuous cubic")
        flatten(segment)
    return result


def shared_component_paths(document):
    """Assemble every component including ALL holes from canonical edge sides."""
    import math
    from collections import defaultdict
    paths={};links={cid:defaultdict(list) for cid in document.components}
    cells=document.component_map;h,w=cells.shape
    for e in document.render_graph["edges"]:
        checkpoint()
        owners={-1:set(),1:set()}
        for index,(a,b) in enumerate(zip(e["lattice"],e["lattice"][1:])):
            if index%256==0:checkpoint()
            dx,dy=b[0]-a[0],b[1]-a[1]
            for sign in (-1,1):
                x=math.floor((a[0]+b[0])/2-sign*dy*.5);y=math.floor((a[1]+b[1])/2+sign*dx*.5)
                owners[sign].add(int(cells[y,x]) if 0<=x<w and 0<=y<h else 4294967295)
        for sign,ids in owners.items():
            if len(ids)!=1:raise ValueError("edge crosses component ownership")
            cid=next(iter(ids))
            if cid not in links:continue
            p=edge_path(e,sign==1);start=(p.elementAt(0).x,p.elementAt(0).y)
            links[cid][start].append(p)
    for cid,out in links.items():
        checkpoint()
        shape=QPainterPath();shape.setFillRule(Qt.OddEvenFill)
        while out:
            origin=next(iter(out));at=origin;ring=QPainterPath()
            while True:
                if at not in out:raise ValueError("open shared component boundary")
                p=out[at].pop()
                if not out[at]:del out[at]
                if ring.isEmpty():ring=QPainterPath(p)
                else:ring.connectPath(p)
                at=(p.currentPosition().x(),p.currentPosition().y())
                if at==origin:break
            ring.closeSubpath();shape.addPath(ring)
        paths[cid]=shape
    return paths


def smooth_fingerprint(document):
    checkpoint()
    import hashlib,json
    h=hashlib.sha256(json.dumps(document.render_graph,sort_keys=True,separators=(",",":")).encode())
    h.update(document.component_map.tobytes());h.update(document.region_map.tobytes())
    import struct
    for cid,p in sorted(document.component_paths.items()):
        checkpoint()
        h.update(struct.pack("<qii",cid,p.fillRule().value,p.elementCount()))
        for i in range(p.elementCount()):
            if i%256==0:checkpoint()
            e=p.elementAt(i);h.update(struct.pack("<idd",e.type.value,e.x,e.y))
    return h.hexdigest()


def check_smooth(document,tolerance=.01,max_deviation=3.):
    """Bounded flattening + bidirectional boundary corridor + planar embedding.

    Shared edges occur once, and component cycles use their actual curves,
    never independently fitted outlines. Near-collisions within flatten error
    fail closed, except adjoining segments at their common endpoint.
    """
    import math
    import numpy as np
    from scipy.spatial import cKDTree
    from collections import defaultdict
    # Sampling thresholds are NOT an exact 3-unit Hausdorff assertion.
    # Add dense-curve half spacing (.25), lattice half spacing (.5),
    # and flattening error to state the actual conservative corridor bound.
    bounds={"curve_to_lattice_bound_source_units":max_deviation+.75+tolerance,
            "lattice_to_curve_bound_source_units":max_deviation+.75+tolerance,
            "distance_validation":"sampled corridor bounds, not exact Hausdorff distance"}
    graph=document.render_graph;bad=set();reasons={};flat={};segments=[];bins=defaultdict(list)
    def fail(eid,why):bad.add(eid);reasons.setdefault(str(eid),set()).add(why)
    def distance(p,a,b):
        dx,dy=b[0]-a[0],b[1]-a[1];den=dx*dx+dy*dy
        t=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/den)) if den else 0
        return math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy)
    for e in graph["edges"]:
        checkpoint()
        eid=e["id"]
        try:points=adaptive_flatten(e,tolerance)
        except ValueError:fail(eid,"flatten-budget");continue
        flat[eid]=points
        if any(not (0<=x<=document.width and 0<=y<=document.height) for x,y in points):fail(eid,"outside-page")
        lattice=e["lattice"]
        if not e["ring"] and (points[0]!=tuple(lattice[0]) or points[-1]!=tuple(lattice[-1])):fail(eid,"endpoint")
        # Densify to <= .5 source units so both directed distance bounds
        # include a conservative .25 covering-radius term.
        dense=[]
        for a,b in zip(points,points[1:]):
            n=max(1,math.ceil(math.dist(a,b)*2))
            if n>200000:fail(eid,"segment-budget");break
            dense.extend((a[0]+(b[0]-a[0])*i/n,a[1]+(b[1]-a[1])*i/n) for i in range(n))
        dense.append(points[-1])
        if len(dense)>400000:fail(eid,"edge-budget");continue
        if max(cKDTree(lattice).query(dense)[0],default=0)>.5+max_deviation or max(cKDTree(dense).query(lattice)[0],default=0)>.25+max_deviation:fail(eid,"boundary-correspondence")
        for i,(a,b) in enumerate(zip(points,points[1:])):
            if a==b:continue
            index=len(segments);segments.append((eid,i,a,b,len(points)-1))
            x0,x1=math.floor((min(a[0],b[0])-2*tolerance)/16),math.floor((max(a[0],b[0])+2*tolerance)/16)
            y0,y1=math.floor((min(a[1],b[1])-2*tolerance)/16),math.floor((max(a[1],b[1])+2*tolerance)/16)
            if (x1-x0+1)*(y1-y0+1)>10000:fail(eid,"spatial-budget");continue
            for x in range(x0,x1+1):
                for y in range(y0,y1+1):bins[x,y].append(index)
    checked=set()
    def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    for ids in bins.values():
        checkpoint()
        for k,ia in enumerate(ids):
            checkpoint()
            ea,pa,a,b,na=segments[ia]
            for ib in ids[k+1:]:
                pair=(min(ia,ib),max(ia,ib))
                if pair in checked:continue
                checked.add(pair)
                if len(checked)>5000000:raise ValueError("planar validation pair budget exceeded")
                eb,pb,c,d,nb=segments[ib]
                if max(a[0],b[0])+2*tolerance<min(c[0],d[0]) or max(c[0],d[0])+2*tolerance<min(a[0],b[0]) or max(a[1],b[1])+2*tolerance<min(c[1],d[1]) or max(c[1],d[1])+2*tolerance<min(a[1],b[1]):continue
                common=set((a,b))&set((c,d))
                if common:
                    # Only adjacent same-edge segments or genuine edge endpoint
                    # contacts may share a point; backtracking overlap is rejected.
                    allowed=(ea==eb and (abs(pa-pb)==1 or {pa,pb}=={0,na-1})) or (ea!=eb and all(p in (flat[ea][0],flat[ea][-1]) and p in (flat[eb][0],flat[eb][-1]) for p in common))
                    if allowed and len(common)==1:
                        p=next(iter(common));q=b if a==p else a;r=d if c==p else c
                        if abs(cross(p,q,r))>1e-10 or (q[0]-p[0])*(r[0]-p[0])+(q[1]-p[1])*(r[1]-p[1])<=0:continue
                hit=cross(a,b,c)*cross(a,b,d)<0 and cross(c,d,a)*cross(c,d,b)<0
                near=min(distance(a,c,d),distance(b,c,d),distance(c,a,b),distance(d,a,b))<=2*tolerance
                if hit or near:fail(ea,"intersection-or-uncertain-clearance");fail(eb,"intersection-or-uncertain-clearance")
    try:
        expected=shared_component_paths(document)
        if set(expected)!=set(document.components):raise ValueError("missing component")
        for cid,path in expected.items():
            if path.isEmpty():raise ValueError("empty component")
            if cid not in document.component_paths or path_svg(path)!=path_svg(document.component_paths[cid]):raise ValueError("component boundary correspondence")
    except ValueError as exc:
        return {**bounds,"valid":False,"offending_edges":sorted(bad),"errors":[str(exc)],"tolerance_source_units":tolerance,"max_deviation_source_units":max_deviation}
    return {**bounds,"valid":not bad,"offending_edges":sorted(bad),"errors":{k:sorted(v) for k,v in reasons.items()},"tolerance_source_units":tolerance,"max_deviation_source_units":max_deviation,"segments_checked":len(segments),"pairs_checked":len(checked)}
