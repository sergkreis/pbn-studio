# SPDX-License-Identifier: GPL-3.0-only
import threading
import pytest
from test_print_edit import small_cells
from pbnstudio.engine import EngineCancelled
from pbnstudio.document import PrintSettings

def test_prepare_cancel_during_search_restores_document(tmp_path):
    d=small_cells(tmp_path)
    state=(d.labels[:],d.issues[:],d.settings,d.revision,d.dirty,len(d._undo),len(d._redo))
    event=threading.Event();seen=[]
    def progress(stage, completed, total):
        seen.append(stage)
        if stage=='labels':event.set()
    with pytest.raises(EngineCancelled):
        d.prepare_labels(PrintSettings(font_pt=8),cancel_event=event,progress=progress)
    assert seen
    assert (d.labels,d.issues,d.settings,d.revision,d.dirty,len(d._undo),len(d._redo))==state


def test_simplify_cancel_at_rebuild_preserves_original(tmp_path):
    import copy
    import numpy as np
    d=small_cells(tmp_path);graph=copy.deepcopy(d.graph);cells=d.component_map.copy()
    state=(d.labels[:],d.issues[:],d.revision,d.dirty,len(d._undo))
    event=threading.Event();stages=[]
    def progress(stage, completed, total):
        stages.append(stage)
        if stage=='rebuild':event.set()
    with pytest.raises(EngineCancelled):
        d.simplify_for_print(cancel_event=event,progress=progress)
    assert 'rebuild' in stages
    assert d.graph==graph and np.array_equal(cells,d.component_map)
    assert (d.labels,d.issues,d.revision,d.dirty,len(d._undo))==state


def test_cancel_inside_leader_candidate_loop(tmp_path, monkeypatch):
    from pbnstudio.geometry import place_leaders, job_scope
    d=small_cells(tmp_path);event=threading.Event();calls=[]
    original=d.label_fits
    def fit(label):
        calls.append(label)
        event.set()
        return original(label)
    monkeypatch.setattr(d,'label_fits',fit)
    with pytest.raises(EngineCancelled):
        with job_scope(event): place_leaders(d,{})
    assert len(calls)<=2


def test_repeated_glyph_footprints_cached_within_job_only(tmp_path,monkeypatch):
    from pbnstudio import geometry
    from pbnstudio.document import Label
    d=small_cells(tmp_path);calls=[];original=geometry.inflated
    def observed(*args,**kwargs):
        calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(geometry,'inflated',observed)
    label=Label(0,1,20,20,7)
    with geometry.job_scope():
        first=d.label_fits(label);second=d.label_fits(label)
    assert first==second and len(calls)==1
    d.label_fits(label)
    assert len(calls)==2


def test_native_rebuild_cancel_cleans_owned_output(tmp_path):
    import numpy as np
    from pbnstudio.graph_edit import rebuild_from_map
    event=threading.Event();seen=[]
    def progress(stage,done,total):
        seen.append(stage);event.set()
    with pytest.raises(EngineCancelled):
        rebuild_from_map(np.zeros((1500,1500),dtype=np.uint32),[[1,2,3]],tmp_path/'cancelled',cancel_event=event,progress=progress)
    assert 'native' in seen and not (tmp_path/'cancelled').exists()


def test_glyph_templates_reused_for_different_candidates(tmp_path,monkeypatch):
    from pbnstudio import geometry
    from pbnstudio.document import Label
    d=small_cells(tmp_path);calls=[];original=geometry.glyph_path
    def observed(*args):calls.append(1);return original(*args)
    monkeypatch.setattr(geometry,'glyph_path',observed)
    with geometry.job_scope():
        a=geometry.cached_ink(d,Label(0,1,20,20,7),.1)
        b=geometry.cached_ink(d,Label(0,1,25,25,7),.1)
    assert len(calls)==1
    from PySide6.QtGui import QTransform
    assert b==QTransform.fromTranslate(5,5).map(a)


def test_existing_valid_labels_skip_distance_search(tmp_path,monkeypatch):
    from pbnstudio.document import ndimage
    d=small_cells(tmp_path);d.simplify_for_print();calls=[]
    original=ndimage.distance_transform_edt
    def observed(*a,**k):calls.append(1);return original(*a,**k)
    monkeypatch.setattr(ndimage,'distance_transform_edt',observed)
    d.prepare_labels()
    assert not calls



def test_label_loop_cancels_after_partial_progress_without_partial_results(tmp_path):
    d=small_cells(tmp_path);before=d.labels[:];event=threading.Event();seen=[]
    def progress(stage,done,total):
        if stage=='labels':
            seen.append(done)
            if done==1:event.set()
    with pytest.raises(EngineCancelled):d.prepare_labels(cancel_event=event,progress=progress)
    assert 1 in seen and d.labels==before


def test_fingerprint_observes_cancel_before_map_serialization(tmp_path):
    from pbnstudio.geometry import smooth_fingerprint,job_scope
    d=small_cells(tmp_path);event=threading.Event()
    with job_scope(event):
        event.set()
        with pytest.raises(EngineCancelled):smooth_fingerprint(d)
        event.clear()
