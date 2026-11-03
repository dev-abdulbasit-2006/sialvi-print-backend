"""
File preview panel for the kiosk.

  * Images (JPEG/PNG/BMP/GIF/WEBP/TIFF): shown scaled to fit, with EXIF
    rotation applied so phone photos appear the right way up.
  * PDF: rendered page-by-page via PyMuPDF, with Prev/Next page buttons.
  * Word (.doc/.docx) and PowerPoint (.ppt/.pptx): previewed off the GUI
    thread (_OfficePreviewWorker) so the kiosk never freezes while a file
    is being read. Two quality levels, tried in this order:
      1. EXACT: if LibreOffice ("soffice") is installed on this PC, the
         file is converted to PDF in the background and shown exactly
         like a real PDF preview (same renderer, real page count).
      2. BUILT-IN: if LibreOffice isn't installed, python-docx / python-pptx
         (pure-Python, no external program needed) read the file's text
         and it is shown as a formatted content preview (Word) or a
         slide-by-slide mock-up (PowerPoint). This is a content preview,
         not an exact print layout.
    Legacy binary .doc/.ppt files can only be read this way if LibreOffice
    is installed (python-docx/python-pptx only understand the modern
    .docx/.pptx XML format) - if neither is available, a clear message is
    shown instead of guessing.
  * Anything else (XLSX, images formats not listed, PSD/AI, ...): a clear
    "no preview" placeholder - the file still uploads and prints normally.

Preview is purely local (reads the customer's chosen file from disk, and -
for the EXACT path - a temp PDF made from it on this same PC); it never
touches the Admin server, so it cannot affect orders or pricing.
"""

import atexit
import html as html_escape
import logging
import os
import shutil
import subprocess
import tempfile

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtGui import QImage, QImageReader, QPixmap, QPainter, QFont, QColor, QPen
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QTextBrowser,
)

logger = logging.getLogger("user_app.preview")

IMAGE_EXTS = {".jpg", ".jpeg", ".jpe", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
WORD_EXTS = {".doc", ".docx"}
SLIDE_EXTS = {".ppt", ".pptx"}
OFFICE_EXTS = WORD_EXTS | SLIDE_EXTS
DESIGN_EXTS = {".psd", ".ai", ".dwg", ".dxf"}

MAX_IMAGE_DIM = 2200          # downscale huge photos so memory stays small
PDF_RENDER_ZOOM = 1.8
SLIDE_SIZE = QSize(960, 540)  # mock slide canvas, 16:9
LIBREOFFICE_TIMEOUT_SECS = 40

# Resolved once at import time: is LibreOffice available on THIS (User/kiosk)
# PC? If not, we fall back to the pure-Python readers below - no install
# required for the built-in preview to work.
LIBREOFFICE_BIN = shutil.which("soffice") or shutil.which("libreoffice")

try:
    import docx  # python-docx - reads modern .docx only, not legacy .doc
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

try:
    from pptx import Presentation  # python-pptx - reads modern .pptx only
    HAS_PPTX = True
except ImportError:
    HAS_PPTX = False


class _OfficePreviewWorker(QThread):
    """
    Reads/converts a Word or PowerPoint file off the GUI thread, so the
    kiosk stays responsive while a large document is being processed.

    Emits `ready(request_id, kind, payload)` exactly once:
      kind="pdf"          payload=(pdf_path, temp_dir_to_clean_up)
      kind="docx_html"    payload=<html string>
      kind="pptx_slides"  payload=<list of {"title": str, "bullets": [str]}>
      kind="error"        payload=<message to show the customer>
    """
    ready = Signal(int, str, object)

    def __init__(self, request_id, path, ext, parent=None):
        super().__init__(parent)
        self.request_id = request_id
        self.path = path
        self.ext = ext

    def run(self):
        try:
            converted = self._try_libreoffice()
        except Exception:
            logger.exception("LibreOffice preview conversion crashed for %s", self.path)
            converted = None
        if converted:
            self.ready.emit(self.request_id, "pdf", converted)
            return

        if self.ext in WORD_EXTS:
            if self.ext == ".docx" and HAS_DOCX:
                try:
                    self.ready.emit(self.request_id, "docx_html", self._extract_docx_html())
                    return
                except Exception as e:
                    logger.info("python-docx could not read %s: %s", self.path, e)
            self.ready.emit(self.request_id, "error", self._fallback_message())
            return

        if self.ext in SLIDE_EXTS:
            if self.ext == ".pptx" and HAS_PPTX:
                try:
                    slides = self._extract_pptx_slides()
                    self.ready.emit(self.request_id, "pptx_slides", slides)
                    return
                except Exception as e:
                    logger.info("python-pptx could not read %s: %s", self.path, e)
            self.ready.emit(self.request_id, "error", self._fallback_message())
            return

        self.ready.emit(self.request_id, "error", "A preview isn't available for this file type.")

    # ------------------------------------------------------------
    def _try_libreoffice(self):
        """Best available preview: convert to PDF with LibreOffice, if it's
        installed on this PC, and reuse the normal PDF preview for it. Works
        for .doc/.docx/.ppt/.pptx alike, old or new format. Returns None
        (never raises) if LibreOffice is missing or the conversion fails, so
        the caller can fall back to the built-in reader."""
        if not LIBREOFFICE_BIN:
            return None
        tmp_dir = tempfile.mkdtemp(prefix="sialviprint_preview_")
        atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
        profile_dir = os.path.join(tmp_dir, "lo_profile")
        # LibreOffice's -env: URI wants forward slashes even on Windows
        # (mixed/backslash paths are sometimes mis-parsed), and a dedicated
        # profile dir per run avoids "another instance is already running"
        # lock errors if two previews are requested back to back.
        profile_uri = "file:///" + profile_dir.replace(os.sep, "/")
        try:
            subprocess.run(
                [
                    LIBREOFFICE_BIN, "--headless", "--norestore",
                    f"-env:UserInstallation={profile_uri}",
                    "--convert-to", "pdf", "--outdir", tmp_dir, self.path,
                ],
                check=True, timeout=LIBREOFFICE_TIMEOUT_SECS,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as e:
            logger.info("LibreOffice preview conversion unavailable/failed for %s: %s", self.path, e)
            return None
        produced = [f for f in os.listdir(tmp_dir) if f.lower().endswith(".pdf")]
        if not produced:
            return None
        return (os.path.join(tmp_dir, produced[0]), tmp_dir)

    def _extract_docx_html(self):
        """Pure-Python content preview for modern .docx via python-docx.
        No external program required. This mirrors reading order/formatting
        at a paragraph level - not an exact page layout (page breaks depend
        on printer/margins, same honesty note as server-side page counting)."""
        doc = docx.Document(self.path)
        parts = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "").lower() if para.style else ""
            escaped = html_escape.escape(text)
            if style == "title" or "heading 1" in style:
                parts.append(f"<h2 style='margin:10px 0 6px 0;'>{escaped}</h2>")
            elif style.startswith("heading"):
                parts.append(f"<h3 style='margin:8px 0 4px 0;'>{escaped}</h3>")
            elif "list" in style:
                parts.append(f"<p style='margin:2px 0 2px 18px;'>&bull; {escaped}</p>")
            else:
                parts.append(f"<p style='margin:4px 0;'>{escaped}</p>")
            if len(parts) >= 400:
                parts.append("<p><i>&hellip; preview truncated for a long document; "
                              "the full document will still print.</i></p>")
                break
        for table in doc.tables:
            rows_html = []
            for row in table.rows:
                cells = "".join(
                    f"<td style='border:1px solid #cbd5e0; padding:4px 8px;'>{html_escape.escape(c.text)}</td>"
                    for c in row.cells
                )
                rows_html.append(f"<tr>{cells}</tr>")
            parts.append(
                "<table style='border-collapse:collapse; margin:10px 0;'>" + "".join(rows_html) + "</table>"
            )
        if not parts:
            parts.append("<p><i>This document doesn't contain readable text "
                          "(it may be blank or image-only). It will still upload and print normally.</i></p>")
        return "".join(parts)

    def _extract_pptx_slides(self):
        """Pure-Python slide text extraction for modern .pptx via python-pptx.
        Used to draw a simplified mock-up of each slide (title + bullet
        text) - not the real slide design/images, just enough to confirm
        it's the right file before paying."""
        prs = Presentation(self.path)
        slides = []
        for slide in prs.slides:
            title = None
            bullets = []
            for shape in slide.shapes:
                if not shape.has_text_frame:
                    continue
                text = shape.text_frame.text.strip()
                if not text:
                    continue
                is_title = False
                if shape.is_placeholder:
                    try:
                        is_title = shape.placeholder_format.idx == 0
                    except Exception:
                        is_title = False
                if is_title and title is None:
                    title = text
                else:
                    for line in text.splitlines():
                        line = line.strip()
                        if line:
                            bullets.append(line)
            slides.append({"title": title or "", "bullets": bullets[:14]})
            if len(slides) >= 200:
                break
        if not slides:
            raise ValueError("No slides found")
        return slides

    def _fallback_message(self):
        names = {
            ".doc": "Older Word format (.doc)",
            ".docx": "This Word document",
            ".ppt": "Older PowerPoint format (.ppt)",
            ".pptx": "This PowerPoint file",
        }
        label = names.get(self.ext, "This file")
        if self.ext in (".doc", ".ppt"):
            return (
                f"{label}\n\nAn exact preview needs LibreOffice installed on this PC.\n"
                "It will still upload and print correctly."
            )
        return f"{label} couldn't be read for preview.\nIt will still upload and print normally."


class PreviewPanel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("PreviewCard")

        self._source = None        # full-quality QPixmap currently shown
        self._pdf = None           # open fitz document (real PDF, or a LibreOffice-made one)
        self._slides = None        # list of {"title","bullets"} for the built-in PPTX mock-up
        self._page = 0
        self._page_cache = {}
        self._worker = None
        self._request_seq = 0
        self._pending_temp_dir = None   # LibreOffice output folder awaiting cleanup

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("Preview")
        title.setObjectName("CardTitle")
        head.addWidget(title)
        head.addStretch()
        self.name_label = QLabel("")
        self.name_label.setObjectName("Muted")
        head.addWidget(self.name_label)
        lay.addLayout(head)

        self.canvas = QLabel()
        self.canvas.setObjectName("PreviewCanvas")
        self.canvas.setAlignment(Qt.AlignCenter)
        self.canvas.setWordWrap(True)
        self.canvas.setMinimumSize(280, 190)   # was 300: lets the whole workspace fit 1366x768 without scrolling
        self.canvas.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        lay.addWidget(self.canvas, 1)

        # Scrollable rich-text view, used only for the built-in Word content
        # preview (docx_html). Occupies the same slot as the canvas; the two
        # are never visible at the same time.
        self.text_view = QTextBrowser()
        self.text_view.setObjectName("PreviewCanvas")
        self.text_view.setOpenExternalLinks(False)
        self.text_view.setMinimumSize(280, 190)
        self.text_view.setVisible(False)
        lay.addWidget(self.text_view, 1)

        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀")
        self.next_btn = QPushButton("▶")
        for b in (self.prev_btn, self.next_btn):
            b.setProperty("class", "ghost")
            b.setFixedWidth(52)
        self.page_label = QLabel("")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.prev_btn.clicked.connect(lambda: self._goto(self._page - 1))
        self.next_btn.clicked.connect(lambda: self._goto(self._page + 1))
        nav.addStretch()
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.page_label)
        nav.addWidget(self.next_btn)
        nav.addStretch()
        lay.addLayout(nav)

        self.clear()

    # ------------------------------------------------------------
    def clear(self):
        self._close_pdf()
        self._slides = None
        self._source = None
        self.name_label.setText("")
        self._show_text_mode(False)
        self.canvas.setPixmap(QPixmap())
        self.canvas.setText("Select a file to see\na preview here")
        self._set_nav(False)

    def show_file(self, path: str):
        self._request_seq += 1          # invalidates any worker still running for the previous file
        self._close_pdf()
        self._slides = None
        self._page_cache = {}
        self._source = None
        self._show_text_mode(False)

        name = os.path.basename(path)
        self.name_label.setText(self._elide(name, 34))
        ext = os.path.splitext(path)[1].lower()

        try:
            if ext in IMAGE_EXTS:
                self._show_image(path)
            elif ext == ".pdf":
                self._show_pdf(path)
            elif ext in OFFICE_EXTS:
                self._show_message("Loading preview…")
                self._start_office_worker(path, ext)
            elif ext in DESIGN_EXTS:
                self._show_message(
                    "Preview is not available for this design file yet.\n"
                    "It will still upload, page-count and print normally."
                )
            else:
                self._show_placeholder(ext)
        except Exception:
            logger.exception("Preview failed for %s", path)
            self._show_message("Preview couldn't be created for this file.\nIt will still print normally.")

    # ------------------------------------------------------------ Office (async)
    def _start_office_worker(self, path, ext):
        request_id = self._request_seq
        worker = _OfficePreviewWorker(request_id, path, ext, self)
        worker.ready.connect(self._on_office_ready)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_office_ready(self, request_id, kind, payload):
        if request_id != self._request_seq:
            if kind == "pdf":
                # a newer file was picked before this conversion finished -
                # clean up its temp PDF right away instead of waiting for
                # the app to exit.
                _, stale_tmp_dir = payload
                shutil.rmtree(stale_tmp_dir, ignore_errors=True)
            return  # the customer already picked a different file; discard this stale result

        if kind == "pdf":
            pdf_path, tmp_dir = payload
            self._release_temp_dir()
            self._pending_temp_dir = tmp_dir
            self._show_pdf(pdf_path)
        elif kind == "docx_html":
            self._show_text_mode(True, payload)
        elif kind == "pptx_slides":
            self._slides = payload
            self._page_cache = {}
            self._show_text_mode(False)
            self._goto(0)
        else:  # "error"
            self._show_text_mode(False)
            self._show_message(payload)

    def _release_temp_dir(self):
        if self._pending_temp_dir:
            shutil.rmtree(self._pending_temp_dir, ignore_errors=True)
            self._pending_temp_dir = None

    # ------------------------------------------------------------
    def _show_image(self, path):
        reader = QImageReader(path)
        reader.setAutoTransform(True)   # honour EXIF orientation
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > MAX_IMAGE_DIM:
            size.scale(MAX_IMAGE_DIM, MAX_IMAGE_DIM, Qt.KeepAspectRatio)
            reader.setScaledSize(size)
        image = reader.read()
        if image.isNull():
            self._show_message("This image can't be previewed.\nIt will still be checked when uploaded.")
            return
        self._source = QPixmap.fromImage(image)
        self._set_nav(False)
        self._rescale()

    def _show_pdf(self, path):
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        if doc.needs_pass:
            doc.close()
            self._show_message("This PDF is password-protected,\nso it can't be previewed or printed.")
            return
        self._pdf = doc
        self._slides = None
        self._page_cache = {}
        self._goto(0)

    def _page_count(self):
        if self._pdf is not None:
            return self._pdf.page_count
        if self._slides is not None:
            return len(self._slides)
        return 0

    def _goto(self, page: int):
        count = self._page_count()
        if count == 0:
            self._show_message("This file has no pages.")
            return
        page = max(0, min(page, count - 1))
        self._page = page
        if page not in self._page_cache:
            if self._pdf is not None:
                pix = self._pdf.load_page(page).get_pixmap(matrix=self._matrix(), alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                self._page_cache[page] = QPixmap.fromImage(img)
            else:
                self._page_cache[page] = self._render_slide_pixmap(self._slides[page], page, count)
        self._source = self._page_cache[page]
        label = "Page" if self._pdf is not None else "Slide"
        self.page_label.setText(f"{label} {page + 1} of {count}")
        self._set_nav(count > 1)
        self._rescale()

    def _matrix(self):
        import fitz
        return fitz.Matrix(PDF_RENDER_ZOOM, PDF_RENDER_ZOOM)

    def _render_slide_pixmap(self, slide, index, count):
        """Simplified mock-up of one PPTX slide (title + bullet text only -
        not the real design, colours or images) built with the built-in
        pure-Python reader, used when LibreOffice isn't installed."""
        pm = QPixmap(SLIDE_SIZE)
        pm.fill(QColor("#ffffff"))
        painter = QPainter(pm)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            border_pen = QPen(QColor("#05203B"))
            border_pen.setWidth(3)
            painter.setPen(border_pen)
            painter.drawRect(2, 2, SLIDE_SIZE.width() - 4, SLIDE_SIZE.height() - 4)

            margin = 48
            y = 36
            title = slide.get("title") or ""
            if title:
                painter.setPen(QColor("#05203B"))
                painter.setFont(QFont("Segoe UI", 26, QFont.Bold))
                title_rect = pm.rect().adjusted(margin, y, -margin, 0)
                title_rect.setHeight(96)
                painter.drawText(title_rect, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, title)
                y += 116
            else:
                y += 20

            painter.setPen(QColor("#33475b"))
            painter.setFont(QFont("Segoe UI", 15))
            body_rect = pm.rect().adjusted(margin, y, -margin, -50)
            bullets = slide.get("bullets") or []
            body_text = "\n".join(f"•  {b}" for b in bullets) if bullets else "(No text content on this slide)"
            painter.drawText(body_rect, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, body_text)

            painter.setPen(QColor("#8a97a6"))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(
                pm.rect().adjusted(0, 0, -16, -14), Qt.AlignRight | Qt.AlignBottom,
                f"Slide {index + 1} of {count} — content preview",
            )
        finally:
            painter.end()
        return pm

    def _show_placeholder(self, ext):
        badge = (ext.lstrip(".") or "file").upper()
        self._show_message(
            f"{badge}\n\nA visual preview isn't available for this file type.\n"
            "It will upload and print normally — the page count appears in the file list."
        )

    # ------------------------------------------------------------
    def _show_text_mode(self, visible, html_content=None):
        self.text_view.setVisible(visible)
        self.canvas.setVisible(not visible)
        if visible:
            self._set_nav(False)
            self.text_view.setHtml(
                "<div style='font-family:\"Segoe UI\",Arial,sans-serif; color:#1b2b3a; font-size:13px;'>"
                + (html_content or "") + "</div>"
            )
            self.text_view.verticalScrollBar().setValue(0)

    def _show_message(self, text):
        self._source = None
        self._show_text_mode(False)
        self.canvas.setPixmap(QPixmap())
        self.canvas.setText(text)
        self._set_nav(False)

    def _set_nav(self, visible: bool):
        for w in (self.prev_btn, self.next_btn, self.page_label):
            w.setVisible(visible)
        if visible:
            count = self._page_count()
            self.prev_btn.setEnabled(self._page > 0)
            self.next_btn.setEnabled(self._page < count - 1)

    def _rescale(self):
        if self._source is None or self._source.isNull():
            return
        target = self.canvas.size() - self._margin()
        self.canvas.setPixmap(self._source.scaled(target, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    @staticmethod
    def _margin():
        from PySide6.QtCore import QSize as _QSize
        return _QSize(12, 12)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def _close_pdf(self):
        if self._pdf is not None:
            try:
                self._pdf.close()
            except Exception:
                pass
        self._pdf = None
        self._page_cache = {}
        self._page = 0
        self._release_temp_dir()

    @staticmethod
    def _elide(text, n):
        return text if len(text) <= n else text[: n - 1] + "…"
