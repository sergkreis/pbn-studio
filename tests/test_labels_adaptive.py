# SPDX-License-Identifier: GPL-3.0-only
import pytest
from pbnstudio.document import Document, PrintSettings
from test_document_domain import FIXTURES


def test_readable_minimum_is_explicit_validated_and_positional_compatible():
    settings = PrintSettings()
    assert settings.min_font_pt == 6.0
    assert settings.font_pt == 7.0
    assert PrintSettings(215.9, 279.4, 12.7, 8, .5).line_pt == .5
    for minimum in (0, 8, float('nan'), True):
        with pytest.raises(ValueError):
            PrintSettings(font_pt=7, min_font_pt=minimum)


def test_real_tiny_hole_adapts_inline_before_leaders_preserving_maps():
    import numpy as np
    d=Document.load(FIXTURES/"hole",trusted=True)
    maps=(d.region_map.copy(),d.component_map.copy(),dict(d.component_colors))
    d.leaders_enabled=True
    d.prepare_labels(PrintSettings(width_mm=10,height_mm=10,margin_mm=0,font_pt=12,min_font_pt=6))
    assert len(d.labels)==2 and not d.issues
    assert [(l.component,l.font_pt) for l in d.labels]==[(0,11),(1,12)]
    assert all(not l.leader and d.label_fits(l) for l in d.labels)
    assert np.array_equal(maps[0],d.region_map) and np.array_equal(maps[1],d.component_map)
    assert maps[2]==d.component_colors


def tiny_document():
    d=Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(PrintSettings(width_mm=10,height_mm=10,margin_mm=0,font_pt=12,min_font_pt=6))
    return d


def test_actual_size_preflight_move_and_reprepare_preserve_size():
    from pbnstudio.exports import preflight
    d=tiny_document()
    assert not any(i.code=="invalid-label" for i in preflight(d))
    label=d.labels[0]
    d.move_label(label.component,label.x,label.y)
    assert d.labels[0]==label
    d.prepare_labels()
    assert d.labels[0]==label


@pytest.mark.parametrize("size",[0,5.9,12.1,float("nan"),float("inf"),True])
def test_invalid_actual_sizes_fail_closed(size):
    from dataclasses import replace
    from pbnstudio.exports import preflight
    d=tiny_document();d.labels[0]=replace(d.labels[0],font_pt=size)
    assert not d.label_fits(d.labels[0])
    assert any(i.code=="invalid-label" for i in preflight(d))


def source_document():
    from PIL import Image
    from io import BytesIO
    d=tiny_document();buf=BytesIO()
    Image.new("RGB",(d.width,d.height)).save(buf,format="PNG")
    d.source_bytes=buf.getvalue()
    return d


def rewrite_archive(source,target,change):
    import zipfile,json,hashlib
    from pbnstudio.storage import encode
    with zipfile.ZipFile(source) as z:blobs={n:z.read(n) for n in z.namelist()}
    state=json.loads(blobs["edits.json"]);manifest=json.loads(blobs["manifest.json"])
    change(state,manifest)
    blobs["edits.json"]=encode(state)
    for entry in manifest["files"]:
        data=blobs[entry["path"]];entry.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    blobs["manifest.json"]=encode(manifest)
    with zipfile.ZipFile(target,"w") as z:
        for name,data in blobs.items():z.writestr(name,data)


def test_schema4_actual_sizes_roundtrip(tmp_path):
    import zipfile,json
    from pbnstudio.storage import save_project,load_project
    d=source_document();path=tmp_path/"actual.pbn"
    save_project(d,path);restored=load_project(path)
    assert restored.labels==d.labels and restored.settings==d.settings
    assert restored.svg("numbers")==d.svg("numbers")
    with zipfile.ZipFile(path) as z:
        assert json.loads(z.read("manifest.json"))["schema"]=="pbn-studio.project/4"


@pytest.mark.parametrize("version",[1,2,3])
def test_old_projects_missing_minimum_preserve_fixed_appearance(tmp_path,version):
    from pbnstudio.storage import save_project,load_project
    d=source_document();d.prepare_labels(PrintSettings(font_pt=7,min_font_pt=7))
    path=tmp_path/"new.pbn";save_project(d,path)
    def legacy(state,manifest):
        manifest["schema"]=f"pbn-studio.project/{version}"
        del state["settings"]["min_font_pt"]
    old=tmp_path/"old.pbn";rewrite_archive(path,old,legacy)
    restored=load_project(old)
    assert restored.settings.min_font_pt==restored.settings.font_pt==7
    assert restored.labels==d.labels


@pytest.mark.parametrize("size",[0,5.9,12.1,True,"7"])
def test_project_rejects_invalid_actual_font(tmp_path,size):
    from pbnstudio.storage import save_project,load_project
    d=source_document();path=tmp_path/"good.pbn";save_project(d,path)
    bad=tmp_path/"bad.pbn"
    rewrite_archive(path,bad,lambda state,manifest:state["labels"][0].update(font_pt=size))
    with pytest.raises(ValueError):load_project(bad)


def test_leaders_adapt_after_all_inline_sizes_fail():
    d=Document.load(FIXTURES/"hole",trusted=True)
    d.leaders_enabled=True
    d.prepare_labels(PrintSettings(width_mm=30,height_mm=30,margin_mm=0,font_pt=44,min_font_pt=38))
    assert not d.issues and len(d.labels)==2
    leader=next(l for l in d.labels if l.leader)
    assert 38<=leader.font_pt<44 and d.label_fits(leader)
    d.prepare_labels()
    assert next(l for l in d.labels if l.leader)==leader


def test_progress_completes_labels_then_finalizes_and_reports_actual_sizes():
    from collections import Counter
    d=Document.load(FIXTURES/"hole",trusted=True);events=[]
    d.prepare_labels(PrintSettings(width_mm=10,height_mm=10,margin_mm=0,font_pt=12,min_font_pt=6),progress=lambda *event:events.append(event))
    assert events[-2:]==[("labels",2,2),("finalizing",0,1)]
    metrics=d.quality_metrics()
    assert metrics["label_size_histogram"]=={f"{size:g}":count for size,count in Counter(l.font_pt for l in d.labels).items()}
    assert metrics["min_font_pt"]==6
    assert metrics["issue_reasons"]==[]


def test_no_fit_reports_minimum_and_bounded_search_reason():
    d=Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(PrintSettings(width_mm=10,height_mm=10,margin_mm=0,font_pt=100,min_font_pt=99.2))
    assert not d.labels
    for issue in d.issues:
        assert issue.code=="label-no-fit"
        assert issue.reason=="inline-no-fit-after-minimum"
        assert issue.metadata["min_font_pt"]==99.2
        assert issue.metadata["attempted_sizes_pt"]==[100,99.5,99.2]
        assert issue.metadata["candidate_limit"]==129
        assert "99.2" in issue.message
    assert len(d.quality_metrics()["issue_reasons"])==2


def test_exact_minimum_and_cancel_at_finalizing_roll_back():
    from pbnstudio.geometry import font_sizes
    from pbnstudio.engine import EngineCancelled
    import threading
    assert font_sizes(PrintSettings(font_pt=7.2,min_font_pt=6.1))==(7.2,6.7,6.2,6.1)
    assert len(font_sizes(PrintSettings(font_pt=200,min_font_pt=1)))==399
    d=tiny_document();before=(d.settings,list(d.labels),list(d.issues),d.revision)
    cancel=threading.Event()
    def progress(stage,completed,total):
        if stage=="finalizing":cancel.set()
    with pytest.raises(EngineCancelled):
        d.prepare_labels(PrintSettings(font_pt=9,min_font_pt=6.1),cancel_event=cancel,progress=progress)
    assert (d.settings,d.labels,d.issues,d.revision)==before


def test_exact_non_half_step_minimum_is_tried_on_real_ink():
    d=Document.load(FIXTURES/"hole",trusted=True)
    d.prepare_labels(PrintSettings(width_mm=10,height_mm=10,margin_mm=0,font_pt=12,min_font_pt=11.4))
    assert next(l for l in d.labels if l.component==0).font_pt==11.4


def test_cancellation_during_adaptive_trials_rolls_back():
    from pbnstudio.engine import EngineCancelled
    class CancelDuringSearch:
        def __init__(self):self.calls=0
        def is_set(self):
            self.calls+=1
            return self.calls>100
    d=tiny_document();before=(d.settings,list(d.labels),list(d.issues),d.revision)
    with pytest.raises(EngineCancelled):
        d.prepare_labels(PrintSettings(width_mm=10,height_mm=10,margin_mm=0,font_pt=200,min_font_pt=100),cancel_event=CancelDuringSearch())
    assert (d.settings,d.labels,d.issues,d.revision)==before


def test_schema3_master_migration_preserves_master(tmp_path):
    from pbnstudio.storage import save_project,load_project
    d=source_document();d.prepare_labels(PrintSettings(font_pt=7,min_font_pt=7));d._preserve_master()
    path=tmp_path/"master.pbn";save_project(d,path)
    def legacy(state,manifest):
        manifest["schema"]="pbn-studio.project/3";del state["settings"]["min_font_pt"]
    old=tmp_path/"legacy-master.pbn";rewrite_archive(path,old,legacy)
    restored=load_project(old)
    assert restored.master_graph==d.master_graph and restored.settings.min_font_pt==7


def test_schema4_requires_explicit_minimum(tmp_path):
    from pbnstudio.storage import save_project,load_project
    d=source_document();path=tmp_path/"new.pbn";save_project(d,path)
    bad=tmp_path/"missing.pbn"
    rewrite_archive(path,bad,lambda state,manifest:state["settings"].pop("min_font_pt"))
    with pytest.raises(ValueError,match="minimum"):load_project(bad)


def test_each_issue_in_summary_has_machine_readable_reason():
    d=Document.load(FIXTURES/"recraft",trusted=True);d.prepare_labels()
    assert d.issues
    assert all(item["reason"] for item in d.quality_metrics()["issue_reasons"])


def test_leader_actual_sizes_roundtrip(tmp_path):
    from pbnstudio.storage import save_project,load_project
    d=source_document();d.labels=[];d.leaders_enabled=True
    d.prepare_labels(PrintSettings(width_mm=30,height_mm=30,margin_mm=0,font_pt=44,min_font_pt=38))
    assert any(l.leader and l.font_pt<44 for l in d.labels)
    path=tmp_path/"leader.pbn";save_project(d,path)
    assert load_project(path).labels==d.labels


@pytest.mark.parametrize("minimum",[0,13,True,"6"])
def test_project_rejects_invalid_minimum(tmp_path,minimum):
    from pbnstudio.storage import save_project,load_project
    d=source_document();path=tmp_path/"new.pbn";save_project(d,path)
    bad=tmp_path/"bad-minimum.pbn"
    rewrite_archive(path,bad,lambda state,manifest:state["settings"].update(min_font_pt=minimum))
    with pytest.raises(ValueError):load_project(bad)


def test_no_fit_leader_metadata_and_progress_are_explicit():
    d=Document.load(FIXTURES/"hole",trusted=True);d.leaders_enabled=True;events=[]
    d.prepare_labels(PrintSettings(width_mm=10,height_mm=10,margin_mm=0,font_pt=100,min_font_pt=99.2),progress=lambda *e:events.append(e))
    assert events[-2:]==[("leaders",2,2),("finalizing",0,1)]
    assert ("labels",2,2) in events
    assert all(i.reason=="inline-and-leader-no-fit-after-minimum" for i in d.issues)
    assert all(i.metadata["leaders_attempted"] and i.metadata["leader_candidates_per_size_limit"]==1008 for i in d.issues)
