# SPDX-License-Identifier: GPL-3.0-only
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from test_ui import app, QuietDialogs, real_document, drain_until
from pbnstudio.ui import MainWindow


def test_explicit_lattice_action_confirms_before_background_edit_and_undo(app, monkeypatch):
    import threading
    from pbnstudio.document import Document
    dialogs = QuietDialogs(); dialogs.choice = "discard"
    warnings = []; accepted = [False]
    def confirm(parent, title, before, after, metrics):
        warnings.append(metrics)
        return accepted[0]
    dialogs.confirm_edit = confirm
    w = MainWindow(dialogs=dialogs); doc = real_document(); w.set_document(doc)
    assert hasattr(w, "lattice_button"), "pixel-exact geometry needs an explicit visible action"
    assert w.lattice_button.text() == "Точные пиксельные границы"
    assert not w.lattice_button.isHidden() and w.lattice_button.isEnabled()
    old_svg = doc.svg("numbers"); old_revision = doc.revision
    assert doc.geometry_mode == "native"
    w.lattice_button.click()
    drain_until(app, lambda: not w.is_processing)
    assert warnings and "ступен" in warnings[0].lower()
    assert w.document is doc and doc.revision == old_revision and not w.is_processing
    original = Document.confirm_lattice_fallback
    threads = []; entered = threading.Event(); release = threading.Event()
    def observed(self, *, confirmed=False):
        threads.append((threading.get_ident(), confirmed)); entered.set(); release.wait(3)
        return original(self, confirmed=confirmed)
    monkeypatch.setattr(Document, "confirm_lattice_fallback", observed)
    accepted[0] = True
    w.lattice_button.click()
    try:
        assert w.is_processing
        drain_until(app, entered.is_set)
        assert threads == [(threads[0][0], True)] and threads[0][0] != threading.get_ident()
        assert w.document is doc and doc.geometry_mode == "native"
        release.set(); drain_until(app, lambda: not w.is_processing)
        assert w.document.geometry_mode == "lattice" and w.document.dirty
        assert len(w.document.labels) == len(w.document.components)
        assert w.geometry_label.text() == "Границы: точные пиксельные"
        assert not w.geometry_label.isHidden()
        assert not w.lattice_button.isEnabled()
        assert not dialogs.messages, dialogs.messages
        assert w.undo_edit()
        assert w.document.geometry_mode == "native"
        assert w.document.svg("numbers") == old_svg
        assert w.lattice_button.isEnabled()
    finally:
        release.set(); drain_until(app, lambda: not w.is_processing); w.close()


def test_page_presets_are_explicit_and_support_is_honest(app):
    from pbnstudio.document import Issue
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); doc = real_document(); w.set_document(doc)
    assert hasattr(w, "page_preset"), "Letter/A4/A3/A0 need convenient presets"
    expected = {"Letter": (215.9,279.4), "A4": (210,297), "A3": (297,420), "A0": (841,1189)}
    for name, dimensions in expected.items():
        index = w.page_preset.findText(name)
        assert index >= 0
        w.page_preset.setCurrentIndex(index)
        assert (w.page_width_spin.value(),w.page_height_spin.value()) == dimensions
        assert doc.settings.width_mm == 215.9, "preset must not silently edit the document"
    assert "плакат" in w.support_label.text().lower() and "KDP" in w.support_label.text()
    assert "точка" in w.leaders_check.toolTip().lower()
    assert w.start_print_settings()
    drain_until(app,lambda:not w.is_processing)
    assert (w.document.settings.width_mm,w.document.settings.height_mm) == expected["A0"]
    w.page_width_spin.setValue(800)
    assert w.page_preset.currentText() == "Свой"
    # Reopening a model reflects actual settings, not a stale dropdown selection.
    w.set_document(w.document)
    assert w.page_preset.currentText() == "A0"
    w.document.issues = [Issue("label-no-fit",1,"does not fit")]
    w.render_document()
    assert "1" in w.status_label.text() and "заблокирован" in w.status_label.text().lower()
    assert "руч" in w.support_label.text().lower()
    w.close()


def test_portable_requires_normal_frozen_receipt_and_crc(tmp_path):
    import importlib.util, json, zipfile, hashlib
    from pathlib import Path
    path = Path(__file__).resolve().parents[1]/"build/package_windows.py"
    spec = importlib.util.spec_from_file_location("package_final",path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert hasattr(module,"verify_release"), "candidate must fail closed on diagnostic smoke or invalid ZIP"
    archive = tmp_path/"portable.zip"
    with zipfile.ZipFile(archive,"w") as bundle: bundle.writestr("README.txt","test")
    receipt = {"status":"passed","frozen":True,"diagnostic":False,"issues":0,"export_count":9,
               "geometry_mode":"native","ui_fallback_confirmations":0,"reopened_geometry_mode":"native",
               "smooth_validation_verified":True,
               "rerun_geometry_mode_before_fallback":"native","normal_vector_pdf_verified":True,
               "png_readback_verified":True,"ui_offscreen":True}
    verified = module.verify_release(receipt,archive)
    assert verified["zip_crc"] == "passed"
    assert verified["zip_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    import pytest
    for field,value in [("diagnostic",True),("issues",1),("frozen",False),("normal_vector_pdf_verified",False),("ui_offscreen",False)]:
        with pytest.raises(ValueError): module.verify_release({**receipt,field:value},archive)
    archive.write_bytes(b"not a zip")
    with pytest.raises((ValueError,zipfile.BadZipFile)): module.verify_release(receipt,archive)
    assert "Точные пиксельные границы" in module.README_RU
    assert "51" in module.README_RU and "A0" in module.README_RU and "KDP" in module.README_RU
    assert "чистой Windows" in module.README_RU and "Arial" in module.README_RU
    assert "--collect-submodules" in module.build_command(path.parents[1],tmp_path)
