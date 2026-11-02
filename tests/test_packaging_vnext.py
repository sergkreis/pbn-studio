# SPDX-License-Identifier: GPL-3.0-only
import importlib.util, zipfile
from pathlib import Path
import pytest

def module():
    spec=importlib.util.spec_from_file_location("packaging_vnext",Path(__file__).parents[1]/"build/package_windows.py")
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_release_accepts_verified_native_no_forced_lattice(tmp_path):
    z=tmp_path/"bundle.zip"
    with zipfile.ZipFile(z,"w") as f:f.writestr("a.txt","ok")
    smoke=dict(status="passed",frozen=True,diagnostic=False,issues=0,export_count=9,
               geometry_mode="native",ui_fallback_confirmations=0,reopened_geometry_mode="native",
               rerun_geometry_mode_before_fallback="native",smooth_validation_verified=True,
               normal_vector_pdf_verified=True,png_readback_verified=True,ui_offscreen=True)
    assert module().verify_release(smoke,z)["zip_crc"]=="passed"
    smoke["smooth_validation_verified"]=False
    with pytest.raises(ValueError):module().verify_release(smoke,z)
