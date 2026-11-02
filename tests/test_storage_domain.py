# SPDX-License-Identifier: GPL-3.0-only
import importlib,hashlib,json,zipfile
from io import BytesIO
from pathlib import Path
import pytest
from test_document_domain import domain,FIXTURES

def storage():
    assert importlib.util.find_spec("pbnstudio.storage") is not None,"storage missing"
    return importlib.import_module("pbnstudio.storage")

def test_project_roundtrip_preserves_master_edits_and_settings(tmp_path):
    s=storage();m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    from PIL import Image
    source=BytesIO();Image.new("RGB",(9,9),(255,0,0)).save(source,format="PNG")
    d.source_bytes=source.getvalue();d.prepare_labels(m.PrintSettings(font_pt=9))
    d.recolor(1,(12,34,56));d.move_label(1,4.5,4.5)
    before=d.svg("numbers");answer=d.svg("color")
    p=tmp_path/"project.pbn";s.save_project(d,p);assert not d.dirty
    loaded=s.load_project(p)
    assert loaded.source_bytes==d.source_bytes
    assert loaded.svg("numbers")==before and loaded.svg("color")==answer
    assert loaded.settings==d.settings and not loaded.dirty and not loaded.undo()
    with pytest.raises(FileExistsError):s.save_project(d,p)

def test_project_traversal_hash_and_missing_source_fail_closed(tmp_path):
    s=storage();m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    with pytest.raises(ValueError,match="source"):s.save_project(d,tmp_path/"missing.pbn")
    assert not (tmp_path/"missing.pbn").exists()
    p=tmp_path/"bad.pbn"
    with zipfile.ZipFile(p,"w") as z:z.writestr("../escape",b"x")
    with pytest.raises(ValueError):s.load_project(p)
    assert not (tmp_path/"escape").exists()


def test_project_keeps_processing_provenance_and_detects_tampering(tmp_path):
    s=storage();m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    from PIL import Image
    buf=BytesIO();Image.new("RGB",(9,9)).save(buf,format="PNG");d.source_bytes=buf.getvalue()
    d.artifact_manifest={"settings":{"colors":12},"original_sha256":"a"*64,"normalization":{"resize":False}}
    d.prepare_labels();p=tmp_path/"a.pbn";s.save_project(d,p)
    assert s.load_project(p).artifact_manifest==d.artifact_manifest
    with zipfile.ZipFile(p) as z:blobs={n:z.read(n) for n in z.namelist()}
    blobs["edits.json"]=b"{}"
    with zipfile.ZipFile(tmp_path/"tampered.pbn","w") as z:
        for n,data in blobs.items():z.writestr(n,data)
    with pytest.raises(ValueError,match="hash"):s.load_project(tmp_path/"tampered.pbn")


def test_project_roundtrip_lattice_leader_and_old_label_schema(tmp_path):
    from PIL import Image
    m=domain();s=storage();d=m.Document.load(FIXTURES/"hole",trusted=True)
    buf=BytesIO();Image.new("RGB",(9,9)).save(buf,format="PNG");d.source_bytes=buf.getvalue()
    d.confirm_lattice_fallback(confirmed=True)
    d.prepare_labels(m.PrintSettings(width_mm=30,height_mm=30,margin_mm=0,font_pt=38,min_font_pt=38));d.enable_leaders()
    assert any(l.leader for l in d.labels)
    p=tmp_path/"leaders.pbn";s.save_project(d,p);loaded=s.load_project(p)
    assert loaded.geometry_mode=="lattice" and loaded.leaders_enabled
    assert loaded.graph==d.graph and loaded.labels==d.labels
    assert loaded.svg("numbers")==d.svg("numbers")
    assert not __import__('pbnstudio.exports',fromlist=['preflight']).preflight(loaded)


def test_load_actual_v1_archive_without_additive_fields(tmp_path):
    from PIL import Image
    m=domain();s=storage();d=m.Document.load(FIXTURES/"hole",trusted=True)
    buf=BytesIO();Image.new("RGB",(9,9)).save(buf,format="PNG");d.source_bytes=buf.getvalue();d.prepare_labels()
    p=tmp_path/"v2.pbn";s.save_project(d,p)
    with zipfile.ZipFile(p) as z:blobs={n:z.read(n) for n in z.namelist()}
    state=json.loads(blobs["edits.json"])
    for key in ("geometry_mode","lattice_confirmed","leaders_enabled"):state.pop(key)
    for label in state["labels"]:label.pop("leader")
    blobs["edits.json"]=s.encode(state)
    manifest=json.loads(blobs["manifest.json"]);manifest["schema"]="pbn-studio.project/1"
    for entry in manifest["files"]:
        data=blobs[entry["path"]];entry["bytes"]=len(data);entry["sha256"]=hashlib.sha256(data).hexdigest()
    blobs["manifest.json"]=s.encode(manifest)
    p=tmp_path/"v1.pbn"
    with zipfile.ZipFile(p,"w") as z:
        for n,b in blobs.items():z.writestr(n,b)
    loaded=s.load_project(p)
    assert loaded.geometry_mode=="native" and not loaded.leaders_enabled
    assert loaded.svg("numbers")==d.svg("numbers")
