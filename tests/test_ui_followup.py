# SPDX-License-Identifier: GPL-3.0-only
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from test_ui import app, QuietDialogs, real_document, drain_until
from pbnstudio.ui import MainWindow

def test_settings_are_one_domain_edit_and_identical_apply_is_clean(app):
    dialogs=QuietDialogs(); dialogs.choice="discard"
    window=MainWindow(dialogs=dialogs); doc=real_document(); window.set_document(doc)
    before=doc.revision
    window.font_spin.setValue(9)
    assert window.apply_print_settings()
    drain_until(app,lambda: not window.is_processing)
    doc=window.document
    assert doc.revision==before+1
    assert doc.dirty
    assert window.undo_edit()
    assert window.document.settings.font_pt==7
    doc.dirty=False; before=doc.revision
    window.font_spin.setValue(7)
    assert window.apply_print_settings()
    drain_until(app,lambda: not window.is_processing)
    assert window.document.revision==before
    assert not window.document.dirty
    window.close()

def test_reopen_can_reprocess_embedded_master_and_restores_engine_options(app,tmp_path):
    from PIL import Image
    source=tmp_path/"original.png"; Image.new("RGB",(24,16),"red").save(source)
    dialogs=QuietDialogs(); dialogs.choice="discard"
    w=MainWindow(dialogs=dialogs); w.colors_spin.setValue(5); w.speckle_spin.setValue(2); w.simplify_spin.setValue(0)
    assert w.load_path(source)
    drain_until(app,lambda:not w.is_processing)
    project=tmp_path/"saved.pbn"; assert w.save_to(project), dialogs.messages
    source.unlink(); w.close()
    w=MainWindow(dialogs=dialogs)
    assert w.load_path(project), dialogs.messages
    assert not w.document.dirty
    assert w.run_button.isEnabled()
    assert (w.engine_options().colors,w.engine_options().speckle,w.engine_options().simplify)==(5,2,0)
    assert w.start_processing()
    temporary=w._worker.source
    assert temporary.is_file()
    drain_until(app,lambda:not w.is_processing)
    assert w.document.dirty, dialogs.messages
    assert w.document.source_bytes
    assert not temporary.exists()
    assert w.start_processing()
    drain_until(app,lambda:not w.is_processing)
    assert w.document.dirty
    w.close()

def test_settings_button_runs_detached_cancellable_job(app,monkeypatch):
    import threading
    from pbnstudio.document import Document
    entered=threading.Event(); release=threading.Event(); threads=[]
    original=Document.prepare_labels
    def slow(self,*args,**kwargs):
        threads.append(threading.get_ident()); entered.set(); release.wait(2)
        return original(self,*args,**kwargs)
    doc=real_document(); dialogs=QuietDialogs(); dialogs.choice="discard"
    w=MainWindow(dialogs=dialogs); w.set_document(doc); before=doc.revision
    monkeypatch.setattr(Document,"prepare_labels",slow)
    w.font_spin.setValue(9)
    w.print_button.click()
    try:
        assert w.is_processing, "button must return before computation completes"
        drain_until(app,entered.is_set)
        assert threads[0]!=threading.get_ident()
        assert doc.revision==before and doc.settings.font_pt==7
        w.cancel_processing(); release.set()
        drain_until(app,lambda:not w.is_processing)
        assert w.document is doc and not doc.dirty
    finally:
        release.set()
        drain_until(app,lambda:not w.is_processing)
        w.close()

def test_native_load_and_labels_do_not_execute_in_ui_thread(app,tmp_path,monkeypatch):
    import threading
    from PIL import Image
    from pbnstudio.document import Document
    source=tmp_path/"native.png"; Image.new("RGB",(24,16),"blue").save(source)
    threads=[]; original=Document.prepare_labels
    def observed(self,*args,**kwargs):
        threads.append(threading.get_ident())
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Document,"prepare_labels",observed)
    d=QuietDialogs();d.choice="discard";w=MainWindow(dialogs=d)
    assert w.load_path(source)
    drain_until(app,lambda:not w.is_processing)
    assert w.document is not None,d.messages
    assert threads and all(t!=threading.get_ident() for t in threads)
    w.close()

def test_export_dialog_is_background_and_cancel_never_publishes(app,tmp_path,monkeypatch):
    import threading
    import pbnstudio.exports as exports
    entered=threading.Event(); release=threading.Event(); ids=[]
    original=exports.export_bundle
    def slow(*args,**kwargs):
        ids.append(threading.get_ident());entered.set();release.wait(2)
        return original(*args,**kwargs)
    d=QuietDialogs(); d.choice="discard"; target=tmp_path/"bundle"
    d.export_dir=lambda _:str(target)
    w=MainWindow(dialogs=d);w.set_document(real_document())
    monkeypatch.setattr(exports,"export_bundle",slow)
    w.export_button.click()
    try:
        assert w.is_processing
        drain_until(app,entered.is_set)
        assert ids[0]!=threading.get_ident()
        assert not target.exists()
        w.cancel_processing();release.set()
        drain_until(app,lambda:not w.is_processing)
        assert not target.exists()
        assert not d.messages,d.messages
    finally:
        release.set();drain_until(app,lambda:not w.is_processing);w.close()

def test_cli_smoke_exercises_real_native_storage_export_without_show(tmp_path):
    import subprocess,sys,json
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    output=tmp_path/"smoke"
    result=subprocess.run([sys.executable,"-m","pbnstudio","--smoke-test-dir",str(output)],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
    receipt=json.loads((output/"receipt.json").read_text())
    assert receipt["status"]=="passed" and receipt["worker_sha256"]
    assert receipt["export_count"]==9
    assert receipt["diagnostic"] is False and receipt["issues"] == 0
    assert receipt["geometry_mode"] == "native"
    assert receipt["ui_fallback_confirmations"] == 0
    assert receipt["reopened_geometry_mode"] == "native"
    assert receipt["rerun_geometry_mode_before_fallback"] == "native"
    assert receipt["smooth_validation_verified"] is True
    assert receipt["normal_vector_pdf_verified"] is True
    assert receipt["png_readback_verified"] is True
    assert (output/"project.pbn").is_file()
    assert (output/"bundle"/(("DIAGNOSTIC-" if receipt["diagnostic"] else "")+"numbered.pdf")).is_file()

def test_windows_builder_includes_worker_runtime_and_offscreen_verification(tmp_path):
    import importlib.util
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    path=root/"build/package_windows.py"
    assert path.is_file(), "portable builder missing"
    spec=importlib.util.spec_from_file_location("package_windows",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    command=module.build_command(root,tmp_path)
    assert "--windowed" in command and "--onedir" in command
    assert "--collect-all" in command
    assert any("assets" in arg and "worker" in arg for arg in command)
    assert str(root/"launch.py") in command
    assert callable(module.build)

def test_leader_toggle_uses_domain_background_edit_and_persists(app,tmp_path):
    from PIL import Image
    from io import BytesIO
    doc=real_document(); data=BytesIO();Image.new("RGB",(doc.width,doc.height),"white").save(data,format="PNG");doc.source_bytes=data.getvalue()
    d=QuietDialogs();d.choice="discard";w=MainWindow(dialogs=d);w.set_document(doc)
    assert hasattr(w,"leaders_check"), "leader mode needs a visible control"
    assert not w.leaders_check.isChecked()
    w.leaders_check.click()
    drain_until(app,lambda:not w.is_processing)
    assert w.document.leaders_enabled and w.document.dirty
    assert w.save_to(tmp_path/"leaders.pbn"),d.messages
    assert w.load_path(tmp_path/"leaders.pbn"),d.messages
    assert w.leaders_check.isChecked() and w.document.leaders_enabled
    w.leaders_check.click();drain_until(app,lambda:not w.is_processing)
    assert not w.document.leaders_enabled
    assert w.undo_edit()
    assert w.document.leaders_enabled and w.leaders_check.isChecked()
    w.close()
