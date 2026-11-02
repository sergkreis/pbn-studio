# SPDX-License-Identifier: GPL-3.0-only
"""Offline sequential batch pipeline; each native job and export is isolated."""
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import tempfile
import threading
import uuid


@dataclass
class BatchItem:
    source: Path
    status: str
    destination: Path | None = None
    message: str = ""
    issues: list = field(default_factory=list)


@dataclass
class BatchResult:
    items: list[BatchItem]
    cancelled: bool
    report_path: Path
    options: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    diagnostic: bool = False
    auto_simplify: bool = False


def run_batch(sources, destination, options=None, *, settings=None, diagnostic=False,
              auto_simplify=False, cancel_event=None, progress=None):
    from .engine import run_image, EngineCancelled, EngineOptions
    from .document import Document, PrintSettings
    from .exports import export_bundle, preflight
    if auto_simplify and not callable(getattr(Document, "simplify_for_print", None)):
        raise ValueError("Автоматическое упрощение недоступно в этой версии")
    options = options or EngineOptions()
    settings = settings or PrintSettings()
    sources = [Path(source) for source in sources]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    cancel_event = cancel_event or threading.Event()
    progress = progress or (lambda payload: None)
    items = []
    for index, source in enumerate(sources):
        if cancel_event.is_set():
            items.append(BatchItem(source, "cancelled", message="Не обработан: отмена"))
            continue
        try:
            with tempfile.TemporaryDirectory(prefix=".pbn-batch-", dir=destination) as temporary:
                root = Path(temporary)
                progress({"phase": "batch", "fraction": index/len(sources), "file": source.name,
                          "index": index+1, "total": len(sources)})
                job = run_image(source, root/"native", options, cancel_event=cancel_event,
                                progress=lambda payload: progress({**payload, "file": source.name,
                                    "index": index+1, "total": len(sources),
                                    "fraction": (index+0.7*(payload.get("fraction") or 0))/len(sources)}))
                if cancel_event.is_set(): raise EngineCancelled()
                document = Document.load(job)
                if callable(getattr(document, "validate_smooth_geometry", None)):
                    document.validate_smooth_geometry(repair=False)
                document.prepare_labels(settings)
                progress({"phase": "prepared", "fraction": (index+0.8)/len(sources),
                          "file": source.name, "index": index+1, "total": len(sources)})
                if cancel_event.is_set(): raise EngineCancelled()
                issues = [asdict(issue) for issue in preflight(document)]
                if issues and not diagnostic:
                    items.append(BatchItem(source, "blocked", message=f"Экспорт заблокирован: замечаний {len(issues)}", issues=issues))
                    continue
                target = destination/(source.stem+"-"+uuid.uuid4().hex)
                files = export_bundle(document, root/"bundle", diagnostic=diagnostic)
                if not files or not all(Path(path).is_file() for path in files):
                    raise ValueError("Экспорт не создал комплект файлов")
                if cancel_event.is_set(): raise EngineCancelled()
                if target.exists(): raise FileExistsError(target)
                (root/"bundle").rename(target)
                items.append(BatchItem(source, "exported", target,
                    "Диагностический комплект — НЕ ДЛЯ ПЕЧАТИ" if diagnostic else "Комплект экспортирован", issues))
        except EngineCancelled:
            cancel_event.set()
            items.append(BatchItem(source, "cancelled", message="Обработка отменена; комплект не опубликован"))
        except Exception as error:
            items.append(BatchItem(source, "error", message="Не удалось обработать файл: проверьте изображение и доступ к папке"))
            # Technical exception is retained in the machine-readable report only.
            items[-1].issues.append({"code": type(error).__name__, "message": str(error)})
    progress({"phase": "complete", "fraction": 1, "total": len(sources)})
    report_path = destination/("Отчёт-"+uuid.uuid4().hex+".json")
    result = BatchResult(items, cancel_event.is_set(), report_path, asdict(options), asdict(settings), diagnostic, auto_simplify)
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(asdict(result), stream, ensure_ascii=False, indent=2, default=str)
    return result
