# SPDX-License-Identifier: GPL-3.0-only
"""Real Qt widgets; controller doubles are explicit, never engine output fixtures."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication

@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    # Windows offscreen Qt exposes no system fonts by default. Register real
    # Segoe UI in this test process so geometry and text captures are meaningful.
    from PySide6.QtGui import QFontDatabase
    from pathlib import Path
    font = Path("C:/Windows/Fonts/segoeui.ttf")
    if not QFontDatabase.families() and font.exists():
        assert QFontDatabase.addApplicationFont(str(font)) >= 0
        symbols = font.with_name("seguisym.ttf")
        if symbols.exists():
            assert QFontDatabase.addApplicationFont(str(symbols)) >= 0
    yield instance

class QuietDialogs:
    def __init__(self):
        self.messages = []
        self.choice = "cancel"
    def dirty(self, parent): return self.choice
    def error(self, parent, title, text): self.messages.append((title, text))
    def info(self, parent, title, text): self.messages.append((title, text))
    def open_file(self, parent): return ""
    def save_file(self, parent): return ""
    def export_dir(self, parent): return ""
    def color(self, parent, initial): return None

def test_compact_russian_window_has_two_views_and_real_options(app):
    from pbnstudio import ui
    assert hasattr(ui, "MainWindow"), "MainWindow must exist"
    window = ui.MainWindow(dialogs=QuietDialogs())
    assert window.original_view is not window.result_view
    assert window.mode_tabs.count() == 3
    assert [window.mode_tabs.tabText(i) for i in range(3)] == ["Цвет", "Контуры", "Номера"]
    window.colors_spin.setValue(16)
    window.speckle_spin.setValue(7)
    window.simplify_spin.setValue(2.5)
    options = window.engine_options()
    assert (options.colors, options.speckle, options.simplify) == (16, 7, 2.5)
    assert not window.export_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert "PBN Studio" in window.windowTitle()
    window.close()

class FixtureDocument:
    """Explicit controller boundary fixture, NOT an engine result."""
    width = 100
    height = 80
    revision = 0
    dirty = False
    palette = [(220, 100, 40), (250, 250, 250)]
    labels = []
    issues = []
    def svg(self, mode="color", diagnostic=True):
        self.last_mode = mode
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 80"><path d="M0 0H100V80H0Z" fill="#dc6428"/></svg>'

def test_document_renders_svg_modes_and_synchronized_navigation(app, tmp_path):
    from pbnstudio.ui import MainWindow
    from PySide6.QtGui import QImage, QColor
    path = tmp_path / "source.png"
    image = QImage(100, 80, QImage.Format.Format_RGB32); image.fill(QColor("red")); image.save(str(path))
    window = MainWindow(dialogs=QuietDialogs())
    assert hasattr(window, "set_document"), "programmatic document loading is required"
    document = FixtureDocument()
    window.set_document(document, source_path=path)
    assert window.original_view.scene().items()
    assert window.result_view.scene().items()
    assert window.export_button.isEnabled()
    window.mode_tabs.setCurrentIndex(2)
    assert document.last_mode == "numbers"
    window.original_view.set_navigation(3.0, 30, 25)
    assert window.result_view.transform().m11() == pytest.approx(3.0)
    assert window.result_view.navigation == (3.0, 30, 25)
    assert window.palette_layout.count() == 2
    window.close()

def drain_until(app, predicate):
    import time
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(0.005)
    assert predicate(), "Qt worker did not settle"

def test_background_cancel_and_revision_fence_preserve_old_document(app, tmp_path):
    import threading
    from pbnstudio.ui import MainWindow
    from pbnstudio.engine import EngineCancelled
    from PySide6.QtGui import QImage, QColor
    image = QImage(100,80,QImage.Format.Format_RGB32); image.fill(QColor("red"))
    source = tmp_path / "source.png"; image.save(str(source))
    entered = threading.Event(); release = threading.Event(); seen = []
    def runner(path, output, options, cancel_event, progress):
        seen.append((threading.get_ident(), options))
        entered.set()
        release.wait(3)
        if cancel_event.is_set(): raise EngineCancelled("test cancellation")
        return output
    new_doc = FixtureDocument()
    window = MainWindow(dialogs=QuietDialogs(), runner=runner, document_loader=lambda _: new_doc)
    assert hasattr(window, "start_processing"), "background controller missing"
    old = FixtureDocument(); window.set_document(old, source_path=source)
    assert window.start_processing()
    drain_until(app, entered.is_set)
    assert seen[0][0] != threading.get_ident()
    window.cancel_processing(); release.set()
    drain_until(app, lambda: not window.is_processing)
    assert window.document is old
    entered.clear(); release.clear()
    assert window.start_processing()
    drain_until(app, entered.is_set)
    old.revision += 1
    release.set(); drain_until(app, lambda: not window.is_processing)
    assert window.document is old
    entered.clear(); release.clear()
    assert window.start_processing()
    drain_until(app, entered.is_set); release.set()
    drain_until(app, lambda: not window.is_processing)
    assert window.document is new_doc
    window.dialogs.choice = "discard"
    window.close()

def test_load_dirty_cancel_discard_and_invalid_input_are_transactional(app, tmp_path):
    from pbnstudio.ui import MainWindow
    from PySide6.QtGui import QImage, QColor
    dialogs = QuietDialogs(); window = MainWindow(dialogs=dialogs)
    assert hasattr(window, "load_path"), "programmatic file loading missing"
    doc = FixtureDocument(); doc.dirty = True
    window.set_document(doc)
    image = QImage(20, 10, QImage.Format.Format_RGB32); image.fill(QColor("green"))
    path = tmp_path / "input.png"; image.save(str(path))
    assert window.load_path(path, process=False) is False
    assert window.document is doc
    dialogs.choice = "discard"
    assert window.load_path(path, process=False) is True
    assert window.document is None
    assert window.source_path == path
    assert window.run_button.isEnabled()
    window.set_document(doc)
    assert window.load_path(tmp_path / "missing.png", process=False) is False
    assert window.document is doc
    assert dialogs.messages
    dialogs.choice = "cancel"
    assert window.close() is False
    dialogs.choice = "discard"
    window.close()

def test_save_failure_does_not_clear_dirty_and_save_prompt_obeys_cancel(app, tmp_path, monkeypatch):
    from pbnstudio.ui import MainWindow
    import sys, types
    # Storage module may be under parallel construction; explicit boundary double.
    fake = types.ModuleType("pbnstudio.storage")
    def failing_save(doc, path): raise ValueError("destination already exists")
    fake.save_project = failing_save
    monkeypatch.setitem(sys.modules, "pbnstudio.storage", fake)
    dialogs = QuietDialogs(); window = MainWindow(dialogs=dialogs)
    doc = FixtureDocument(); doc.dirty = True; window.set_document(doc)
    assert hasattr(window, "save_to"), "safe save controller missing"
    assert window.save_to(tmp_path / "project.pbn") is False
    assert doc.dirty
    dialogs.choice = "save"
    assert window.confirm_replace() is False
    dialogs.choice = "discard"; window.close()

def real_document():
    from pathlib import Path
    from pbnstudio.document import Document
    fixture = Path(__file__).resolve().parents[2] / "research/stage1/native-gate/outputs/hole"
    doc = Document.load(fixture, trusted=True)
    doc.prepare_labels()
    return doc

def test_physical_settings_recolor_move_undo_use_real_domain(app):
    from pbnstudio.ui import MainWindow
    dialogs = QuietDialogs(); window = MainWindow(dialogs=dialogs)
    doc = real_document(); window.set_document(doc)
    assert hasattr(window, "apply_print_settings"), "physical settings missing"
    window.page_width_spin.setValue(210)
    window.page_height_spin.setValue(297)
    window.margin_spin.setValue(10)
    window.font_spin.setValue(9)
    window.line_spin.setValue(0.5)
    assert window.apply_print_settings()
    drain_until(app, lambda: not window.is_processing)
    doc = window.document
    assert (doc.settings.width_mm, doc.settings.height_mm, doc.settings.font_pt) == (210,297,9)
    assert doc.dirty and doc.revision > 0
    assert window.select_component_at(4,4) == 1
    old_rgb = doc.component_colors[1]
    assert window.recolor_selected((10,20,30))
    assert doc.component_colors[1] == (10,20,30)
    assert window.undo_edit()
    assert doc.component_colors[1] == old_rgb
    assert window.redo_edit()
    assert doc.component_colors[1] == (10,20,30)
    before = doc.revision
    assert window.move_selected(4.5,4.5)
    assert doc.revision == before + 1
    assert not window.move_selected(-100,-100)
    assert doc.revision == before + 1
    assert dialogs.messages
    dialogs.choice = "discard"; window.close()

def test_svg_scene_coordinates_remain_source_pixels_with_physical_svg(app):
    from pbnstudio.ui import MainWindow
    window = MainWindow(dialogs=QuietDialogs()); doc = real_document()
    window.set_document(doc)
    item = window.result_view.scene().items()[0]
    bounds = item.sceneBoundingRect()
    assert bounds.width() == pytest.approx(doc.width), "SVG mm must not scale edit coordinates"
    assert bounds.height() == pytest.approx(doc.height)
    window.close()

def test_export_blocks_issues_and_diagnostic_mode_is_explicit(app, tmp_path, monkeypatch):
    from pbnstudio.ui import MainWindow
    from pbnstudio.document import Issue
    import sys, types
    calls=[]
    fake=types.ModuleType("pbnstudio.exports")
    def export_bundle(doc, destination, diagnostic=False):
        calls.append((doc,destination,diagnostic))
        destination.mkdir()
        result=destination/("DIAGNOSTIC-numbered.svg" if diagnostic else "numbered.svg")
        result.write_text(doc.svg("numbers"),encoding="utf-8")
        return [result]
    from pbnstudio.exports import preflight
    fake.preflight=preflight
    fake.export_bundle=export_bundle; monkeypatch.setitem(sys.modules,"pbnstudio.exports",fake)
    dialogs=QuietDialogs(); window=MainWindow(dialogs=dialogs)
    doc=real_document(); doc.issues=[Issue("label-no-fit",1,"Number does not fit")]; window.set_document(doc)
    assert hasattr(window,"export_to"), "preflight export controller missing"
    assert not window.export_to(tmp_path/"blocked")
    assert not calls and not (tmp_path/"blocked").exists()
    assert window.export_to(tmp_path/"diagnostic", diagnostic=True)
    assert calls[-1][2] is True
    assert "не для печати" in window.status_label.text().lower()
    assert "label-no-fit" in window.issue_list.item(0).text()
    doc.issues=[]
    assert window.export_to(tmp_path/"normal")
    assert calls[-1][2] is False
    assert "Комплект" in window.status_label.text()
    window.close()

def test_entrypoints_are_import_safe_and_support_cli_help(app):
    import importlib.util, subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert (root/"pbnstudio/__main__.py").is_file(), "module entrypoint missing"
    assert (root/"launch.py").is_file(), "source launcher missing"
    result=subprocess.run([sys.executable,"-m","pbnstudio","--help"],cwd=root,capture_output=True,text=True,encoding="utf-8",timeout=10)
    assert result.returncode == 0
    assert "PBN Studio" in result.stdout
    spec=importlib.util.spec_from_file_location("safe_launcher",root/"launch.py")
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert callable(module.main)

def test_document_replacement_render_failure_keeps_previous_document(app):
    from pbnstudio.ui import MainWindow
    window=MainWindow(dialogs=QuietDialogs()); old=FixtureDocument(); window.set_document(old)
    class BrokenDocument(FixtureDocument):
        def svg(self, *args, **kwargs): raise ValueError("bad geometry")
    with pytest.raises(ValueError): window.set_document(BrokenDocument())
    assert window.document is old, "failed rendering must not commit replacement"
    window.close()

def test_loading_image_clears_stale_edit_actions(app, tmp_path):
    from pbnstudio.ui import MainWindow
    from PySide6.QtGui import QImage, QColor
    window=MainWindow(dialogs=QuietDialogs()); doc=real_document(); window.set_document(doc)
    window.select_component_at(4,4)
    image=QImage(10,10,QImage.Format.Format_RGB32); image.fill(QColor("white"))
    path=tmp_path/"next.png"; image.save(str(path))
    assert window.load_path(path,process=False)
    assert not window.print_button.isEnabled()
    assert not window.diagnostic_button.isEnabled()
    assert not window.recolor_button.isEnabled()
    assert window.selected_component is None
    assert window.issue_list.count()==0
    window.close()

def test_real_storage_and_export_through_ui_controller(app, tmp_path):
    from pbnstudio.ui import MainWindow
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtCore import QBuffer, QIODevice
    doc=real_document()
    image=QImage(doc.width,doc.height,QImage.Format.Format_RGB32); image.fill(QColor("white"))
    buffer=QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer,"PNG")
    doc.source_bytes=bytes(buffer.data())
    dialogs=QuietDialogs(); window=MainWindow(dialogs=dialogs); window.set_document(doc)
    window.select_component_at(4,4); window.recolor_selected((12,34,56))
    path=tmp_path/"actual.pbn"
    assert window.save_to(path), dialogs.messages
    assert path.is_file() and not doc.dirty
    assert window.load_path(path)
    assert window.document.component_colors[1]==(12,34,56)
    assert window.original_view.scene().items()
    assert window.export_to(tmp_path/"bundle"), dialogs.messages
    assert (tmp_path/"bundle/numbered.svg").is_file()
    window.close()

def test_open_new_image_processing_error_preserves_old_document_and_source(app, tmp_path):
    from pbnstudio.ui import MainWindow
    from pbnstudio.engine import EngineError
    from PySide6.QtGui import QImage, QColor
    def failing_runner(*args,**kwargs): raise EngineError("deliberate worker failure")
    dialogs=QuietDialogs(); window=MainWindow(dialogs=dialogs,runner=failing_runner)
    old=FixtureDocument(); old_source=tmp_path/"old.png"
    image=QImage(100,80,QImage.Format.Format_RGB32); image.fill(QColor("blue")); image.save(str(old_source))
    new_source=tmp_path/"new.png"; image.fill(QColor("red")); image.save(str(new_source))
    window.set_document(old,source_path=old_source)
    assert window.load_path(new_source)
    drain_until(app,lambda: not window.is_processing)
    assert window.document is old
    assert window.source_path == old_source
    assert dialogs.messages
    window.close()

def test_real_native_job_commits_document_and_selected_print_settings(app, tmp_path):
    from pbnstudio.ui import MainWindow
    from PIL import Image, ImageDraw
    image=Image.new("RGB",(48,32),"white")
    ImageDraw.Draw(image).rectangle((8,8,30,24),fill="#aa5511")
    source=tmp_path/"native.png"; image.save(source)
    dialogs=QuietDialogs(); window=MainWindow(dialogs=dialogs)
    window.simplify_spin.setValue(0)
    window.colors_spin.setValue(4)
    window.font_spin.setValue(8)
    window.page_width_spin.setValue(210)
    assert window.load_path(source)
    drain_until(app,lambda: not window.is_processing)
    assert window.document is not None, dialogs.messages
    assert (window.document.width,window.document.height)==(48,32)
    assert window.document.source_bytes
    assert window.document.settings.width_mm==210
    assert window.document.settings.font_pt==8
    assert window.original_view.scene().items() and window.result_view.scene().items()
    assert window.save_to(tmp_path/"native.pbn"), dialogs.messages
    assert window.export_to(tmp_path/"native-bundle",diagnostic=bool(window.document.issues)), dialogs.messages
    window.close()

def test_new_native_document_is_unsaved_and_close_during_job_is_safe(app, tmp_path):
    import threading
    from pbnstudio.ui import MainWindow
    from pbnstudio.engine import EngineCancelled
    from PIL import Image
    source=tmp_path/"unsaved.png"; Image.new("RGB",(12,12),"red").save(source)
    dialogs=QuietDialogs(); window=MainWindow(dialogs=dialogs)
    window.simplify_spin.setValue(0)
    assert window.load_path(source)
    drain_until(app,lambda: not window.is_processing)
    assert window.document.dirty, "a generated, never-saved document must prompt on close"
    assert not window.close()
    dialogs.choice="discard"; window.close()
    entered=threading.Event()
    def waiting_runner(path,output,options,cancel_event,progress):
        entered.set(); cancel_event.wait(4)
        raise EngineCancelled("closed")
    other=MainWindow(dialogs=dialogs,runner=waiting_runner)
    assert other.load_path(source)
    drain_until(app,entered.is_set)
    assert not other.close(), "must defer close until the QThread exits"
    assert not other.centralWidget().isEnabled(), "no edits after close approval"
    drain_until(app,lambda: not other.is_processing)

def test_repeated_zoom_stays_bounded_and_bidirectional(app):
    from pbnstudio.ui import MainWindow
    window=MainWindow(dialogs=QuietDialogs()); window.set_document(FixtureDocument())
    window.result_view.set_navigation(999,20,30)
    assert window.original_view.navigation==(64.0,20,30)
    window.result_view.set_navigation(0.0001,20,30)
    assert window.original_view.navigation==(0.02,20,30)
    window.close()

def test_invalid_physical_settings_do_not_start_worker(app,tmp_path):
    from pbnstudio.ui import MainWindow
    from PIL import Image
    source=tmp_path/"input.png"; Image.new("RGB",(8,8),"white").save(source)
    dialogs=QuietDialogs(); window=MainWindow(dialogs=dialogs)
    window.page_width_spin.setValue(10); window.margin_spin.setValue(10)
    assert not window.load_path(source)
    assert not window.is_processing
    assert dialogs.messages
    window.close()
