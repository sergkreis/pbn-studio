# SPDX-License-Identifier: GPL-3.0-only
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from test_ui import app, QuietDialogs, real_document, drain_until
from pbnstudio.ui import MainWindow
from PySide6.QtWidgets import QPushButton


def test_manual_block_removed_and_zoom_in_view_controls(app):
    w = MainWindow(dialogs=QuietDialogs()); w.show(); app.processEvents()
    try:
        for name in ("tool_combo", "selection_label", "recolor_button", "undo_button", "redo_button"):
            assert not getattr(w, name).isVisible(), name
        for text in ("Вписать", "100 %"):
            button = next(b for b in w.findChildren(QPushButton) if b.text() == text)
            assert button.isVisible()
            assert abs(button.geometry().center().y() - w.mode_tabs.geometry().center().y()) < 30
        assert w.undo_edit() is False
    finally: w.close()


def test_pixel_option_is_advanced_and_preview_cancel_is_transactional(app):
    d=QuietDialogs();d.choice="discard";seen=[]
    def preview(parent,title,before,after,metrics):
        seen.append((before,after,metrics));return False
    d.confirm_edit=preview
    w=MainWindow(dialogs=d);doc=real_document();w.set_document(doc);w.show();app.processEvents()
    try:
        assert hasattr(w,"advanced_toggle")
        assert not w.lattice_button.isVisible()
        w.advanced_toggle.click();app.processEvents()
        assert w.lattice_button.isVisible()
        w.lattice_button.click();drain_until(app,lambda:not w.is_processing)
        assert seen and "<svg" in seen[0][0] and "<svg" in seen[0][1]
        assert "Областей" in seen[0][2] and "ступен" in seen[0][2]
        assert w.document is doc and doc.geometry_mode=="native" and not d.messages
    finally:w.close()


def test_explicit_minimum_preferred_and_actual_font_settings(app):
    d=QuietDialogs();d.choice="discard";w=MainWindow(dialogs=d);doc=real_document();w.set_document(doc)
    try:
        assert hasattr(w,"min_font_spin")
        w.font_spin.setValue(9);w.min_font_spin.setValue(6.5)
        assert w.print_settings().min_font_pt==6.5
        w.print_button.click();drain_until(app,lambda:not w.is_processing)
        assert w.document.settings.font_pt==9 and w.document.settings.min_font_pt==6.5
        assert "Фактически" in w.font_summary.text()
        assert "6.5" in w.font_summary.text() and "9" in w.font_summary.text()
        assert "миним" in w.min_font_spin.toolTip().lower()
    finally:w.close()


def test_completion_preflight_off_thread_once_and_named_journal_stages(app,monkeypatch):
    import threading,time
    from pbnstudio import diagnostics,exports
    d=QuietDialogs();d.choice="discard";d.confirm_edit=lambda *args:True
    w=MainWindow(dialogs=d);w.set_document(real_document())
    ids=[];events=[];original=exports.preflight
    def observed(doc):
        ids.append(threading.get_ident());return original(doc)
    monkeypatch.setattr(exports,"preflight",observed)
    monkeypatch.setattr(diagnostics,"event",lambda name,**fields:events.append((name,fields)))
    try:
        w.font_spin.setValue(8);w.print_button.click();drain_until(app,lambda:not w.is_processing)
        assert ids and threading.get_ident() not in ids, "completion must not recompute preflight on GUI thread"
        assert len(ids)==1
        w.mode_tabs.setCurrentIndex(1);w.mode_tabs.setCurrentIndex(2);w.render_document()
        assert len(ids)==1,"view changes must use revision-scoped preflight result"
        assert ("ui.action","apply-print") in [(name,f.get("action")) for name,f in events]
        names=[name for name,_ in events]
        for name in ("job.compute.end","ui.render.start","ui.render.end","ui.preflight.start","ui.preflight.end"):
            assert name in names,name
        assert not d.messages,d.messages
    finally:w.close()


def test_preview_journal_separates_user_wait_from_compute_and_render(app,monkeypatch):
    from pbnstudio import diagnostics,exports
    import threading
    events=[];ids=[];original=exports.preflight
    def check(doc):ids.append(threading.get_ident());return original(doc)
    monkeypatch.setattr(exports,"preflight",check)
    monkeypatch.setattr(diagnostics,"event",lambda name,**fields:events.append((name,fields)))
    d=QuietDialogs();d.choice="discard";d.confirm_edit=lambda *args:True
    w=MainWindow(dialogs=d);w.set_document(real_document());ids.clear();events.clear()
    try:
        w.lattice_button.click();drain_until(app,lambda:not w.is_processing)
        assert w.document.geometry_mode=="lattice",d.messages
        assert threading.get_ident() not in ids
        names=[name for name,_ in events]
        assert names.index("job.compute.end") < names.index("ui.preview.shown") < names.index("ui.preview.accepted") < names.index("ui.render.start")
        assert all(f.get("action")=="lattice" for name,f in events if name in ("ui.action","job.compute.end","ui.preview.shown","ui.preview.accepted"))
    finally:w.close()


def test_worker_display_is_bounded_image_and_preserves_source_coordinates(app):
    from pbnstudio.ui import prepare_display
    d=QuietDialogs();d.choice="discard";w=MainWindow(dialogs=d);doc=real_document()
    try:
        display=prepare_display(doc)
        assert "images" in display
        assert max(display["images"]["numbers"].size().toTuple())<=1600
        w.set_document(doc)
        assert w.result_view.sceneRect().width()==doc.width
        assert w.result_view.sceneRect().height()==doc.height
        assert w.result_view.scene().items()[0].__class__.__name__=="QGraphicsPixmapItem"
    finally:w.close()


def test_marker_only_has_exact_issue_code_help_and_click_never_edits(app):
    from pbnstudio.document import Issue
    from PySide6.QtCore import QEvent,Qt,QPointF
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QApplication,QToolTip
    from PySide6.QtTest import QTest
    d=QuietDialogs();d.choice="discard";w=MainWindow(dialogs=d);doc=real_document()
    doc.issues=[Issue("geometry-unmapped",1,"unmapped"),Issue("label-no-fit",1,"no fit")]
    w.set_document(doc);w.show();app.processEvents()
    try:
        markers=[item for item in w.result_view.scene().items() if item.toolTip()]
        assert markers,"red cross needs its own hit-target, not a canvas tooltip"
        tip=markers[0].toolTip()
        assert "geometry-unmapped" in tip and "label-no-fit" in tip
        point=w.result_view.mapFromScene(markers[0].sceneBoundingRect().center())
        viewport=w.result_view.viewport()
        QApplication.sendEvent(viewport,QHelpEvent(QEvent.Type.ToolTip,point,viewport.mapToGlobal(point)))
        assert QToolTip.text()==tip
        before=doc.revision
        QTest.mouseClick(viewport,Qt.MouseButton.LeftButton,pos=point);app.processEvents()
        assert w.selected_component==1 and doc.revision==before
        assert w.tool_combo.currentIndex()==0
        assert not w.result_view.toolTip()
    finally:QToolTip.hideText();w.close()


def test_apply_button_detaches_document_off_gui_thread(app,monkeypatch):
    import threading
    d=QuietDialogs();d.choice="discard";w=MainWindow(dialogs=d);w.set_document(real_document())
    ids=[];original=w._detached_document
    def detach(*args):ids.append(threading.get_ident());return original(*args)
    monkeypatch.setattr(w,"_detached_document",detach)
    try:
        w.font_spin.setValue(8);w.print_button.click();drain_until(app,lambda:not w.is_processing)
        assert ids and threading.get_ident() not in ids
        assert not d.messages,d.messages
    finally:w.close()


def test_native_completion_does_not_preflight_on_gui(app,monkeypatch,tmp_path):
    import threading
    from PIL import Image
    from pbnstudio import exports
    ids=[];original=exports.preflight
    def check(doc):ids.append(threading.get_ident());return original(doc)
    monkeypatch.setattr(exports,"preflight",check)
    source=tmp_path/"source.png";Image.new("RGB",(30,20),"blue").save(source)
    d=QuietDialogs();d.choice="discard";w=MainWindow(dialogs=d)
    try:
        assert w.load_path(source);drain_until(app,lambda:not w.is_processing)
        assert w.document is not None,d.messages
        assert ids and threading.get_ident() not in ids
    finally:w.close()


def test_print_controls_fit_minimum_workspace_width(app):
    w=MainWindow(dialogs=QuietDialogs());w.resize(960,760);w.show();w.print_settings_toggle.click();app.processEvents()
    try:
        assert w.width()<=1000
        assert w.min_font_spin.isVisible() and w.print_button.isVisible()
    finally:w.close()


def test_view_switch_reuses_prepared_marker_coordinates(app,monkeypatch):
    import numpy as np
    from pbnstudio.document import Issue
    d=QuietDialogs();d.choice="discard";w=MainWindow(dialogs=d);doc=real_document()
    doc.issues=[Issue("label-no-fit",1,"no fit")];w.set_document(doc)
    calls=[];original=np.where
    def where(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(np,"where",where)
    try:
        w.mode_tabs.setCurrentIndex(2);w.mode_tabs.setCurrentIndex(0)
        assert not calls,"marker pixel scan must not repeat per view"
    finally:w.close()


def test_journal_readback_names_compute_preview_and_render(app,monkeypatch,tmp_path):
    import json
    from pbnstudio import diagnostics
    diagnostics.shutdown()
    monkeypatch.setenv("PBN_DIAGNOSTICS_DIR",str(tmp_path))
    path=diagnostics.initialize()
    d=QuietDialogs();d.choice="discard";d.confirm_edit=lambda *args:True
    w=MainWindow(dialogs=d);w.set_document(real_document())
    try:
        w.lattice_button.click();drain_until(app,lambda:not w.is_processing)
        assert w.document.geometry_mode=="lattice",d.messages
    finally:w.close();diagnostics.shutdown()
    records=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    named=[r for r in records if r["fields"].get("action")=="lattice"]
    names=[r["event"] for r in named]
    for event in ("ui.action","job.compute.end","ui.preview.shown","ui.preview.accepted","ui.preflight.start","ui.preflight.end","ui.render.start","ui.render.end"):
        assert event in names,event
