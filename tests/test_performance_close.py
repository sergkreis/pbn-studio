# SPDX-License-Identifier: GPL-3.0-only
import threading,time
from test_ui import app,QuietDialogs,real_document,drain_until
from pbnstudio.ui import MainWindow
from pbnstudio.engine import EngineCancelled

def test_close_requests_cooperative_cancel_and_keeps_event_loop_alive(app):
    dialogs=QuietDialogs();dialogs.choice='discard'
    w=MainWindow(dialogs=dialogs);old=real_document();w.set_document(old);w.show()
    entered=threading.Event();stopped=threading.Event();ticks=[]
    from PySide6.QtCore import QTimer
    timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(1));timer.start()
    def operation(*,cancel_event,progress):
        entered.set();progress('leaders',1,500)
        try:
            if not cancel_event.wait(2):raise AssertionError('cancel not delivered')
            time.sleep(.05)
            raise EngineCancelled('cancelled')
        finally:stopped.set()
    assert w._start_document_task(operation,lambda result:None)
    deadline=time.monotonic()+1
    while not entered.is_set() and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
    assert entered.is_set()
    start=time.monotonic();w.close()
    assert time.monotonic()-start<.2
    drain_until(app,lambda:not w.is_processing)
    timer.stop()
    assert stopped.is_set() and ticks and not w.isVisible()
    assert w.document is old and not dialogs.messages


def test_simplify_button_stage_cancel_and_journal(app,monkeypatch,tmp_path):
    from test_print_edit import small_cells
    from pbnstudio.document import Document
    from pbnstudio import diagnostics
    events=[];monkeypatch.setattr(diagnostics,'event',lambda name,**fields:events.append((name,fields)))
    dialogs=QuietDialogs();dialogs.choice='discard';dialogs.confirm_edit=lambda *a:True
    w=MainWindow(dialogs=dialogs);old=small_cells(tmp_path);w.set_document(old)
    entered=threading.Event()
    original=Document.simplify_for_print
    def observed(self,**kwargs):
        from pbnstudio.geometry import checkpoint
        entered.set()
        while True:
            checkpoint('leaders',1,500);time.sleep(.005)
    monkeypatch.setattr(Document,'simplify_for_print',observed)
    w.simplify_button.click()
    deadline=time.monotonic()+2
    while not entered.is_set() and time.monotonic()<deadline:app.processEvents();time.sleep(.005)
    assert entered.is_set()
    app.processEvents();w.cancel_button.click();drain_until(app,lambda:not w.is_processing)
    assert w.document is old and not dialogs.messages
    names=[n for n,f in events]
    assert 'job.stage' in names and 'job.cancel_request' in names and 'job.cancelled' in names
    w.close()


def test_candidate_simplify_does_not_repeat_prepared_label_pass(app,monkeypatch,tmp_path):
    from test_print_edit import small_cells
    from pbnstudio.document import Document
    dialogs=QuietDialogs();dialogs.choice='discard';dialogs.confirm_edit=lambda *a:False
    w=MainWindow(dialogs=dialogs);w.set_document(small_cells(tmp_path))
    calls=[];original=Document.prepare_labels
    def observed(self,*a,**k):calls.append(self);return original(self,*a,**k)
    monkeypatch.setattr(Document,'prepare_labels',observed)
    w.simplify_button.click();drain_until(app,lambda:not w.is_processing)
    # One rebuilt candidate label pass, one explicit leaders pass; not a third UI pass.
    assert len(calls)==2
    w.close()
