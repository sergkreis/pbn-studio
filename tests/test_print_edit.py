# SPDX-License-Identifier: GPL-3.0-only
import copy
import numpy as np
import pytest
from test_document_domain import domain, FIXTURES


def small_cells(tmp_path):
    from pbnstudio.graph_edit import rebuild_from_map
    m=domain()
    pixels=np.zeros((100,100),dtype=np.uint32)
    pixels[:,50:]=1
    pixels[48:50,49:51]=2
    out=rebuild_from_map(pixels,[[250,250,250],[20,20,20],[30,30,30]],tmp_path/'native')
    d=m.Document._from_data(m.read_json((out/'graph.json').read_bytes()),(out/'labels.u32le').read_bytes(),(out/'components4.u32le').read_bytes())
    d.validate_smooth_geometry(repair=True);d.prepare_labels()
    return d


def test_simplify_perceptual_adjacent_small_cell_exact_area_atomic_undo(tmp_path):
    d=small_cells(tmp_path)
    before=d._rgb_map().copy();ownership=d.component_map.copy();master=d.master_graph
    settings=d.settings;undo=len(d._undo)
    assert sum(i.code=='label-no-fit' for i in d.issues)==1
    result=d.simplify_for_print()
    assert result['before']['components']==3 and result['after']['components']==2
    assert result['after']['unresolved']==0 and d.topology_certified
    assert result['changed_pixels']==4
    assert result['changed_area_fraction']==4/10000
    assert tuple(d._rgb_map()[48,49])==(20,20,20)
    assert np.array_equal(d._rgb_map()[:40],before[:40])
    assert d.settings==settings and all(l.font_pt>=7 for l in d.labels)
    assert d.master_graph==master and len(d._undo)==undo+1
    assert d.undo() and np.array_equal(d.component_map,ownership)
    assert np.array_equal(d._rgb_map(),before)
    assert d.redo() and len(d.components)==2


def test_split_real_division_keeps_all_pixels_and_color_and_undo():
    d=domain().Document.load(FIXTURES/'hole',trusted=True);d.prepare_labels()
    original=d.component_map.copy();rgb=d._rgb_map().copy();count=len(d.components)
    undo=len(d._undo)
    result=d.split_component(1,[(4.5,2),(4.5,7)])
    assert len(d.components)==count+1 and d.topology_certified
    assert result['changed_pixels']==0 and np.array_equal(d._rgb_map(),rgb)
    inside=original==1
    assert len(np.unique(d.component_map[inside]))==2
    assert len(np.unique(d.component_map[~inside]))==1
    assert sum(c['area'] for c in d.components.values())==d.width*d.height
    assert len(d._undo)==undo+1
    assert d.undo() and np.array_equal(d.component_map,original)
    assert d.redo() and len(d.components)==count+1


def test_split_nondividing_line_and_invalid_coordinates_are_atomic():
    d=domain().Document.load(FIXTURES/'hole',trusted=True)
    before=copy.deepcopy(d.graph)
    for line in [[(3,3),(3,4)],[(float('nan'),0),(4,8)],[(-1,0),(4,8)],[(1,1)]]:
        with pytest.raises(ValueError):d.split_component(1,line)
    assert d.graph==before and not d.can_undo


def attach_source(d):
    from PIL import Image
    from io import BytesIO
    b=BytesIO();Image.fromarray(d._rgb_map()).save(b,format='PNG');d.source_bytes=b.getvalue()


def test_edited_project_restores_master_maps_colors_and_revalidates(tmp_path):
    from pbnstudio.storage import save_project,load_project
    d=small_cells(tmp_path);attach_source(d)
    master=d.master_graph;original=d._rgb_map().copy()
    d.simplify_for_print();current=d._rgb_map().copy();before=d.svg('numbers',diagnostic=False)
    p=tmp_path/'edited.pbn';save_project(d,p);loaded=load_project(p)
    assert loaded.master_graph==master
    assert np.array_equal(loaded._master_region_map,d._master_region_map)
    assert np.array_equal(loaded._master_component_map,d._master_component_map)
    assert loaded._master_colors==d._master_colors
    assert loaded.topology_certified and loaded.smooth_report['valid']
    assert loaded.svg('numbers',diagnostic=False)==before
    assert np.array_equal(loaded._rgb_map(),current)
    assert loaded.edit_report==d.edit_report
    assert not loaded.can_undo and not loaded.can_redo


def test_project_rejects_rehashed_invalid_master_map(tmp_path):
    import zipfile,json,hashlib
    from pbnstudio.storage import save_project,load_project,encode
    d=small_cells(tmp_path);attach_source(d);d.simplify_for_print()
    p=tmp_path/'good.pbn';save_project(d,p)
    with zipfile.ZipFile(p) as z:blobs={n:z.read(n) for n in z.namelist()}
    assert 'master-components4.u32le' in blobs
    blobs['master-components4.u32le']=bytes(len(blobs['master-components4.u32le']))
    manifest=json.loads(blobs['manifest.json'])
    for item in manifest['files']:
        data=blobs[item['path']];item.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    blobs['manifest.json']=encode(manifest)
    bad=tmp_path/'bad.pbn'
    with zipfile.ZipFile(bad,'w') as z:
        for n,data in blobs.items():z.writestr(n,data)
    with pytest.raises(ValueError):load_project(bad)


def test_smooth_certificate_tracks_full_precision_paths_and_fill_rule():
    from PySide6.QtCore import Qt
    d=domain().Document.load(FIXTURES/'hole',trusted=True)
    assert d.validate_smooth_geometry()['valid'] and d.topology_certified
    path=d.component_paths[0]
    path.setFillRule(Qt.WindingFill)
    assert not d.topology_certified
    path.setFillRule(Qt.OddEvenFill)
    assert d.topology_certified
    p=path.elementAt(1);path.setElementPositionAt(1,p.x+1e-10,p.y)
    assert not d.topology_certified


def test_lattice_switch_invalidates_smooth_certificate():
    d=domain().Document.load(FIXTURES/'hole',trusted=True)
    d.validate_smooth_geometry()
    assert d._smooth_certificate
    d.confirm_lattice_fallback(confirmed=True)
    assert d._smooth_certificate is None and d.topology_certified


def test_simplify_budget_large_object_conservation_and_noop(tmp_path):
    d=small_cells(tmp_path);before=d._rgb_map().copy();undo=len(d._undo)
    r=d.simplify_for_print(max_changed_fraction=0)
    assert r['changed_pixels']==0 and r['before']==r['after']
    assert len(d._undo)==undo and np.array_equal(d._rgb_map(),before)
    r=d.simplify_for_print(max_cell_area_mm2=.01)
    assert r['changed_pixels']==0 and np.array_equal(d._rgb_map(),before)
    for kw in [{'max_passes':0},{'max_passes':33},{'max_changed_fraction':float('nan')},{'max_cell_area_mm2':101}]:
        with pytest.raises(ValueError):d.simplify_for_print(**kw)


def test_merge_exact_changed_area_independent_actual_fixture():
    d=domain().Document.load(FIXTURES/'hole',trusted=True);d.prepare_labels()
    before=d._rgb_map().copy();owned=int(np.count_nonzero(d.component_map==1))
    result=d.merge_components(0,1)
    actual=int(np.count_nonzero(np.any(d._rgb_map()!=before,axis=2)))
    assert result['changed_pixels']==actual==owned==25
    assert d.undo() and np.array_equal(d._rgb_map(),before)


def test_smooth_report_discloses_conservative_distance_bounds():
    d=domain().Document.load(FIXTURES/'hole',trusted=True)
    result=d.validate_smooth_geometry()
    assert result['curve_to_lattice_bound_source_units']==3.76
    assert result['lattice_to_curve_bound_source_units']==3.76
    assert result['distance_validation']=='sampled corridor bounds, not exact Hausdorff distance'


def test_default_simplify_real_recraft_letter7pt_is_print_ready_one_undo():
    from pbnstudio.exports import preflight
    d=domain().Document.load(FIXTURES/'recraft',trusted=True)
    assert d.validate_smooth_geometry(repair=True)['valid']
    d.prepare_labels();before=d._rgb_map().copy();settings=d.settings;undo=len(d._undo)
    result=d.simplify_for_print()
    assert not preflight(d)
    assert result['after']['unresolved']==0
    assert result['changed_pixels']==int(np.count_nonzero(np.any(before!=d._rgb_map(),axis=2)))
    assert result['changed_area_fraction']<.02
    assert d.settings==settings and d.settings.font_pt==7
    assert len(d._undo)==undo+1
    assert d.undo() and np.array_equal(d._rgb_map(),before) and not d.leaders_enabled
    assert d.redo() and not preflight(d)
