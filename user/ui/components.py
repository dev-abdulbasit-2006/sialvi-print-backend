"""Reusable premium widgets for the Sialvi Print customer kiosk (PySide6 / Qt Widgets).

Pure presentation: nothing here talks to the network, pricing, payment or printing code.
Animations respect ``theme_user.REDUCE_MOTION`` and never block input.
"""
from __future__ import annotations

import math
import os
import re

from PySide6.QtCore import (
    Qt, Signal, QPropertyAnimation, QEasingCurve, QVariantAnimation, QRectF, QPointF, QTimer,
)
from PySide6.QtGui import QPainter, QPen, QColor, QPixmap, QFont
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QBoxLayout, QProgressBar,
    QGraphicsOpacityEffect, QDialog, QGraphicsDropShadowEffect, QSpinBox, QStackedWidget,
)

from user.ui import theme_user as T
from shared.resources import asset_path

# Extensions the kiosk accepts (same list as the existing "Choose files" dialog filter).
SUPPORTED_EXTS = (
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "rtf", "csv",
    "jpg", "jpeg", "png", "bmp", "tiff", "psd", "ai", "dwg", "dxf",
)
PAGE_RANGE_RE = re.compile(r"^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$")


def is_supported(path: str) -> bool:
    return os.path.splitext(path)[1].lstrip(".").lower() in SUPPORTED_EXTS


def page_range_looks_valid(text: str) -> bool:
    """Friendly client-side format hint only; the server remains the authority."""
    return not text.strip() or bool(PAGE_RANGE_RE.match(text))


def repolish(w: QWidget):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def soft_shadow(widget: QWidget, blur: int = 28, dy: int = 4, alpha: int = 110):
    fx = QGraphicsDropShadowEffect(widget)
    fx.setBlurRadius(blur)
    fx.setOffset(0, dy)
    fx.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(fx)


def fade_in(widget: QWidget, duration: int = T.DUR_MED):
    """Fade a widget in. Skipped if motion is reduced or the widget already owns a graphics effect."""
    if T.REDUCE_MOTION or widget.graphicsEffect() is not None:
        return
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(0.0)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.OutCubic)

    def _done():
        try:
            if widget.graphicsEffect() is eff:
                widget.setGraphicsEffect(None)   # also frees the effect (cheaper repaints afterwards)
        except RuntimeError:
            pass

    anim.finished.connect(_done)
    anim.start(QPropertyAnimation.DeleteWhenStopped)


def switch_page(stack: QStackedWidget, index: int):
    stack.setCurrentIndex(index)
    page = stack.currentWidget()
    if page is not None:
        fade_in(page, T.DUR_SLOW)


# =================================================================================== steps ====
class StepIndicator(QFrame):
    """4-step progress rail (Files > Settings > Price & Pay > Print). Presentation only."""

    LABELS = ("Add files", "Choose settings", "Price & pay", "Print")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StepBar")
        self.setFixedHeight(56)
        self._step = 0
        self._pos = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(T.DUR_SLOW)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_value)

    @property
    def step(self) -> int:
        return self._step

    def set_step(self, step: int):
        step = max(0, min(len(self.LABELS), step))   # len(LABELS) = all steps complete
        if step == self._step:
            return
        self._step = step
        if T.REDUCE_MOTION:
            self._pos = float(step)
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(float(self._pos))
        self._anim.setEndValue(float(step))
        self._anim.start()

    def _on_value(self, v):
        self._pos = float(v)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.LABELS)
        w, h = self.width(), self.height()
        margin = max(80, int(w * 0.08))
        span = max(1, w - 2 * margin)
        xs = [margin + span * i / (n - 1) for i in range(n)]
        cy = h / 2 - 8
        r = 15
        # connecting lines (filled part follows the animated position)
        for i in range(n - 1):
            x1, x2 = xs[i] + r + 6, xs[i + 1] - r - 6
            p.setPen(QPen(QColor(T.BORDER_STRONG), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x1, cy), QPointF(x2, cy))
            frac = max(0.0, min(1.0, self._pos - i))
            if frac > 0:
                p.setPen(QPen(QColor(T.ACCENT), 3, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(x1, cy), QPointF(x1 + (x2 - x1) * frac, cy))
        font = QFont(self.font())
        for i, x in enumerate(xs):
            done, active = i < self._step, i == self._step
            if done:
                fill, edge, glyph, gcol = QColor(T.ACCENT), QColor(T.ACCENT), "✓", QColor(T.NAVY_900)
            elif active:
                fill, edge, glyph, gcol = QColor(T.PRIMARY_SOFT), QColor(T.ACCENT), str(i + 1), QColor(T.ACCENT_LIGHT)
            else:
                fill, edge, glyph, gcol = QColor(T.SURFACE), QColor(T.BORDER_STRONG), str(i + 1), QColor(T.MUTED)
            p.setPen(QPen(edge, 3 if active else 2))
            p.setBrush(fill)
            p.drawEllipse(QPointF(x, cy), r, r)
            font.setBold(True)
            font.setPixelSize(15)
            p.setFont(font)
            p.setPen(gcol)
            p.drawText(QRectF(x - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, glyph)
            font.setBold(active or done)
            font.setPixelSize(13)
            p.setFont(font)
            p.setPen(QColor(T.TEXT if (active or done) else T.MUTED))
            p.drawText(QRectF(x - 90, cy + r + 4, 180, 20), Qt.AlignHCenter | Qt.AlignTop, self.LABELS[i])
        p.end()


# ================================================================================ drop zone ===
class DropZone(QFrame):
    """Large drag-and-drop target plus a 'Choose Files' button (slim bar once files exist)."""
    files_dropped = Signal(list)
    browse_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setProperty("hover", False)
        self.lay = QBoxLayout(QBoxLayout.TopToBottom, self)
        self.lay.setContentsMargins(20, 14, 20, 14)
        self.lay.setSpacing(6)
        self.icon = QLabel("⇪")
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setStyleSheet(f"font-size: 34px; color: {T.ACCENT}; font-weight: 800;")
        self.title = QLabel("Drag & drop your documents here")
        self.title.setObjectName("DropZoneTitle")
        self.title.setAlignment(Qt.AlignCenter)
        self.formats = QLabel("PDF · Word · Excel · PowerPoint · Images · Photoshop · Illustrator · AutoCAD")
        self.formats.setObjectName("Hint")
        self.formats.setAlignment(Qt.AlignCenter)
        self.formats.setWordWrap(True)
        self.btn = QPushButton("Choose Files")
        self.btn.setProperty("class", "primary")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setMinimumWidth(190)
        self.btn.clicked.connect(self.browse_requested.emit)
        self.lay.addWidget(self.icon)
        self.lay.addWidget(self.title)
        self.lay.addWidget(self.formats)
        self.lay.addWidget(self.btn, 0, Qt.AlignCenter)
        self.set_compact(False)

    def set_compact(self, compact: bool):
        self.icon.setVisible(not compact)
        self.formats.setVisible(not compact)
        self.title.setText("Drop more files here" if compact else "Drag & drop your documents here")
        self.btn.setText("Add more files" if compact else "Choose Files")
        self.btn.setMinimumWidth(150 if compact else 190)
        if compact:
            self.lay.setDirection(QBoxLayout.LeftToRight)
            self.lay.setContentsMargins(16, 8, 16, 8)
            self.title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.lay.setStretchFactor(self.title, 1)
        else:
            self.lay.setDirection(QBoxLayout.TopToBottom)
            self.lay.setContentsMargins(20, 14, 20, 14)
            self.title.setAlignment(Qt.AlignCenter)
            self.lay.setStretchFactor(self.title, 0)

    def _set_hover(self, on: bool):
        self.setProperty("hover", on)
        repolish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self._set_hover(True)
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, _e):
        self._set_hover(False)

    def dropEvent(self, e):
        self._set_hover(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.isfile(p)]
        if paths:
            self.files_dropped.emit(paths)
            e.acceptProposedAction()


# ================================================================================== file row ===
class FileRowWidget(QWidget):
    """One row: type badge, name, status chip + real upload progress, remove button.

    ``set_status(text)`` keeps the original API; ``set_state``/``set_progress`` add the new visuals.
    """

    def __init__(self, display_name: str, on_remove):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 10)
        lay.setSpacing(12)
        ext = os.path.splitext(display_name)[1].lstrip(".").upper()[:4] or "FILE"
        badge = QLabel(ext)
        badge.setObjectName("FileBadge")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(48, 36)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.name_label = QLabel(display_name)
        self.name_label.setStyleSheet("font-weight: 700;")
        self.name_label.setToolTip(display_name)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.chip = QLabel("Waiting")
        self.chip.setProperty("chip", "pending")
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        row.addWidget(self.chip, 0)
        row.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        col.addWidget(self.name_label)
        col.addLayout(row)
        col.addWidget(self.progress)
        self.remove_btn = QPushButton("✕")
        self.remove_btn.setProperty("class", "rowremove")
        self.remove_btn.setFixedSize(40, 40)
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.setToolTip("Remove this file")
        self.remove_btn.clicked.connect(on_remove)
        lay.addWidget(badge)
        lay.addLayout(col, 1)
        lay.addWidget(self.remove_btn)

    def set_state(self, state: str, text: str = ""):
        labels = {"pending": "Waiting", "uploading": "Uploading", "ready": "Ready", "failed": "Failed"}
        self.chip.setText(labels.get(state, state.title()))
        self.chip.setProperty("chip", state)
        repolish(self.chip)
        self.status_label.setText(text)
        self.progress.setVisible(state == "uploading")

    def set_progress(self, pct: int):
        self.progress.setVisible(True)
        self.progress.setValue(max(0, min(100, pct)))

    def set_status(self, text: str):      # backwards compatible entry point
        self.status_label.setText(text)


# ================================================================================ state glyph ===
class StateGlyph(QWidget):
    """Animated glyph: 'wait' (rotating arc), 'check' (drawn tick), 'alert' (exclamation)."""

    def __init__(self, parent=None, size: int = 56):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._mode = "wait"
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._tick)

    def set_mode(self, mode: str):
        self._mode = mode
        self._anim.stop()
        if mode == "wait":
            self._t = 0.0
            if not T.REDUCE_MOTION and self.isVisible():
                self._anim.setStartValue(0.0)
                self._anim.setEndValue(360.0)
                self._anim.setDuration(1100)
                self._anim.setLoopCount(-1)
                self._anim.setEasingCurve(QEasingCurve.Linear)
                self._anim.start()
        elif mode == "check":
            self._t = 1.0 if T.REDUCE_MOTION else 0.0
            if not T.REDUCE_MOTION:
                self._anim.setStartValue(0.0)
                self._anim.setEndValue(1.0)
                self._anim.setDuration(480)
                self._anim.setLoopCount(1)
                self._anim.setEasingCurve(QEasingCurve.OutCubic)
                self._anim.start()
        else:
            self._t = 1.0
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        if self._mode == "wait":
            self.set_mode("wait")

    def hideEvent(self, e):
        self._anim.stop()          # no background animation while hidden (saves CPU)
        super().hideEvent(e)

    def _tick(self, v):
        self._t = float(v)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = min(self.width(), self.height())
        rect = QRectF(5, 5, s - 10, s - 10)
        if self._mode == "wait":
            p.setPen(QPen(QColor(T.BORDER_STRONG), 5))
            p.drawEllipse(rect)
            p.setPen(QPen(QColor(T.ACCENT), 5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, int(-self._t * 16), 100 * 16)
        elif self._mode == "check":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.SUCCESS))
            k = 0.6 + 0.4 * min(1.0, self._t * 1.6)
            c = rect.center()
            rr = rect.width() / 2 * k
            p.drawEllipse(c, rr, rr)
            pen = QPen(QColor("#FFFFFF"), 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            p.setPen(pen)
            a, b, d = QPointF(s * 0.30, s * 0.52), QPointF(s * 0.44, s * 0.66), QPointF(s * 0.72, s * 0.36)
            prog = max(0.0, min(1.0, (self._t - 0.3) / 0.7))
            if prog > 0:
                seg1 = min(1.0, prog * 2)
                p.drawLine(a, QPointF(a.x() + (b.x() - a.x()) * seg1, a.y() + (b.y() - a.y()) * seg1))
                if prog > 0.5:
                    seg2 = (prog - 0.5) * 2
                    p.drawLine(b, QPointF(b.x() + (d.x() - b.x()) * seg2, b.y() + (d.y() - b.y()) * seg2))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T.DANGER))
            p.drawEllipse(rect)
            p.setPen(QPen(QColor("#FFFFFF"), 5, Qt.SolidLine, Qt.RoundCap))
            cx = rect.center().x()
            p.drawLine(QPointF(cx, s * 0.28), QPointF(cx, s * 0.55))
            p.drawPoint(QPointF(cx, s * 0.70))
        p.end()


class StatusCard(QFrame):
    """Honest order-status panel: waiting for the attendant / printing / printed / failed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusCard")
        self.setProperty("kind", "waiting")
        self.setMinimumHeight(112)          # room for 2-3 wrapped lines of status text
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(14)
        self.glyph = StateGlyph(self, 52)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.title = QLabel("")
        self.title.setObjectName("StatusTitle")
        self.sub = QLabel("")
        self.sub.setObjectName("StatusSub")
        self.sub.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)              # indeterminate: the app has no real percentage
        self.bar.setTextVisible(False)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        col.addWidget(self.bar)
        lay.addWidget(self.glyph, 0, Qt.AlignTop)
        lay.addLayout(col, 1)
        self.setVisible(False)

    def show_state(self, kind: str, title: str, sub: str = ""):
        """kind: waiting | printing | success | failed"""
        self.title.setText(title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        busy = kind in ("waiting", "printing")
        self.bar.setVisible(busy)
        self.setProperty("kind", kind)
        repolish(self)
        was_hidden = not self.isVisible()
        self.setVisible(True)
        self.glyph.set_mode({"success": "check", "failed": "alert"}.get(kind, "wait"))
        if was_hidden:
            fade_in(self, T.DUR_MED)

    def clear(self):
        self.setVisible(False)
        self.glyph._anim.stop()


# ==================================================================== segmented control / stepper ===
class SegmentedControl(QWidget):
    """Touch-friendly segmented selector that mirrors an existing QComboBox (combo stays the source of truth)."""

    def __init__(self, combo, labels: dict | None = None, parent=None):
        super().__init__(parent)
        self.combo = combo
        combo.setParent(self)
        combo.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.buttons = []
        n = combo.count()
        for i in range(n):
            text = combo.itemText(i)
            b = QPushButton((labels or {}).get(text, text.title()))
            b.setCheckable(True)
            b.setProperty("class", "seg")
            b.setProperty("pos", "first" if i == 0 else ("last" if i == n - 1 else "mid"))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: combo.setCurrentIndex(idx))
            lay.addWidget(b)
            self.buttons.append(b)
        combo.currentIndexChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_):
        for i, b in enumerate(self.buttons):
            b.setChecked(i == self.combo.currentIndex())
            repolish(b)


class CopiesStepper(QWidget):
    """[-] [ n ] [+]. The existing QSpinBox stays the single source of truth (value, range); it is only hidden,
    and a label shows its value (Qt style sheets cannot reliably render a button-less QSpinBox)."""

    def __init__(self, spin: QSpinBox, parent=None):
        super().__init__(parent)
        self.spin = spin
        spin.setParent(self)
        spin.setVisible(False)
        self.value_label = QLabel(str(spin.value()))
        self.value_label.setObjectName("CopiesValue")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setMinimumWidth(80)
        self.value_label.setFixedHeight(T.HIT_MIN - 4)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.minus = QPushButton("−")
        self.plus = QPushButton("+")
        for b, fn in ((self.minus, spin.stepDown), (self.plus, spin.stepUp)):
            b.setProperty("class", "step")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
        lay.addWidget(self.minus)
        lay.addWidget(self.value_label)
        lay.addWidget(self.plus)
        lay.addStretch()
        spin.valueChanged.connect(self._sync)
        self._sync()

    def _sync(self, *_):
        self.value_label.setText(str(self.spin.value()))
        self.minus.setEnabled(self.spin.value() > self.spin.minimum())
        self.plus.setEnabled(self.spin.value() < self.spin.maximum())


# ======================================================================================= welcome ===
class WelcomePage(QWidget):
    start_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 24, 40, 32)
        outer.addStretch(2)
        # The emblem has white lettering, so it sits on a navy tile (artwork untouched).
        mark = QFrame()
        mark.setObjectName("LogoTile")
        mark.setFixedSize(300, 190)
        ml = QVBoxLayout(mark)
        ml.setContentsMargins(0, 0, 0, 0)
        mark_img = QLabel()
        mark_img.setAlignment(Qt.AlignCenter)
        pix = QPixmap(asset_path("emblem.png"))
        if not pix.isNull():
            mark_img.setPixmap(pix.scaledToHeight(150, Qt.SmoothTransformation))
        ml.addWidget(mark_img)
        soft_shadow(mark, 36, 8, 50)
        head = QLabel("Print your documents, simply.")
        head.setObjectName("Headline")
        head.setAlignment(Qt.AlignCenter)
        lead = QLabel("Add your files, choose your settings, and we handle the rest.")
        lead.setObjectName("Lead")
        lead.setAlignment(Qt.AlignCenter)
        lead.setWordWrap(True)
        self.start_btn = QPushButton("Start Printing")
        self.start_btn.setProperty("class", "hero")
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.clicked.connect(self.start_clicked.emit)
        outer.addWidget(mark, 0, Qt.AlignCenter)
        outer.addSpacing(14)
        outer.addWidget(head)
        outer.addWidget(lead)
        outer.addSpacing(22)
        outer.addWidget(self.start_btn, 0, Qt.AlignCenter)
        outer.addSpacing(34)
        steps = QHBoxLayout()
        steps.setSpacing(16)
        steps.addStretch()
        for n, (t, d) in enumerate((
            ("Add your files", "PDF, Word, images and more"),
            ("Choose settings", "Printer, copies, page range"),
            ("Pay & collect", "The attendant confirms your payment"),
        ), start=1):
            card = QFrame()
            card.setObjectName("HintCard")
            card.setFixedWidth(260)
            cl = QHBoxLayout(card)
            cl.setContentsMargins(14, 12, 14, 12)
            cl.setSpacing(12)
            num = QLabel(str(n))
            num.setObjectName("HintNumber")
            num.setFixedSize(30, 30)
            num.setAlignment(Qt.AlignCenter)
            tx = QVBoxLayout()
            tx.setSpacing(1)
            a = QLabel(t)
            a.setStyleSheet("font-weight: 800;")
            b = QLabel(d)
            b.setObjectName("Hint")
            b.setWordWrap(True)
            tx.addWidget(a)
            tx.addWidget(b)
            cl.addWidget(num)
            cl.addLayout(tx, 1)
            steps.addWidget(card)
        steps.addStretch()
        outer.addLayout(steps)
        outer.addStretch(3)


# ===================================================================================== dialogs ===
class ConfirmDialog(QDialog):
    """Styled yes/no confirmation with the same accept/reject semantics as QMessageBox.question."""

    def __init__(self, parent, title: str, body: str, yes: str = "Yes", no: str = "No",
                 amount: str | None = None, default_yes: bool = True):
        super().__init__(parent)
        self.setObjectName("ConfirmDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(10)
        t = QLabel(title)
        t.setObjectName("ConfirmTitle")
        lay.addWidget(t)
        if amount:
            a = QLabel(amount)
            a.setObjectName("ConfirmAmount")
            lay.addWidget(a)
        b = QLabel(body)
        b.setObjectName("ConfirmBody")
        b.setWordWrap(True)
        lay.addWidget(b)
        lay.addSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch()
        self.no_btn = QPushButton(no)
        self.no_btn.setProperty("class", "secondary")
        self.yes_btn = QPushButton(yes)
        self.yes_btn.setProperty("class", "primary")
        for btn in (self.no_btn, self.yes_btn):
            btn.setMinimumWidth(130)
            btn.setCursor(Qt.PointingHandCursor)
        self.no_btn.clicked.connect(self.reject)
        self.yes_btn.clicked.connect(self.accept)
        row.addWidget(self.no_btn)
        row.addWidget(self.yes_btn)
        lay.addLayout(row)
        (self.yes_btn if default_yes else self.no_btn).setDefault(True)
        (self.yes_btn if default_yes else self.no_btn).setFocus()

    def showEvent(self, e):
        super().showEvent(e)
        if not T.REDUCE_MOTION:
            self.setWindowOpacity(0.0)
            a = QPropertyAnimation(self, b"windowOpacity", self)
            a.setDuration(T.DUR_FAST)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.start(QPropertyAnimation.DeleteWhenStopped)

    @staticmethod
    def ask(parent, title, body, yes="Yes", no="No", amount=None, default_yes=True) -> bool:
        return ConfirmDialog(parent, title, body, yes, no, amount, default_yes).exec() == QDialog.Accepted
