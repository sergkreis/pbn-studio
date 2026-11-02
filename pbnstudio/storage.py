# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, hash-verified, versioned project ZIP; no extraction and no pickle."""
from pathlib import Path
from dataclasses import asdict
from io import BytesIO
import hashlib,json,os,tempfile,zipfile
from PIL import Image
from .document import Document,PrintSettings,Label,MAX_BYTES,safe_name,read_json

SCHEMA="pbn-studio.project/4"
READ_SCHEMAS={"pbn-studio.project/1","pbn-studio.project/2","pbn-studio.project/3",SCHEMA}
REQUIRED={"graph.json","labels.u32le","components4.u32le","source.png","edits.json"}
MASTER={"master-graph.json","master-labels.u32le","master-components4.u32le","master-colors.json"}

def encode(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode("utf-8")

def validate_source(data,document):
    if not data:raise ValueError("project requires preserved source/master PNG")
    with Image.open(BytesIO(data)) as im:
        if im.format!="PNG" or im.size!=(document.width,document.height):raise ValueError("source/master dimensions or format mismatch")
        im.verify()

def save_project(doc,path):
    path=Path(path)
    if path.exists():raise FileExistsError(path)
    validate_source(doc.source_bytes,doc)
    state={"geometry_mode":doc.geometry_mode,"lattice_confirmed":doc.geometry_mode=="lattice","leaders_enabled":doc.leaders_enabled,"artifact_manifest":doc.artifact_manifest,"settings":asdict(doc.settings),"component_colors":{str(k):list(v) for k,v in doc.component_colors.items()},"labels":[asdict(l) for l in doc.labels]}
    blobs={"graph.json":encode(doc.graph),"labels.u32le":doc.region_map.astype("<u4").tobytes(),"components4.u32le":doc.component_map.astype("<u4").tobytes(),"source.png":doc.source_bytes,"edits.json":encode(state)}
    state["edit_report"]=getattr(doc,"edit_report",None)
    state["geometry_notice"]=doc.geometry_notice
    # A saved fingerprint is never authority. Re-run validation on load.
    state["smooth_validated"]=bool(getattr(doc,"_smooth_certificate",None))
    blobs["edits.json"]=encode(state)
    if hasattr(doc,"_master_graph"):
        blobs.update({"master-graph.json":encode(doc._master_graph),
                      "master-labels.u32le":doc._master_region_map.astype("<u4").tobytes(),
                      "master-components4.u32le":doc._master_component_map.astype("<u4").tobytes(),
                      "master-colors.json":encode({str(k):list(v) for k,v in doc._master_colors.items()})})
    if sum(map(len,blobs.values()))>MAX_BYTES:raise ValueError("project exceeds size limit")
    manifest={"schema":SCHEMA,"width":doc.width,"height":doc.height,"files":[{"path":name,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()} for name,data in blobs.items()]}
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix=".pbn-project-",dir=path.parent);os.close(fd)
    try:
        with zipfile.ZipFile(temp,"w",compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json",encode(manifest))
            for name,data in blobs.items():archive.writestr(name,data)
        # Read back actual archive before publishing; a failed validation leaves no destination.
        load_project(temp)
        if path.exists():raise FileExistsError(path)
        os.rename(temp,path);doc.dirty=False
    finally:
        if os.path.exists(temp):os.unlink(temp)

def load_project(path):
    try:
        with zipfile.ZipFile(path) as archive:
            entries=archive.infolist()
            if len(entries) not in (6,10) or sum(e.file_size for e in entries)>MAX_BYTES:raise ValueError("project archive size/count limit")
            names=[safe_name(e.filename) for e in entries]
            if len(set(names))!=len(names) or set(names) not in (REQUIRED|{"manifest.json"},REQUIRED|MASTER|{"manifest.json"}):raise ValueError("unexpected/duplicate project members")
            if any(e.flag_bits&1 or e.file_size>MAX_BYTES or (e.external_attr>>16)&0o170000==0o120000 for e in entries):raise ValueError("unsafe ZIP member")
            manifest=read_json(archive.read("manifest.json"))
            if manifest.get("schema") not in READ_SCHEMAS:raise ValueError("unsupported project version")
            expected=REQUIRED|(MASTER if set(names)&MASTER else set())
            if expected!=REQUIRED and manifest["schema"] not in ("pbn-studio.project/3",SCHEMA):raise ValueError("master members require schema 3 or later")
            listed=manifest.get("files",[])
            if len(listed)!=len(expected) or {safe_name(e["path"]) for e in listed}!=expected:raise ValueError("incomplete project manifest")
            blobs={}
            for item in listed:
                data=archive.read(item["path"])
                if len(data)!=item["bytes"] or hashlib.sha256(data).hexdigest()!=item["sha256"]:raise ValueError("project hash mismatch")
                blobs[item["path"]]=data
        doc=Document._from_data(read_json(blobs["graph.json"]),blobs["labels.u32le"],blobs["components4.u32le"])
        if (manifest.get("width"),manifest.get("height"))!=(doc.width,doc.height):raise ValueError("project dimensions mismatch")
        if MASTER<=blobs.keys():
            master=Document._from_data(read_json(blobs["master-graph.json"]),blobs["master-labels.u32le"],blobs["master-components4.u32le"])
            if (master.width,master.height)!=(doc.width,doc.height):raise ValueError("master dimensions mismatch")
            colors=read_json(blobs["master-colors.json"])
            if set(colors)!={str(cid) for cid in master.components}:raise ValueError("master color references mismatch")
            for cid in master.components:
                rgb=colors[str(cid)]
                if len(rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in rgb):raise ValueError("invalid master color")
                master.component_colors[cid]=tuple(rgb)
            doc._master_graph=master.graph;doc._master_region_map=master.region_map
            doc._master_component_map=master.component_map;doc._master_colors=master.component_colors
        doc.source_bytes=blobs["source.png"];validate_source(doc.source_bytes,doc)
        state=read_json(blobs["edits.json"])
        settings=dict(state["settings"])
        if "min_font_pt" not in settings:
            if manifest["schema"]==SCHEMA:raise ValueError("schema 4 requires explicit minimum font")
            settings["min_font_pt"]=settings["font_pt"]
        doc.settings=PrintSettings(**settings)
        doc.artifact_manifest=state.get("artifact_manifest")
        colors=state["component_colors"]
        if set(colors)!={str(cid) for cid in doc.components}:raise ValueError("edit component references mismatch")
        for cid in doc.components:
            rgb=colors[str(cid)]
            if len(rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in rgb):raise ValueError("invalid stored color")
            doc.component_colors[cid]=tuple(rgb)
        doc.palette=list(dict.fromkeys(doc.component_colors.values()))
        mode=state.get("geometry_mode","native")
        if mode not in ("native","lattice"):raise ValueError("invalid geometry mode")
        if mode=="lattice":
            if state.get("lattice_confirmed") is not True:raise ValueError("missing lattice confirmation")
            doc.confirm_lattice_fallback(confirmed=True)
        if state.get("smooth_validated",False):
            if state["smooth_validated"] is not True:raise ValueError("invalid smooth validation request")
            if not doc.validate_smooth_geometry(repair=False)["valid"]:raise ValueError("stored smooth geometry failed revalidation")
        doc.edit_report=state.get("edit_report")
        doc.geometry_notice=state.get("geometry_notice",doc.geometry_notice)
        enabled=state.get("leaders_enabled",False)
        if type(enabled) is not bool:raise ValueError("invalid leader mode")
        doc.leaders_enabled=enabled
        labels=[Label(**(v|{"leader":tuple(tuple(p) for p in v.get("leader",()))})) for v in state["labels"]]
        doc.labels=labels  # Validate every route against all other restored labels.

        seen=set()
        for l in labels:
            if (l.leader and not doc.leaders_enabled) or l.component not in doc.components or l.component in seen or l.number!=doc.palette.index(doc.component_colors[l.component])+1 or not doc.label_fits(l):raise ValueError("invalid stored label footprint/reference")
            seen.add(l.component)
        doc.labels=labels;doc.prepare_labels()
        doc.dirty=False;doc.revision=0;doc._undo=[];doc._redo=[]
        return doc
    except (KeyError,TypeError,OSError,zipfile.BadZipFile,OverflowError) as exc:raise ValueError("invalid project archive") from exc
