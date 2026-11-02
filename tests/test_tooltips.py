# SPDX-License-Identifier: GPL-3.0-only
"""Hover help exercised via real Qt events, never a fake tooltip service."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import re
import pytest
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import (QApplication, QAbstractButton, QAbstractSpinBox,
    QComboBox, QLineEdit, QTabBar, QToolTip, QWidget)
from test_ui import app, QuietDialogs, real_document
from pbnstudio.ui import MainWindow


def assert_tip(widget):
    text = widget.toolTip()
    assert "<b>" in text and "380" in text, (type(widget).__name__, widget.objectName(), text)
    assert re.search("[А-Яа-я]", text) and len(text) > 110, text
    assert widget.toolTipDuration() == 15000


def show_help(app, widget):
    QToolTip.hideText(); app.processEvents()
    position = widget.rect().center()
    event = QHelpEvent(QEvent.Type.ToolTip, position, widget.mapToGlobal(position))
    QApplication.sendEvent(widget, event); app.processEvents()
    assert QToolTip.isVisible(), (type(widget).__name__, widget.toolTip())
    return QToolTip.text()


def test_every_main_control_and_hidden_print_input_has_russian_help(app):
    window = MainWindow(dialogs=QuietDialogs())
    try:
        controls = [w for w in window.findChildren(QWidget)
                    if isinstance(w, (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QTabBar))
                    and w.objectName() not in ("ScrollLeftButton", "ScrollRightButton")]
        assert len(controls) >= 39
        for control in controls:
            assert_tip(control)
        for name in ("preflight_label", "status_label", "geometry_label", "selection_label", "progress_bar"):
            assert_tip(getattr(window, name))
        assert window.print_settings_panel.isHidden()
        for i in range(window.mode_tabs.count()):
            assert "<b>" in window.mode_tabs.tabToolTip(i)
    finally:
        window.close()


def test_real_hover_works_on_disabled_controls_without_enabling_them(app):
    window = MainWindow(dialogs=QuietDialogs()); window.show(); app.processEvents()
    try:
        assert not window.run_button.isEnabled()
        assert window.testAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        assert "исходник" in show_help(app, window.run_button).lower()
        assert not window.run_button.isEnabled() and window.document is None
        assert "сохран" in show_help(app, window.save_button).lower()
        assert not window.save_button.isEnabled()
        assert "цвет" in show_help(app, window.colors_spin.lineEdit()).lower()
    finally:
        QToolTip.hideText(); window.close()


def test_dynamic_palette_recolors_only_selected_region_and_explains_preflight(app):
    from pbnstudio.document import Issue
    d = QuietDialogs(); d.choice = "discard"
    w = MainWindow(dialogs=d); doc = real_document(); w.set_document(doc)
    try:
        swatch = w.palette_layout.itemAt(0).widget()
        assert_tip(swatch)
        assert not swatch.isEnabled()
        assert "выбранн" in swatch.toolTip().lower() and "RGB" in swatch.toolTip()
        assert "не" in swatch.toolTip().lower() and "вс" in swatch.toolTip().lower()
        w.select_component_at(4, 4)
        other = dict(doc.component_colors); color = tuple(doc.palette[0])
        w.palette_layout.itemAt(0).widget().click()
        assert doc.component_colors[1] == color
        assert all(doc.component_colors[k] == v for k, v in other.items() if k != 1)
        doc.issues = [Issue("label-no-fit", 1, "does not fit") for _ in range(99)]
        w.render_document()
        assert "99" in w.preflight_label.text()
        for control in (w.preflight_label, w.status_label, w.export_button):
            assert "заблокирован" in control.toolTip().lower() or "устран" in control.toolTip().lower()
        row = w.issue_list.item(0)
        assert "<b>" in row.toolTip() and "выбер" in row.toolTip().lower()
        w.show(); app.processEvents()
        assert "замечан" in show_help(app, w.preflight_label).lower()
        assert "не для печати" in w.diagnostic_button.toolTip().lower()
        assert "предпросмотр" in w.simplify_button.toolTip().lower()
    finally:
        QToolTip.hideText(); w.close()


def test_each_tool_and_tab_has_its_own_real_hover_help(app):
    w = MainWindow(dialogs=QuietDialogs()); w.show(); app.processEvents()
    try:
        tips = []
        for i in range(w.tool_combo.count()):
            text = w.tool_combo.itemData(i, Qt.ItemDataRole.ToolTipRole)
            assert "<b>" in text
            tips.append(text)
            w.tool_combo.setCurrentIndex(i)
            assert w.tool_combo.toolTip() == text
            assert show_help(app, w.tool_combo) == text
        assert len(set(tips)) == w.tool_combo.count()
        for i in range(w.mode_tabs.count()):
            position = w.mode_tabs.tabRect(i).center()
            event = QHelpEvent(QEvent.Type.ToolTip, position, w.mode_tabs.mapToGlobal(position))
            QApplication.sendEvent(w.mode_tabs, event); app.processEvents()
            assert QToolTip.text() == w.mode_tabs.tabToolTip(i)
        # Disabled merge/split entries still explain prerequisites on popup hover.
        w.tool_combo.showPopup(); app.processEvents()
        view = w.tool_combo.view(); index = w.tool_combo.model().index(3, 0)
        position = view.visualRect(index).center()
        QApplication.sendEvent(view.viewport(), QHelpEvent(QEvent.Type.ToolTip, position, view.viewport().mapToGlobal(position)))
        app.processEvents()
        assert QToolTip.text() == tips[3]
        assert not w.tool_combo.model().item(3).isEnabled()
        w.tool_combo.hidePopup()
    finally:
        QToolTip.hideText(); w.close()


@pytest.mark.parametrize("method", ["dirty", "confirm_edit", "confirm_lattice", "error", "info", "batch_options"])
def test_all_real_modal_dialog_controls_have_actionable_hover_help(app, method):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QFileDialog, QColorDialog, QAbstractItemView
    from pbnstudio.ui import Dialogs
    seen = []; failures = []
    def inspect_and_cancel():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QDialog)
            assert dialog.testAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
            controls = [w for w in dialog.findChildren(QWidget)
                        if isinstance(w, (QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QAbstractItemView))]
            assert controls
            for control in controls:
                assert_tip(control)
                seen.append((type(control).__name__, control.objectName(), control.toolTip()))
                if control.isVisible():
                    assert "<b>" in show_help(app, control)
            if method == "dirty":
                by_text = {b.text(): b for b in dialog.findChildren(QAbstractButton)}
                assert "потер" in by_text["Не сохранять"].toolTip().lower()
                assert "продолж" in by_text["Сохранить…"].toolTip().lower()
                assert "оста" in by_text["Отмена"].toolTip().lower()
                assert by_text["Отмена"].isDefault()
            if method == "confirm_edit":
                by_text = {b.text(): b for b in dialog.findChildren(QAbstractButton)}
                assert "предпросмотр" in by_text["Применить изменения"].toolTip().lower()
                assert by_text["Не применять"].isDefault()
        except BaseException as error:
            failures.append(error)
        finally:
            QToolTip.hideText()
            if dialog: dialog.reject()
    QTimer.singleShot(0, inspect_and_cancel)
    dialogs = Dialogs()
    if method == "confirm_edit":
        svg = real_document().svg("numbers")
        result = dialogs.confirm_edit(None, "Сравнение", svg, svg, "Областей: 2 → 2")
        assert result is False
    elif method == "confirm_lattice":
        assert dialogs.confirm_lattice(None, "Проверка") is False
    elif method in ("error", "info"):
        getattr(dialogs, method)(None, "Проверка", "Сообщение")
    elif method == "color":
        assert dialogs.color(None, (20, 40, 60)) is None
    else:
        result = getattr(dialogs, method)(None)
        assert result in (None, "", [], "cancel")
    if failures: raise failures[0]
    assert seen


def test_tooltip_is_high_contrast_and_readably_bounded(app):
    from PySide6.QtWidgets import QStyle
    w = MainWindow(dialogs=QuietDialogs()); w.show(); app.processEvents()
    try:
        show_help(app, w.run_button)
        popup = next(x for x in app.topLevelWidgets() if x.metaObject().className() == "QTipLabel" and x.isVisible())
        assert 350 <= popup.width() <= 420
        assert popup.palette().color(popup.backgroundRole()).name() == "#172336"
        assert popup.palette().color(popup.foregroundRole()).name() == "#ffffff"
        assert w.style().styleHint(QStyle.StyleHint.SH_ToolTip_WakeUpDelay) > 0
    finally:
        QToolTip.hideText(); w.close()


def test_split_tool_help_does_not_promise_an_unwired_action(app):
    # Current controller has no tool==4 branch: document API exists, UI gesture doesn't.
    w = MainWindow(dialogs=QuietDialogs())
    try:
        import inspect
        source = inspect.getsource(MainWindow._canvas_clicked)
        if "tool == 4" not in source:
            assert "не подключ" in w.tool_combo.itemData(4, Qt.ItemDataRole.ToolTipRole).lower()
    finally:
        w.close()


def test_disabled_hidden_settings_and_palette_display_without_state_change(app):
    w = MainWindow(dialogs=QuietDialogs()); w.show(); w.print_settings_toggle.click(); app.processEvents()
    try:
        assert not w.leaders_check.isEnabled()
        assert "вынос" in show_help(app, w.leaders_check).lower()
        assert not w.leaders_check.isEnabled() and not w.leaders_check.isChecked()
        for control in (w.font_spin, w.margin_spin, w.page_width_spin, w.line_spin):
            assert show_help(app, control.lineEdit()) == control.toolTip()
        w.set_document(real_document()); app.processEvents()
        swatch = w.palette_layout.itemAt(0).widget()
        assert not swatch.isEnabled()
        assert "выбранную область" in show_help(app, swatch).lower()
        assert not swatch.isEnabled() and w.selected_component is None
    finally:
        QToolTip.hideText(); w.close()


def test_batch_directory_step_has_help_and_cancel_never_starts(app):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QFileDialog, QCheckBox
    from pbnstudio.ui import Dialogs
    seen = []; failures = []
    def folder():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QFileDialog)
            assert dialog.fileMode() == QFileDialog.FileMode.Directory
            assert not dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
            assert not dialog.property("pbnHoverHelp")
            seen.append("folder")
        except BaseException as error: failures.append(error)
        finally: dialog.reject()
    def options():
        dialog = QApplication.activeModalWidget()
        try:
            assert isinstance(dialog, QDialog)
            checks = dialog.findChildren(QCheckBox)
            assert len(checks) == 2 and not any(c.isChecked() for c in checks)
            assert "БЕЗ отдельного предпросмотра" in checks[1].toolTip()
            seen.append("options")
            QTimer.singleShot(0, folder)
            next(b for b in dialog.findChildren(QAbstractButton) if b.text() == "Выбрать папку и начать…").click()
        except BaseException as error:
            failures.append(error); dialog.reject()
    QTimer.singleShot(0, options)
    assert Dialogs().batch_options(None) is None
    if failures: raise failures[0]
    assert seen == ["options", "folder"]


@pytest.mark.parametrize("choice, expected", [("Сохранить…", "save"), ("Не сохранять", "discard"), ("Отмена", "cancel")])
def test_dirty_dialog_buttons_keep_decision_semantics(app, choice, expected):
    from PySide6.QtCore import QTimer
    from pbnstudio.ui import Dialogs
    def choose():
        box = QApplication.activeModalWidget()
        next(b for b in box.findChildren(QAbstractButton) if b.text() == choice).click()
    QTimer.singleShot(0, choose)
    assert Dialogs().dirty(None) == expected


def test_qt_file_and_color_acceptance_return_actual_selection(app, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QColor
    from pbnstudio.ui import Dialogs
    target = tmp_path / "новый-проект.pbn"
    def file_accept():
        dialog = QApplication.activeModalWidget()
        dialog.selectFile(str(target)); dialog.accept()
    QTimer.singleShot(0, file_accept)
    assert Dialogs().save_file(None).replace("\\", "/") == str(target).replace("\\", "/")
    assert not target.exists(), "path selection alone must not create a file"
    def color_accept():
        dialog = QApplication.activeModalWidget()
        dialog.setCurrentColor(QColor(12, 34, 56)); dialog.accept()
    QTimer.singleShot(0, color_accept)
    assert Dialogs().color(None, (200, 200, 200)) == (12, 34, 56)


def test_batch_rows_and_status_remain_rich_after_completion(app, tmp_path):
    from types import SimpleNamespace
    w = MainWindow(dialogs=QuietDialogs())
    try:
        item = SimpleNamespace(source=tmp_path/"input.png", destination=tmp_path/"out", message="Готово", status="exported")
        result = SimpleNamespace(items=[item], report_path=tmp_path/"report.json")
        w._batch_outcome(result, None)
        assert_tip(w.status_label)
        assert "<b>" in w.batch_report_list.item(0).toolTip()
        assert str(result.report_path) in w.status_label.toolTip()
    finally: w.close()


@pytest.mark.parametrize("disabled", [False, True])
def test_repeated_hover_and_mouse_movement_keep_one_stable_anchor(app, monkeypatch, disabled):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    from pbnstudio.tooltips import install, set_tip
    from PySide6.QtWidgets import QPushButton
    window = QWidget(); window.resize(600, 160)
    button = QPushButton("Hover target", window); button.setGeometry(20, 20, 300, 50)
    set_tip(button, "Стабильная подсказка", "Наведение не двигает окно.", "Повторные события не перезапускают подсказку.")
    button.setEnabled(not disabled); install(window)
    calls = []; original = QToolTip.showText
    def record(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(QToolTip, "showText", record)
    window.show(); app.processEvents()
    try:
        positions = [QPoint(10 + i * 5, 15 + i % 3) for i in range(20)]
        popup_rects = []
        for position in positions:
            QTest.mouseMove(button, position)
            QApplication.sendEvent(button, QHelpEvent(QEvent.Type.ToolTip, position, button.mapToGlobal(position)))
            app.processEvents()
            assert QToolTip.isVisible() and QToolTip.text() == button.toolTip()
            popup = next(w for w in app.topLevelWidgets() if w.metaObject().className() == "QTipLabel" and w.isVisible())
            popup_rects.append(popup.geometry())
        assert all(rect == popup_rects[0] for rect in popup_rects), "visible popup must not move during pointer motion"
        assert len(calls) == 1, "same hover target must not restart/move showText"
        assert calls[0][0] == button.mapToGlobal(QPoint(0, button.height() + 6))
        assert button.isEnabled() is not disabled
        # A text change must not be swallowed by the cache.
        set_tip(button, "Новый текст", "Изменившаяся справка.", "Немедленно обновляется.")
        position = positions[-1]
        QApplication.sendEvent(button, QHelpEvent(QEvent.Type.ToolTip, position, button.mapToGlobal(position)))
        app.processEvents()
        assert len(calls) == 2 and QToolTip.text() == button.toolTip()
    finally:
        QToolTip.hideText(); window.close()


@pytest.mark.parametrize("method", ["open_file", "batch_files", "save_file", "export_dir", "color"])
def test_os_dialogs_request_native_ui_without_application_tooltip_ownership(app, monkeypatch, method):
    from PySide6.QtWidgets import QFileDialog, QColorDialog, QDialog
    from pbnstudio.ui import Dialogs
    seen = []
    def inspect(dialog):
        if isinstance(dialog, QFileDialog):
            assert not dialog.testOption(QFileDialog.Option.DontUseNativeDialog)
            expected = {"open_file": QFileDialog.FileMode.ExistingFile,
                        "batch_files": QFileDialog.FileMode.ExistingFiles,
                        "save_file": QFileDialog.FileMode.AnyFile,
                        "export_dir": QFileDialog.FileMode.AnyFile}
            assert dialog.fileMode() == expected[method]
            assert dialog.acceptMode() == (QFileDialog.AcceptMode.AcceptSave if method in ("save_file", "export_dir") else QFileDialog.AcceptMode.AcceptOpen)
        else:
            assert not dialog.testOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
            assert dialog.currentColor().getRgb()[:3] == (20, 40, 60)
        assert not dialog.property("pbnHoverHelp")
        seen.append(dialog)
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(QFileDialog, "exec", inspect)
    monkeypatch.setattr(QColorDialog, "exec", inspect)
    result = Dialogs().color(None, (20, 40, 60)) if method == "color" else getattr(Dialogs(), method)(None)
    assert result in (None, "", []) and len(seen) == 1


def test_spin_editor_and_arrows_share_target_but_reentry_can_show_again(app, monkeypatch):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    window = MainWindow(dialogs=QuietDialogs()); window.show(); app.processEvents()
    calls = []; original = QToolTip.showText
    def record(*args):
        calls.append(args); return original(*args)
    monkeypatch.setattr(QToolTip, "showText", record)
    try:
        for widget in (window.colors_spin, window.colors_spin.lineEdit(), window.colors_spin):
            position = widget.rect().center()
            QApplication.sendEvent(widget, QHelpEvent(QEvent.Type.ToolTip, position, widget.mapToGlobal(position)))
            app.processEvents()
        assert len(calls) == 1 and calls[0][2] is window.colors_spin
        # Actual dismissal must not leave a stale cache suppressing reentry.
        QToolTip.hideText(); QTest.qWait(350); app.processEvents()
        assert not QToolTip.isVisible()
        show_help(app, window.colors_spin)
        assert len(calls) == 2
        # Another logical target gets its own fixed anchor.
        show_help(app, window.speckle_spin)
        assert len(calls) == 3 and calls[-1][2] is window.speckle_spin
        assert calls[-1][0] == window.speckle_spin.mapToGlobal(QPoint(0, window.speckle_spin.height() + 6))
    finally:
        QToolTip.hideText(); window.close()
