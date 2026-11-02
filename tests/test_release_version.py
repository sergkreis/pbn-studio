# SPDX-License-Identifier: GPL-3.0-only
import tomllib, importlib.util
from pathlib import Path
from pbnstudio import __version__

def test_version_consistent_across_metadata_ui_and_packager():
    root=Path(__file__).parents[1]
    assert __version__=="0.2.1rc2"
    assert tomllib.loads((root/"pyproject.toml").read_text())["project"]["version"]==__version__
    from pbnstudio import RELEASE_VERSION
    assert RELEASE_VERSION=="0.2.1-rc.2"
    from pbnstudio.geometry import ensure_app
    from pbnstudio.ui import MainWindow
    ensure_app();w=MainWindow();assert RELEASE_VERSION in w.windowTitle();w.close()
    spec=importlib.util.spec_from_file_location("version_packager",root/"build/package_windows.py")
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert m.NAME=="PBN-Studio-"+RELEASE_VERSION
