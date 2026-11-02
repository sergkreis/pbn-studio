# SPDX-License-Identifier: GPL-3.0-only
"""Russian desktop workspace. Domain/engine imports are lazy, not substituted."""
from __future__ import annotations
from . import RELEASE_VERSION
from pathlib import Path
import threading
import tempfile
import shutil
import copy
import time
import inspect
from . import diagnostics
from PySide6.QtCore import Qt, Signal, QPointF, QRectF, QByteArray, QThread, QStandardPaths, QTimer, QPoint, QRect, QSize
from PySide6.QtGui import QColor, QPainter, QImage, QPixmap, QTransform, QPen, QBrush, QPalette
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QDoubleSpinBox, QTabBar, QSplitter,
    QGraphicsView, QGraphicsScene, QProgressBar, QScrollArea,
    QFileDialog, QMessageBox, QColorDialog, QComboBox, QListWidget, QListWidgetItem, QCheckBox, QDialog, QDialogButtonBox, QLayout, QSizePolicy,
)

def user_error(error):
    """No untranslated native/OS exception is displayed in a user warning."""
    if isinstance(error, FileExistsError): return "Файл или папка уже существует. Выберите новое имя; перезапись запрещена."
    if isinstance(error, FileNotFoundError): return "Файл не найден. Проверьте путь и доступность исходника."
    if isinstance(error, PermissionError): return "Нет доступа к файлу или папке. Выберите доступную папку."
    text = str(error)
    if any("а" <= c.lower() <= "я" or c.lower() == "ё" for c in text): return text
    lowered = text.lower()
    translations = (("adjacent", "Выберите две соседние области с общей границей."),
                    ("preflight", "Экспорт заблокирован замечаниями проверки."),
                    ("print settings", "Недопустимые параметры печати. Проверьте формат, поля, номер и толщину линии."),
                    ("footprint", "Номер не помещается внутри области при выбранном размере."),
                    ("image", "Не удалось прочитать изображение. Проверьте формат и целостность файла."),
                    ("cancel", "Операция отменена."),
                    ("timed out", "Превышено время обработки. Попробуйте менее сложное изображение."),
                    ("hash", "Проверка целостности файла не пройдена. Откройте неповреждённый проект."),
                    ("exist", "Файл или папка уже существует. Выберите новое имя."))
    for token, message in translations:
        if token in lowered: return message
    return "Операция не выполнена: данные или параметры не прошли проверку. Предыдущий результат сохранён."

from .tooltips import setup_dialog

def issue_explanation(issue):
    """Plain-language actual reason, without exposing raw diagnostic payloads."""
    if issue.code in ("label-no-fit", "missing-label"):
        meta = getattr(issue, "metadata", {}) or {}
        minimum, preferred = meta.get("min_font_pt"), meta.get("preferred_font_pt")
        detail = (f"Проверены размеры от {preferred:g} до минимального {minimum:g} пт. "
                  if isinstance(minimum, (int, float)) and isinstance(preferred, (int, float)) else "")
        detail += "Номер не помещается с безопасным отступом от границ. "
        detail += ("Выноски тоже проверены, безопасное место не найдено." if meta.get("leaders_attempted") else "Можно включить выноски или изменить параметры печати.")
        return detail
    if issue.code in ("geometry-unmapped", "topology-uncertified"):
        return "Границы области не прошли проверку. Это не означает, что причина в размере номера."
    if issue.code == "palette-layout":
        return "Цветовая легенда не помещается в печатные поля страницы."
    return "Проверка обнаружила проблему этого типа; обычный экспорт заблокирован до её устранения."

class Dialogs:
    """Replace this small boundary in automation; Qt dialogs never leak into tests."""
    def dirty(self, parent):
        box = QMessageBox(parent)
        box.setWindowTitle("Несохранённые изменения")
        box.setText("Сохранить изменения перед продолжением?")
        save = box.addButton("Сохранить…", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Не сохранять", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel); setup_dialog(box); box.exec()
        return "save" if box.clickedButton() is save else "discard" if box.clickedButton() is discard else "cancel"

    def confirm_edit(self, parent, title, before, after, metrics):
        dialog = QDialog(parent); dialog.setWindowTitle(title); dialog.resize(1000, 680)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(metrics))
        layout.addWidget(QLabel("Слева — до, справа — после. Экранный предпросмотр ограничен 1600 px; векторный экспорт сохраняет исходные контуры. Изменения применяются только после подтверждения."))
        views = QSplitter(Qt.Orientation.Horizontal)
        left, right = CanvasView(), CanvasView()
        prepared = getattr(parent, "_preview_images", None)
        if prepared:
            before_image, after_image, width, height = prepared
            left.set_preview_image(before_image, width, height)
            right.set_preview_image(after_image, width, height)
        else:
            left.set_svg(before); right.set_svg(after)
        views.addWidget(left); views.addWidget(right); layout.addWidget(views, 1)
        left.navigationChanged.connect(lambda z,x,y: right.set_navigation(z,x,y,notify=False))
        right.navigationChanged.connect(lambda z,x,y: left.set_navigation(z,x,y,notify=False))
        buttons = QDialogButtonBox()
        accept = buttons.addButton("Применить изменения", QDialogButtonBox.ButtonRole.AcceptRole)
        accept.setAutoDefault(False)
        cancel = buttons.addButton("Не применять", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setDefault(True); cancel.setFocus()
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, left.fit_content)
        setup_dialog(dialog)
        QTimer.singleShot(0, lambda: diagnostics.event("ui.preview.shown",action=getattr(parent,"_preview_action","preview")))
        return dialog.exec() == QDialog.DialogCode.Accepted

    def confirm_lattice(self, parent, text):
        box = QMessageBox(parent)
        box.setWindowTitle("Точные пиксельные границы")
        box.setText(text)
        accept = box.addButton("Применить точные границы", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        setup_dialog(box)
        box.exec()
        return box.clickedButton() is accept

    def _message(self, parent, title, text, icon):
        box = QMessageBox(parent); box.setWindowTitle(title); box.setText(text); box.setIcon(icon)
        button = box.addButton("Закрыть", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(button); setup_dialog(box); box.exec()

    def error(self, parent, title, text): self._message(parent, title, text, QMessageBox.Icon.Critical)
    def info(self, parent, title, text): self._message(parent, title, text, QMessageBox.Icon.Information)

    def _path(self, parent, title, filters, *, mode=QFileDialog.FileMode.ExistingFile, save=False, initial="", purpose="", confirm_overwrite=True):
        # Let Windows own file navigation/accessibility; app tips stop here.
        dialog = QFileDialog(parent, title)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, False)
        dialog.setOption(QFileDialog.Option.DontConfirmOverwrite, not confirm_overwrite)
        dialog.setFileMode(mode)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave if save else QFileDialog.AcceptMode.AcceptOpen)
        dialog.setNameFilter(filters)
        dialog.setLabelText(QFileDialog.DialogLabel.LookIn, "Папка:")
        dialog.setLabelText(QFileDialog.DialogLabel.FileName, "Имя:")
        dialog.setLabelText(QFileDialog.DialogLabel.FileType, "Тип файлов:")
        dialog.setLabelText(QFileDialog.DialogLabel.Accept, "Сохранить" if save else "Выбрать")
        dialog.setLabelText(QFileDialog.DialogLabel.Reject, "Отмена")
        if initial: dialog.selectFile(initial)
        return dialog.selectedFiles() if dialog.exec() == QDialog.DialogCode.Accepted else []

    def open_file(self, parent):
        files = self._path(parent, "Открыть изображение или проект", "Изображения и проекты (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.pbn *.pbnx);;Все файлы (*)",
                           purpose="Открыть выбранное изображение для построения или восстановить проект с правками. При несохранённых изменениях будет запрос.")
        return files[0] if files else ""

    def batch_files(self, parent):
        return self._path(parent, "Выберите изображения для пакетной обработки", "Изображения (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)",
                          mode=QFileDialog.FileMode.ExistingFiles, purpose="Подтвердить выбранные изображения и перейти к параметрам пакета. Обработка ещё не начинается.")

    def batch_options(self, parent):
        from .document import Document
        dialog = QDialog(parent); dialog.setWindowTitle("Пакетная обработка")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Каждый файл: обработка → номера → проверка → отдельный комплект.\nИсходники и существующие комплекты не перезаписываются."))
        diagnostic = QCheckBox("Диагностические комплекты — НЕ ДЛЯ ПЕЧАТИ")
        simplify = QCheckBox("Автоматически упростить для печати (изменяет области)")
        simplify.setEnabled(callable(getattr(Document, "simplify_for_print", None)))
        layout.addWidget(diagnostic); layout.addWidget(simplify)
        buttons = QDialogButtonBox(); accept = buttons.addButton("Выбрать папку и начать…", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        setup_dialog(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted: return None
        folders = self._path(parent, "Папка результатов", "Все папки (*)", mode=QFileDialog.FileMode.Directory,
                             purpose="Выбрать существующую родительскую папку и начать пакет. Для каждого файла создаётся отдельный новый комплект; существующие комплекты не перезаписываются.")
        folder = folders[0] if folders else ""
        return (folder, diagnostic.isChecked(), simplify.isChecked()) if folder else None

    def save_file(self, parent):
        files = self._path(parent, "Сохранить как новый проект (без перезаписи)", "Проект PBN Studio (*.pbn)",
                           mode=QFileDialog.FileMode.AnyFile, save=True, initial="Раскраска.pbn", confirm_overwrite=False,
                           purpose="Сохранить редактируемый проект под новым именем. Если файл уже существует, приложение откажет в сохранении: перезапись запрещена.")
        return files[0] if files else ""

    def export_dir(self, parent):
        files = self._path(parent, "Новая папка комплекта (не должна существовать)", "Папка комплекта (*)",
                           mode=QFileDialog.FileMode.AnyFile, save=True, initial="Комплект", confirm_overwrite=False,
                           purpose="Выбрать имя новой папки для комплекта. Она не должна существовать; не создавайте конечную папку кнопкой создания папки.")
        return files[0] if files else ""

    def color(self, parent, initial):
        dialog = QColorDialog(QColor(*initial), parent)
        dialog.setWindowTitle("Цвет выбранной области")
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, False)
        return dialog.currentColor().getRgb()[:3] if dialog.exec() == QDialog.DialogCode.Accepted else None

class PaletteSwatch(QPushButton):
    """Keyboard-accessible colour action; checked means selected region's colour."""
    def __init__(self, number, rgb, parent=None):
        super().__init__(str(number), parent)
        self.rgb = tuple(rgb)
        self.setFixedSize(44, 44)
        self.setStyleSheet("min-width:44px; max-width:44px; min-height:44px; max-height:44px; padding:0; border:0;")
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"Цвет {number} · RGB {rgb[0]}, {rgb[1]}, {rgb[2]}")
        self.setAccessibleDescription("Перекрасить только выбранную область. Отмечен её текущий цвет; сначала выберите область справа.")
        channels = [c / 255 for c in rgb]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        luminance = sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
        self.ink = QColor("#000000" if luminance > .179 else "#ffffff")
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Button, QColor(*rgb))
        palette.setColor(QPalette.ColorRole.ButtonText, self.ink)
        self.setPalette(palette)
        font = self.font(); font.setPixelSize(13); font.setBold(True); self.setFont(font)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        circle = QRectF(4, 4, self.width() - 8, self.height() - 8)
        painter.setBrush(QColor(*self.rgb))
        painter.setPen(QPen(QColor("#64748b"), 1))
        painter.drawEllipse(circle)
        if self.isChecked():
            # Two-tone ring remains visible against both pale and dark colours.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#ffffff"), 3))
            painter.drawEllipse(circle.adjusted(2, 2, -2, -2))
            painter.setPen(QPen(QColor("#172336"), 1.5))
            painter.drawEllipse(circle.adjusted(2, 2, -2, -2))
        elif self.underMouse() and self.isEnabled():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self.ink, 2))
            painter.drawEllipse(circle.adjusted(2, 2, -2, -2))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#075985"), 2, Qt.PenStyle.DotLine))
            painter.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2), 10, 10)
        painter.setPen(self.ink)
        painter.setFont(self.font())
        painter.drawText(self.rect().translated(0, 1 if self.isDown() else 0), Qt.AlignmentFlag.AlignCenter, self.text())


class PaletteLayout(QLayout):
    """Height-for-width flow: keep every swatch visible without a scroll strip."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(4, 4, 4, 4)
        self.setSpacing(8)

    def addItem(self, item):
        self._items.append(item)
        self.invalidate()

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def _arrange(self, rect, *, apply):
        left, top, right, bottom = self.getContentsMargins()
        area = rect.adjusted(left, top, -right, -bottom)
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            size = item.sizeHint()
            if row_height and x + size.width() > area.right() + 1:
                x, y, row_height = area.x(), y + row_height + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        return y + row_height - rect.y() + bottom


class CanvasView(QGraphicsView):
    navigationChanged = Signal(float, float, float)
    clicked = Signal(float, float)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setBackgroundBrush(QColor("#e6e9ed"))
        self.setMinimumSize(220, 220)
        self.navigation = (1.0, 0.0, 0.0)
        self._syncing = False
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)

    def set_navigation(self, zoom, x, y, *, notify=True):
        zoom = min(64.0, max(0.02, zoom))
        self._syncing = True
        self.resetTransform()
        self.scale(zoom, zoom)
        self.centerOn(x, y)
        self.navigation = (zoom, x, y)
        self._syncing = False
        if notify: self.navigationChanged.emit(zoom, x, y)

    def _scrolled(self, *_):
        if self._syncing: return
        center = self.mapToScene(self.viewport().rect().center())
        self.navigation = (self.transform().m11(), center.x(), center.y())
        self.navigationChanged.emit(*self.navigation)

    def mousePressEvent(self, event):
        self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and (event.position().toPoint() - getattr(self, "_press_pos", event.position().toPoint())).manhattanLength() < 5:
            point = self.mapToScene(event.position().toPoint())
            self.clicked.emit(point.x(), point.y())
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        cursor = self.mapToScene(event.position().toPoint())
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.set_navigation(self.transform().m11() * factor, cursor.x(), cursor.y())
        event.accept()

    def fit_content(self):
        rect = self.scene().itemsBoundingRect()
        if rect.isEmpty(): return
        zoom = min((self.viewport().width()-24)/rect.width(), (self.viewport().height()-24)/rect.height())
        self.set_navigation(zoom, rect.center().x(), rect.center().y())

    def set_image(self, image):
        self._syncing = True
        self.scene().clear()
        self.scene().addPixmap(QPixmap.fromImage(image))
        self.setSceneRect(QRectF(image.rect()))
        self._syncing = False

    def set_preview_image(self, image, width, height):
        self._syncing = True
        self.scene().clear()
        item = self.scene().addPixmap(QPixmap.fromImage(image))
        item.setTransform(QTransform.fromScale(width/image.width(), height/image.height()))
        self.setSceneRect(QRectF(0, 0, width, height))
        self._syncing = False

    def set_svg(self, svg):
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtSvgWidgets import QGraphicsSvgItem
        self._syncing = True
        self.scene().clear()
        previous = getattr(self, "renderer", None)
        if previous is not None: previous.deleteLater()
        self.renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")), self)
        if not self.renderer.isValid(): raise ValueError("Невозможно отобразить SVG")
        item = QGraphicsSvgItem()
        item.setSharedRenderer(self.renderer)
        bounds = item.boundingRect()
        viewbox = self.renderer.viewBoxF()
        if bounds.width() and bounds.height():
            item.setTransform(QTransform.fromScale(viewbox.width()/bounds.width(), viewbox.height()/bounds.height()))
            item.setPos(viewbox.topLeft())
        self.scene().addItem(item)
        self.setSceneRect(self.renderer.viewBoxF())
        self._syncing = False

class EngineThread(QThread):
    progress = Signal(object)
    outcome = Signal(object, object)

    def __init__(self, runner, source, output, options, parent=None, *, loader=None, settings=None):
        super().__init__(parent)
        self.runner, self.source, self.output, self.options = runner, source, output, options
        self.cancel_event = threading.Event()
        self.loader, self.settings = loader, settings

    def run(self):
        try:
            from .geometry import job_scope
            last=[None,0.]
            def stage(name,completed,total):
                now=time.monotonic()
                if name!=last[0] or now-last[1]>=.1:
                    last[:]=[name,now]
                    self.progress.emit({"phase":name,"completed":completed,"total":total})
                    diagnostics.event("job.stage",job="engine",phase=name,completed=completed,total=total)
            with diagnostics.span("job.engine"), job_scope(self.cancel_event,stage):
                document=self._prepare_document()
            self.outcome.emit(document,None)
        except Exception as error:
            from .engine import EngineCancelled
            diagnostics.event("job.cancelled" if isinstance(error,EngineCancelled) else "job.error",job="engine",exception_type=type(error).__name__)
            self.outcome.emit(None,error)

    def _prepare_document(self):
        path = self.runner(self.source, self.output, self.options, cancel_event=self.cancel_event, progress=self.progress.emit)
        if self.cancel_event.is_set():
            from .engine import EngineCancelled
            raise EngineCancelled("cancelled")
        document = self.loader(path)
        if callable(getattr(document, "validate_smooth_geometry", None)):
            document.validate_smooth_geometry(repair=False)
        if hasattr(document, "prepare_labels"):
            document.prepare_labels(self.settings)
        document.dirty = True
        prepare_display(document, action="build")
        diagnostics.event("job.compute.end",action="build")
        return document

def _display_key(document):
    return (getattr(document, "revision", 0), getattr(document, "geometry_mode", None),
            repr(getattr(document, "settings", None)),
            tuple((i.code, i.component) for i in getattr(document, "issues", ())))


def prepare_display(document, *, action="render"):
    """Revision-scoped UI payload; expensive preflight/SVG work runs in job scope."""
    cached = getattr(document, "_ui_display", None)
    key = _display_key(document)
    if cached is not None and cached["key"] == key:
        return cached
    started = time.monotonic()
    diagnostics.event("ui.preflight.start", action=action)
    from .exports import preflight
    issues = preflight(document) if hasattr(document, "settings") else list(document.issues)
    diagnostics.event("ui.preflight.end", action=action, duration_ms=(time.monotonic()-started)*1000, issues=len(issues))
    svgs = {mode: document.svg(mode=mode, diagnostic=True) for mode in ("color", "contours", "numbers")}
    # Validate before replacement while still on the worker, not again per paint.
    from PySide6.QtSvg import QSvgRenderer
    images = {}
    scale = 1600/max(document.width, document.height)
    for mode, svg in svgs.items():
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        if not renderer.isValid(): raise ValueError("Невозможно отобразить SVG")
        image = QImage(max(1, round(document.width*scale)), max(1, round(document.height*scale)), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.white)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            renderer.render(painter)
        finally: painter.end()
        images[mode] = image
    markers = {}
    if hasattr(document, "component_map"):
        import numpy as np
        for issue in issues:
            component = issue.component
            if component in markers or component not in document.components: continue
            x0,y0,x1,y1 = document.components[component]["bbox"]
            yy,xx = np.where(document.component_map[y0:y1,x0:x1] == component)
            if len(xx):
                k=len(xx)//2
                markers[component]=(float(xx[k]+x0)+.5, float(yy[k]+y0)+.5)
    cached = {"key": key, "issues": issues, "svgs": svgs, "images": images, "markers": markers}
    document._ui_display = cached
    return cached


class DocumentThread(QThread):
    """Own a detached model; never access a widget or shared mutable document."""
    outcome = Signal(object, object)
    progress = Signal(object)

    def __init__(self, operation, parent=None, *, action="document"):
        super().__init__(parent)
        self.operation = operation
        self.action = action
        self.cancel_event = threading.Event()

    def run(self):
        try:
            from .geometry import job_scope
            started=time.monotonic()
            last=[None,0.]
            def progress(stage,completed,total):
                now=time.monotonic()
                if stage!=last[0] or now-last[1]>=.1:
                    last[:]=[stage,now]
                    payload={"phase":stage,"completed":completed,"total":total,"elapsed":now-started}
                    self.progress.emit(payload)
                    diagnostics.event("job.stage",action=self.action,**payload)
            diagnostics.event("job.start",job="document",action=self.action)
            parameters=inspect.signature(self.operation).parameters
            kwargs={}
            if "cancel_event" in parameters:kwargs["cancel_event"]=self.cancel_event
            if "progress" in parameters:kwargs["progress"]=progress
            with diagnostics.span("job.document"), job_scope(self.cancel_event,progress):
                result = self.operation(**kwargs)
            diagnostics.event("job.compute.end",action=self.action,elapsed=time.monotonic()-started)
            diagnostics.event("job.complete",job="document",action=self.action,elapsed=time.monotonic()-started)
            self.outcome.emit(result, None)
        except Exception as error:
            from .engine import EngineCancelled
            diagnostics.event("job.cancelled" if isinstance(error,EngineCancelled) else "job.error",job="document",action=self.action,exception_type=type(error).__name__)
            self.outcome.emit(None, error)

class BatchThread(QThread):
    progress = Signal(object)
    outcome = Signal(object, object)

    def __init__(self, sources, destination, options, settings, diagnostic, auto_simplify, parent):
        super().__init__(parent)
        self.arguments = (sources, destination, options)
        self.settings, self.diagnostic, self.auto_simplify = settings, diagnostic, auto_simplify
        self.cancel_event = threading.Event()

    def run(self):
        from .batch import run_batch
        try:
            result = run_batch(*self.arguments, settings=self.settings, diagnostic=self.diagnostic,
                               auto_simplify=self.auto_simplify, cancel_event=self.cancel_event,
                               progress=self.progress.emit)
            self.outcome.emit(result, None)
        except Exception as error:
            self.outcome.emit(None, error)

class MainWindow(QMainWindow):
    def __init__(self, *, dialogs=None, runner=None, document_loader=None, parent=None):
        super().__init__(parent)
        self.dialogs = dialogs if dialogs is not None else Dialogs()
        self.runner = runner
        self.document_loader = document_loader
        self.document = None
        self._generation = 0
        self._worker = None
        self.is_processing = False
        self._job_root = None
        self._close_when_idle = False
        self._job_started=0.
        self._job_stage=""
        self._cancel_started=None
        self._job_timer=QTimer(self)
        self._job_timer.setInterval(250)
        self._job_timer.timeout.connect(self._update_job_elapsed)
        self.source_path = None
        self.selected_component = None
        self.setWindowTitle(f"PBN Studio · {RELEASE_VERSION}")
        self.resize(1320, 880)
        self.setMinimumSize(960, 640)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)
        header = QHBoxLayout()
        brand = QLabel(f"PBN Studio  <span style='color:#64748b;font-size:12px'>{RELEASE_VERSION}</span>")
        brand.setStyleSheet("font-size:22px;font-weight:600")
        header.addWidget(brand)
        header.addStretch()
        self.help_toggle = QPushButton("Справка ▾")
        self.help_toggle.setCheckable(True)
        header.addWidget(self.help_toggle)
        self.batch_button = QPushButton("Пакет…")
        self.batch_button.clicked.connect(self.batch_dialog)
        header.addWidget(self.batch_button)
        self.last_batch_result = None
        self.open_button = QPushButton("Открыть…")
        self.save_button = QPushButton("Сохранить проект…")
        self.export_button = QPushButton("Экспорт…")
        self.diagnostic_button = QPushButton("Диагностика…"); self.diagnostic_button.setEnabled(False)
        self.diagnostic_button.setToolTip("Отдельный комплект с предупреждениями — не для печати")
        self.diagnostic_button.clicked.connect(lambda: self.export_dialog(diagnostic=True))
        self.export_button.clicked.connect(lambda: self.export_dialog(diagnostic=False))
        self.save_button.setEnabled(False)
        self.export_button.setEnabled(False)
        for button in (self.open_button, self.save_button, self.export_button, self.diagnostic_button): header.addWidget(button)
        self.open_button.clicked.connect(self.open_dialog)
        self.save_button.clicked.connect(self.save_dialog)
        layout.addLayout(header)
        options = QHBoxLayout()
        self.colors_spin = QSpinBox(); self.colors_spin.setRange(2,64); self.colors_spin.setValue(24)
        self.speckle_spin = QSpinBox(); self.speckle_spin.setRange(0,32); self.speckle_spin.setValue(4)
        self.speckle_spin.setToolTip("Порог мелких деталей: длина в пикселях исходника; площадь = длина², не миллиметры.")
        self.simplify_spin = QDoubleSpinBox(); self.simplify_spin.setRange(0,5); self.simplify_spin.setSingleStep(0.1); self.simplify_spin.setValue(1)
        for text, control in (("Цветов",self.colors_spin),("Детали, px",self.speckle_spin),("Упрощение, px",self.simplify_spin)):
            options.addWidget(QLabel(text)); options.addWidget(control)
        options.addStretch()
        self.print_settings_toggle = QPushButton("Параметры печати ▾")
        self.print_settings_toggle.setCheckable(True)
        options.addWidget(self.print_settings_toggle)
        self.run_button = QPushButton("Построить раскраску"); self.run_button.setObjectName("primary"); self.run_button.setEnabled(False)
        self.cancel_button = QPushButton("Отменить"); self.cancel_button.setEnabled(False)
        options.addWidget(self.run_button); options.addWidget(self.cancel_button)
        self.run_button.clicked.connect(self.start_processing)
        self.cancel_button.clicked.connect(self.cancel_processing)
        layout.addLayout(options)
        physical = QHBoxLayout()
        self.page_width_spin = QDoubleSpinBox(); self.page_width_spin.setRange(10,2000); self.page_width_spin.setValue(215.9)
        self.page_height_spin = QDoubleSpinBox(); self.page_height_spin.setRange(10,2000); self.page_height_spin.setValue(279.4)
        self.page_presets = {"Letter": (215.9, 279.4), "A4": (210, 297), "A3": (297, 420), "A0": (841, 1189)}
        self.page_preset = QComboBox()
        self.page_preset.addItems([*self.page_presets, "Свой"])
        self.page_preset.setToolTip("Выберите формат, затем «Применить к печати». A0 — плакат, не решение для KDP Letter.")
        self.page_preset.currentTextChanged.connect(self.select_page_preset)
        self.page_width_spin.valueChanged.connect(self.sync_page_preset)
        self.page_height_spin.valueChanged.connect(self.sync_page_preset)
        physical.addWidget(self.page_preset)
        self.margin_spin = QDoubleSpinBox(); self.margin_spin.setRange(0,999); self.margin_spin.setValue(12.7)
        self.font_spin = QDoubleSpinBox(); self.font_spin.setRange(1,200); self.font_spin.setValue(7)
        self.min_font_spin = QDoubleSpinBox(); self.min_font_spin.setRange(1,200); self.min_font_spin.setValue(6)
        self.line_spin = QDoubleSpinBox(); self.line_spin.setRange(0.01,20); self.line_spin.setSingleStep(0.1); self.line_spin.setValue(0.4)
        for text, widget in (("Лист, мм",self.page_width_spin),("×",self.page_height_spin),("Поля, мм",self.margin_spin),("Номер: желаемый, pt",self.font_spin),("минимум, pt",self.min_font_spin),("Линия, pt",self.line_spin)):
            physical.addWidget(QLabel(text)); physical.addWidget(widget)
        self.leaders_check = QCheckBox("Выноски для тесных областей")
        self.leaders_check.setToolTip("Номер в соседней области; точка указывает область для раскрашивания. Только безопасные прямые выноски до 18 мм, без пересечения третьих областей. Размер шрифта сохраняется; ручное редактирование выносок не поддерживается.")
        self.leaders_check.setEnabled(False)
        self.leaders_check.clicked.connect(self.start_leaders)
        physical.addWidget(self.leaders_check)
        physical.addStretch()
        self.print_button = QPushButton("Применить к печати"); self.print_button.setEnabled(False)
        self.print_button.clicked.connect(self.start_print_settings); physical.addWidget(self.print_button)
        self.print_settings_panel = QWidget()
        print_layout = QVBoxLayout(self.print_settings_panel); print_layout.addLayout(physical)
        self.font_summary = QLabel("Номера размещаются от желаемого размера до указанного минимума; ниже минимума не уменьшаются.")
        self.font_summary.setWordWrap(True); print_layout.addWidget(self.font_summary)
        layout.addWidget(self.print_settings_panel); self.print_settings_panel.hide()
        self.print_settings_toggle.toggled.connect(self.print_settings_panel.setVisible)
        geometry_row = QHBoxLayout()
        self.lattice_button = QPushButton("Точные пиксельные границы")
        self.lattice_button.setEnabled(False)
        self.lattice_button.clicked.connect(self.start_lattice_fallback)
        self.advanced_toggle = QPushButton("Дополнительно ▾")
        self.advanced_toggle.setCheckable(True)
        geometry_row.addWidget(self.advanced_toggle)
        self.simplify_button = QPushButton("Упростить для печати")
        self.simplify_button.setEnabled(False)
        self.simplify_button.clicked.connect(lambda: self.start_candidate_edit("Упростить для печати", lambda d: d.simplify_for_print()))
        geometry_row.addWidget(self.simplify_button)
        self.geometry_label = QLabel("")
        self.geometry_label.setWordWrap(True)
        geometry_row.addWidget(self.geometry_label, 1)
        layout.addLayout(geometry_row)
        self.advanced_panel = QWidget()
        advanced_layout = QHBoxLayout(self.advanced_panel)
        advanced_layout.addWidget(self.lattice_button)
        reason = QLabel("Если проверка сообщает об ошибке границ, можно сравнить вариант без сглаживания. "
                        "Красный крест сам по себе не означает ошибку границ: смотрите код замечания. "
                        "Контуры могут стать ступенчатыми; вариант применяется только после сравнения.")
        reason.setWordWrap(True); advanced_layout.addWidget(reason, 1)
        layout.addWidget(self.advanced_panel); self.advanced_panel.hide()
        self.advanced_toggle.toggled.connect(self.advanced_panel.setVisible)
        self.support_label = QLabel("Обычный экспорт заблокирован при замечаниях. Точные границы могут быть ступенчатыми; выноски поддерживаются только в соседнюю область. "
                                    "A0 — плакат, не решение для KDP Letter. Нужна ручная проверка читаемости и пробная печать; допуск KDP не гарантируется.")
        self.support_label.setWordWrap(True)
        layout.addWidget(self.support_label)
        self.support_label.hide()
        self.help_toggle.toggled.connect(self.support_label.setVisible)
        # Internal compatibility controls are not part of the visible workspace.
        self._internal_edit_controls = QWidget(self)
        self._internal_edit_controls.hide()
        tools = QHBoxLayout(self._internal_edit_controls)
        self.tool_combo = QComboBox(); self.tool_combo.addItems(["Просмотр / перемещение", "Выбрать область", "Переместить номер", "Объединить две области", "Разделить линией"])
        self.tool_combo.model().item(3).setEnabled(False)
        self.tool_combo.model().item(4).setEnabled(False)
        self.tool_combo.setToolTip("Номер: сначала щёлкните область, затем новое положение. Перетаскивание фона — панорама; колесо — масштаб.")
        tools.addWidget(self.tool_combo)
        self.selection_label = QLabel("Область не выбрана"); tools.addWidget(self.selection_label)
        self.recolor_button = QPushButton("Цвет области…"); self.recolor_button.setEnabled(False)
        self.recolor_button.clicked.connect(self.choose_color); tools.addWidget(self.recolor_button)
        self.undo_button = QPushButton("↶ Назад"); self.undo_button.setEnabled(False); self.undo_button.setShortcut("Ctrl+Z")
        self.redo_button = QPushButton("↷ Вперёд"); self.redo_button.setEnabled(False); self.redo_button.setShortcut("Ctrl+Shift+Z")
        self.undo_button.clicked.connect(self.undo_edit); self.redo_button.clicked.connect(self.redo_edit)
        tools.addWidget(self.undo_button); tools.addWidget(self.redo_button); tools.addStretch()
        fit = QPushButton("Вписать"); fit.clicked.connect(lambda: self.result_view.fit_content() if self.document else self.original_view.fit_content())
        actual = QPushButton("100 %"); actual.clicked.connect(lambda: self.result_view.set_navigation(1, *self.result_view.navigation[1:]))
        self.mode_tabs = QTabBar(); self.mode_tabs.setExpanding(False)
        for text in ("Цвет", "Контуры", "Номера"): self.mode_tabs.addTab(text)
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("ИСХОДНИК")); title_row.addStretch()
        self.preflight_label = QLabel("Проверка: нет документа")
        title_row.addWidget(self.preflight_label)
        title_row.addWidget(self.mode_tabs)
        title_row.addWidget(fit); title_row.addWidget(actual)
        layout.addLayout(title_row)
        self.original_view = CanvasView(); self.result_view = CanvasView()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.original_view); splitter.addWidget(self.result_view)
        splitter.setSizes([640,640]); splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
        self.original_view.navigationChanged.connect(lambda z,x,y: self.result_view.set_navigation(z,x,y,notify=False))
        self.result_view.navigationChanged.connect(lambda z,x,y: self.original_view.set_navigation(z,x,y,notify=False))
        self.mode_tabs.currentChanged.connect(self.render_document)
        self.result_view.clicked.connect(self._canvas_clicked)
        self.tool_combo.currentIndexChanged.connect(self._tool_changed)
        self.palette_layout = PaletteLayout()
        self.palette_layout.addWidget(QLabel("Палитра появится после обработки"))
        self.palette_widget = QWidget(); self.palette_widget.setLayout(self.palette_layout)
        self.palette_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.palette_widget.setAccessibleName("Палитра цветов")
        layout.addWidget(self.palette_widget)
        self.issue_list = QListWidget(); self.issue_list.setMaximumHeight(90); self.issue_list.hide()
        self.issue_list.itemClicked.connect(self._issue_clicked)
        layout.addWidget(self.issue_list)
        self.batch_report_list = QListWidget()
        self.batch_report_list.setMaximumHeight(100); self.batch_report_list.hide()
        layout.addWidget(self.batch_report_list)
        footer = QHBoxLayout()
        self.status_label = QLabel("Откройте изображение или проект. Обработка выполняется на этом компьютере.")
        footer.addWidget(self.status_label,1)
        self.progress_bar = QProgressBar(); self.progress_bar.setRange(0,100); self.progress_bar.setValue(0); self.progress_bar.setMaximumWidth(160); self.progress_bar.hide()
        footer.addWidget(self.progress_bar)
        layout.addLayout(footer)
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#f7f8fa; color:#172336; font-family:'Segoe UI'; font-size:13px; }
            QLabel { background:transparent; }
            QPushButton { background:#ffffff; border:1px solid #cbd2db; border-radius:8px; padding:5px 10px; min-height:20px; }
            QPushButton:hover { background:#edf2f5; border-color:#75899a; }
            QPushButton:pressed { background:#dce8e7; }
            QPushButton:checked { background:#dceeea; color:#075c55; border-color:#08786e; }
            QPushButton:focus { border:1px solid #075985; background:#e4f1fa; }
            QPushButton:disabled { color:#8b96a5; background:#eef0f3; }
            QPushButton#primary { background:#086b63; color:white; border-color:#086b63; font-weight:600; }
            QPushButton#primary:disabled { background:#9fbcb8; border-color:#9fbcb8; }
            QSpinBox, QDoubleSpinBox, QComboBox { background:white; border:1px solid #cbd2db; border-radius:7px; padding:4px 6px; min-height:22px; }
            QSpinBox, QDoubleSpinBox { min-width:48px; }
            QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color:#075985; background:#f0f8ff; }
            QComboBox QAbstractItemView { background:white; selection-background-color:#dceeea; selection-color:#172336; }
            QCheckBox { spacing:6px; min-height:30px; }
            QCheckBox:focus { color:#075985; }

            QTabBar::tab { padding:7px 20px; background:#e9edf1; }
            QTabBar::tab:selected { background:#172f43; color:white; }
            QGraphicsView { border:1px solid #ccd3dc; }
            QScrollArea { border:0; }
            QProgressBar { border:0; background:#e0e5e9; border-radius:3px; text-align:center; }
            QProgressBar::chunk { background:#086b63; }
        """)
        from .tooltips import setup_main
        setup_main(self)

    def batch_dialog(self):
        if self.is_processing: return False
        sources = self.dialogs.batch_files(self)
        if not sources: return False
        options = self.dialogs.batch_options(self)
        if not options: return False
        destination, diagnostic, simplify = options
        return self.start_batch(sources, destination, diagnostic=diagnostic, auto_simplify=simplify)

    def start_batch(self, sources, destination, *, diagnostic=False, auto_simplify=False):
        if self.is_processing or not sources: return False
        try:
            options, settings = self.engine_options(), self.print_settings()
            from .geometry import glyph_path
            glyph_path(1, 7, 1, 0, 0)
            self._worker = BatchThread(list(sources), Path(destination), options, settings,
                                       diagnostic, auto_simplify, self)
        except Exception as error:
            self.dialogs.error(self, "Проверьте параметры пакета", user_error(error)); return False
        self._worker.progress.connect(self._job_progress)
        self._worker.outcome.connect(self._batch_outcome)
        self._worker.finished.connect(self._job_finished)
        self.is_processing = True
        self.simplify_button.setEnabled(False)
        for index in (3,4): self.tool_combo.model().item(index).setEnabled(False)
        self.batch_button.setEnabled(False); self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0, 100); self.progress_bar.show()
        self.batch_report_list.clear(); self.batch_report_list.show()
        self._job_started=time.monotonic();self._cancel_started=None
        self._job_timer.start()
        self._worker.start()
        return True

    def _batch_outcome(self, result, error):
        if error:
            self.dialogs.error(self, "Ошибка пакетной обработки", user_error(error)); return
        self.last_batch_result = result
        from .tooltips import set_tip, rich
        set_tip(self.status_label, "Пакет завершён", "Полный отчёт: " + str(result.report_path),
                "Отмена останавливает текущий и оставшиеся файлы; уже опубликованные комплекты сохраняются. Заблокированные файлы требуют исправления замечаний. Диагностические комплекты НЕ ДЛЯ ПЕЧАТИ.")
        self.batch_report_list.clear()
        for item in result.items:
            row = QListWidgetItem(f"{item.source.name} — {item.message}")
            row.setToolTip(rich("Результат файла: " + item.source.name,
                                item.message + " · " + str(item.destination or result.report_path),
                                "Строка показывает результат, но не открывает файл. Обычный комплект публикуется только после проверки; диагностический комплект НЕ ДЛЯ ПЕЧАТИ."))
            self.batch_report_list.addItem(row)
        from collections import Counter
        counts = Counter(item.status for item in result.items)
        self.status_label.setText(f"Пакет: готово {counts['exported']}, заблокировано {counts['blocked']}, ошибок {counts['error']}, отменено {counts['cancelled']}. Отчёт: {result.report_path.name}")

    def engine_options(self):
        from .engine import EngineOptions
        return EngineOptions(colors=self.colors_spin.value(), speckle=self.speckle_spin.value(), simplify=self.simplify_spin.value())

    def can_reprocess(self):
        return self.source_path is not None or bool(getattr(self.document, "source_bytes", None))

    def set_document(self, document, *, source_path=None):
        # Jobs prepare this detached payload before UI acceptance. Direct programmatic
        # loads retain the synchronous API, but never repeat preflight per view/paint.
        display = prepare_display(document, action=getattr(self, "_job_action", "load"))
        mode = ("color", "contours", "numbers")[self.mode_tabs.currentIndex()]
        svg = display["svgs"][mode]
        from .engine import EngineOptions
        manifest = getattr(document, "artifact_manifest", None) or {}
        stored = manifest.get("settings")
        # Manifest provenance is preserved by storage; validate before touching UI.
        restored_options = EngineOptions(**stored) if stored else None
        self._generation += 1
        self.document = document
        if restored_options:
            self.colors_spin.setValue(restored_options.colors)
            self.speckle_spin.setValue(restored_options.speckle)
            self.simplify_spin.setValue(restored_options.simplify)
        self.selected_component = None
        self.selection_label.setText("Область не выбрана")
        if hasattr(document, "settings"):
            for widget, key in ((self.page_width_spin,"width_mm"),(self.page_height_spin,"height_mm"),(self.margin_spin,"margin_mm"),(self.font_spin,"font_pt"),(self.min_font_spin,"min_font_pt"),(self.line_spin,"line_pt")):
                widget.setValue(getattr(document.settings,key))
        self.print_button.setEnabled(True)
        self.source_path = Path(source_path) if source_path else None
        image = QImage()
        if getattr(document, "source_bytes", None): image.loadFromData(document.source_bytes)
        elif self.source_path: image.load(str(self.source_path))
        if not image.isNull(): self.original_view.set_image(image)
        else: self.original_view.scene().clear()
        self.render_document(cached_svg=svg)
        self.result_view.fit_content()
        self.save_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self.diagnostic_button.setEnabled(True)
        self.run_button.setEnabled(self.can_reprocess())

    def render_document(self, *_, cached_svg=None):
        if self.document is None: return
        started = time.monotonic()
        diagnostics.event("ui.render.start",action=getattr(self,"_job_action","view"))
        display = prepare_display(self.document)
        self.geometry_label.setText("Границы: точные пиксельные" if getattr(self.document, "geometry_mode", "native") == "lattice" else "Границы: исходные кривые")
        self.simplify_button.setEnabled(callable(getattr(self.document, "simplify_for_print", None)) and not self.is_processing)
        for index, method in ((3, "merge_components"), (4, "split_component")):
            self.tool_combo.model().item(index).setEnabled(callable(getattr(self.document, method, None)) and not self.is_processing)
        self.lattice_button.setEnabled(hasattr(self.document, "confirm_lattice_fallback") and getattr(self.document, "geometry_mode", "native") != "lattice" and not self.is_processing)
        self.leaders_check.setChecked(bool(getattr(self.document, "leaders_enabled", False)))
        self.leaders_check.setEnabled(hasattr(self.document, "enable_leaders") and not self.is_processing)
        if hasattr(self.document, "settings"):
            settings = self.document.settings
            sizes = [label.font_pt for label in self.document.labels]
            actual = f"{min(sizes):g}–{max(sizes):g} pt" if sizes else "номеров пока нет"
            self.font_summary.setText(f"Номер: желаемый {settings.font_pt:g} pt, минимум {settings.min_font_pt:g} pt. "
                                      f"Фактически: {actual}. Ниже указанного минимума номера не уменьшаются.")
        mode = ("color", "contours", "numbers")[self.mode_tabs.currentIndex()]
        nav = self.result_view.navigation
        if hasattr(self.document, "settings"):
            self.result_view.set_preview_image(display["images"][mode], self.document.width, self.document.height)
        else:
            self.result_view.set_svg(display["svgs"][mode])
        self.result_view.set_navigation(*nav, notify=False)
        self.selection_overlay = None
        if self.selected_component is not None and hasattr(self.document, "component_path"):
            if self.selected_component in self.document.components:
                pen = QPen(QColor("#087fff")); pen.setWidthF(2); pen.setCosmetic(True)
                self.selection_overlay = self.result_view.scene().addPath(self.document.component_path(self.selected_component), pen, QBrush(QColor(0,120,255,45)))
                self.selection_overlay.setZValue(10)
        focused = QApplication.focusWidget()
        focus_rgb = focused.rgb if isinstance(focused, PaletteSwatch) and focused.parentWidget() is self.palette_widget else None
        while self.palette_layout.count():
            item = self.palette_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        selected_rgb = getattr(self.document, "component_colors", {}).get(self.selected_component)
        for number, rgb in enumerate(self.document.palette, 1):
            swatch = PaletteSwatch(number, rgb)
            r,g,b = rgb
            swatch.setChecked(selected_rgb is not None and tuple(rgb) == tuple(selected_rgb))
            from .tooltips import set_tip
            set_tip(swatch, f"Цвет {number} · RGB {r}, {g}, {b}",
                    "Перекрасить только выбранную область в этот цвет палитры.",
                    "Сначала выберите область справа. Это не выбор всех областей данного цвета и не изменение всего цвета палитры. Палитра и номера пересчитываются; история правок сохраняется внутри документа.")
            swatch.clicked.connect(lambda checked=False, color=rgb: self.recolor_selected(color))
            swatch.setEnabled(self.selected_component is not None)
            self.palette_layout.addWidget(swatch)
            if focus_rgb == tuple(rgb):
                swatch.setFocus(Qt.FocusReason.OtherFocusReason)
        self.recolor_button.setEnabled(self.selected_component is not None)
        self.undo_button.setEnabled(bool(getattr(self.document,"can_undo",False)))
        self.redo_button.setEnabled(bool(getattr(self.document,"can_redo",False)))
        self.issue_list.clear()
        self.preflight_issues = display["issues"]
        translations = {"palette-layout":"Палитра не помещается на листе", "missing-label":"Область без проверенного номера", "invalid-label":"Номер не прошёл проверку", "label-no-fit":"Номер не помещается", "geometry-unmapped":"Геометрия области не подтверждена", "topology-uncertified":"Топология кривых не сертифицирована", "labels-not-prepared":"Номера ещё не подготовлены"}
        for issue in self.preflight_issues:
            region = f" · область {issue.component}" if issue.component is not None else ""
            row = QListWidgetItem(f"{translations.get(issue.code, 'Замечание')}{region} [{issue.code}]")
            from .tooltips import rich
            row.setToolTip(rich(translations.get(issue.code, "Замечание проверки") + region,
                "Выберите эту строку: область выделится, вид переместится к ней. Просмотр замечания не изменяет документ." if issue.component is not None else "Общее замечание без привязки к области: щелчок не выбирает область.",
                issue_explanation(issue) + " Обычный экспорт заблокирован до устранения замечаний. Проверьте номер и параметры печати; «Упростить для печати» предлагает изменения с предпросмотром, но не гарантирует исправление. Диагностика НЕ ДЛЯ ПЕЧАТИ."))
            row.setData(Qt.ItemDataRole.UserRole, issue.component)
            self.issue_list.addItem(row)
        # Hit targets correspond only to diagnostic crosses, never the full canvas.
        grouped = {}
        for issue in self.preflight_issues:
            if issue.component is not None:
                grouped.setdefault(issue.component, []).append(issue)
        self._issue_markers = []
        for component, issues in grouped.items():
            position = display["markers"].get(component)
            if position is None: continue
            x,y = position
            radius=.8/self.document.mm_per_unit
            item=self.result_view.scene().addRect(QRectF(x-radius,y-radius,2*radius,2*radius),
                QPen(Qt.PenStyle.NoPen), QBrush(QColor(0,0,0,0)))
            item.setZValue(20); item.setData(0,component)
            from .tooltips import rich
            detail="; ".join(f"{translations.get(issue.code, 'Замечание')} [{issue.code}]: {issue_explanation(issue)}" for issue in issues)
            item.setToolTip(rich(f"Область {component}",detail,
                "Красный крест отмечает замечание, а не одну конкретную причину. Щелчок выделяет область и строку списка; документ не изменяется."))
            self._issue_markers.append(item)
        self.issue_list.setVisible(bool(self.preflight_issues))
        count = len(self.preflight_issues)
        self.preflight_label.setText(f"Проверка: {count} замечаний · экспорт заблокирован" if count else "Проверка: замечаний нет")
        self.status_label.setText(f"{self.document.width} × {self.document.height} px · {len(self.document.palette)} цветов · " + (f"Замечаний: {count}. Обычный экспорт заблокирован; выберите замечание в списке." if count else "Замечаний проверки нет."))
        self.setWindowTitle(f"PBN Studio · {RELEASE_VERSION}" + (" · Есть изменения" if self.document.dirty else ""))
        diagnostics.event("ui.render.end",action=getattr(self,"_job_action","view"),duration_ms=(time.monotonic()-started)*1000)

    def start_processing(self, checked=False, *, source_path=None, confirmed=False):
        self._job_action = "build"
        diagnostics.event("ui.action",action="build")
        source = Path(source_path) if source_path else self.source_path
        if self.is_processing or (source is None and not self.can_reprocess()): return False
        if not confirmed and not self.confirm_replace(): return False
        from .engine import run_image
        try:
            options = self.engine_options()
            self._job_print_settings = self.print_settings()
        except Exception as error:
            self.dialogs.error(self, "Проверьте параметры", user_error(error))
            return False
        self._generation += 1
        self._job_generation = self._generation
        self._job_source = source
        self._job_document = self.document
        self._job_revision = self.document.revision if self.document else None
        cache = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "pbn-jobs"
        cache.mkdir(parents=True, exist_ok=True)
        self._job_root = Path(tempfile.mkdtemp(prefix="job-", dir=cache))
        if source is None or not source.is_file():
            master = getattr(self.document, "source_bytes", None)
            if not master:
                shutil.rmtree(self._job_root, ignore_errors=True)
                self._job_root = None
                self.dialogs.error(self, "Нет исходника", "Откройте исходное изображение заново.")
                return False
            source = self._job_root / "embedded-master.png"
            source.write_bytes(master)
            self._job_source = None  # Never retain the disposable job input.
        from .document import Document
        from .geometry import glyph_path
        glyph_path(1, 7, 1, 0, 0)
        self._worker = EngineThread(self.runner or run_image, source, self._job_root / "artifacts", options, self,
                                    loader=self.document_loader or Document.load, settings=self._job_print_settings)
        self._worker.progress.connect(self._job_progress)
        self._worker.outcome.connect(self._job_outcome)
        self._worker.finished.connect(self._job_finished)
        self.is_processing = True
        self.simplify_button.setEnabled(False)
        for index in (3,4): self.tool_combo.model().item(index).setEnabled(False)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0,0); self.progress_bar.show()
        self.status_label.setText("Обработка… Исходный документ сохранён до успешного завершения.")
        self._job_started=time.monotonic();self._cancel_started=None
        self._job_timer.start()
        self._worker.start()
        return True

    def cancel_processing(self):
        if self._worker:
            if self._cancel_started is None:self._cancel_started=time.monotonic()
            diagnostics.event("job.cancel_request",job=type(self._worker).__name__,stage=self._job_stage)
            self._generation += 1
            self._worker.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("Отмена пакета… Уже опубликованные комплекты сохраняются." if isinstance(self._worker, BatchThread) else "Отмена… Предыдущий результат останется без изменений.")

    def _update_job_elapsed(self):
        if not self.is_processing:return
        elapsed=time.monotonic()-self._job_started
        if self._cancel_started is not None:
            waiting=time.monotonic()-self._cancel_started
            text="Закрытие: отмена задания…" if self._close_when_idle else "Отмена задания…"
            if waiting>=2:
                text+=" Ожидаем завершения текущей нативной операции; документ сохранён без изменений."
            self.status_label.setText(f"{text} · {elapsed:.1f} с")
        elif self._job_stage:
            self.status_label.setText(f"{self._job_stage} · {elapsed:.1f} с")

    def _job_progress(self, payload):
        if not self._worker or self._worker.cancel_event.is_set(): return
        value = payload.get("fraction")
        if value is None: self.progress_bar.setRange(0,0)
        else:
            self.progress_bar.setRange(0,100)
            self.progress_bar.setValue(round(min(1,max(0,float(value)))*100))
        phases = {"decode":"Чтение изображения", "worker":"Построение контуров", "native":"Построение контуров", "batch":"Пакетная обработка", "prepared":"Проверка номеров", "verify":"Проверка результата", "normalize":"Подготовка исходника", "complete":"Завершение", "simplify":"Упрощение · проход", "labels":"Размещение номеров", "leaders":"Размещение выносок", "rebuild":"Перестроение областей", "validate":"Проверка геометрии", "adopt":"Подготовка результата"}
        self._job_stage=(f"{payload['index']}/{payload['total']} · {payload['file']} · " if "file" in payload else "") + phases.get(payload.get("phase"), "Обработка изображения…")
        if "completed" in payload:self._job_stage+=f" {payload['completed']}/{payload['total']}"
        self._update_job_elapsed()

    def _job_outcome(self, path, error):
        from .engine import EngineCancelled
        if self._worker.cancel_event.is_set() or isinstance(error, EngineCancelled):
            self.status_label.setText("Обработка отменена. Предыдущий результат сохранён.")
            return
        if error:
            self.status_label.setText("Ошибка обработки. Предыдущий результат сохранён.")
            self.dialogs.error(self, "Не удалось обработать изображение", user_error(error))
            return
        if self._generation != self._job_generation or self.document is not self._job_document or (self.document and self.document.revision != self._job_revision):
            self.status_label.setText("Устаревший результат не применён: документ изменился во время обработки.")
            return
        try:
            self.set_document(path, source_path=self._job_source)
        except Exception as error:
            self.dialogs.error(self, "Не удалось открыть результат", user_error(error))
            self.status_label.setText("Результат отклонён. Предыдущий документ сохранён.")

    def _job_finished(self):
        diagnostics.event("job.finished",action=getattr(self,"_job_action","engine"),close_pending=self._close_when_idle,elapsed=time.monotonic()-self._job_started)
        self._job_timer.stop()
        self.is_processing = False
        self.batch_button.setEnabled(True)
        self.simplify_button.setEnabled(callable(getattr(self.document, "simplify_for_print", None)))
        for index, method in ((3, "merge_components"), (4, "split_component")):
            self.tool_combo.model().item(index).setEnabled(callable(getattr(self.document, method, None)))
        for button in (self.print_button, self.export_button, self.diagnostic_button):
            button.setEnabled(self.document is not None)
        self.cancel_button.setEnabled(False)
        self.lattice_button.setEnabled(hasattr(self.document, "confirm_lattice_fallback") and getattr(self.document, "geometry_mode", "native") != "lattice")
        self.leaders_check.setEnabled(hasattr(self.document, "enable_leaders"))
        self.leaders_check.setChecked(bool(getattr(self.document, "leaders_enabled", False)))
        self.run_button.setEnabled(self.can_reprocess())
        self.progress_bar.hide()
        worker, self._worker = self._worker, None
        worker.deleteLater()
        if self._job_root:
            shutil.rmtree(self._job_root, ignore_errors=True)
            self._job_root = None
        if self._close_when_idle: self.close()

    def confirm_replace(self):
        if self.document is None or not self.document.dirty: return True
        answer = self.dialogs.dirty(self)
        if answer == "discard": return True
        if answer == "save": return self.save_dialog()
        return False

    def open_dialog(self):
        path = self.dialogs.open_file(self)
        return self.load_path(path) if path else False

    def load_path(self, path, *, process=True):
        """Transactional image/project load. Only engine artifacts use Document.load."""
        if self.is_processing:
            self.dialogs.info(self, "Обработка ещё выполняется", "Отмените задание и дождитесь завершения, затем откройте другой файл.")
            return False
        if not self.confirm_replace(): return False
        path = Path(path)
        try:
            if path.suffix.lower() in (".pbn", ".pbnx"):
                from .storage import load_project
                document = load_project(path)
                self.cancel_processing()
                self.set_document(document)
                return True
            image = QImage(str(path))
            if image.isNull(): raise ValueError("Не удалось прочитать изображение. Выберите PNG, JPEG, BMP, TIFF или WebP.")
            if image.width() * image.height() > 40_000_000: raise ValueError("Изображение превышает 40 миллионов пикселей.")
            if process:
                return self.start_processing(source_path=path, confirmed=True)
            self.cancel_processing()
            self._generation += 1
            self.document = None
            self.selected_component = None
            self.selection_label.setText("Область не выбрана")
            self.issue_list.clear(); self.issue_list.hide()
            self.geometry_label.clear()
            self.preflight_label.setText("Проверка: нет документа")
            self.simplify_button.setEnabled(False)
            for index in (3,4): self.tool_combo.model().item(index).setEnabled(False)
            for button in (self.print_button,self.diagnostic_button,self.recolor_button,self.undo_button,self.redo_button,self.leaders_check,self.lattice_button): button.setEnabled(False)
            while self.palette_layout.count():
                item = self.palette_layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            self.palette_layout.addWidget(QLabel("Палитра появится после обработки"))
            self.setWindowTitle(f"PBN Studio · {RELEASE_VERSION}")
            self.source_path = path
            self.original_view.set_image(image)
            self.result_view.scene().clear()
            self.original_view.fit_content()
            self.save_button.setEnabled(False); self.export_button.setEnabled(False)
            self.run_button.setEnabled(not self.is_processing)
            self.status_label.setText(f"{path.name} · {image.width()} × {image.height()} px · Готово к обработке")
            return True
        except Exception as error:
            self.dialogs.error(self, "Не удалось открыть файл", user_error(error))
            return False

    def save_dialog(self):
        if self.document is None: return False
        path = self.dialogs.save_file(self)
        return self.save_to(path) if path else False

    def save_to(self, path):
        if self.document is None: return False
        try:
            from .storage import save_project
            save_project(self.document, Path(path))
            self.render_document()
            self.status_label.setText(f"Проект сохранён: {Path(path).name}")
            return True
        except Exception as error:
            self.dialogs.error(self, "Не удалось сохранить проект", user_error(error))
            return False

    def closeEvent(self, event):
        diagnostics.event("ui.close",busy=self.is_processing,approved=self._close_when_idle)
        if not self._close_when_idle and not self.confirm_replace():
            event.ignore(); return
        if self.is_processing:
            self._close_when_idle = True
            self.centralWidget().setEnabled(False)
            self.cancel_processing()
            self.status_label.setText("Завершение фонового задания перед закрытием…")
            event.ignore(); return
        event.accept()

    def select_page_preset(self, name):
        dimensions = self.page_presets.get(name)
        if dimensions:
            for widget, value in zip((self.page_width_spin, self.page_height_spin), dimensions):
                widget.blockSignals(True); widget.setValue(value); widget.blockSignals(False)

    def sync_page_preset(self, *_):
        dimensions = (self.page_width_spin.value(), self.page_height_spin.value())
        name = next((name for name, size in self.page_presets.items() if size == dimensions), "Свой")
        self.page_preset.blockSignals(True)
        self.page_preset.setCurrentText(name)
        self.page_preset.blockSignals(False)

    def print_settings(self):
        from .document import PrintSettings
        settings=PrintSettings(width_mm=self.page_width_spin.value(), height_mm=self.page_height_spin.value(), margin_mm=self.margin_spin.value(), font_pt=self.font_spin.value(), min_font_pt=self.min_font_spin.value(), line_pt=self.line_spin.value())
        from dataclasses import asdict
        diagnostics.event("ui.settings",**asdict(settings))
        return settings

    def _start_document_task(self, operation, on_success, *, action="document"):
        if self.is_processing: return False
        # Initialize application/font infrastructure on the GUI owner thread.
        # Worker uses only detached QPainterPath/QFont value objects, QImage,
        # and worker-local SVG/PDF renderers; no widgets or QPixmap cross threads.
        from .geometry import glyph_path
        glyph_path(1, 7, 1, 0, 0)
        self._generation += 1
        generation, document = self._generation, self.document
        revision = document.revision if document else None
        self._worker = DocumentThread(operation, self, action=action)
        self._job_action = action
        self._worker.progress.connect(self._job_progress)
        diagnostics.event("ui.action",action=action,width=getattr(document,"width",None),height=getattr(document,"height",None),components=len(getattr(document,"components",{})))
        worker = self._worker
        def outcome(result, error):
            if worker.cancel_event.is_set():
                self.status_label.setText("Задание отменено. Документ не изменён.")
            elif error:
                self.dialogs.error(self, "Не удалось выполнить задание", user_error(error))
            elif (generation == self._generation and self.document is document
                  and (document is None or document.revision == revision)):
                try: on_success(result)
                except Exception as exc: self.dialogs.error(self, "Результат отклонён", user_error(exc))
        self._worker.outcome.connect(outcome)
        self._worker.finished.connect(self._job_finished)
        self.is_processing = True
        self.simplify_button.setEnabled(False)
        for index in (3,4): self.tool_combo.model().item(index).setEnabled(False)
        self.run_button.setEnabled(False)
        self.print_button.setEnabled(False)
        self.leaders_check.setEnabled(False)
        self.lattice_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.diagnostic_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0,0); self.progress_bar.show()
        self.status_label.setText("Фоновая подготовка… Можно отменить без изменения документа.")
        self._job_started=time.monotonic();self._cancel_started=None
        self._job_timer.start()
        self._worker.start()
        return True

    def _detached_document(self, document=None):
        from PySide6.QtGui import QPainterPath
        memo = {}
        document = self.document if document is None else document
        cached = getattr(document, "_ui_display", None)
        if cached is not None:
            # Immutable revision payload; QImage cannot be deep-copied by pickle.
            memo[id(cached)] = cached
        for name in ("component_paths", "face_paths"):
            for path in getattr(document, name, {}).values():
                memo[id(path)] = QPainterPath(path)
        return copy.deepcopy(document, memo)

    def start_candidate_edit(self, title, operation, *, action="simplify"):
        """Prepare a detached candidate; show real before/after before committing."""
        if self.document is None or self.is_processing: return False
        try:
            old = self.document; revision = old.revision; source = self.source_path
            navigation = self.result_view.navigation
            from .exports import preflight
            def prepare():
                candidate = self._detached_document(old)
                before_display = prepare_display(candidate, action=action)
                before = before_display["svgs"]["numbers"]
                before_count = len(candidate.components)
                before_issues = len(before_display["issues"])
                before_labels = len(candidate.labels)
                report = operation(candidate)
                # Domain geometry edits already produce validated physical labels.
                # Generic extensions retain the legacy preparation contract.
                if action != "lattice" and not (isinstance(report,dict) and report.get("operation") in ("simplify","merge","split")):
                    candidate.prepare_labels()
                after_display = prepare_display(candidate, action=action)
                metrics = (f"Областей: {before_count} → {len(candidate.components)} · "
                           f"Номеров: {before_labels} → {len(candidate.labels)} · "
                           f"Замечаний: {before_issues} → {len(after_display['issues'])}")
                if isinstance(report, dict) and "changed_area_fraction" in report:
                    metrics += (f"\nИзменено площади: {report['changed_area_fraction']:.2%} "
                                f"({report['changed_area_mm2']:.2f} мм²) · "
                                f"Размер номера: {candidate.settings.font_pt:g} пт · "
                                f"Исправлено границ: {len(report.get('fallback_edges', []))}")
                if action == "lattice":
                    metrics += "\nБез сглаживания: контуры могут стать ступенчатыми. Цвета и области не объединяются; исправление всех замечаний не гарантируется."
                return candidate, before, after_display["svgs"]["numbers"], metrics, report, (before_display["images"]["numbers"], after_display["images"]["numbers"], candidate.width, candidate.height)
            def preview(result):
                doc, before, after, metrics, report, images = result
                self._preview_images = images
                generation = self._generation
                cancel_event = self._worker.cancel_event
                diagnostics.event("ui.preview.start",action=action)
                self._preview_action = action
                # Real dialog emits shown only after its views are ready. Injected
                # nonvisual dialog boundaries emit here for controller tests.
                if not isinstance(self.dialogs, Dialogs):
                    diagnostics.event("ui.preview.shown",action=action)
                accepted = self.dialogs.confirm_edit(self, title, before, after, metrics)
                diagnostics.event("ui.preview.accepted" if accepted else "ui.preview.rejected",action=action)
                if not accepted:
                    self.status_label.setText("Изменения отклонены. Документ сохранён без изменений."); return
                if (self.document is not old or old.revision != revision or self._generation != generation
                        or cancel_event.is_set()):
                    self.status_label.setText("Предпросмотр устарел. Изменения не применены."); return
                self.set_document(doc, source_path=source)
                self.result_view.set_navigation(*navigation)
                self.last_edit_report = report
            return self._start_document_task(prepare, preview, action=action)
        except Exception as error:
            self.dialogs.error(self, "Не удалось подготовить предпросмотр", user_error(error)); return False

    def start_lattice_fallback(self):
        if self.document is None or self.is_processing or self.document.geometry_mode == "lattice": return False
        return self.start_candidate_edit("Сравнить границы без сглаживания",
            lambda doc: doc.confirm_lattice_fallback(confirmed=True), action="lattice")

    def start_leaders(self, enabled):
        if self.document is None or self.is_processing: return False
        try:
            old = self.document
            def prepare():
                candidate = self._detached_document(old)
                candidate.enable_leaders(bool(enabled))
                prepare_display(candidate, action="leaders")
                return candidate
            return self._start_document_task(prepare, lambda doc: self.set_document(doc, source_path=self.source_path), action="leaders")
        except Exception as error:
            self.leaders_check.setChecked(bool(getattr(self.document, "leaders_enabled", False)))
            self.dialogs.error(self, "Не удалось подготовить выноски", user_error(error))
            return False

    def start_print_settings(self):
        if self.document is None or self.is_processing: return False
        try:
            settings = self.print_settings()
            old = self.document
            def prepare():
                candidate = self._detached_document(old)
                candidate.prepare_labels(settings)
                prepare_display(candidate, action="apply-print")
                return candidate
            return self._start_document_task(prepare, lambda doc: self.set_document(doc, source_path=self.source_path), action="apply-print")
        except Exception as error:
            self.dialogs.error(self, "Проверьте параметры печати", user_error(error))
            return False

    def apply_print_settings(self):
        return self.start_print_settings()

    def select_component_at(self, x, y):
        if self.document is None: return None
        self.selected_component = self.document.sample_component(x,y)
        self.selection_label.setText(f"Область {self.selected_component}" if self.selected_component is not None else "Область не выбрана")
        self.render_document()
        return self.selected_component

    def _tool_changed(self, index):
        if index in (3, 4):
            self.selected_component = None
            self.selection_label.setText("Выберите первую область" if index == 3 else "Выберите область, затем две точки разреза")
            self.render_document()

    def _canvas_clicked(self, x, y):
        if self.is_processing or self.document is None: return
        for marker in getattr(self, "_issue_markers", ()):
            if marker.sceneBoundingRect().contains(QPointF(x,y)):
                component=marker.data(0)
                for index in range(self.issue_list.count()):
                    row=self.issue_list.item(index)
                    if row.data(Qt.ItemDataRole.UserRole)==component:
                        self.issue_list.setCurrentItem(row)
                        self._issue_clicked(row)
                        return
        tool = self.tool_combo.currentIndex()
        if tool == 1 or (tool == 2 and self.selected_component is None): self.select_component_at(x,y)
        elif tool == 2: self.move_selected(x,y)
        elif tool == 3 and callable(getattr(self.document, "merge_components", None)):
            if self.selected_component is None:
                self.select_component_at(x, y)
                self.selection_label.setText(f"Область {self.selected_component} → выберите соседа")
            else:
                first, second = self.selected_component, self.document.sample_component(x, y)
                if second is not None:
                    self.start_candidate_edit("Объединить области (цвет первой сохраняется)",
                                              lambda doc: doc.merge_components(first, second), action="merge-internal")

    def choose_color(self):
        if self.is_processing or self.document is None or self.selected_component is None: return False
        initial = self.document.component_colors[self.selected_component]
        color = self.dialogs.color(self, initial)
        return self.recolor_selected(color) if color is not None else False

    def recolor_selected(self, rgb):
        if self.is_processing or self.document is None or self.selected_component is None: return False
        try:
            self.document.recolor(self.selected_component, tuple(rgb))
            self.render_document(); return True
        except Exception as error:
            self.dialogs.error(self, "Не удалось изменить цвет", user_error(error)); return False

    def move_selected(self, x, y):
        if self.is_processing or self.document is None or self.selected_component is None: return False
        try:
            self.document.move_label(self.selected_component, x, y)
            self.render_document(); return True
        except Exception as error:
            self.dialogs.error(self, "Номер не помещается", "Выберите положение внутри выбранной области. Размер номера автоматически не уменьшается.\n" + user_error(error)); return False

    def undo_edit(self):
        if self.is_processing or self.document is None: return False
        diagnostics.event("ui.action",action="undo-internal")
        changed = self.document.undo()
        if changed:
            if self.selected_component not in self.document.components:
                self.selected_component = None
                self.selection_label.setText("Область не выбрана")
            self.render_document()
        return changed

    def redo_edit(self):
        if self.is_processing or self.document is None: return False
        diagnostics.event("ui.action",action="redo-internal")
        changed = self.document.redo()
        if changed:
            if self.selected_component not in self.document.components:
                self.selected_component = None
                self.selection_label.setText("Область не выбрана")
            self.render_document()
        return changed

    def _issue_clicked(self, item):
        if self.document is None: return
        component = item.data(Qt.ItemDataRole.UserRole)
        if component is None: return
        self.selected_component = component
        self.selection_label.setText(f"Область {component}")
        self.tool_combo.setCurrentIndex(0)
        info = getattr(self.document,"components",{}).get(component)
        if info:
            x0,y0,x1,y1 = info["bbox"]
            self.result_view.set_navigation(self.result_view.navigation[0],(x0+x1)/2,(y0+y1)/2)
        self.render_document()

    def export_dialog(self, *, diagnostic=False):
        if self.document is None: return False
        if self.document.issues and not diagnostic:
            return self.export_to(None, diagnostic=False)
        path = self.dialogs.export_dir(self)
        return self.start_export(path, diagnostic=diagnostic) if path else False

    def start_export(self, path, *, diagnostic=False):
        if self.document is None or self.is_processing: return False
        if self.document.issues and not diagnostic: return self.export_to(None)
        try:
            from .exports import export_bundle
            destination = Path(path)
            if destination.exists(): raise FileExistsError(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            candidate = self._detached_document()
            self._job_root = Path(tempfile.mkdtemp(prefix=".pbn-ui-export-", dir=destination.parent))
            staging = self._job_root / "bundle"
            def export():
                files = export_bundle(candidate, staging, diagnostic=diagnostic)
                if not files or not all(Path(p).is_file() for p in files):
                    raise ValueError("Экспорт не подтвердил наличие всех файлов")
                return files
            def publish(files):
                # Windows rename refuses existing targets; no user directory is removed.
                if destination.exists(): raise FileExistsError(destination)
                staging.rename(destination)
                self.status_label.setText(("Диагностический комплект — НЕ ДЛЯ ПЕЧАТИ" if diagnostic else "Комплект экспортирован") + f" · {len(files)} файлов · {destination}")
            return self._start_document_task(export, publish, action="export-diagnostic" if diagnostic else "export")
        except Exception as error:
            if self._job_root:
                shutil.rmtree(self._job_root, ignore_errors=True); self._job_root = None
            self.dialogs.error(self, "Не удалось экспортировать комплект", user_error(error))
            return False

    def export_to(self, path, *, diagnostic=False):
        if self.document is None: return False
        if self.document.issues and not diagnostic:
            self.dialogs.error(self, "Экспорт заблокирован проверкой", f"Не устранено замечаний: {len(self.document.issues)}. Проверьте список под палитрой. Диагностический комплект доступен отдельно и не предназначен для печати.")
            return False
        try:
            from .exports import export_bundle
            files = export_bundle(self.document, Path(path), diagnostic=diagnostic)
            if not files or not all(Path(p).is_file() for p in files):
                raise ValueError("Экспорт не подтвердил наличие всех файлов")
            self.status_label.setText(("Диагностический комплект — НЕ ДЛЯ ПЕЧАТИ" if diagnostic else "Комплект экспортирован") + f" · {len(files)} файлов · {path}")
            return True
        except Exception as error:
            self.dialogs.error(self, "Не удалось экспортировать комплект", user_error(error))
            return False
