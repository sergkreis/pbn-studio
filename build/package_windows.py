# SPDX-License-Identifier: GPL-3.0-only
"""Reproducible Windows onedir candidate; no installation or source mutation."""
from pathlib import Path
import argparse, hashlib, importlib.metadata, json, os, shutil, subprocess, sys, time, zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pbnstudio import RELEASE_VERSION
NAME = "PBN-Studio-" + RELEASE_VERSION

README_RU = 'PBN Studio — тестовая сборка с журналом и исправлениями удобства\n\nЗапуск PBN-Studio-0.2.exe, папку _internal оставить рядом.\nВсплывающие подсказки закреплены у элемента; палитра переносится рядами.\nОткрытие/сохранение/выбор цвета используют системные окна Windows.\nПри обработке показываются этап и время; Отменить прекращает вычисления\nна ближайшей проверке отмены. Закрытие запрашивает остановку, не убивает поток.\nНекоторые вызовы Qt/NumPy не прерываются посередине; скорость зависит от изображения.\nЖУРНАЛ: %LOCALAPPDATA%\\PBNStudio\\logs — JSONL, ротация, действия/этапы/ошибки/стэки.\nИзображения, содержимое проектов и секреты в журнал не включаются.\nДля отключения подробной диагностики: PBN_DIAGNOSTICS=0 перед запуском.\nПока это диагностическая версия; физическая проверка печати не выполнена.\nФормат Letter/A4 не заменять A0 ради успешной проверки; A0 — плакат, не KDP.\nСложное изображение может сохранять замечания после упрощения.\nУпростить для печати меняет мелкие области только после предпросмотра/подтверждения.\nРанее пример имел51замечание; это не фиксированное число для всех картинок.\nТочные пиксельные границы — запасной режим со ступеньками.\nЭкспорт: SVG/PDF векторные, PNG150dpi; проекты сохраняют master и правки.\nПолный набор тестов имеет известное неверное ожидание fixture9 вместо25пикселей.\nНужны системный Arial и VC runtime; сборка unsigned, на чистой Windows не проверена.\nЛицензии в LEGAL и _internal/assets/worker; публичное распространение отдельно проверяется.\n'

def build_command(root, output):
    return [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onedir", "--windowed", "--name", NAME,
            "--distpath", str(output/"dist"), "--workpath", str(output/"work"),
            "--specpath", str(output/"spec"), "--paths", str(root),
            "--add-data", str(root/"assets/worker")+os.pathsep+"assets/worker",
            "--collect-submodules", "pbnstudio",
            "--hidden-import", "PySide6.QtPdf",
            "--collect-all", "scipy", "--collect-all", "PIL",
            "--hidden-import", "PySide6.QtSvgWidgets", "--hidden-import", "PySide6.QtPrintSupport",
            str(root/"launch.py")]

def collect_licenses(portable):
    legal=portable/"LEGAL"; legal.mkdir()
    versions={}
    for package in ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "numpy", "scipy", "Pillow", "PyInstaller"):
        dist=importlib.metadata.distribution(package); versions[package]=dist.version
        target=legal/package; target.mkdir()
        (target/"METADATA.txt").write_text(dist.read_text("METADATA") or "", encoding="utf-8")
        for entry in dist.files or []:
            if any(word in entry.name.lower() for word in ("license", "copying", "copyright", "notice")):
                source=Path(dist.locate_file(entry))
                if source.is_file():
                    destination=target/str(entry).replace("..", "__")
                    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
    base=Path(sys.base_prefix)
    for name in ("LICENSE.txt", "LICENSE"):
        if (base/name).is_file():shutil.copy2(base/name, legal/("Python-"+name))
    (legal/"NOTICE.txt").write_text(
        f"PBN Studio {RELEASE_VERSION} portable verification candidate.\n"
        "Python, PySide6/Qt, Shiboken, NumPy, SciPy, Pillow and PyInstaller are third-party software; see adjacent license texts/metadata.\n"
        "Qt/PySide6 libraries are dynamically linked and replaceable in _internal. LGPL rights, including reverse engineering for debugging modifications, are not restricted.\n"
        "Upstream source: https://download.qt.io/official_releases/QtForPython/ and https://download.qt.io/official_releases/qt/ .\n"
        "Native worker source pin, dependency licenses and provenance are in _internal/assets/worker.\n"
        "Arial is a Windows system prerequisite, NOT bundled or relicensed. No system fonts are copied.\n"
        "Redistribution legal review and exact corresponding Qt source provision remain release-owner responsibilities.\n",encoding="utf-8")
    return versions

def verify_release(smoke, archive):
    expected = {"status":"passed", "frozen":True, "diagnostic":False, "issues":0,
                "export_count":9, "geometry_mode":"native", "ui_fallback_confirmations":0,
                "reopened_geometry_mode":"native", "rerun_geometry_mode_before_fallback":"native",
                "smooth_validation_verified":True,
                "normal_vector_pdf_verified":True, "png_readback_verified":True, "ui_offscreen":True}
    if any(smoke.get(key) != value for key, value in expected.items()):
        raise ValueError("Frozen verification must demonstrate validated native geometry and normal vector exports")
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None: raise ValueError("ZIP CRC verification failed")
    return {"zip_crc":"passed", "zip_sha256":hashlib.sha256(archive.read_bytes()).hexdigest()}

def build(root, output):
    root=Path(root).resolve();output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    env=os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH"): env.pop(key,None)
    env["QT_QPA_PLATFORM"]="offscreen"
    command=build_command(root,output)
    (output/"command.json").write_text(json.dumps(command,indent=2),encoding="utf-8")
    sources={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in [*sorted((root/"pbnstudio").glob("*.py")),root/"launch.py",root/"build/package_windows.py",root/"assets/worker/pbn_worker.exe"]}
    with (output/"pyinstaller.log").open("w",encoding="utf-8") as log:
        result=subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode: raise RuntimeError("PyInstaller failed; see pyinstaller.log")
    portable=output/"dist"/NAME
    versions=collect_licenses(portable)
    (portable/"README-PORTABLE-RU.txt").write_text(README_RU.replace("PBN-Studio-0.2.exe", NAME+".exe"), encoding="utf-8")
    # Launch from an unrelated directory without a development PYTHONPATH.
    smoke=output/"smoke"
    result=subprocess.run([str(portable/(NAME+".exe")),"--smoke-test-dir",str(smoke)],cwd=output,env=env,timeout=180)
    if result.returncode or not (smoke/"receipt.json").is_file():
        raise RuntimeError("Frozen native smoke failed; inspect smoke/failure.txt")
    smoke_receipt=json.loads((smoke/"receipt.json").read_text(encoding="utf-8"))
    if not smoke_receipt.get("frozen") or smoke_receipt.get("status")!="passed":
        raise RuntimeError("Frozen verification receipt invalid")
    archive=Path(shutil.make_archive(str(output/(NAME+"-portable")),"zip",root_dir=portable.parent,base_dir=portable.name))
    zip_verification=verify_release(smoke_receipt,archive)
    changed=[name for name,digest in sources.items() if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest]
    if changed: raise RuntimeError("Sources changed during packaging: " + str(changed))
    receipt={"status":"passed", **zip_verification, "source_changed_during_build":changed,"source_sha256":sources,"versions":versions,
             "portable":str(portable),"zip":str(archive),"zip_bytes":archive.stat().st_size,
             "smoke":smoke_receipt,
             "limits":["unsigned candidate", "not tested on clean Windows VM", "Arial system font prerequisite", "release legal review required"]}
    (output/"build-receipt.json").write_text(json.dumps(receipt,indent=2),encoding="utf-8")
    return receipt

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True,help="New output directory; existing directories refused")
    args=parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1],args.output),indent=2))
