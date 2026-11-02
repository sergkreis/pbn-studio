# SPDX-License-Identifier: GPL-3.0-only
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from test_ui import app, QuietDialogs, real_document, drain_until
from pbnstudio.ui import MainWindow


def test_help_is_collapsed_and_issue_warning_is_russian(app):
    from pbnstudio.document import Issue
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); doc = real_document(); w.set_document(doc)
    assert hasattr(w, "help_toggle"), "technical paragraphs must be collapsible"
    assert w.support_label.isHidden()
    w.help_toggle.click(); assert not w.support_label.isHidden()
    doc.issues = [Issue("label-no-fit", 1, "Number does not fit")]
    w.render_document()
    assert "Number does not fit" not in w.issue_list.item(0).toolTip()
    assert "Номер" in w.issue_list.item(0).toolTip()
    assert "заблокирован" in w.status_label.text()
    assert w.issue_list.count() == 1 and not w.issue_list.isHidden()
    w.show(); app.processEvents()
    w.help_toggle.click(); app.processEvents()
    assert w.result_view.height() >= 400
    w.close()


def test_batch_controller_is_background_and_preserves_dirty_document(app, tmp_path, monkeypatch):
    from PIL import Image
    from pbnstudio.document import Document
    import threading
    d = QuietDialogs(); d.choice = "cancel"
    w = MainWindow(dialogs=d); old = real_document(); old.dirty = True; w.set_document(old)
    source = tmp_path/"one.png"; Image.new("RGB", (24,16), "red").save(source)
    threads = []; original = Document.prepare_labels
    def observed(self, *args, **kwargs):
        threads.append(threading.get_ident()); return original(self, *args, **kwargs)
    monkeypatch.setattr(Document, "prepare_labels", observed)
    assert hasattr(w, "start_batch"), "batch UI action is missing"
    assert w.start_batch([source, source], tmp_path/"results", diagnostic=True)
    assert w.is_processing and w.cancel_button.isEnabled()
    drain_until(app, lambda: not w.is_processing)
    assert w.document is old and old.dirty
    assert threads and all(t != threading.get_ident() for t in threads)
    assert len(w.last_batch_result.items) == 2
    assert w.batch_report_list.count() == 2
    assert "НЕ ДЛЯ ПЕЧАТИ" in w.batch_report_list.item(0).text()
    assert not d.messages, d.messages
    d.choice = "discard"; w.close()


def test_english_exceptions_are_translated_at_user_boundary(app, tmp_path):
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); w.set_document(real_document())
    target = tmp_path/"exists.pbn"; target.write_bytes(b"keep")
    assert not w.save_to(target)
    assert d.messages
    assert "exist" not in d.messages[-1][1].lower()
    assert "существ" in d.messages[-1][1].lower()
    assert target.read_bytes() == b"keep"
    w.close()


def test_geometry_tools_are_gated_by_real_domain_capabilities(app):
    from pbnstudio.document import Document
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); w.set_document(real_document())
    assert hasattr(w, "simplify_button"), "explicit print simplify action missing"
    assert w.simplify_button.text() == "Упростить для печати"
    assert w.simplify_button.isEnabled() == callable(getattr(Document, "simplify_for_print", None))
    assert w.tool_combo.model().item(3).isEnabled() == callable(getattr(Document, "merge_components", None))
    assert w.tool_combo.model().item(4).isEnabled() == callable(getattr(Document, "split_component", None))
    w.close()


def test_geometry_notice_is_short_russian_not_permanent_technical_paragraph(app):
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); doc = real_document()
    doc.confirm_lattice_fallback(confirmed=True); w.set_document(doc)
    assert "Confirmed" not in w.geometry_label.text()
    assert w.geometry_label.text() == "Границы: точные пиксельные"
    assert "ступен" in w.support_label.text()
    w.close()


def test_candidate_preview_is_transactional_confirmed_and_undoable(app):
    d = QuietDialogs(); d.choice = "discard"
    seen = []; accept = [False]
    def confirm(parent, title, before, after, metrics):
        seen.append((title, before, after, metrics))
        assert parent.document.component_colors[1] != (20,30,40)
        return accept[0]
    d.confirm_edit = confirm
    w = MainWindow(dialogs=d); old = real_document(); w.set_document(old)
    original = old.component_colors[1]
    assert hasattr(w, "start_candidate_edit"), "transactional edit preview missing"
    assert w.start_candidate_edit("Проба цвета", lambda doc: doc.recolor(1, (20,30,40)))
    drain_until(app, lambda: not w.is_processing)
    assert seen and "<svg" in seen[0][1] and "<svg" in seen[0][2]
    assert "Областей" in seen[0][3] and "Замечаний" in seen[0][3]
    assert w.document is old and old.component_colors[1] == original
    accept[0] = True
    w.result_view.set_navigation(2,4,4)
    assert w.start_candidate_edit("Проба цвета", lambda doc: doc.recolor(1, (20,30,40)))
    drain_until(app, lambda: not w.is_processing)
    assert w.document is not old and w.document.component_colors[1] == (20,30,40)
    assert w.result_view.navigation == (2,4,4)
    assert w.undo_edit() and w.document.component_colors[1] == original
    w.close()


def test_batch_cancel_during_real_labels_keeps_completed_outputs_only(app, tmp_path, monkeypatch):
    from PIL import Image
    from pbnstudio.document import Document
    import threading
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); old = real_document(); w.set_document(old)
    source = tmp_path/"one.png"; Image.new("RGB", (24,16), "red").save(source)
    original = Document.prepare_labels; entered = threading.Event(); release = threading.Event()
    def waiting(self, *args, **kwargs):
        entered.set(); release.wait(3); return original(self, *args, **kwargs)
    monkeypatch.setattr(Document, "prepare_labels", waiting)
    assert w.start_batch([source, source], tmp_path/"out", diagnostic=True)
    try:
        drain_until(app, entered.is_set)
        w.cancel_processing(); release.set()
        drain_until(app, lambda: not w.is_processing)
        assert w.last_batch_result.cancelled
        assert [item.status for item in w.last_batch_result.items] == ["cancelled", "cancelled"]
        assert w.document is old
        assert "опубликованные" in w.status_label.toolTip()
        assert not list((tmp_path/"out").glob("*/DIAGNOSTIC-numbered.pdf"))
    finally:
        release.set(); drain_until(app, lambda: not w.is_processing); w.close()


def test_merge_two_clicked_adjacent_components_real_domain_preview_undo(app):
    d = QuietDialogs(); d.choice = "discard"; previews = []
    d.confirm_edit = lambda parent, title, before, after, metrics: previews.append(metrics) or True
    w = MainWindow(dialogs=d); doc = real_document(); w.set_document(doc)
    before = len(doc.components)
    w.tool_combo.setCurrentIndex(3)
    w._canvas_clicked(4,4)
    assert w.selected_component == doc.sample_component(4,4)
    assert not w.is_processing
    w._canvas_clicked(0.5,0.5)
    assert w.is_processing, "second adjacent selection must run real merge"
    drain_until(app, lambda: not w.is_processing)
    assert previews and not d.messages, d.messages
    assert len(w.document.components) == before-1
    assert w.last_edit_report["operation"] == "merge"
    assert w.undo_edit() and len(w.document.components) == before
    w.close()


def test_native_ui_validates_smooth_without_implicit_repair(app, tmp_path):
    from PIL import Image
    from pbnstudio.exports import preflight
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d)
    source = tmp_path/"solid.png"; Image.new("RGB", (24,16), "red").save(source)
    w.simplify_spin.setValue(0)
    assert w.load_path(source)
    drain_until(app, lambda: not w.is_processing)
    assert w.document is not None, d.messages
    assert not preflight(w.document)
    assert w.document.geometry_mode == "native"
    assert not w.document.smooth_report["fallback_edges"]
    w.close()


def test_all_preflight_issues_remain_visible_including_palette_layout(app):
    from pbnstudio.document import PrintSettings
    from pbnstudio.exports import preflight
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); doc = real_document()
    doc.prepare_labels(PrintSettings(width_mm=20,height_mm=20,margin_mm=8,font_pt=100))
    w.set_document(doc)
    assert w.issue_list.count() == len(preflight(doc))
    assert any("Палитра" in w.issue_list.item(i).text() for i in range(w.issue_list.count()))
    w.close()


def test_geometry_tools_reenable_after_job_and_selection_is_marked(app):
    d = QuietDialogs(); d.choice = "discard"; d.confirm_edit=lambda *args: True
    w = MainWindow(dialogs=d); w.set_document(real_document())
    w.tool_combo.setCurrentIndex(3); w._canvas_clicked(4,4)
    assert hasattr(w, "selection_overlay") and w.selection_overlay is not None
    w._canvas_clicked(0.5,0.5); drain_until(app, lambda: not w.is_processing)
    assert w.tool_combo.model().item(3).isEnabled()
    w.close()


def test_candidate_rejects_revision_changed_during_confirmation(app):
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); old = real_document(); w.set_document(old)
    def confirm(*args):
        old.recolor(0, (1,2,3)); return True
    d.confirm_edit = confirm
    assert w.start_candidate_edit("Цвет", lambda doc: doc.recolor(1,(20,30,40)))
    drain_until(app, lambda: not w.is_processing)
    assert w.document is old and old.component_colors[0] == (1,2,3)
    assert "устарел" in w.status_label.text()
    w.close()


def test_switching_tools_resets_pending_merge_selection(app):
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); w.set_document(real_document())
    w.tool_combo.setCurrentIndex(1); w._canvas_clicked(4,4)
    w.tool_combo.setCurrentIndex(3)
    assert w.selected_component is None, "merge must start with an explicit fresh first component"
    w.close()


def test_merge_preview_reports_real_area_and_font_metrics(app):
    d = QuietDialogs(); d.choice = "discard"; metrics = []
    d.confirm_edit=lambda parent,title,before,after,text: metrics.append(text) or False
    w = MainWindow(dialogs=d); doc = real_document(); w.set_document(doc)
    assert w.start_candidate_edit("Объединение", lambda candidate: candidate.merge_components(1,0))
    drain_until(app, lambda: not w.is_processing)
    assert metrics and "Изменено площади" in metrics[0]
    assert "%" in metrics[0] and "мм²" in metrics[0] and "Размер номера" in metrics[0]
    assert w.document is doc
    w.close()


def test_compact_print_settings_are_expandable_without_hiding_preflight(app):
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d)
    assert hasattr(w, "print_settings_toggle")
    assert w.print_settings_panel.isHidden()
    w.print_settings_toggle.click(); assert not w.print_settings_panel.isHidden()
    w.print_settings_toggle.click()
    w.set_document(real_document()); w.show(); w.resize(1000,720); app.processEvents()
    assert w.result_view.height() >= 350
    assert w.result_view.width() >= 350
    w.close()


def test_preview_dialog_defaults_to_cancel_and_uses_real_two_canvases(app):
    from pbnstudio.ui import Dialogs, CanvasView
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QPushButton
    svg = real_document().svg("numbers")
    seen = []
    def reject():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        assert len(dialog.findChildren(CanvasView)) == 2
        defaults = [b.text() for b in dialog.findChildren(QPushButton) if b.isDefault()]
        seen.extend(defaults); dialog.reject()
    QTimer.singleShot(0, reject)
    assert not Dialogs().confirm_edit(None, "Проверка", svg, svg, "Областей: 2 → 2")
    assert seen == ["Не применять"]


def test_new_image_clears_geometry_selection_tools(app, tmp_path):
    from PIL import Image
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); w.set_document(real_document())
    source = tmp_path/"new.png"; Image.new("RGB", (24,16), "red").save(source)
    assert w.load_path(source, process=False)
    assert not w.tool_combo.model().item(3).isEnabled()
    assert not w.tool_combo.model().item(4).isEnabled()
    assert not w.simplify_button.isEnabled()
    assert not w.geometry_label.text()
    w.close()


def test_confirmation_nested_event_loop_survives_worker_finished(app):
    from PySide6.QtWidgets import QDialog
    from PySide6.QtCore import QTimer
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); w.set_document(real_document())
    def confirm(*args):
        dialog = QDialog(w)
        QTimer.singleShot(30, dialog.accept)
        return dialog.exec() == QDialog.DialogCode.Accepted
    d.confirm_edit = confirm
    assert w.start_candidate_edit("Цвет", lambda doc: doc.recolor(1,(20,30,40)))
    drain_until(app, lambda: not w.is_processing)
    assert not d.messages, d.messages
    assert w.document.component_colors[1] == (20,30,40)
    w.close()


def test_simplify_button_uses_real_domain_and_requires_confirmation(app):
    from pbnstudio.document import Document
    if not callable(getattr(Document, "simplify_for_print", None)):
        import pytest
        pytest.skip("Pending real domain simplify_for_print API; no substitute used")
    d = QuietDialogs(); d.choice = "discard"; previews = []
    d.confirm_edit=lambda parent,title,before,after,text: previews.append(text) or False
    w = MainWindow(dialogs=d); doc = real_document(); w.set_document(doc)
    revision = doc.revision
    w.simplify_button.click()
    assert w.is_processing
    drain_until(app, lambda: not w.is_processing)
    assert previews and not d.messages, d.messages
    assert w.document is doc and doc.revision == revision
    w.close()


def test_preflight_count_remains_visible_during_jobs(app, tmp_path):
    from PIL import Image
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); w.set_document(real_document())
    assert hasattr(w, "preflight_label"), "persistent preflight summary must not be overwritten by job progress"
    initial = w.preflight_label.text()
    assert "Проверка" in initial
    source = tmp_path/"new.png"; Image.new("RGB", (24,16), "red").save(source)
    assert w.start_batch([source], tmp_path/"out")
    drain_until(app, lambda: not w.is_processing)
    assert w.preflight_label.text() == initial
    w.close()


def test_batch_dialog_buttons_choose_multiple_images_and_explicit_options(app, tmp_path):
    from PIL import Image
    d = QuietDialogs(); d.choice = "discard"
    files = []
    for index in range(2):
        source = tmp_path/f"{index}.png"; Image.new("RGB", (24,16), "red").save(source); files.append(source)
    d.batch_files = lambda parent: files
    d.batch_options = lambda parent: (tmp_path/"out", False, False)
    w = MainWindow(dialogs=d)
    w.batch_button.click(); drain_until(app, lambda: not w.is_processing)
    assert w.last_batch_result and len(w.last_batch_result.items) == 2
    assert all(item.status == "exported" for item in w.last_batch_result.items)
    assert not w.last_batch_result.diagnostic
    assert "готово 2" in w.status_label.text()
    w.close()


def test_busy_geometry_edit_controls_disable_until_preview_is_ready(app, monkeypatch):
    import threading
    d = QuietDialogs(); d.choice = "discard"; d.confirm_edit=lambda *args: False
    w = MainWindow(dialogs=d); w.set_document(real_document())
    entered = threading.Event(); release = threading.Event()
    def operation(doc):
        entered.set(); release.wait(3); return doc.merge_components(1,0)
    assert w.start_candidate_edit("Объединение", operation)
    try:
        drain_until(app, entered.is_set)
        assert not w.tool_combo.model().item(3).isEnabled()
        assert not w.simplify_button.isEnabled()
    finally:
        release.set(); drain_until(app, lambda: not w.is_processing); w.close()


def test_merge_nonadjacent_error_is_russian_and_keeps_document(app):
    from pbnstudio.document import Document
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]/"research/stage1/native-gate/outputs/checkerboard"
    doc = Document.load(root, trusted=True); doc.prepare_labels()
    d = QuietDialogs(); d.choice = "discard"; d.confirm_edit=lambda *args: True
    w = MainWindow(dialogs=d); w.set_document(doc)
    original = doc.component_map.copy(); revision = doc.revision
    assert w.start_candidate_edit("Объединение", lambda candidate: candidate.merge_components(0,3))
    drain_until(app, lambda: not w.is_processing)
    assert w.document is doc and doc.revision == revision
    assert (original == doc.component_map).all()
    assert d.messages and "соседние" in d.messages[-1][1]
    w.close()


def test_changed_component_ids_reset_selection_after_undo_redo(app):
    d = QuietDialogs(); d.choice = "discard"; d.confirm_edit=lambda *args: True
    w = MainWindow(dialogs=d); w.set_document(real_document())
    assert w.start_candidate_edit("Объединение", lambda doc: doc.merge_components(1,0))
    drain_until(app, lambda: not w.is_processing)
    assert w.undo_edit()
    w.select_component_at(4,4)
    assert w.selected_component == 1
    assert w.redo_edit()
    assert w.selected_component is None
    assert not w.recolor_button.isEnabled()
    w.close()
