# SPDX-License-Identifier: GPL-3.0-only
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from test_ui import app, QuietDialogs
from pbnstudio.ui import MainWindow
from PySide6.QtWidgets import QApplication, QToolTip, QWidget, QSpinBox
from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QTest
from pbnstudio.tooltips import install, set_tip


def test_empty_canvas_and_list_space_never_inherit_help(app):
    w=MainWindow(dialogs=QuietDialogs());w.show();app.processEvents()
    try:
        for widget in (w.original_view,w.result_view,w.issue_list,w.batch_report_list):
            assert not widget.toolTip()
            pos=widget.viewport().rect().center()
            QToolTip.hideText();QTest.qWait(350)
            QApplication.sendEvent(widget.viewport(),QHelpEvent(QEvent.Type.ToolTip,pos,widget.viewport().mapToGlobal(pos)))
            assert not QToolTip.isVisible()
        assert w.status_label.toolTip() and w.preflight_label.toolTip()
    finally: QToolTip.hideText();w.close()


@pytest.mark.parametrize("disabled", [False, True])
def test_real_child_enter_hover_has_no_repeated_popup_restart(app,monkeypatch,disabled):
    # Real QWidget mouse/enter/leave dispatch, without synthetic QHelpEvent.
    w=QWidget();w.resize(500,180)
    spin=QSpinBox(w);spin.setGeometry(20,20,180,40);spin.setEnabled(not disabled)
    set_tip(spin,"Номер","Размер номера.","Применяется к печати.");install(w)
    calls=[];original=QToolTip.showText
    def show(*args): calls.append(args);return original(*args)
    monkeypatch.setattr(QToolTip,"showText",show)
    w.show();app.processEvents()
    try:
        QTest.mouseMove(w,QPoint(450,140));QTest.qWait(350)
        for _ in range(3):
            QTest.mouseMove(spin.lineEdit(),QPoint(10,10));QTest.qWait(850)
            QTest.mouseMove(spin,QPoint(170,10));QTest.qWait(850)
        assert calls, "native hover timer must actually deliver ToolTip"
        assert len(calls)==1, f"child transitions restarted popup {len(calls)} times"
    finally:QToolTip.hideText();w.close()


def test_real_hover_exit_click_and_return_dismiss_and_reopen(app,monkeypatch):
    from PySide6.QtCore import Qt
    w=QWidget();w.resize(500,180);spin=QSpinBox(w);spin.setGeometry(20,20,180,40)
    set_tip(spin,"Номер","Размер номера.","Применяется к печати.");install(w)
    calls=[];original=QToolTip.showText
    def show(*args):calls.append(args);return original(*args)
    monkeypatch.setattr(QToolTip,"showText",show)
    w.show();app.processEvents()
    try:
        QTest.mouseMove(spin.lineEdit(),QPoint(10,10));QTest.qWait(900)
        assert QToolTip.isVisible()
        QTest.mouseMove(w,QPoint(450,140));QTest.qWait(400)
        assert not QToolTip.isVisible()
        QTest.mouseMove(spin,QPoint(170,10));QTest.qWait(900)
        assert QToolTip.isVisible() and len(calls)==2
        QTest.mouseClick(spin,Qt.MouseButton.LeftButton,pos=QPoint(170,10));QTest.qWait(400)
        assert not QToolTip.isVisible()
    finally:QToolTip.hideText();w.close()


def test_unmanaged_windows_do_not_have_tooltip_events_intercepted(app):
    from PySide6.QtWidgets import QListWidget
    from pbnstudio.tooltips import HoverHelp
    other=QListWidget();pos=QPoint(1,1)
    event=QHelpEvent(QEvent.Type.ToolTip,pos,pos)
    assert not HoverHelp().eventFilter(other.viewport(),event)
    other.close()
