# SPDX-License-Identifier: GPL-3.0-only
"""RC2 source-feature metadata and conservative print-edit regressions."""
import copy
import numpy as np
from test_print_edit import small_cells, attach_source


def test_simplify_does_not_erase_protected_no_fit(tmp_path):
    d=small_cells(tmp_path)
    cid=d.sample_component(49,48)
    d.graph['source_features']={'mode':'dark-anchor-local-ink/1',
        'protected_regions':[d.components[cid]['region']], 'warnings':[]}
    before=d._rgb_map().copy()
    result=d.simplify_for_print()
    assert np.array_equal(d._rgb_map(),before)
    assert any(i.code=='label-no-fit' and i.component==cid for i in d.issues)
    assert result['details']['protected_components_skipped']==[cid]


def test_rebuild_remaps_protected_region_owners_and_preserves_history(tmp_path):
    from pbnstudio.storage import save_project, load_project
    from pbnstudio.graph_edit import rebuild_from_map
    from pbnstudio.document import Document, read_json
    pixels=np.zeros((100,100),dtype=np.uint32);pixels[:,50:]=1
    pixels[48:50,49:52]=7
    out=rebuild_from_map(pixels,[[250,250,250],[20,20,20]]+[[30,30,30]]*6,tmp_path/'native')
    graph=read_json((out/'graph.json').read_bytes())
    graph['source_features']={'mode':'dark-anchor-local-ink/1','protected_regions':[7],
                             'restored_pixels':4,'warnings':['bounded source detector']}
    d=Document._from_data(graph,(out/'labels.u32le').read_bytes(),(out/'components4.u32le').read_bytes())
    attach_source(d)
    d.validate_smooth_geometry(repair=True);d.prepare_labels()
    cid=d.sample_component(49,48)
    original_region=d.components[cid]['region']
    # Native region IDs need not equal raster-order component IDs.
    assert original_region!=cid
    features={'mode':'dark-anchor-local-ink/1','protected_regions':[original_region],
              'restored_pixels':4,'warnings':['bounded source detector']}
    d.graph['source_features']=copy.deepcopy(features)
    before=d._rgb_map().copy()
    d.split_component(cid,[(50,48),(50,50)])
    current=d.graph.get('source_features')
    assert current is not None
    expected=sorted(set(int(v) for v in d.region_map[48:50,49:52].ravel()))
    assert current['protected_regions']==expected
    assert current['native_protected_regions']==[original_region]
    assert current['restored_pixels']==4 and current['warnings']==features['warnings']
    assert np.array_equal(d._rgb_map(),before)
    for e in d.graph['edges']:
        if e['left'] in expected or e['right'] in expected:
            assert e['kind']=='polyline' and e['geometry']==e['lattice']
    assert d.undo() and d.graph['source_features']==features
    assert d.redo() and d.graph['source_features']==current
    path=tmp_path/'features.pbn';save_project(d,path);loaded=load_project(path)
    assert loaded.graph['source_features']==current
    assert loaded.master_graph['source_features']==features
    loaded.simplify_for_print()
    assert np.array_equal(loaded._rgb_map(),before)


def test_simplify_does_not_grow_protected_target(tmp_path):
    d=small_cells(tmp_path)
    protected=d.sample_component(80,48)
    d.graph['source_features']={'protected_regions':[d.components[protected]['region']]}
    mask=d.component_map==protected
    before=d._rgb_map().copy()
    d.simplify_for_print()
    assert np.array_equal(d._rgb_map()[mask],before[mask])
    assert tuple(d._rgb_map()[48,49])==(250,250,250)


def test_source_metadata_survives_recolor_and_explicit_merge(tmp_path):
    d=small_cells(tmp_path)
    protected=d.sample_component(49,48)
    features={'protected_regions':[d.components[protected]['region']],'mode':'test'}
    d.graph['source_features']=copy.deepcopy(features)
    d.recolor(protected,(9,8,7))
    assert d.graph['source_features']==features
    d.undo();d.redo()
    target=d.sample_component(80,48)
    d.merge_components(target,protected)
    owner=int(d.region_map[48,49])
    assert owner in d.graph['source_features']['protected_regions']
    assert d.graph['source_features']['native_protected_regions']==features['protected_regions']
