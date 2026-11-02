# SPDX-License-Identifier: GPL-3.0-only
"""Validated native graph document. IDs of regions, faces8 and cells4 are distinct."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from pathlib import Path
import copy, hashlib, json, math
import numpy as np
from scipy import ndimage

MAX_BYTES=512*1024*1024
MAX_PIXELS=40_000_000
OUTSIDE=4294967295

def read_json(data):
    def bad(value): raise ValueError("non-finite JSON value")
    try: return json.loads(data,parse_constant=bad)
    except (TypeError,KeyError,json.JSONDecodeError) as exc: raise ValueError("invalid JSON") from exc

def safe_name(name):
    if not isinstance(name,str) or not name or name in (".","..") or any(c in name for c in "/\\:"):
        raise ValueError("unsafe artifact path")
    return name

def bounded_read(path):
    if path.is_symlink() or path.stat().st_size>MAX_BYTES: raise ValueError("unsafe or oversized artifact")
    return path.read_bytes()

@dataclass(frozen=True)
class PrintSettings:
    width_mm:float=215.9
    height_mm:float=279.4
    margin_mm:float=12.7
    font_pt:float=7.0
    line_pt:float=0.4
    # Preferred font_pt adapts per cell, never below this readable floor.
    min_font_pt:float=6.0
    def __post_init__(self): self.validate()
    def validate(self):
        if any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in asdict(self).values()):
            raise ValueError("print settings must be finite")
        if not (10<=self.width_mm<=2000 and 10<=self.height_mm<=2000 and 0<=self.margin_mm<min(self.width_mm,self.height_mm)/2 and 1<=self.min_font_pt<=self.font_pt<=200 and 0<self.line_pt<=20):
            raise ValueError("print settings out of range")

@dataclass(frozen=True)
class Label:
    component:int
    number:int
    x:float
    y:float
    font_pt:float
    leader:tuple=()  # optional source-unit route: target anchor -> glyph edge

@dataclass(frozen=True)
class Issue:
    code:str
    component:int|None
    message:str
    reason:str=""
    metadata:dict=field(default_factory=dict)
    def __post_init__(self):
        if not self.reason:object.__setattr__(self,"reason",self.code)

class Document:
    @classmethod
    def load(cls,job_dir,*,trusted=False):
        base=Path(job_dir)
        manifest=None
        if not trusted:
            try: manifest=read_json(bounded_read(base/"manifest.json"))
            except OSError as exc: raise ValueError("required manifest missing") from exc
            if not isinstance(manifest,dict) or manifest.get("schema")!="studio-artifacts/1": raise ValueError("unsupported manifest schema")
            seen=set()
            for entry in manifest.get("files",[]):
                name=safe_name(entry["path"])
                if name in seen or name=="manifest.json": raise ValueError("duplicate manifest path")
                seen.add(name); data=bounded_read(base/name)
                if len(data)!=entry["bytes"] or hashlib.sha256(data).hexdigest()!=entry["sha256"]: raise ValueError("manifest hash/size mismatch")
            if not {"graph.json","labels.u32le","components4.u32le","source.png","preview.svg"}<=seen: raise ValueError("incomplete manifest")
        try:
            graph=read_json(bounded_read(base/"graph.json"))
            obj=cls._from_data(graph,bounded_read(base/"labels.u32le"),bounded_read(base/"components4.u32le"))
            if manifest and (manifest.get("width"),manifest.get("height"))!=(obj.width,obj.height): raise ValueError("manifest dimensions mismatch")
            obj.source_bytes=bounded_read(base/"source.png") if (base/"source.png").exists() else None
            if obj.source_bytes:
                from PIL import Image
                from io import BytesIO
                with Image.open(BytesIO(obj.source_bytes)) as im:
                    if im.size!=(obj.width,obj.height): raise ValueError("source dimensions mismatch")
                    im.verify()
            obj.artifact_manifest=manifest
            return obj
        except (KeyError,IndexError,TypeError,OSError) as exc: raise ValueError("invalid graph artifacts") from exc

    @classmethod
    def _from_data(cls,g,region_bytes,component_bytes):
        if g.get("schema")!="native-gate.graph/1" or g.get("outside")!=OUTSIDE: raise ValueError("unsupported graph schema")
        w,h=g["width"],g["height"]
        if type(w) is not int or type(h) is not int or min(w,h)<1 or w*h>MAX_PIXELS: raise ValueError("invalid dimensions")
        if len(region_bytes)!=w*h*4 or len(component_bytes)!=w*h*4: raise ValueError("map length mismatch")
        palette=g["palette"]
        if not palette or any(len(c)!=4 or any(type(v) is not int or not 0<=v<=255 for v in c) or c[3]!=255 for c in palette): raise ValueError("invalid palette")
        regions=np.frombuffer(region_bytes,dtype="<u4").reshape(h,w)
        cells=np.frombuffer(component_bytes,dtype="<u4").reshape(h,w)
        comps=g["components"]
        if [c["id"] for c in comps]!=list(range(len(comps))) or not comps: raise ValueError("invalid component ids")
        if np.any((regions!=OUTSIDE)&(regions>=len(palette))) or np.any((cells!=OUTSIDE)&(cells>=len(comps))): raise ValueError("invalid map reference")
        if not np.array_equal(regions==OUTSIDE,cells==OUTSIDE): raise ValueError("map outside mismatch")
        counts=np.bincount(cells[cells!=OUTSIDE].astype(np.int64),minlength=len(comps))
        from .geometry import checkpoint
        for c in comps:
            checkpoint()
            cid,r=c["id"],c["region"]
            if type(r) is not int or not 0<=r<len(palette): raise ValueError("invalid region reference")
            x0,y0,x1,y1=c["bbox"]
            if any(type(v) is not int for v in c["bbox"]) or not (0<=x0<x1<=w and 0<=y0<y1<=h): raise ValueError("invalid component bbox")
            mask=cells[y0:y1,x0:x1]==cid
            if counts[cid]!=c["area"] or mask.sum()!=counts[cid] or not counts[cid] or np.any(regions[y0:y1,x0:x1][mask]!=r): raise ValueError("component metadata mismatch")
            if ndimage.label(mask)[1]!=1: raise ValueError("component is not 4-connected")
        # Validate every geometry number, including non-finite or huge coordinates.
        def coords(value):
            if isinstance(value,list):
                for v in value: coords(v)
            elif not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or abs(value)>1e7: raise ValueError("invalid geometry coordinate")
        coords(g["nodes"])
        edges=g["edges"]
        if [e["id"] for e in edges]!=list(range(len(edges))): raise ValueError("invalid edge ids")
        for e in edges:
            checkpoint()
            if any(v!=OUTSIDE and (type(v) is not int or not 0<=v<len(palette)) for v in (e["left"],e["right"])): raise ValueError("invalid edge owner")
            for endpoint in (e["start"],e["end"]):
                if endpoint is not None and (type(endpoint) is not int or not 0<=endpoint<len(g["nodes"])): raise ValueError("invalid node reference")
            coords(e["geometry"]);coords(e["lattice"])
            if e["kind"]=="polyline":
                if len(e["geometry"])<2 or any(len(p)!=2 for p in e["geometry"]): raise ValueError("invalid polyline")
            elif e["kind"]=="cubic":
                if not e["geometry"] or any(len(s)!=4 or any(len(p)!=2 for p in s) for s in e["geometry"]): raise ValueError("invalid cubic")
            else: raise ValueError("unknown edge kind")
        # Validate canonical lattice coverage and ownership against the region map.
        segments=set()
        for e in edges:
            checkpoint()
            lattice=e["lattice"]
            if len(lattice)<2 or any(len(p)!=2 or any(type(v) is not int for v in p) for p in lattice):raise ValueError("invalid lattice")
            if e["contact"]!=len(lattice)-1:raise ValueError("edge contact mismatch")
            if e["ring"] and lattice[0]!=lattice[-1]:raise ValueError("edge ring mismatch")
            geometry=e["geometry"]
            start=geometry[0] if e["kind"]=="polyline" else geometry[0][0]
            end=geometry[-1] if e["kind"]=="polyline" else geometry[-1][-1]
            if (e["ring"] and start!=end) or (not e["ring"] and (start!=lattice[0] or end!=lattice[-1])):raise ValueError("geometry endpoints mismatch")
            for key,point in (("start",lattice[0]),("end",lattice[-1])):
                if e[key] is not None and g["nodes"][e[key]]!=point:raise ValueError("node endpoint mismatch")
            for index,(a,b) in enumerate(zip(lattice,lattice[1:])):
                if index%256==0:checkpoint()
                dx,dy=b[0]-a[0],b[1]-a[1]
                if abs(dx)+abs(dy)!=1 or not (0<=a[0]<=w and 0<=a[1]<=h and 0<=b[0]<=w and 0<=b[1]<=h):raise ValueError("invalid lattice segment")
                key=tuple(sorted((tuple(a),tuple(b))))
                if key in segments:raise ValueError("duplicate shared border")
                segments.add(key)
                for side,sign in (("right",1),("left",-1)):
                    x=math.floor((a[0]+b[0])/2-sign*dy*.5);y=math.floor((a[1]+b[1])/2+sign*dx*.5)
                    owner=int(regions[y,x]) if 0<=x<w and 0<=y<h else OUTSIDE
                    if e[side]!=owner:raise ValueError("lattice ownership mismatch")
        padded=np.pad(regions,1,constant_values=OUTSIDE)
        expected=int(np.count_nonzero(padded[:,1:]!=padded[:,:-1])+np.count_nonzero(padded[1:,:]!=padded[:-1,:]))
        if len(segments)!=expected:raise ValueError("incomplete lattice boundary coverage")
        for a in g["adjacency"]:
            if any(type(a[k]) is not int or not 0<=a[k]<len(palette) for k in ("a","b")) or a["a"]==a["b"]:raise ValueError("invalid adjacency owner")
            if not a["edges"] or len(set(a["edges"]))!=len(a["edges"]):raise ValueError("invalid adjacency edges")
            for eid in a["edges"]:
                if type(eid) is not int or not 0<=eid<len(edges) or {edges[eid]["left"],edges[eid]["right"]}!={a["a"],a["b"]}:raise ValueError("invalid adjacency reference")
            if a["contact"]!=sum(edges[e]["contact"] for e in a["edges"]):raise ValueError("adjacency contact mismatch")
        faces=g["faces8"]
        if [f["id"] for f in faces]!=list(range(len(faces))): raise ValueError("invalid face ids")
        used_sides=set()
        for f in faces:
            if not 0<=f["region"]<len(palette) or not f["contours"]: raise ValueError("invalid face")
            for contour in f["contours"]:
                if not contour: raise ValueError("empty contour")
                for eid,forward in contour:
                    if type(eid) is not int or not 0<=eid<len(edges) or type(forward) is not bool: raise ValueError("invalid edge reference")
                    if edges[eid]["left" if forward else "right"]!=f["region"]: raise ValueError("edge orientation owner mismatch")
                    if (eid,forward) in used_sides:raise ValueError("duplicate face boundary")
                    used_sides.add((eid,forward))
        expected_sides={(e["id"],forward) for e in edges for forward,side in ((True,"left"),(False,"right")) if e[side]!=OUTSIDE}
        if used_sides!=expected_sides:raise ValueError("missing face boundary")
        obj=cls();obj.graph=copy.deepcopy(g);obj.width=w;obj.height=h
        obj.region_map=regions;obj.component_map=cells;obj.components={c["id"]:c for c in comps}
        obj.component_colors={c["id"]:tuple(palette[c["region"]][:3]) for c in comps}
        obj.palette=list(dict.fromkeys(obj.component_colors.values()))
        obj.face_components={f["id"]:[c["id"] for c in comps if c["region"]==f["region"]] for f in faces}
        obj.labels=[];obj.issues=[Issue("labels-not-prepared",None,"Prepare physical labels before export")]
        obj.settings=PrintSettings();obj.revision=0;obj.dirty=False;obj.source_bytes=None;obj.artifact_manifest=None
        obj._undo=[];obj._redo=[];obj.geometry_mode="native";obj.geometry_notice="";obj._lattice_graph=None;obj.leaders_enabled=False
        from .geometry import build_paths
        obj.face_paths,obj.component_paths,obj.face_components=build_paths(obj)
        return obj

    def sample_component(self,x,y):
        if not math.isfinite(x) or not math.isfinite(y) or not (0<=x<self.width and 0<=y<self.height): return None
        cid=int(self.component_map[int(y),int(x)])
        return None if cid==OUTSIDE else cid

    def component_path(self,component):
        from PySide6.QtGui import QPainterPath
        return QPainterPath(self.component_paths.get(component,QPainterPath()))

    @property
    def mm_per_unit(self):
        s=self.settings
        return min((s.width_mm-2*s.margin_mm)/self.width,(s.height_mm-2*s.margin_mm)/self.height)

    def svg(self,mode="color",diagnostic=True):
        from .geometry import edge_path,path_svg
        if mode not in ("color","contours","numbers"): raise ValueError("invalid render mode")
        if not diagnostic and self.issues: raise ValueError("unresolved preflight issues")
        parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width*self.mm_per_unit:.9g}mm" height="{self.height*self.mm_per_unit:.9g}mm" viewBox="0 0 {self.width} {self.height}">']
        parts.append(f'<rect width="{self.width}" height="{self.height}" fill="white"/>')
        if mode=="color":
            for cid,path in self.component_paths.items():
                color="#%02x%02x%02x"%self.component_colors[cid]
                parts.append(f'<path data-component="{cid}" d="{path_svg(path)}" fill="{color}" fill-rule="evenodd"/>')
        else:
            stroke=self.settings.line_pt*25.4/72/self.mm_per_unit
            for e in self.render_graph["edges"]:
                parts.append(f'<path data-edge="{e["id"]}" d="{path_svg(edge_path(e))}" fill="none" stroke="black" stroke-width="{stroke:.9g}"/>')
        if mode=="numbers":
            from .geometry import leader_path
            for label in self.labels:
                if label.leader:
                    ax,ay=label.leader[0]
                    parts.append(f'<circle data-leader-anchor="{label.component}" cx="{ax:.9g}" cy="{ay:.9g}" r="{.12/self.mm_per_unit:.9g}" fill="black"/>')
                    parts.append(f'<path data-leader="{label.component}" d="{path_svg(leader_path(label))}" fill="none" stroke="black" stroke-width="{.12/self.mm_per_unit:.9g}"/>')
                parts.append(f'<path data-label="{label.component}" data-number="{label.number}" data-font-pt="{label.font_pt}" d="{path_svg(self.glyph_path(label))}" fill="black" fill-rule="evenodd"/>')
        if diagnostic and self.issues:
            for issue in self.issues:
                if issue.component is None:continue
                c=self.components[issue.component];x0,y0,x1,y1=c["bbox"]
                mask=self.component_map[y0:y1,x0:x1]==issue.component
                yy,xx=np.where(mask);k=len(xx)//2
                x=float(xx[k]+x0)+.5;y=float(yy[k]+y0)+.5;r=.6/self.mm_per_unit
                parts.append(f'<path data-issue="{issue.component}" d="M{x-r:.9g},{y-r:.9g} L{x+r:.9g},{y+r:.9g} M{x-r:.9g},{y+r:.9g} L{x+r:.9g},{y-r:.9g}" stroke="#c00000" stroke-width="{.2/self.mm_per_unit:.9g}"/>')
            parts.append(f'<text x="1" y="{self.height*.025:.9g}" font-size="{self.height*.02:.9g}" fill="#c00000">DIAGNOSTIC / NOT PRINT READY ({len(self.issues)} issues)</text>')
        parts.append('</svg>');return "\n".join(parts)

    def glyph_path(self,label):
        from .geometry import glyph_path
        return glyph_path(label.number,label.font_pt,self.mm_per_unit,label.x,label.y)

    def label_fits(self,label):
        from .geometry import checkpoint
        checkpoint()
        from .geometry import inflated
        from PySide6.QtCore import QRectF
        if label.component not in self.component_paths or not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (label.x,label.y,label.font_pt)): return False
        if not self.settings.min_font_pt<=label.font_pt<=self.settings.font_pt:return False
        if label.leader:
            from .geometry import leader_fits
            return leader_fits(self,label)
        clearance=(self.settings.line_pt/2*25.4/72+.15)/self.mm_per_unit
        from .geometry import cached_ink
        footprint=cached_ink(self,label,clearance)
        if not self.component_paths[label.component].contains(footprint): return False
        b=footprint.boundingRect()
        if b.left()<0 or b.top()<0 or b.right()>self.width or b.bottom()>self.height: return False
        # Certify every pixel square touched by actual ink plus clearance, not just center/bbox.
        for y in range(math.floor(b.top()),min(self.height,math.ceil(b.bottom()))):
            checkpoint()
            for x in range(math.floor(b.left()),min(self.width,math.ceil(b.right()))):
                if self.component_map[y,x]!=label.component and footprint.intersects(QRectF(x,y,1,1)): return False
        return True

    @property
    def render_graph(self):
        if self.geometry_mode=="native":return self.graph
        if self._lattice_graph is None:
            self._lattice_graph=copy.deepcopy(self.graph)
            for e in self._lattice_graph["edges"]:
                e["kind"]="polyline";e["geometry"]=copy.deepcopy(e["lattice"])
        return self._lattice_graph

    @property
    def topology_certified(self):
        from .geometry import certify_lattice
        if getattr(self,"_smooth_certificate",None):
            from .geometry import smooth_fingerprint
            return self._smooth_certificate==smooth_fingerprint(self)
        return certify_lattice(self)

    @property
    def master_graph(self):
        return copy.deepcopy(getattr(self,"_master_graph",self.graph))

    def validate_smooth_geometry(self,*,repair=False):
        from .geometry import shared_component_paths,check_smooth,smooth_fingerprint
        self._smooth_certificate=None
        self.component_paths=shared_component_paths(self)
        result=check_smooth(self);fallback=[];recorded=False
        # Repair only offending noncanonical edges, then re-run the entire
        # embedding check; canonical edges are not blamed for a curve crossing.
        while repair and not result["valid"]:
            ids=[i for i in result["offending_edges"] if self.graph["edges"][i]["kind"]!="polyline" or self.graph["edges"][i]["geometry"]!=self.graph["edges"][i]["lattice"]]
            if not ids:break
            if not recorded:
                self._preserve_master();self._record();recorded=True
            for i in ids:
                e=self.graph["edges"][i];e["kind"]="polyline";e["geometry"]=copy.deepcopy(e["lattice"])
            fallback.extend(ids);self.component_paths=shared_component_paths(self)
            result=check_smooth(self)
        result["fallback_edges"]=sorted(fallback)
        if result["valid"]:self._smooth_certificate=smooth_fingerprint(self)
        if fallback:self.geometry_notice=f"Validated native curves; {len(fallback)} offending edges use explicit local pixel-boundary repair. Master preserved."
        self.smooth_report=result
        return result

    def _preserve_master(self):
        if not hasattr(self,"_master_graph"):
            self._master_graph=copy.deepcopy(self.graph)
            self._master_region_map=self.region_map.copy()
            self._master_component_map=self.component_map.copy()
            self._master_colors=dict(self.component_colors)

    def confirm_lattice_fallback(self,*,confirmed=False):
        if confirmed is not True:raise ValueError("confirm pixel-exact lattice fallback explicitly; curves will become stair-stepped")
        if self.geometry_mode=="lattice":return
        self._record();self.geometry_mode="lattice";self._smooth_certificate=None
        self.geometry_notice="Confirmed pixel-exact lattice fallback: original curves preserved in master; rendered borders are stair-stepped, no detail or color removed."
        from .geometry import build_paths
        self.face_paths,self.component_paths,self.face_components=build_paths(self)
        if not self.topology_certified:
            self._restore(self._undo.pop());raise ValueError("lattice component/hole coverage validation failed")
        self.prepare_labels()


    def prepare_labels(self,settings=None,*,cancel_event=None,progress=None):
        from .geometry import job_scope
        # All modified fields are replaced, except history lists (_record).
        state = self.__dict__.copy()
        state["_undo"] = self._undo.copy(); state["_redo"] = self._redo.copy()
        try:
            with job_scope(cancel_event, progress):
                return self._prepare_labels(settings)
        except BaseException:
            self.__dict__.clear(); self.__dict__.update(state)
            raise

    def _prepare_labels(self,settings=None):
        from .geometry import checkpoint,font_sizes
        checkpoint("labels",0,len(self.components))
        if settings is not None:
            settings.validate()
            if settings!=self.settings:self._record()
            self.settings=settings
        self.palette=list(dict.fromkeys(self.component_colors.values()))
        old={l.component:l for l in self.labels};self.labels=[];self.issues=[]
        if not self.topology_certified:
            self.issues.append(Issue("topology-uncertified",None,"Modified/smoothed curve global intersections/coverage are not certified; diagnostic export only"))
        for index,(cid,c) in enumerate(self.components.items()):
            checkpoint("labels",index,len(self.components))
            if cid not in self.component_paths:
                self.issues.append(Issue("geometry-unmapped",cid,"No certified final curve component"));continue
            number=self.palette.index(self.component_colors[cid])+1
            candidates=[]
            if cid in old and not old[cid].leader:
                previous=Label(cid,number,old[cid].x,old[cid].y,old[cid].font_pt)
                if self.label_fits(previous):
                    self.labels.append(previous);continue
                candidates.append((old[cid].x,old[cid].y))
            x0,y0,x1,y1=c["bbox"]
            mask=self.component_map[y0:y1,x0:x1]==cid
            dist=ndimage.distance_transform_edt(np.pad(mask,1))[1:-1,1:-1]
            # EDT maxima first; at most 129 anchors per bounded font trial.
            maxima=(dist==ndimage.maximum_filter(dist,size=3))&mask
            yy,xx=np.where(maxima)
            order=np.argsort(-dist[yy,xx],kind="stable")[:128]
            candidates.extend((float(xx[i]+x0)+.5,float(yy[i]+y0)+.5) for i in order)
            chosen=None
            for size in font_sizes(self.settings):
                for x,y in candidates:
                    checkpoint()
                    label=Label(cid,number,x,y,size)
                    if self.label_fits(label): chosen=label;break
                if chosen:break
            if chosen: self.labels.append(chosen)
            else:
                self.issues.append(Issue("label-no-fit",cid,
                    f"Number {number} has no certified inline fit after minimum {self.settings.min_font_pt:g} pt (preferred {self.settings.font_pt:g} pt); bounded search exhausted. Enable adjacent-region leaders, move label, or enlarge print size",
                    "inline-no-fit-after-minimum",
                    {"preferred_font_pt":self.settings.font_pt,"min_font_pt":self.settings.min_font_pt,
                     "attempted_sizes_pt":list(font_sizes(self.settings)),"candidate_limit":129,
                     "candidates_tested_per_size":len(candidates),"leaders_attempted":False}))
        checkpoint("labels",len(self.components),len(self.components))
        if self.leaders_enabled:
            from .geometry import place_leaders
            place_leaders(self,old)
        checkpoint("finalizing",0,1)
        return self.issues

    def enable_leaders(self,enabled=True):
        if type(enabled) is not bool:raise ValueError("leader mode must be boolean")
        if self.leaders_enabled==enabled:return
        self._record();self.leaders_enabled=enabled;self.prepare_labels()

    def _snapshot(self):
        names=("component_colors","labels","issues","settings","geometry_mode","geometry_notice","leaders_enabled","graph","region_map","component_map","components","_smooth_certificate","smooth_report","edit_report")
        return {k:copy.deepcopy(getattr(self,k,None)) for k in names}

    def _restore(self,state):
        for k,v in state.items():setattr(self,k,copy.deepcopy(v))
        self._lattice_graph=None
        from .geometry import build_paths,shared_component_paths
        self.face_paths,self.component_paths,self.face_components=build_paths(self)
        if self._smooth_certificate:self.component_paths=shared_component_paths(self)
        self.palette=list(dict.fromkeys(self.component_colors.values()))
        self.revision+=1;self.dirty=True

    def _record(self):
        self._undo.append(self._snapshot());self._redo.clear()
        self.revision+=1;self.dirty=True

    @property
    def can_undo(self): return bool(self._undo)
    @property
    def can_redo(self): return bool(self._redo)

    def undo(self):
        if not self._undo:return False
        self._redo.append(self._snapshot());self._restore(self._undo.pop());return True

    def redo(self):
        if not self._redo:return False
        self._undo.append(self._snapshot());self._restore(self._redo.pop());return True

    def quality_metrics(self):
        from collections import Counter
        return {"min_font_pt":self.settings.min_font_pt,
                "label_size_histogram":{f"{size:g}":count for size,count in sorted(Counter(l.font_pt for l in self.labels).items())},
                "issue_reasons":[asdict(i) for i in self.issues],"components":len(self.components),"labels":len(self.labels),
                "unresolved":sum(i.component is not None for i in self.issues),
                "font_pt":self.settings.font_pt,"width_mm":self.settings.width_mm,
                "height_mm":self.settings.height_mm,"palette":len(self.palette),
                "smooth_edges":sum(e["kind"]=="cubic" for e in self.render_graph["edges"]),
                "edges":len(self.render_graph["edges"]),"topology_certified":self.topology_certified}

    def _rgb_map(self):
        table=np.array([self.component_colors[i] for i in self.components],dtype=np.uint8)
        return table[self.component_map]

    def _rebuilt(self,labels,colors,*,cancel_event=None,progress=None):
        from .geometry import job_scope
        with job_scope(cancel_event,progress):
            return self._rebuild_candidate(labels,colors)

    def _rebuild_candidate(self,labels,colors):
        from .geometry import checkpoint,_JOB
        checkpoint("rebuild",0,1)
        state=_JOB.get()
        from .graph_edit import rebuild_from_map
        import tempfile,os
        # Native bridge receives exact component IDs, including equal colors;
        # it never quantizes or merges a second time.
        with tempfile.TemporaryDirectory(prefix="pbn-edit-",dir=os.environ.get("TMPDIR")) as tmp:
            out=rebuild_from_map(np.asarray(labels,dtype=np.uint32),[list(c) for c in colors],Path(tmp)/"graph",simplify=1.0,cancel_event=state["cancel"],progress=state["progress"])
            graph=read_json((out/"graph.json").read_bytes())
            features=self.graph.get("source_features")
            if features is not None:
                features=copy.deepcopy(features)
                original=features.get("protected_regions",[])
                protected_pixels=np.isin(self.region_map,original)
                # Rebuilt owners are input labels, not old regions or new cells4.
                # Follow pixel overlap through splits/explicit merges, retaining
                # native provenance separately from the current graph's owners.
                features.setdefault("native_protected_regions",copy.deepcopy(original))
                owners=sorted(int(v) for v in np.unique(labels[protected_pixels]))
                features["protected_regions"]=owners
                graph["source_features"]=features
                protected=set(owners)
                for edge in graph["edges"]:
                    if edge["left"] in protected or edge["right"] in protected:
                        edge["kind"]="polyline";edge["geometry"]=copy.deepcopy(edge["lattice"])
            candidate=Document._from_data(graph,(out/"labels.u32le").read_bytes(),(out/"components4.u32le").read_bytes())
        candidate.settings=self.settings;candidate.leaders_enabled=self.leaders_enabled
        checkpoint("validate",0,1)
        result=candidate.validate_smooth_geometry(repair=True)
        if not result["valid"]:raise ValueError("edited graph failed planar/boundary validation")
        candidate.prepare_labels()
        return candidate

    def _adopt_edit(self,candidate,before_rgb,before_metrics,operation,details):
        changed=int(np.count_nonzero(np.any(candidate._rgb_map()!=before_rgb,axis=2)))
        self._preserve_master();self._record()
        for name in ("graph","region_map","component_map","components","component_colors","palette","labels","issues","face_paths","component_paths","face_components","geometry_mode","leaders_enabled","_smooth_certificate","smooth_report"):
            setattr(self,name,getattr(candidate,name))
        self._lattice_graph=None
        report={"operation":operation,"before":before_metrics,"after":self.quality_metrics(),
                "changed_pixels":changed,"changed_area_fraction":changed/(self.width*self.height),
                "changed_area_mm2":changed*self.mm_per_unit**2,"details":details,
                "fallback_edges":candidate.smooth_report["fallback_edges"]}
        self.edit_report=report
        self.geometry_notice=f"Explicit {operation}: {before_metrics['components']} -> {len(self.components)} components; {changed/(self.width*self.height):.2%} pixel color area changed; {self.settings.font_pt:g} pt preserved; native master retained. Local repaired edges: {len(report['fallback_edges'])}."
        return report

    def merge_components(self,a,b):
        """Merge adjacent b INTO a, retaining a's color. One atomic undo step."""
        if a not in self.components or b not in self.components or a==b:raise ValueError("two distinct components required")
        mask=self.component_map==a
        adjacent=ndimage.binary_dilation(mask)&(self.component_map==b)
        if not adjacent.any():raise ValueError("components must be edge-adjacent")
        if np.any(self.component_map==OUTSIDE):raise ValueError("editing transparent outside is not supported")
        labels=self.component_map.copy();labels[labels==b]=a
        candidate=self._rebuilt(labels,[self.component_colors[i] for i in self.components])
        return self._adopt_edit(candidate,self._rgb_map(),self.quality_metrics(),"merge",{"target":a,"source":b,"source_pixels":self.components[b]["area"]})

    def split_component(self,cid,polyline):
        """Cut a cell with a raster line, then fill the cut without losing ink.

        New region IDs deliberately retain duplicate palette colors so the
        native bridge preserves the internal boundary instead of recoloring it.
        """
        if cid not in self.components:raise ValueError("unknown component")
        if not isinstance(polyline,(list,tuple)) or not 2<=len(polyline)<=4096:
            raise ValueError("split requires a bounded polyline")
        for point in polyline:
            if not isinstance(point,(list,tuple)) or len(point)!=2 or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in point):
                raise ValueError("invalid split coordinates")
            if not (0<=point[0]<=self.width and 0<=point[1]<=self.height):raise ValueError("split coordinates outside image")
        if np.any(self.component_map==OUTSIDE):raise ValueError("editing transparent outside is not supported")
        from PIL import Image,ImageDraw
        x0,y0,x1,y1=self.components[cid]["bbox"]
        line=Image.new("1",(x1-x0,y1-y0))
        ImageDraw.Draw(line).line([(math.floor(x-x0),math.floor(y-y0)) for x,y in polyline],fill=1,width=1)
        mask=self.component_map[y0:y1,x0:x1]==cid
        cut=np.asarray(line,dtype=bool)&mask
        parts,count=ndimage.label(mask&~cut)
        if count<2:raise ValueError("polyline does not divide the component")
        # Geodesic flood only through this component. Every cut pixel receives
        # a neighboring part's ID; no pixel becomes background or a new color.
        pending=cut.copy()
        while pending.any():
            neighbors=np.maximum.reduce([np.pad(parts[1:],((0,1),(0,0))),np.pad(parts[:-1],((1,0),(0,0))),np.pad(parts[:,1:],((0,0),(0,1))),np.pad(parts[:,:-1],((0,0),(1,0)))])
            fill=pending&(neighbors>0)
            if not fill.any():raise ValueError("cannot fill split ownership")
            parts[fill]=neighbors[fill];pending[fill]=False
        labels=self.component_map.copy();colors=[self.component_colors[i] for i in self.components]
        mapping=[0,cid]+list(range(len(colors),len(colors)+count-1))
        colors.extend([self.component_colors[cid]]*(count-1))
        view=labels[y0:y1,x0:x1];view[mask]=np.asarray(mapping,dtype=np.uint32)[parts[mask]]
        candidate=self._rebuilt(labels,colors)
        if len(candidate.components)<=len(self.components):raise ValueError("split did not create distinct components")
        return self._adopt_edit(candidate,self._rgb_map(),self.quality_metrics(),"split",{"source":cid,"parts":count,"polyline":[list(p) for p in polyline],"cut_pixels":int(cut.sum())})

    def simplify_for_print(self,*,max_passes=8,max_changed_fraction=.03,max_cell_area_mm2=50.,leaders=True,cancel_event=None,progress=None):
        from .geometry import job_scope
        state=self.__dict__.copy()
        state["_undo"]=self._undo.copy();state["_redo"]=self._redo.copy()
        try:
            with job_scope(cancel_event,progress):
                return self._simplify_for_print(max_passes=max_passes,max_changed_fraction=max_changed_fraction,max_cell_area_mm2=max_cell_area_mm2,leaders=leaders)
        except BaseException:
            self.__dict__.clear();self.__dict__.update(state)
            raise

    def _simplify_for_print(self,*,max_passes=8,max_changed_fraction=.03,max_cell_area_mm2=50.,leaders=True):
        """Explicit bounded RAG edits; never resize pages or reduce label size.

        Only unresolved small unprotected cells may disappear. Protected source
        owners are neither merge sources nor targets. Adjacent targets are
        ranked in perceptual OKLab, with contact length as a stable tie breaker.
        A batch changes ownership once, then uses the real native graph rebuild.
        """
        if type(max_passes) is not int or not 1<=max_passes<=32:
            raise ValueError("max_passes must be between 1 and 32")
        if not math.isfinite(max_changed_fraction) or not 0<=max_changed_fraction<=.15:
            raise ValueError("changed fraction must be between 0 and .15")
        if not math.isfinite(max_cell_area_mm2) or not 0<max_cell_area_mm2<=100:
            raise ValueError("small cell area must be between 0 and 100 mm2")
        if np.any(self.component_map==OUTSIDE):raise ValueError("editing transparent outside is not supported")
        if type(leaders) is not bool:raise ValueError("leaders must be boolean")
        from .geometry import checkpoint
        checkpoint("simplify",0,max_passes)
        before_rgb=self._rgb_map();before=self.quality_metrics();work=self
        budget=math.floor(self.width*self.height*max_changed_fraction)
        passes=[];protected_skipped=set()
        def perceptual(colors):
            rgb=np.asarray(colors,dtype=float)/255
            rgb=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
            lms=rgb@np.array([[.4122214708,.5363325363,.0514459929],[.2119034982,.6806995451,.1073969566],[.0883024619,.2817188376,.6299787005]]).T
            return np.cbrt(lms)@np.array([[.2104542553,.7936177850,-.0040720468],[1.9779984951,-2.4285922050,.4505937099],[.0259040371,.7827717662,-.8086757660]]).T
        for iteration in range(max_passes):
            checkpoint("simplify",iteration+1,max_passes)
            missing={i.component for i in work.issues if i.code=="label-no-fit"}
            protected_regions=set((work.graph.get("source_features") or {}).get("protected_regions",[]))
            protected={cid for cid,c in work.components.items() if c["region"] in protected_regions}
            protected_skipped.update(missing & protected)
            small=[cid for cid in missing-protected if work.components[cid]["area"]*self.mm_per_unit**2<=max_cell_area_mm2]
            if not small:break
            cells=work.component_map
            colors=[work.component_colors[i] for i in work.components]
            lab=perceptual(colors);parents=np.arange(len(colors),dtype=np.uint32)
            area=np.array([work.components[i]["area"] for i in work.components],dtype=np.int64)
            adjacency={i:{} for i in work.components}
            for a,b in ((cells[:,1:],cells[:,:-1]),(cells[1:,:],cells[:-1,:])):
                mask=a!=b
                pairs,counts=np.unique(np.sort(np.stack((a[mask],b[mask]),axis=1),axis=1),axis=0,return_counts=True)
                for (a,b),n in zip(pairs,counts):
                    a,b=int(a),int(b);adjacency[a][b]=adjacency[a].get(b,0)+int(n);adjacency[b][a]=adjacency[b].get(a,0)+int(n)
            # Reserve original-pixel changes conservatively; targets never change
            # their color, and a target that has received a cell is not a source.
            reserved=int(np.count_nonzero(np.any(work._rgb_map()!=before_rgb,axis=2)))
            received=set();merges=[]
            for cid in sorted(small,key=lambda i:(area[i],i)):
                checkpoint()
                if cid in received or int(parents[cid])!=cid:continue
                neighbors={int(parents[n]):contact for n,contact in adjacency[cid].items() if int(parents[n])!=cid and int(parents[n]) not in protected}
                if not neighbors:continue
                target=min(neighbors,key=lambda n:(float(np.sum((lab[cid]-lab[n])**2)),-neighbors[n],-area[n],n))
                cost=int(area[cid]) if colors[cid]!=colors[target] else 0
                if reserved+cost>budget:continue
                parents[cid]=target;received.add(target);area[target]+=area[cid];reserved+=cost
                merges.append({"source":cid,"target":target,"pixels":int(area[cid]),"oklab_distance":float(np.linalg.norm(lab[cid]-lab[target]))})
            if not merges:break
            labels=parents[cells]
            candidate=work._rebuilt(labels,colors)
            changed=int(np.count_nonzero(np.any(candidate._rgb_map()!=before_rgb,axis=2)))
            if changed>budget:raise ValueError("simplification exceeded changed area budget")
            passes.append({"pass":iteration+1,"merges":merges,"changed_pixels":changed,"unresolved":candidate.quality_metrics()["unresolved"]})
            work=candidate
        if work is not self and leaders:
            work.leaders_enabled=True;work.prepare_labels()
        details={"leaders":leaders,"protected_components_skipped":sorted(protected_skipped),"passes":passes,"max_passes":max_passes,"max_changed_fraction":max_changed_fraction,"max_cell_area_mm2":max_cell_area_mm2}
        if work is self:
            return {"operation":"simplify","before":before,"after":before.copy(),"changed_pixels":0,"changed_area_fraction":0.,"changed_area_mm2":0.,"details":details,"fallback_edges":[]}
        checkpoint("adopt",1,1)
        return self._adopt_edit(work,before_rgb,before,"simplify",details)

    def recolor(self,component,rgb):
        if component not in self.components or len(rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in rgb): raise ValueError("invalid recolor")
        if self.component_colors[component]==tuple(rgb):return
        self._record();self.component_colors[component]=tuple(rgb);self.prepare_labels()

    def move_label(self,component,x,y):
        if component not in self.components:raise ValueError("unknown component")
        from .geometry import font_sizes,job_scope,checkpoint
        previous_label=next((l for l in self.labels if l.component==component),None)
        sizes=font_sizes(self.settings)
        if previous_label and self.settings.min_font_pt<=previous_label.font_pt<=self.settings.font_pt:
            sizes=(previous_label.font_pt,)+tuple(s for s in sizes if s!=previous_label.font_pt)
        label=None
        with job_scope():
            for size in sizes:
                checkpoint()
                candidate=Label(component,self.palette.index(self.component_colors[component])+1,x,y,size)
                if self.label_fits(candidate):label=candidate;break
        if label is None:raise ValueError("label footprint does not fit final geometry/component")
        previous=self.labels
        self.labels=[l for l in previous if l.component!=component]+[label]
        valid=all(self.label_fits(l) for l in self.labels if l.leader)
        self.labels=previous
        if not valid:raise ValueError("label move causes leader collision")
        self._record();self.labels=[l for l in self.labels if l.component!=component]+[label]
        self.labels.sort(key=lambda l:l.component)
        self.issues=[i for i in self.issues if not(i.component==component and i.code=="label-no-fit")]
