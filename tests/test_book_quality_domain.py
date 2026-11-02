# SPDX-License-Identifier: GPL-3.0-only
import copy, json
import numpy as np
import pytest
from test_document_domain import domain, FIXTURES

def test_smooth_shared_curves_are_actually_validated_and_mutation_revokes():
    d=domain().Document.load(FIXTURES/"hole",trusted=True)
    e=d.graph["edges"][1]
    # Closed inner square with genuine cubic sides (exact straight cubics).
    points=[[3,3],[3,6],[6,6],[6,3],[3,3]]
    # Keep the native orientation and starting point.
    points=e["lattice"]
    e["kind"]="cubic";e["geometry"]=[[a,[a[0]+(b[0]-a[0])/3,a[1]+(b[1]-a[1])/3],[a[0]+2*(b[0]-a[0])/3,a[1]+2*(b[1]-a[1])/3],b] for a,b in zip(points,points[1:])]
    assert hasattr(d,"validate_smooth_geometry")
    result=d.validate_smooth_geometry(repair=False)
    assert result["valid"] and result["fallback_edges"]==[]
    assert d.topology_certified and d.prepare_labels()==[]
    assert "C" in d.svg("contours",diagnostic=False)
    e["geometry"][0][1]=[100,100]
    assert not d.topology_certified


def test_bad_edge_repaired_locally_preserves_native_master_and_undo():
    d=domain().Document.load(FIXTURES/"hole",trusted=True)
    d.graph["edges"][1]["geometry"][1]=[100,100]
    before=copy.deepcopy(d.graph)
    result=d.validate_smooth_geometry(repair=True)
    assert result["valid"] and result["fallback_edges"]==[1]
    assert d.master_graph==before
    assert d.graph["edges"][0]==before["edges"][0]
    assert d.graph["edges"][1]["geometry"]==d.graph["edges"][1]["lattice"]
    assert d.topology_certified
    assert d.undo() and d.graph==before and not d.topology_certified
    assert d.redo() and d.topology_certified


def test_merge_uses_actual_graph_and_mask_and_is_undoable():
    d=domain().Document.load(FIXTURES/"hole",trusted=True);d.prepare_labels()
    original=d.component_map.copy();master=copy.deepcopy(d.graph)
    assert hasattr(d,"merge_components")
    r=d.merge_components(0,1)
    assert r["before"]["components"]==2 and r["after"]["components"]==1
    assert r["changed_pixels"] == int(np.count_nonzero(original == 1))
    assert int(np.count_nonzero(original == 1)) == 25  # 5 x 5 hole fixture
    assert len(d.components)==1 and np.unique(d.component_map).tolist()==[0]
    assert len(d.graph["edges"])==1 and d.master_graph==master
    assert d.undo() and np.array_equal(d.component_map,original)
    assert d.redo() and len(d.components)==1


def test_merge_rejects_nonadjacent_without_mutation():
    d=domain().Document.load(FIXTURES/"checkerboard",trusted=True)
    before=copy.deepcopy(d.graph)
    assert hasattr(d,"merge_components")
    with pytest.raises(ValueError,match="adjacent"):
        d.merge_components(0,3)
    assert d.graph==before and not d.can_undo
