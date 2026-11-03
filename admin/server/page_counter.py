"""
Determines page count per file.

Honesty note (per requirement 9): true page count for flow-based
formats (DOCX, XLSX, PPTX, TXT, RTF, CSV) depends on rendering/layout,
which varies by printer driver, margins, and content. This module:

  - Gives EXACT counts for formats with real page boundaries: PDF
    (PyMuPDF), PPTX (slide count = page count), and images (always 1).
  - Gives the BEST AVAILABLE real count for DOCX by converting to PDF
    via LibreOffice headless (if installed) and counting the result -
    this is the same rendering path that will actually be printed, so
    the count matches what prints.
  - For XLSX/CSV/TXT/RTF, requires the same LibreOffice conversion
    path; if LibreOffice is not available on the Admin PC, the file is
    marked FAILED with a clear message rather than guessing a page
    count that could under-charge or under-print the customer.
  - PSD/AI and AutoCAD files are converted to a PDF by the shared design-file
    converter and then counted exactly from that PDF. If the required external
    converter is missing, the file is marked FAILED with a clear dependency
    message rather than guessing.
"""

import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("page_counter")

try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

def _find_libreoffice() -> str | None:
    """PATH first (works everywhere); if not found, check the locations
    LibreOffice's own Windows installer uses by default - many shop PCs
    never get soffice added to PATH even though it's genuinely installed."""
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ]
        for c in candidates:
            if Path(c).is_file():
                return c
    return None


LIBREOFFICE_BIN = _find_libreoffice()


class PageCountError(Exception):
    pass


def count_pages(file_path: str, extension: str) -> int:
    ext = extension.lower()

    if ext == "pdf":
        return _count_pdf(file_path)

    if ext in ("jpg", "jpeg", "png", "bmp", "tiff"):
        return 1  # a single image is always one printable page

    if ext == "pptx":
        return _count_pptx(file_path)

    if ext in ("doc", "docx", "xls", "xlsx", "ppt", "txt", "rtf", "csv"):
        return _count_via_libreoffice(file_path)

    if ext in ("psd", "ai", "dwg", "dxf"):
        return _count_via_design_converter(file_path, ext)

    raise PageCountError(f"No page-counting strategy defined for .{ext}")


def _count_via_design_converter(file_path: str, extension: str) -> int:
    """Convert Adobe/CAD input to the exact PDF that will be printed, then count it."""
    try:
        from admin.server.design_file_converter import convert_design_to_pdf
        temp_dir, pdf_path = convert_design_to_pdf(file_path, extension)
    except Exception as e:
        raise PageCountError(str(e)) from e

    try:
        return _count_pdf(pdf_path)
    finally:
        temp_dir.cleanup()


def _count_pdf(file_path: str) -> int:
    if not HAS_FITZ:
        raise PageCountError("PyMuPDF (fitz) is not installed on the Admin PC.")
    try:
        doc = fitz.open(file_path)
        try:
            return doc.page_count
        finally:
            doc.close()
    except Exception as e:
        raise PageCountError(f"Could not read PDF: {e}")


def _count_pptx(file_path: str) -> int:
    try:
        from pptx import Presentation
    except ImportError:
        raise PageCountError("python-pptx is not installed on the Admin PC.")
    try:
        prs = Presentation(file_path)
        return len(prs.slides.__iter__.__self__._sldIdLst)  # slide count
    except Exception as e:
        raise PageCountError(f"Could not read PPTX: {e}")


def _count_via_libreoffice(file_path: str) -> int:
    """
    Converts the file to PDF using the same LibreOffice headless engine
    that will later be used to actually render it for printing, then
    counts the resulting PDF's pages. This guarantees the page count
    shown to the customer matches what physically prints.
    """
    if not LIBREOFFICE_BIN:
        raise PageCountError(
            "LibreOffice is required on the Admin PC to accurately count "
            "pages for this file type and was not found. Install "
            "LibreOffice (soffice) and ensure it is on PATH."
        )
    if not HAS_FITZ:
        raise PageCountError("PyMuPDF (fitz) is not installed on the Admin PC.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            subprocess.run(
                [LIBREOFFICE_BIN, "--headless", "--convert-to", "pdf",
                 "--outdir", tmp_dir, file_path],
                check=True, timeout=120,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except subprocess.TimeoutExpired:
            raise PageCountError("Conversion to PDF timed out.")
        except subprocess.CalledProcessError as e:
            raise PageCountError(f"LibreOffice conversion failed: {e.stderr.decode(errors='ignore')[:300]}")

        produced = list(Path(tmp_dir).glob("*.pdf"))
        if not produced:
            raise PageCountError("LibreOffice did not produce a PDF output.")
        return _count_pdf(str(produced[0]))
