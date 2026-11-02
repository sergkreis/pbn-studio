# SPDX-License-Identifier: GPL-3.0-only
"""Public startup must bound native BLAS pools before lazy SciPy imports."""
import os
from pathlib import Path
import subprocess
import sys


def test_public_startup_bounds_blas_threads_before_document_import():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    for name in ("PYTHONHOME", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
        env.pop(name, None)
    env["PYTHONPATH"] = str(root)
    script = r'''import ctypes, pathlib, os
from pbnstudio.__main__ import main
try:
    main(["--help"])
except SystemExit as error:
    assert error.code == 0
# Real extension initialization, not an environment-only assertion.
import scipy.special
for library in (pathlib.Path(scipy.__file__).parent.parent / "scipy.libs").glob("*openblas*.dll"):
    dll = ctypes.CDLL(str(library))
    count = dll.scipy_openblas_get_num_threads()
    assert count == 1, f"SciPy started {count} BLAS threads before first document"
assert os.environ.get("OPENBLAS_NUM_THREADS") == "1"
print("bounded native import passed")
'''
    result = subprocess.run([sys.executable, "-c", script], env=env, cwd=root,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_startup_preserves_explicit_thread_budget():
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.update(PYTHONPATH=str(root), OPENBLAS_NUM_THREADS="2", OMP_NUM_THREADS="3")
    script = """import os
from pbnstudio.__main__ import main
try: main(['--help'])
except SystemExit: pass
assert os.environ['OPENBLAS_NUM_THREADS'] == '2'
assert os.environ['OMP_NUM_THREADS'] == '3'
"""
    result = subprocess.run([sys.executable, "-c", script], env=env, cwd=root,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_shown_full_recraft_project_public_startup_is_responsive(tmp_path):
    import pytest
    root = Path(__file__).resolve().parents[1]
    project = root / "reports/book-complete/Recraft-Letter7pt.pbn"
    if not project.is_file():
        pytest.skip("full Recraft acceptance project is not present")
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("OPENBLAS_NUM_THREADS", None)
    env.pop("OMP_NUM_THREADS", None)
    env.update(PYTHONPATH=str(root), QT_QPA_PLATFORM="offscreen")
    script = r'''import sys, faulthandler
faulthandler.enable()
faulthandler.dump_traceback_later(12, repeat=True)
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from pbnstudio.__main__ import main
app = QApplication([])
ticks = []
def poll():
    for window in app.topLevelWidgets():
        if getattr(window, "document", None) is not None:
            ticks.append(window)
            if len(ticks) == 10:
                assert window.isVisible()
                assert window.grab().save(sys.argv[2])
                app.quit()
timer = QTimer()
timer.setInterval(50)
timer.timeout.connect(poll)
timer.start()
assert main([sys.argv[1]]) == 0
assert len(ticks) == 10
window = ticks[-1]
assert len(window.document.labels) == 96
assert not window.preflight_issues
assert window.original_view.scene().items()
assert window.result_view.scene().items()
faulthandler.cancel_dump_traceback_later()
'''
    result = subprocess.run([sys.executable, "-c", script, str(project), str(tmp_path / "shown.png")],
                            env=env, cwd=root, capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
