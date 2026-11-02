# SPDX-License-Identifier: GPL-3.0-only
"""Responsive palette, visual states and native UI regression checks."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QPushButton
from test_ui import app, QuietDialogs, FixtureDocument, real_document
from pbnstudio.ui import MainWindow


def swatches(window):
    return [window.palette_layout.itemAt(i).widget() for i in range(window.palette_layout.count())
            if isinstance(window.palette_layout.itemAt(i).widget(), QPushButton)]


def settle(app):
    for _ in range(5): app.processEvents()


def test_palette_wraps_on_resize_and_has_no_scrollbar_even_for_one_row(app):
    window = MainWindow(dialogs=QuietDialogs())
    doc = FixtureDocument()
    doc.palette = [(i * 4, 120, 180) for i in range(64)]
    window.set_document(doc); window.resize(1320, 880); window.show(); settle(app)
    try:
        buttons = swatches(window)
        assert len(buttons) == 64
        wide_rows = {b.geometry().top() for b in buttons}
        assert len(wide_rows) >= 2, "palette must wrap instead of scrolling a single strip"
        window.resize(960, 760); settle(app)
        narrow_rows = {b.geometry().top() for b in buttons}
        assert len(narrow_rows) > len(wide_rows)
        host = buttons[0].parentWidget()
        for button in buttons:
            assert button.width() >= 40 and button.height() >= 40
            assert host.rect().contains(button.geometry()), (host.rect(), button.geometry())
        for i, button in enumerate(buttons):
            assert not any(button.geometry().intersects(other.geometry()) for other in buttons[i+1:])
        for area in window.findChildren(QScrollArea):
            assert not area.verticalScrollBar().isVisible()
            assert not area.horizontalScrollBar().isVisible()
        doc.palette = doc.palette[:3]; window.render_document(); settle(app)
        buttons = swatches(window)
        assert len({b.y() for b in buttons}) == 1
        assert buttons[0].parentWidget().height() <= 64
        for area in window.findChildren(QScrollArea):
            assert not area.verticalScrollBar().isVisible()
    finally:
        window.close()


def luminance(rgb):
    channels = [c / 255 for c in rgb]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))


def test_swatches_are_round_readable_accessible_and_track_selected_region(app):
    from PySide6.QtGui import QPalette, QAccessible
    from PySide6.QtTest import QTest
    dialogs = QuietDialogs(); dialogs.choice = "discard"
    window = MainWindow(dialogs=dialogs); window.set_document(real_document())
    window.show(); settle(app)
    try:
        for button in swatches(window):
            assert button.isCheckable()
            assert button.focusPolicy() == Qt.FocusPolicy.StrongFocus
            assert "RGB" in button.accessibleName() and button.text() in button.accessibleName()
            assert "выбранную область" in button.accessibleDescription()
            assert not button.isChecked() and not button.isEnabled()
        window.select_component_at(4, 4); settle(app)
        selected_color = tuple(window.document.component_colors[window.selected_component])
        assert [b.isChecked() for b in swatches(window)] == [tuple(rgb) == selected_color for rgb in window.document.palette]
        button = next(b for b in swatches(window) if not b.isChecked())
        before = dict(window.document.component_colors)
        target_color = tuple(window.document.palette[int(button.text()) - 1])
        button.setFocus(); settle(app)
        assert button.hasFocus()
        interface = QAccessible.queryAccessibleInterface(button)
        assert interface.state().focusable and interface.state().checkable
        # Real keyboard activation must retain the existing region-only handler.
        QTest.keyClick(button, Qt.Key.Key_Space); settle(app)
        assert window.document.component_colors[1] == target_color
        assert all(window.document.component_colors[k] == v for k, v in before.items() if k != 1)
        checked = [b for b in swatches(window) if b.isChecked()]
        assert len(checked) == 1 and checked[0].hasFocus()
        window.undo_button.click(); settle(app)
        assert window.document.component_colors == before
    finally:
        window.close()


def test_swatch_contrast_and_round_focus_rendering(app):
    from pbnstudio.ui import PaletteSwatch
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QWidget
    window = QWidget(); window.resize(100, 100)
    try:
        for rgb in [(0,0,0), (255,255,255), (255,0,0), (0,140,0), (120,120,120), (255,255,0), (30,60,240)]:
            button = PaletteSwatch(64, rgb, window); button.move(10, 10)
            window.show(); button.show(); button.setFocus(); settle(app)
            fg = button.palette().color(QPalette.ColorRole.ButtonText).getRgb()[:3]
            hi, lo = sorted((luminance(rgb), luminance(fg)), reverse=True)
            assert (hi + .05) / (lo + .05) >= 4.5
            button.clearFocus(); settle(app)
            plain = button.grab().toImage()
            center = plain.pixelColor(button.width() // 2, 10)
            corner = plain.pixelColor(0, 0)
            assert center.getRgb()[:3] == rgb
            assert corner != center, "swatches must have round corners, not a rectangular fill"
            button.setChecked(True); settle(app)
            selected = button.grab().toImage()
            assert selected != plain
            button.setFocus(); settle(app)
            assert button.grab().toImage() != selected, "keyboard focus must remain distinct from selection"
            button.setParent(None); button.deleteLater(); settle(app)
    finally:
        window.close()


def test_controls_have_comfortable_targets_and_distinct_toggle_focus_states(app):
    window = MainWindow(dialogs=QuietDialogs()); window.show(); settle(app)
    try:
        for control in (window.open_button, window.colors_spin, window.simplify_spin, window.tool_combo):
            assert control.height() >= 30
        button = window.help_toggle
        button.clearFocus(); settle(app)
        plain = button.grab().toImage()
        button.setChecked(True); settle(app)
        checked = button.grab().toImage()
        assert checked != plain
        button.setFocus(); settle(app)
        assert button.grab().toImage() != checked
    finally:
        window.close()
