"""
Silent (dialog-free) printing with the customer's print properties.

Why this exists
---------------
Sending an image to the Windows "print" verb opens the Windows *Print
Pictures* wizard on the Admin PC (paper size / quality / paper type ...),
which forced the shop owner to click through it AFTER verifying payment.
Instead, the customer now chooses those properties on the User PC BEFORE
paying (they are stored with the order), and this module prints on the
Admin PC automatically, applying them directly to the printer with no
window of any kind.

How it prints
-------------
  * Pages are rendered (PDF -> PyMuPDF, images -> Pillow, EXIF-rotated)
    at up to 300 DPI and drawn onto a printer device context created with
    a DEVMODE that carries: paper size, paper type (media), print quality,
    orientation.  Fit-to-page, centred.
  * Property names chosen by the customer are matched against what the
    printer driver itself reports (DeviceCapabilities), so only real
    options are offered and applied.
  * The customer's page range and number of copies are honoured.

Windows-only (pywin32 + Pillow.ImageWin). Nothing here is imported on
other platforms except the pure helper functions.
"""

import ctypes
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger("silent_print")

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import win32con
    import win32gui
    import win32print
    import win32ui

IMAGE_EXTS = {".jpg", ".jpeg", ".jpe", ".png", ".bmp", ".tif", ".tiff"}

# DEVMODE.dmFields bits
DM_ORIENTATION = 0x00000001
DM_PAPERSIZE = 0x00000002
DM_PAPERLENGTH = 0x00000004
DM_PAPERWIDTH = 0x00000008
DM_PRINTQUALITY = 0x00000400
DM_COLOR = 0x00000800
DM_MEDIATYPE = 0x01000000
# DeviceCapabilities codes
DC_PAPERS = 2
DC_PAPERNAMES = 16
DC_MEDIATYPENAMES = 34
DC_MEDIATYPES = 35
# Print quality (dmPrintQuality negative values)
QUALITY_TO_DMRES = {"draft": -1, "normal": -3, "high": -4}
QUALITY_CHOICES = ["Draft", "Normal", "High"]

MAX_RENDER_DPI = 300

# Used only if the driver reports nothing (or when developing off-Windows)
FALLBACK_PAPER_SIZES = ["A3", "A4", "A5", "Letter", "Legal"]
FALLBACK_PAPER_TYPES = ["Plain Paper", "Glossy Photo Paper", "Matte Photo Paper", "Luster Photo Paper"]


class SilentPrintError(Exception):
    pass


# ------------------------------------------------------------------
# Pure helpers (unit-testable anywhere)
# ------------------------------------------------------------------
def dedupe(items):
    seen, out = set(), []
    for x in items:
        key = x.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(x.strip())
    return out


def pick_default(names: list[str], *tokens: str):
    """First name containing any token (case-insensitive), preferring the shortest match."""
    best = None
    for tok in tokens:
        if not tok:
            continue
        for n in names:
            if tok.lower() in n.lower() and (best is None or len(n) < len(best)):
                best = n
        if best:
            return best
    return None


def match_index(names: list[str], wanted: str, startswith_token: str | None = None):
    """Index of `wanted` in names (exact, case-insensitive), else best 'starts with token' match."""
    if not names:
        return None
    if wanted:
        for i, n in enumerate(names):
            if n.strip().lower() == wanted.strip().lower():
                return i
    token = (startswith_token or wanted or "").strip().lower()
    if token:
        cands = [(len(n), i) for i, n in enumerate(names)
                 if re.match(rf"^{re.escape(token)}(\b|\s|\()", n.strip().lower())]
        if cands:
            return min(cands)[1]
    return None


def fit_size(w: float, h: float, box_w: float, box_h: float) -> tuple[int, int]:
    """Largest size with the same aspect as (w,h) that fits in the box (up- or down-scaling)."""
    if w <= 0 or h <= 0:
        return int(box_w), int(box_h)
    s = min(box_w / w, box_h / h)
    return max(1, int(w * s)), max(1, int(h * s))


# ------------------------------------------------------------------
# What the driver supports (drives the User's "Print Properties" popup)
# ------------------------------------------------------------------
# Driver-capability helpers live in shared/winprint.py (pure ctypes, also used by the User app)
from shared import winprint
from shared.winprint import dc_strings as _dc_strings, dc_numbers as _dc_numbers  # noqa: E402


def _port_of(printer_name: str) -> str:
    h = win32print.OpenPrinter(printer_name)
    try:
        return win32print.GetPrinter(h, 2).get("pPortName", "") or ""
    finally:
        win32print.ClosePrinter(h)


def get_print_options(printer_name: str, profile_paper: str = "", profile_media: str = "") -> dict:
    """
    Options offered to the customer. On Windows these come from the printer
    driver; if the driver reports nothing, safe generic lists are returned.
    """
    paper_sizes, paper_types = [], []
    if IS_WINDOWS:
        try:
            port = _port_of(printer_name)
            paper_sizes = dedupe(_dc_strings(printer_name, port, DC_PAPERNAMES))
            paper_types = dedupe(_dc_strings(printer_name, port, DC_MEDIATYPENAMES))
        except Exception:
            logger.exception("Could not read print options from driver for '%s'", printer_name)

    from_driver = bool(paper_sizes or paper_types)
    if not paper_sizes:
        paper_sizes = list(FALLBACK_PAPER_SIZES)
    if not paper_types:
        paper_types = list(FALLBACK_PAPER_TYPES)

    media_words = {"GLOSSY": ("gloss",), "MATTE": ("matte",), "LUSTER": ("luster", "lustre"),
                   "PLAIN": ("plain",)}.get((profile_media or "").upper(), ())
    idx = match_index(paper_sizes, "", profile_paper) if profile_paper else None
    return {
        "paper_sizes": paper_sizes,
        "paper_types": paper_types,
        "qualities": list(QUALITY_CHOICES),
        "from_driver": from_driver,
        "defaults": {
            "paper_size": paper_sizes[idx] if idx is not None else paper_sizes[0],
            "paper_type": pick_default(paper_types, *media_words) or paper_types[0],
            "quality": "High",
        },
    }


# ------------------------------------------------------------------
# DEVMODE
# ------------------------------------------------------------------
def _try_set(dm, attr, value):
    try:
        setattr(dm, attr, value)
        return True
    except Exception:
        logger.warning("Driver DEVMODE does not accept %s=%r", attr, value)
        return False


def _build_devmode(printer_name: str, landscape: bool, settings: dict, profile_paper: str, mono: bool = False):
    h = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(h, 2)
        port = info.get("pPortName", "") or ""
        dm = info.get("pDevMode")
        if dm is None:
            dm = win32print.DocumentProperties(0, h, printer_name, None, None, win32con.DM_OUT_BUFFER)

        dm.Fields |= DM_ORIENTATION
        _try_set(dm, "Orientation", 2 if landscape else 1)
        if mono and _try_set(dm, "Color", 1):        # DMCOLOR_MONOCHROME: Black & White types never use color ink
            dm.Fields |= DM_COLOR

        # ---- paper size ----
        names = _dc_strings(printer_name, port, DC_PAPERNAMES)
        ids = _dc_numbers(printer_name, port, DC_PAPERS, ctypes.c_uint16)
        wanted = settings.get("paper_size", "")
        i = match_index(names, wanted, None if wanted else profile_paper)
        if i is None and wanted:
            i = match_index(names, "", profile_paper)
        if i is not None and i < len(ids):
            dm.Fields &= ~(DM_PAPERLENGTH | DM_PAPERWIDTH)
            if _try_set(dm, "PaperSize", ids[i]):
                dm.Fields |= DM_PAPERSIZE
                logger.info("Paper size -> %s (id %s)", names[i], ids[i])
        else:
            logger.warning("Paper size '%s' not found on '%s'; driver default used.", wanted or profile_paper, printer_name)

        # ---- paper type (media) ----
        wanted_media = settings.get("paper_type", "")
        if wanted_media:
            mnames = _dc_strings(printer_name, port, DC_MEDIATYPENAMES)
            mids = _dc_numbers(printer_name, port, DC_MEDIATYPES, ctypes.c_uint32)
            j = match_index(mnames, wanted_media)
            if j is not None and j < len(mids):
                if _try_set(dm, "MediaType", mids[j]):
                    dm.Fields |= DM_MEDIATYPE
                    logger.info("Paper type -> %s (id %s)", mnames[j], mids[j])
            else:
                logger.warning("Paper type '%s' not reported by driver of '%s'; driver default used.",
                               wanted_media, printer_name)

        # ---- quality ----
        q = QUALITY_TO_DMRES.get(settings.get("quality", "").strip().lower())
        if q is not None and _try_set(dm, "PrintQuality", q):
            dm.Fields |= DM_PRINTQUALITY

        # Let the driver validate/merge (also fills driver-private settings consistently)
        try:
            win32print.DocumentProperties(0, h, printer_name, dm, dm,
                                          win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
        except Exception:
            logger.warning("Driver DEVMODE validation failed; using values as set.", exc_info=True)
        return dm
    finally:
        win32print.ClosePrinter(h)


# ------------------------------------------------------------------
# Page sources
# ------------------------------------------------------------------
class _Source:
    """Uniform access to the pages of a PDF or an image."""

    def __init__(self, path: str, page_range: str):
        def _parse_page_range(range_str: str, total_pages: int) -> list[int]:
            if not range_str or not range_str.strip():
                return list(range(1, total_pages + 1))
            pages = set()
            for chunk in range_str.split(","):
                chunk = chunk.strip()
                m = re.match(r"^(\d+)\s*(?:-\s*(\d+))?$", chunk)
                if not m:
                    raise SilentPrintError(f"Invalid page range segment: '{chunk}'.")
                start = int(m.group(1))
                end = int(m.group(2) or start)
                if start > end:
                    start, end = end, start
                if start < 1 or end > total_pages:
                    raise SilentPrintError(
                        f"Page range '{chunk}' is outside document bounds (1-{total_pages})."
                    )
                pages.update(range(start, end + 1))
            if not pages:
                raise SilentPrintError("Page range resolved to zero pages.")
            return sorted(pages)

        self.ext = Path(path).suffix.lower()
        self.path = path
        self.doc = None
        if self.ext == ".pdf":
            import fitz
            self.doc = fitz.open(path)
            if self.doc.needs_pass:
                raise SilentPrintError("PDF is password-protected.")
            total = self.doc.page_count
        elif self.ext in IMAGE_EXTS:
            total = 1
        else:
            raise SilentPrintError(f"Silent printing does not support '{self.ext}' files.")
        self.pages = _parse_page_range(page_range or "", total)   # 1-based, validated

    def close(self):
        if self.doc is not None:
            self.doc.close()

    def size(self, page_no: int) -> tuple[float, float]:
        if self.doc is not None:
            r = self.doc.load_page(page_no - 1).rect
            return r.width, r.height
        from PIL import Image
        with Image.open(self.path) as im:
            w, h = im.size
            try:
                if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):
                    w, h = h, w
            except Exception:
                pass
            return w, h

    def render(self, page_no: int, box_w: int, box_h: int):
        """Returns an RGB PIL image no larger than the box."""
        from PIL import Image, ImageOps
        if self.doc is not None:
            page = self.doc.load_page(page_no - 1)
            import fitz
            z = min(box_w / page.rect.width, box_h / page.rect.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
            return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        with Image.open(self.path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            if im.width > box_w or im.height > box_h:
                w, h = fit_size(im.width, im.height, box_w, box_h)
                im = im.resize((w, h), Image.LANCZOS)
            return im.copy()


def _describe_createdc_failure(printer_name: str, exc: Exception | None) -> str:
    """
    CreateDC on a printer driver can fail without pywin32 raising a useful
    message (sometimes the exception text is just the driver token
    "winspool" with no real explanation, because the driver never called
    SetLastError). This always produces something a person can act on.
    """
    code = ctypes.GetLastError()
    win_text = ""
    if code:
        try:
            win_text = f" Windows error {code}: {ctypes.FormatError(code)}"
        except Exception:
            win_text = f" Windows error {code}."
    exc_text = f" ({exc})" if exc and str(exc) not in ("", "winspool") else ""
    return (
        f"Could not open printer '{printer_name}' for printing.{win_text}{exc_text} "
        f"This usually means the printer is off/disconnected/unreachable on the network, "
        f"its driver needs reinstalling, or the Windows Print Spooler service needs "
        f"restarting (Admin PC: Win+R -> services.msc -> Print Spooler -> Restart)."
    )


def _check(result, step_name: str, printer_name: str):
    """
    GDI/spooler calls (StartDoc/StartPage/EndPage/EndDoc) return <= 0 on
    failure without always raising a Python exception - silently treating
    that as success is exactly how a job can be marked PRINTED while no
    paper actually came out. Fail loudly and specifically instead.
    """
    # NOTE: pywin32's win32ui CDC.StartPage/EndPage/EndDoc return None on success
    # and raise win32ui.error themselves on failure - None is therefore SUCCESS.
    # Only a real integer <= 0 (or False) is a failure signal.
    if isinstance(result, int) and result <= 0:
        code = ctypes.GetLastError()
        detail = f" Windows error {code}: {ctypes.FormatError(code)}" if code else ""
        raise SilentPrintError(
            f"{step_name} failed on printer '{printer_name}' (returned {result}).{detail} "
            f"The job did not complete - treat this as NOT printed, do not assume it did."
        )
    return result


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------
def print_file(printer_name: str, file_path: str, copies: int = 1, orientation: str = "AUTO",
               page_range: str = "", settings: dict | None = None, profile_paper: str = "",
               color_mode: str = ""):
    if not IS_WINDOWS:
        raise SilentPrintError("Printing requires Windows.")
    from PIL import ImageWin

    settings = settings or {}
    src = _Source(file_path, page_range)
    dc = None
    job_id = None
    try:
        first_w, first_h = src.size(src.pages[0])
        orient = (orientation or "AUTO").upper()
        landscape = (orient == "LANDSCAPE") or (orient == "AUTO" and first_w > first_h)

        if settings.get("devmode_b64"):
            # The customer chose their settings in the printer's own Windows
            # "Printing Preferences" window on the User PC; that exact DEVMODE
            # (incl. the driver's private settings) is applied here.
            try:
                raw = winprint.decode(settings["devmode_b64"])
                hdc = winprint.make_printer_dc(printer_name, raw, orientation=2 if landscape else 1)
                logger.info("Printing with the customer's Windows printer preferences: %s",
                            {k: settings.get(k) for k in ("paper_size", "quality", "paper_type")})
            except winprint.WinPrintError as e:
                raise SilentPrintError(str(e))
        else:
            dm = _build_devmode(printer_name, landscape, settings, profile_paper, mono=(color_mode == "BW"))
            try:
                hdc = win32gui.CreateDC("WINSPOOL", printer_name, dm)
            except Exception as e:
                raise SilentPrintError(_describe_createdc_failure(printer_name, e))
        if not hdc:
            raise SilentPrintError(_describe_createdc_failure(printer_name, None))
        dc = win32ui.CreateDCFromHandle(hdc)

        dev_w = dc.GetDeviceCaps(win32con.HORZRES)
        dev_h = dc.GetDeviceCaps(win32con.VERTRES)
        dpi = max(1, dc.GetDeviceCaps(win32con.LOGPIXELSX))
        scale = min(1.0, MAX_RENDER_DPI / dpi)
        box_w, box_h = max(1, int(dev_w * scale)), max(1, int(dev_h * scale))
        dev_landscape = dev_w > dev_h

        cached = None
        # StartDoc's return value IS the real Windows spooler job ID when it
        # succeeds (and <=0 on failure) - log it so a failure can be traced
        # in Windows' own print queue (Devices and Printers -> this printer
        # -> "See what's printing"), not just in our own log.
        job_id = _check(dc.StartDoc("SialviPrint"), "StartDoc", printer_name)
        logger.info("Spooler job #%s opened on '%s' for '%s'", job_id, printer_name, Path(file_path).name)
        try:
            for _copy in range(max(1, int(copies))):
                for page_no in src.pages:
                    pw, ph = src.size(page_no)
                    rotate = (orient == "AUTO") and ((pw > ph) != dev_landscape)
                    if len(src.pages) == 1 and cached is not None:
                        img = cached
                    else:
                        bw, bh = (box_h, box_w) if rotate else (box_w, box_h)
                        img = src.render(page_no, bw, bh)
                        if rotate:
                            img = img.rotate(90, expand=True)
                        if len(src.pages) == 1:
                            cached = img
                    if color_mode == "BW" and img.mode != "L":
                        img = img.convert("L")            # grayscale: smaller spool data, no color
                    tw, th = fit_size(img.width, img.height, dev_w, dev_h)
                    x0, y0 = (dev_w - tw) // 2, (dev_h - th) // 2
                    _check(dc.StartPage(), "StartPage", printer_name)
                    # Keep the Dib explicitly alive through the draw AND the
                    # matching EndPage: GDI's own drawing/spooling can happen
                    # asynchronously relative to Python's draw() call
                    # returning, and if the Dib is garbage-collected first
                    # its DC/bitmap handles can be torn down mid-spool -
                    # producing a blank, corrupted, or silently-dropped page.
                    # This is a well-known pywin32 ImageWin.Dib pitfall.
                    dib = ImageWin.Dib(img)
                    try:
                        dib.draw(dc.GetHandleOutput(), (x0, y0, x0 + tw, y0 + th))
                        _check(dc.EndPage(), "EndPage", printer_name)
                    finally:
                        del dib
            _check(dc.EndDoc(), "EndDoc", printer_name)
            logger.info("Spooler job #%s completed successfully on '%s'", job_id, printer_name)
        except Exception:
            try:
                dc.AbortDoc()
                logger.error("Spooler job #%s aborted on '%s'", job_id, printer_name)
            except Exception:
                pass
            raise
    except SilentPrintError:
        raise
    except Exception as e:
        logger.exception("Silent print failed for %s (job #%s)", file_path, job_id)
        raise SilentPrintError(f"Could not print '{Path(file_path).name}': {e}")
    finally:
        if dc is not None:
            try:
                dc.DeleteDC()
            except Exception:
                pass
        src.close()
