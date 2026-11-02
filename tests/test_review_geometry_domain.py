# SPDX-License-Identifier: GPL-3.0-only
"""Independent-review regressions; hostile loads always run in isolation."""
import hashlib, json, os, subprocess, sys, zipfile
from pathlib import Path
from io import BytesIO
import pytest
from test_document_domain import domain, FIXTURES


def test_hostile_leader_archive_rejected_within_timeout(tmp_path):
    from PIL import Image
    from pbnstudio.storage import save_project, encode
    m=domain();d=m.Document.load(FIXTURES/"hole",trusted=True)
    source=BytesIO();Image.new("RGB",(9,9)).save(source,format="PNG");d.source_bytes=source.getvalue()
    d.prepare_labels(m.PrintSettings(width_mm=30,height_mm=30,margin_mm=0,font_pt=38,min_font_pt=38));d.enable_leaders()
    valid=tmp_path/"valid.pbn";save_project(d,valid)
    with zipfile.ZipFile(valid) as z:blobs={n:z.read(n) for n in z.namelist()}
    edits=json.loads(blobs["edits.json"])
    next(l for l in edits["labels"] if l["leader"])["leader"][1]=[1e12,1e12]
    blobs["edits.json"]=encode(edits);manifest=json.loads(blobs["manifest.json"])
    for entry in manifest["files"]:
        data=blobs[entry["path"]];entry.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    blobs["manifest.json"]=encode(manifest);hostile=tmp_path/"hostile.pbn"
    with zipfile.ZipFile(hostile,"w",compression=zipfile.ZIP_DEFLATED) as z:
        for name,data in blobs.items():z.writestr(name,data)
    env=os.environ.copy();env.pop("PYTHONHOME",None);env.pop("PYTHONPATH",None);env["QT_QPA_PLATFORM"]="offscreen"
    code="""
import sys
from pbnstudio.storage import load_project
try:
    load_project(sys.argv[1])
except ValueError as exc:
    assert 'label' in str(exc), str(exc)
    print('rejected invalid stored leader')
else:
    raise AssertionError('hostile project was accepted')
"""
    try:
        result=subprocess.run([sys.executable,"-c",code,str(hostile)],cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail("hostile leader load exceeded 5 seconds (child killed)")
    assert result.returncode==0,result.stdout+result.stderr
    assert "rejected invalid stored leader" in result.stdout


def test_segment_owners_caps_work_even_for_in_bounds_short_physical_route():
    from types import SimpleNamespace
    from pbnstudio.geometry import segment_owners
    # A legal thin image can have millions of grid crossings in 18mm.
    document=SimpleNamespace(width=200000,height=1,mm_per_unit=.00001,
                             sample_component=lambda x,y:1)
    assert segment_owners(document,(.5,.5),(100000.5,.5))==[]


@pytest.mark.parametrize("a,b,mm",[
    ((.5,.5),(1e12,1e12),1),
    ((1e12,1e12),(.5,.5),1),
    ((-.01,.5),(.5,.5),1),
    ((.5,.5),(9,.5),1),
    ((.5,.5),(.5,9),1),
    ((float("nan"),.5),(.5,.5),1),
    ((.5,.5),(float("inf"),.5),1),
    ((.5,.5),(8.5,.5),3),
])
def test_segment_rejects_invalid_endpoints_or_length_before_sampling(a,b,mm):
    from types import SimpleNamespace
    from pbnstudio.geometry import segment_owners
    def forbidden(*args):
        pytest.fail("invalid segment reached grid sampling")
    d=SimpleNamespace(width=9,height=9,mm_per_unit=mm,sample_component=forbidden)
    assert segment_owners(d,a,b)==[]


def test_segment_preserves_exact_owner_sequence_at_length_limit():
    from types import SimpleNamespace
    from pbnstudio.geometry import segment_owners
    d=SimpleNamespace(width=10,height=1,mm_per_unit=2,
                      sample_component=lambda x,y:0 if x<5 else 1)
    assert segment_owners(d,(.5,.5),(9.5,.5))==[0,1]
