# SPDX-License-Identifier: GPL-3.0-only
"""Source and packaged entry point; importing never starts Qt."""
from __future__ import annotations
from . import RELEASE_VERSION, __version__
import argparse
import sys

def main(argv=None):
    from . import diagnostics
    diagnostics.initialize()  # stdlib only; precedes Qt and scientific imports
    import os
    # This desktop pipeline does not need a BLAS pool. Initialize its budget
    # before lazy NumPy/SciPy imports: on Windows exhausted worker creation
    # can block inside OpenBLAS DLL initialization, before Qt can paint again.
    # Preserve a deliberately configured deployment budget.
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    parser = argparse.ArgumentParser(description=f"PBN Studio {RELEASE_VERSION} — раскраски по номерам, локальная обработка")
    parser.add_argument("path", nargs="?", help="Изображение или проект .pbn")
    parser.add_argument("--smoke-test-dir", help="Проверка обработки, проекта и экспорта без видимого окна, в новой папке")
    args = parser.parse_args(argv)
    if args.smoke_test_dir:
        import os
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        return smoke_test(args.smoke_test_dir)
    with diagnostics.span("startup.qt_import"):
        from PySide6.QtCore import QTimer
        diagnostics.install_qt_handler()
        from PySide6.QtWidgets import QApplication
    with diagnostics.span("startup.ui_import"):
        from .ui import MainWindow
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("PBN Studio")
    app.setOrganizationName("PBN Studio")
    app.setApplicationVersion(__version__)
    window = MainWindow()
    window.show()
    if args.path: QTimer.singleShot(0, lambda: window.load_path(args.path))
    diagnostics.event("startup.ready")
    result = app.exec()
    diagnostics.event("application.exit", code=result)
    diagnostics.shutdown()
    return result

def smoke_test(destination):
    """No visible window. Exercise bundled imports and the actual native binary."""
    from . import diagnostics
    diagnostics.initialize()
    from PySide6 import QtCore
    diagnostics.install_qt_handler()
    from pathlib import Path
    import hashlib, json, time, traceback
    from PIL import Image, ImageDraw
    from PySide6.QtWidgets import QApplication
    from .ui import MainWindow
    from .engine import WORKER_PATH
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    class SmokeDialogs:
        messages = []
        confirmations = 0
        def confirm_lattice(self, parent, text):
            assert "ступен" in text.lower() and "крив" in text.lower()
            self.confirmations += 1
            return True  # Explicit opt-in solely for this documented tiny smoke fixture.
        def dirty(self, parent): return "discard"
        def error(self, parent, title, text): self.messages.append((title, text))
        info = error
    dialogs = SmokeDialogs()
    window = MainWindow(dialogs=dialogs)
    def wait():
        deadline = time.monotonic()+90
        while window.is_processing and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.005)
        if window.is_processing:
            window.cancel_processing()
            # Never destroy a running QThread, including failure reporting.
            while window.is_processing: app.processEvents(); time.sleep(.01)
            raise TimeoutError("smoke job exceeded 90 seconds")
        if dialogs.messages: raise RuntimeError(str(dialogs.messages))
    try:
        source=output/"source.png"
        image=Image.new("RGB",(48,32),"white")
        ImageDraw.Draw(image).rectangle((8,8,30,24),fill="#aa5511")
        image.save(source)
        window.simplify_spin.setValue(0); window.colors_spin.setValue(4)
        assert window.load_path(source); wait()
        assert window.document is not None and window.document.dirty
        window.font_spin.setValue(8)
        assert window.start_print_settings(); wait()
        assert window.document.settings.font_pt==8
        assert window.document.geometry_mode == "native"
        assert window.document.topology_certified and not window.document.issues
        assert window.document.smooth_report["valid"]
        assert window.save_to(output/"project.pbn")
        source.unlink()
        assert window.load_path(output/"project.pbn")
        assert not window.document.dirty
        reopened_geometry_mode = window.document.geometry_mode
        assert reopened_geometry_mode == "native"
        assert window.start_processing(); wait()
        assert window.document.source_bytes and window.document.dirty
        rerun_geometry_mode = window.document.geometry_mode
        assert rerun_geometry_mode == "native", "reprocessing must not silently opt in"
        assert window.document.topology_certified and not window.document.issues
        assert window.document.smooth_report["valid"]
        diagnostic = False
        assert window.start_export(output/"bundle", diagnostic=diagnostic), dialogs.messages; wait()
        files=sorted((output/"bundle").iterdir())
        assert len(files)==9
        assert not window.isVisible()
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtCore import QSize
        from xml.etree import ElementTree as ET
        import re
        for stem in ("answer", "numbered", "palette"):
            svg = output/"bundle"/(stem+".svg")
            assert "DIAGNOSTIC" not in svg.read_text(encoding="utf-8")
            assert not any(element.tag.endswith("}image") for element in ET.parse(svg).iter())
            pdf_path = output/"bundle"/(stem+".pdf")
            assert not re.search(rb"/Subtype\s*/Image\b", pdf_path.read_bytes())
            pdf = QPdfDocument()
            assert pdf.load(str(pdf_path)) == QPdfDocument.Error.None_
            assert pdf.pageCount() == 1 and not pdf.render(0, QSize(300,400)).isNull()
            size = pdf.pagePointSize(0)
            assert abs(size.width()-window.document.settings.width_mm/25.4*72) < 1
            assert abs(size.height()-window.document.settings.height_mm/25.4*72) < 1
            pdf.close()
            with Image.open(output/"bundle"/(stem+".png")) as png:
                assert png.size == (round(window.document.settings.width_mm/25.4*150), round(window.document.settings.height_mm/25.4*150))
                png.verify()
        receipt={"status":"passed", "frozen":bool(getattr(sys,"frozen",False)),
                 "worker_sha256":hashlib.sha256(WORKER_PATH.read_bytes()).hexdigest(),
                 "geometry_mode":window.document.geometry_mode,
                 "ui_fallback_confirmations":dialogs.confirmations,
                 "reopened_geometry_mode":reopened_geometry_mode,
                 "rerun_geometry_mode_before_fallback":rerun_geometry_mode,
                 "smooth_validation_verified":True,
                 "normal_vector_pdf_verified":True, "png_readback_verified":True,
                 "ui_offscreen":not window.isVisible(),
                 "export_count":len(files), "diagnostic":diagnostic, "width":window.document.width,
                 "height":window.document.height, "issues":len(window.document.issues),
                 "files":{p.name:{"bytes":p.stat().st_size,"sha256":hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}}
        (output/"receipt.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
        return 0
    except Exception:
        (output/"failure.txt").write_text(traceback.format_exc(),encoding="utf-8")
        return 1
    finally:
        window.close()

if __name__ == "__main__":
    raise SystemExit(main())
