# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import numpy as np


def test_same_rgb_distinct_ids_survive(tmp_path):
    assert importlib.util.find_spec("pbnstudio.graph_edit") is not None, "exact map bridge missing"
    from pbnstudio.graph_edit import rebuild_from_map
    from pbnstudio.document import Document
    labels = np.array([[1, 1, 0, 0], [1, 1, 0, 0]], dtype=np.uint32)
    palette = [[23, 45, 67], [23, 45, 67, 255]]
    out = rebuild_from_map(labels, palette, tmp_path / "out")
    graph = json.loads((out / "graph.json").read_text())
    assert (out / "labels.u32le").read_bytes() == labels.astype("<u4").tobytes()
    assert graph["palette"] == [[23,45,67,255]] * 2
    assert {c["region"] for c in graph["components"]} == {0,1}
    assert len(graph["components"]) == 2
    assert graph["merge_diff"] == -1 and graph["color_fitters"] == 0
    assert any({e["left"],e["right"]} == {0,1} for e in graph["edges"])
    doc = Document._from_data(graph, (out / "labels.u32le").read_bytes(), (out / "components4.u32le").read_bytes())
    assert doc.width == 4
    assert any(e["kind"] == "cubic" for e in graph["edges"])
    assert not (out / "source.png").exists()


def test_native_map_mode_refuses_existing_artifact_dir(tmp_path):
    import subprocess
    from pbnstudio.engine import WORKER_PATH
    labels = tmp_path / "labels"; labels.write_bytes(bytes(16))
    palette = tmp_path / "palette"; palette.write_bytes(bytes([1,2,3,255]))
    out = tmp_path / "out"; out.mkdir()
    (out / "graph.json").write_text("original")
    result = subprocess.run([str(WORKER_PATH), "--map", str(labels), "2", "2", str(out), str(palette), "1"], capture_output=True)
    assert result.returncode != 0
    assert (out / "graph.json").read_text() == "original"


def test_packaged_worker_receipt_matches_owned_source():
    import hashlib
    from pathlib import Path
    from pbnstudio.engine import WORKER_PATH
    root = Path(__file__).resolve().parents[1]
    receipt_path = WORKER_PATH.parent / "build-receipt.json"
    assert receipt_path.is_file(), "worker package lacks verifiable adapter build receipt"
    receipt = json.loads(receipt_path.read_text())
    assert receipt["sha256"] == hashlib.sha256(WORKER_PATH.read_bytes()).hexdigest()
    assert receipt["adapter_sha256"] == hashlib.sha256((root / "native/worker/main.rs").read_bytes()).hexdigest()
    assert receipt["lock_sha256"] == hashlib.sha256((root / "vendor/vtracer/Cargo.lock").read_bytes()).hexdigest()
    assert "--locked" in receipt["argv"] and receipt["exit_code"] == 0
    assert "exact-map-cli/1" in receipt["protocols"]


import hashlib
import pytest
from pathlib import Path
from pbnstudio.graph_edit import rebuild_from_map
from pbnstudio.engine import EngineError


def load_graph(out):
    from pbnstudio.document import Document
    g = json.loads((out / "graph.json").read_text())
    doc = Document._from_data(g, (out / "labels.u32le").read_bytes(), (out / "components4.u32le").read_bytes())
    return g, doc


def test_nested_holes_shared_edges_and_point_contact(tmp_path):
    labels = np.zeros((12,12), dtype=np.uint32)
    labels[2:10,2:10] = 1; labels[4:8,4:8] = 2
    out = rebuild_from_map(labels, [[30,40,50],[60,70,80],[90,100,110]], tmp_path / "holes")
    g, doc = load_graph(out)
    assert sorted(len(f["contours"]) for f in g["faces8"]) == [1,2,2]
    assert [c["area"] for c in g["components"]] == [80,48,16]
    assert np.array_equal(doc.region_map, labels)
    used = [(eid, forward) for f in g["faces8"] for contour in f["contours"] for eid, forward in contour]
    for e in g["edges"]:
        for forward, side in [(True,"left"),(False,"right")]:
            assert used.count((e["id"],forward)) == (e[side] != 0xffffffff)
    check = np.array([[0,1],[1,0]], dtype=np.uint32)
    cg, cd = load_graph(rebuild_from_map(check, [[0,0,0],[255,255,255]], tmp_path / "pinch"))
    assert len(cg["components"]) == 4 and len(cg["faces8"]) == 2
    assert [c["region"] for c in cg["components"]] == [0,1,1,0]
    assert np.array_equal(cd.region_map, check)


def test_manual_merge_split_keeps_other_ownership_and_unused_ids(tmp_path):
    labels = np.zeros((12,20), dtype=np.uint32)
    labels[:,6:12] = 2; labels[:,12:] = 3
    palette = [[0,0,0],[255,0,0],[50,50,50],[100,100,100],[100,100,100]]
    # Merge into 0, then split region 3 while keeping identical color.
    labels[labels==2] = 0; labels[6:,12:] = 4
    out = rebuild_from_map(labels[:,::-1][:,::-1], palette, tmp_path / "edited")
    g, doc = load_graph(out)
    assert np.array_equal(doc.region_map, labels)
    assert [c["region"] for c in g["components"]] == [0,3,4]
    assert g["palette"] == [c+[255] for c in palette]
    assert any({e["left"],e["right"]}=={3,4} for e in g["edges"])


@pytest.mark.parametrize("labels,palette,simplify", [
    (np.zeros((0,2),np.uint32), [[0,0,0]], 1),
    (np.zeros((2,2),np.int64), [[0,0,0]], 1),
    (np.zeros((2,2),float), [[0,0,0]], 1),
    (np.zeros((2,2,1),np.uint32), [[0,0,0]], 1),
    (np.full((2,2),0xffffffff,np.uint32), [[0,0,0]], 1),
    (np.zeros((2,2),np.uint32), [], 1),
    (np.zeros((2,2),np.uint32), [[-1,0,0]], 1),
    (np.zeros((2,2),np.uint32), [[256,0,0]], 1),
    (np.zeros((2,2),np.uint32), [[True,0,0]], 1),
    (np.zeros((2,2),np.uint32), [[0,0,0,254]], 1),
    (np.zeros((2,2),np.uint32), [[0,0,0]], float("nan")),
    (np.zeros((2,2),np.uint32), [[0,0,0]], float("inf")),
    (np.zeros((2,2),np.uint32), [[0,0,0]], -1),
    (np.zeros((2,2),np.uint32), [[0,0,0]], 6),
])
def test_rejects_bad_input_without_artifacts(tmp_path,labels,palette,simplify):
    with pytest.raises(EngineError): rebuild_from_map(labels,palette,tmp_path/"out",simplify)
    assert not (tmp_path/"out").exists()


def test_resource_budget_and_existing_directory(tmp_path):
    huge = np.broadcast_to(np.uint32(0),(40000001,1))
    with pytest.raises(EngineError,match="budget"): rebuild_from_map(huge,[[0,0,0]],tmp_path/"out")
    with pytest.raises(EngineError,match="budget"): rebuild_from_map(np.zeros((1,1),np.uint32),[[0,0,0]]*1000001,tmp_path/"out")
    out=tmp_path/"out";out.mkdir();(out/"keep").write_text("keep")
    with pytest.raises(EngineError,match="new"): rebuild_from_map(np.zeros((1,1),np.uint32),[[0,0,0]],out)
    assert (out/"keep").read_text()=="keep"


def test_hash_manifest_exact_inputs_and_no_source_claim(tmp_path):
    labels=np.arange(6,dtype=np.uint32).reshape(2,3)[:,::-1].astype(">u4")
    colors=[[1,2,3]]*6
    out=rebuild_from_map(labels,colors,tmp_path/"out",0)
    manifest=json.loads((out/"manifest.json").read_text())
    assert manifest["schema"]=="graph-edit-artifacts/1"
    assert manifest["input_labels_sha256"]==hashlib.sha256(labels.astype("<u4").tobytes()).hexdigest()
    assert manifest["input_palette_rgba_sha256"]==hashlib.sha256(bytes([1,2,3,255])*6).hexdigest()
    assert "original_sha256" not in manifest
    for e in manifest["files"]:
        data=(out/e["path"]).read_bytes()
        assert len(data)==e["bytes"] and hashlib.sha256(data).hexdigest()==e["sha256"]
    assert not (out/"input.u32le").exists() and not (out/"palette.rgba").exists()


def test_timeout_and_missing_worker_clean_partial_job(tmp_path,monkeypatch):
    from pbnstudio import engine
    monkeypatch.setattr(engine,"NATIVE_TIMEOUT_SECONDS",0)
    with pytest.raises(EngineError): rebuild_from_map(np.zeros((50,50),np.uint32),[[0,0,0]],tmp_path/"timeout")
    assert not (tmp_path/"timeout").exists()
    monkeypatch.setattr(engine,"WORKER_PATH",tmp_path/"missing.exe")
    with pytest.raises(EngineError): rebuild_from_map(np.zeros((2,2),np.uint32),[[0,0,0]],tmp_path/"missing")
    assert not (tmp_path/"missing").exists()


@pytest.mark.parametrize("width,height,labels,palette,simplify", [
    ("0","1",b"",bytes([0,0,0,255]),"1"),
    ("40000001","1",b"",bytes([0,0,0,255]),"1"),
    ("4294967295","4294967295",b"",bytes([0,0,0,255]),"1"),
    ("1","1",bytes(3),bytes([0,0,0,255]),"1"),
    ("1","1",bytes([255])*4,bytes([0,0,0,255]),"1"),
    ("1","1",bytes(4),bytes([0,0,0,254]),"1"),
    ("1","1",bytes(4),bytes(3),"1"),
    ("1","1",bytes(4),bytes([0,0,0,255]),"NaN"),
])
def test_native_cli_bounds(tmp_path,width,height,labels,palette,simplify):
    import subprocess
    from pbnstudio.engine import WORKER_PATH
    lf=tmp_path/"labels";lf.write_bytes(labels)
    pf=tmp_path/"palette";pf.write_bytes(palette)
    out=tmp_path/"out"
    r=subprocess.run([str(WORKER_PATH),"--map",str(lf),width,height,str(out),str(pf),simplify],capture_output=True)
    assert r.returncode != 0 and not out.exists()


def test_packaging_collects_bridge_and_worker():
    import importlib.util
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location("package_windows",root/"build/package_windows.py")
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    command=mod.build_command(root,root/"unused")
    assert command[command.index("--collect-submodules")+1]=="pbnstudio"
    assert "assets/worker" in command[command.index("--add-data")+1]


RECRAFT=Path(__file__).resolve().parents[2]/"exports/recraft-japan-proof/00-original.png"
@pytest.mark.skipif(not RECRAFT.exists(),reason="approved Recraft source unavailable")
def test_actual_recraft_edited_map_without_requantization(tmp_path):
    from pbnstudio.engine import run_image,EngineOptions
    assert hashlib.sha256(RECRAFT.read_bytes()).hexdigest()=="fd15b0960284764680bb3b533b6c6c295bce4b239b9c34fda3b3800d93508482"
    baseline=run_image(RECRAFT,tmp_path/"baseline",EngineOptions(16,4,1.0))
    graph=json.loads((baseline/"graph.json").read_text())
    # Every original 4-component becomes its own ownership ID, even equal colors.
    labels=np.fromfile(baseline/"components4.u32le",dtype="<u4").reshape(graph["height"],graph["width"])
    palette=[graph["palette"][c["region"]] for c in graph["components"]]
    original=labels.copy()
    # Merge a genuinely adjacent pair with different original component IDs.
    ys,xs=np.where(labels[:,:-1]!=labels[:,1:])
    a,b=int(labels[ys[0],xs[0]]),int(labels[ys[0],xs[0]+1])
    labels[labels==b]=a
    # Split the largest remaining cell by a horizontal cut; preserve its RGB.
    counts=np.bincount(labels.ravel());target=int(counts.argmax())
    yy,xx=np.where(labels==target);cut=int(np.median(yy))
    new=len(palette);palette.append(palette[target][:])
    mask=(labels==target)&(np.indices(labels.shape)[0]>cut)
    assert mask.any() and ((labels==target)&~mask).any()
    labels[mask]=new
    out=rebuild_from_map(labels,palette,tmp_path/"edited",1.0)
    result,doc=load_graph(out)
    assert np.array_equal(doc.region_map,labels)
    assert result["palette"]==palette
    assert result["merge_diff"]==-1 and result["color_fitters"]==0
    assert result["segment_ms"]==0
    assert np.count_nonzero(labels!=original)>0
    assert not np.any(doc.region_map==b)
    assert any({e["left"],e["right"]}=={target,new} for e in result["edges"])
    unchanged=(original!=b)&~mask
    rgb=np.array(palette,dtype=np.uint8)
    assert np.array_equal(rgb[doc.region_map[unchanged]],rgb[original[unchanged]])
    receipt=json.loads((baseline/"worker.stdout.log").read_text().splitlines()[-1])
    assert receipt["preview_upstream_identical"] == ((baseline/"preview.svg").read_bytes()==(baseline/"upstream.svg").read_bytes())
    assert graph["source_features"]["mode"] == "dark-anchor-local-ink/1"
    receipt={"width":doc.width,"height":doc.height,"changed_pixels":int(np.count_nonzero(labels!=original)),
             "merge_from":b,"merge_to":a,"split_from":target,"split_to":new,
             "components_before":len(graph["components"]),"components_after":len(result["components"]),
             "edges_after":len(result["edges"]),"cubic_edges":sum(e["kind"]=="cubic" for e in result["edges"]),
             "exact_label_sha256":hashlib.sha256(labels.astype("<u4").tobytes()).hexdigest(),
             "actual_label_sha256":hashlib.sha256((out/"labels.u32le").read_bytes()).hexdigest(),
             "colors_unchanged_outside_edit":True,"baseline_preview_upstream_identical":True}
    (tmp_path/"recraft-edit-receipt.json").write_text(json.dumps(receipt,indent=2))



def test_simplify_changes_only_geometry_and_runs_from_frozen_resource_root(tmp_path):
    import shutil,subprocess,sys,os
    from pbnstudio.engine import WORKER_PATH
    labels=np.zeros((40,40),np.uint32)
    yy,xx=np.indices(labels.shape);labels[(xx-20)**2+(yy-20)**2<144]=1
    colors=[[10,20,30],[40,50,60]]
    a=rebuild_from_map(labels,colors,tmp_path/"a",0)
    b=rebuild_from_map(labels,colors,tmp_path/"b",5)
    ga,da=load_graph(a);gb,db=load_graph(b)
    assert ga["components"]==gb["components"] and ga["faces8"]==gb["faces8"]
    assert ga["palette"]==gb["palette"]
    assert (a/"labels.u32le").read_bytes()==(b/"labels.u32le").read_bytes()
    assert (a/"components4.u32le").read_bytes()==(b/"components4.u32le").read_bytes()
    assert [e["geometry"] for e in ga["edges"]]!=[e["geometry"] for e in gb["edges"]]
    # Exercise actual child EXE at the frozen resource path, outside source cwd.
    frozen=tmp_path/"frozen";worker=frozen/"assets/worker/pbn_worker.exe"
    worker.parent.mkdir(parents=True);shutil.copy2(WORKER_PATH,worker)
    code="import sys;sys._MEIPASS=sys.argv[1];sys.path.insert(0,sys.argv[2]);import numpy as np;from pbnstudio.graph_edit import rebuild_from_map;rebuild_from_map(np.zeros((2,2),np.uint32),[[1,2,3]],sys.argv[3])"
    env=os.environ.copy();env.pop("PYTHONHOME",None);env.pop("PYTHONPATH",None)
    result=subprocess.run([sys.executable,"-c",code,str(frozen),str(Path(__file__).resolve().parents[1]),str(tmp_path/"packaged-output")],cwd=tmp_path,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads((tmp_path/"packaged-output/graph.json").read_text())["palette"]==[[1,2,3,255]]
