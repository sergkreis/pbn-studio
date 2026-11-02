# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import pytest
from PIL import Image
from pbnstudio import engine

def test_owned_cleanup_retries_transient_lock_after_original_failure(tmp_path,monkeypatch):
    source=tmp_path/"source.png";Image.new("RGB",(8,8),"red").save(source)
    output=tmp_path/"job";original=engine.shutil.rmtree;calls=[]
    def transient(path,*args,**kwargs):
        calls.append(str(path))
        if len(calls)==1:raise PermissionError("controlled sharing violation")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(engine.shutil,"rmtree",transient)
    def fail(payload):raise engine.EngineError("original failure")
    with pytest.raises(engine.EngineError,match="original failure"):
        engine.run_image(source,output,progress=fail)
    assert len(calls)==2 and not output.exists()

def test_owned_cleanup_failure_preserves_original_and_reports_residue(tmp_path,monkeypatch):
    source=tmp_path/"source.png";Image.new("RGB",(8,8),"red").save(source)
    output=tmp_path/"job"
    def locked(*a,**kw):raise PermissionError("persistent lock")
    monkeypatch.setattr(engine.shutil,"rmtree",locked)
    def fail(payload):raise engine.EngineError("original failure")
    with pytest.raises(engine.EngineError,match="original failure") as caught:
        engine.run_image(source,output,progress=fail)
    assert any("cleanup" in x.lower() for x in getattr(caught.value,"__notes__",[]))
