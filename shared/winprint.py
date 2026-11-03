"""
Windows printer-preferences helpers shared by the Admin and User apps.
Pure ctypes (no pywin32), so the User app needs no extra packages.

Used for the "Print Properties" step of high-profile print types:

  User PC  : show the printer's OWN Windows "Printing Preferences" window
             (paper size, quality, paper type ...), capture the customer's
             choices as a DEVMODE (the exact settings block Windows uses,
             including the driver's private settings).
  Admin PC : after payment is verified, print silently using that same
             DEVMODE - no window appears.
"""

import base64
import ctypes
import logging
import sys

logger = logging.getLogger("winprint")

IS_WINDOWS = sys.platform == "win32"

# DocumentProperties modes
DM_OUT_BUFFER = 2
DM_IN_BUFFER = 8
DM_IN_PROMPT = 4
IDOK = 1
# dmFields bits
DM_ORIENTATION = 0x1
DM_PAPERSIZE = 0x2
DM_COPIES = 0x100
DM_PRINTQUALITY = 0x400
DM_MEDIATYPE = 0x1000000
# DeviceCapabilities
DC_PAPERS = 2
DC_PAPERNAMES = 16
DC_MEDIATYPENAMES = 34
DC_MEDIATYPES = 35

MAX_DEVMODE_BYTES = 16 * 1024


class WinPrintError(Exception):
    pass


class DEVMODEW(ctypes.Structure):
    """Public part of the Windows DEVMODEW structure (printer view)."""
    _fields_ = [
        ("dmDeviceName", ctypes.c_uint16 * 32),   # UTF-16 (explicit so the layout is identical on every platform)
        ("dmSpecVersion", ctypes.c_ushort),
        ("dmDriverVersion", ctypes.c_ushort),
        ("dmSize", ctypes.c_ushort),
        ("dmDriverExtra", ctypes.c_ushort),
        ("dmFields", ctypes.c_uint32),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_uint16 * 32),
        ("dmLogPixels", ctypes.c_ushort),
        ("dmBitsPerPel", ctypes.c_uint32),
        ("dmPelsWidth", ctypes.c_uint32),
        ("dmPelsHeight", ctypes.c_uint32),
        ("dmDisplayFlags", ctypes.c_uint32),
        ("dmDisplayFrequency", ctypes.c_uint32),
        ("dmICMMethod", ctypes.c_uint32),
        ("dmICMIntent", ctypes.c_uint32),
        ("dmMediaType", ctypes.c_uint32),
        ("dmDitherType", ctypes.c_uint32),
        ("dmReserved1", ctypes.c_uint32),
        ("dmReserved2", ctypes.c_uint32),
        ("dmPanningWidth", ctypes.c_uint32),
        ("dmPanningHeight", ctypes.c_uint32),
    ]


def _utf16(arr) -> str:
    return bytes(arr).decode("utf-16-le", errors="ignore").split("\x00")[0].strip()


# ------------------------------------------------------------------
# Pure helpers (testable anywhere)
# ------------------------------------------------------------------
def find_matching_printer(admin_name: str, local_names: list[str]) -> str | None:
    """
    Finds a local Windows printer matching a requested printer name.
    Retained as a compatibility helper for legacy/shared-printer names.
    """
    if not admin_name:
        return None
    want = admin_name.strip().lower()
    base = want.split("\\")[-1]
    for n in local_names:
        if n.strip().lower() == want:
            return n
    for n in local_names:
        low = n.strip().lower()
        if low.split("\\")[-1] == base:
            return n
    for n in local_names:
        low = n.strip().lower()
        if base and (base in low or low.split("\\")[-1] in base) and len(low.split("\\")[-1]) >= 6:
            return n
    return None


def _clean_size(raw: bytes) -> int:
    dm = DEVMODEW.from_buffer_copy(raw[:ctypes.sizeof(DEVMODEW)].ljust(ctypes.sizeof(DEVMODEW), b"\0"))
    return int(dm.dmSize) + int(dm.dmDriverExtra)


def validate_devmode_bytes(raw: bytes) -> None:
    """Sanity checks for a DEVMODE that arrives over the network."""
    if not raw or len(raw) < ctypes.sizeof(DEVMODEW) - 16 or len(raw) > MAX_DEVMODE_BYTES:
        raise WinPrintError("Print properties data has an invalid size.")
    if _clean_size(raw) > len(raw) or _clean_size(raw) < 100:
        raise WinPrintError("Print properties data is corrupt.")


def apply_overrides(raw: bytes, orientation: int | None = None, copies: int | None = None) -> bytes:
    """Returns a copy of the DEVMODE with orientation (1/2) and/or copies overridden."""
    buf = ctypes.create_string_buffer(raw, len(raw))
    dm = DEVMODEW.from_buffer(buf)
    if orientation in (1, 2):
        dm.dmOrientation = orientation
        dm.dmFields |= DM_ORIENTATION
    if copies is not None:
        dm.dmCopies = max(1, int(copies))
        dm.dmFields |= DM_COPIES
    return bytes(buf.raw)


def encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def decode(b64: str) -> bytes:
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        raise WinPrintError("Print properties data is not valid.")
    validate_devmode_bytes(raw)
    return raw


QUALITY_NAMES = {-1: "Draft", -2: "Low", -3: "Normal", -4: "High"}


def describe(raw: bytes, paper_names=None, paper_ids=None, media_names=None, media_ids=None) -> dict:
    """Human-readable {paper_size, quality, paper_type} for showing to the customer/admin."""
    dm = DEVMODEW.from_buffer_copy(raw[:ctypes.sizeof(DEVMODEW)].ljust(ctypes.sizeof(DEVMODEW), b"\0"))
    paper = ""
    if dm.dmFields & DM_PAPERSIZE and paper_ids and paper_names and dm.dmPaperSize in paper_ids:
        i = paper_ids.index(dm.dmPaperSize)
        paper = paper_names[i] if i < len(paper_names) else ""
    if not paper:
        paper = _utf16(dm.dmFormName)

    quality = ""
    if dm.dmFields & DM_PRINTQUALITY:
        q = dm.dmPrintQuality
        quality = QUALITY_NAMES.get(q, f"{q} dpi" if q > 0 else "")

    media = ""
    if dm.dmFields & DM_MEDIATYPE and media_ids and media_names and dm.dmMediaType in media_ids:
        i = media_ids.index(dm.dmMediaType)
        media = media_names[i] if i < len(media_names) else ""

    return {"paper_size": paper, "quality": quality, "paper_type": media}


# ------------------------------------------------------------------
# Windows API (ctypes)
# ------------------------------------------------------------------
_WINSPOOL = None


def _load_winspool():
    """
    The Windows print-spooler library is 'winspool.drv' - NOT 'winspool.dll'.
    'ctypes.windll.winspool' asks Windows for 'winspool.dll', which does not
    exist, so it raises AttributeError/FileNotFoundError ("Could not find module
    'winspool'") - and inside a PyInstaller-built EXE it fails every time.
    Always load it by its real file name, with a full-path fallback.
    """
    try:
        return ctypes.WinDLL("winspool.drv")
    except OSError:
        import os
        root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
        last = None
        for sub in ("System32", "SysWOW64"):
            try:
                return ctypes.WinDLL(os.path.join(root, sub, "winspool.drv"))
            except OSError as e:
                last = e
        raise WinPrintError(f"Could not load the Windows print spooler library (winspool.drv): {last}")


def _winspool():
    global _WINSPOOL
    if _WINSPOOL is not None:
        return _WINSPOOL
    ws = _load_winspool()
    ws.OpenPrinterW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    ws.OpenPrinterW.restype = ctypes.c_int
    ws.ClosePrinter.argtypes = [ctypes.c_void_p]
    ws.DocumentPropertiesW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
    ws.DocumentPropertiesW.restype = ctypes.c_long
    ws.DeviceCapabilitiesW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ushort,
                                       ctypes.c_void_p, ctypes.c_void_p]
    ws.DeviceCapabilitiesW.restype = ctypes.c_int
    _WINSPOOL = ws
    return ws


def dc(name, port, cap, out=None):
    return _winspool().DeviceCapabilitiesW(name, port, cap, out, None)


def dc_strings(name, port, cap, width=64) -> list[str]:
    n = dc(name, port, cap, None)
    if n <= 0:
        return []
    buf = ctypes.create_unicode_buffer(n * width)
    got = dc(name, port, cap, buf)
    if got <= 0:
        return []
    return [buf[i * width:(i + 1) * width].split("\x00")[0].strip() for i in range(got)]


def dc_numbers(name, port, cap, ctype) -> list[int]:
    n = dc(name, port, cap, None)
    if n <= 0:
        return []
    arr = (ctype * n)()
    got = dc(name, port, cap, arr)
    return list(arr[:got]) if got > 0 else []


def list_local_printers() -> list[str]:
    """Names of printers installed on THIS PC (including shared-printer connections)."""
    if not IS_WINDOWS:
        return []

    class PRINTER_INFO_4(ctypes.Structure):
        _fields_ = [("pPrinterName", ctypes.c_wchar_p), ("pServerName", ctypes.c_wchar_p),
                    ("Attributes", ctypes.c_uint32)]

    ws = _winspool()
    ws.EnumPrintersW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p,
                                 ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    flags = 0x2 | 0x4       # PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    needed, returned = ctypes.c_uint32(0), ctypes.c_uint32(0)
    ws.EnumPrintersW(flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned))
    if needed.value == 0:
        return []
    buf = ctypes.create_string_buffer(needed.value)
    if not ws.EnumPrintersW(flags, None, 4, buf, needed.value, ctypes.byref(needed), ctypes.byref(returned)):
        return []
    arr = (PRINTER_INFO_4 * returned.value).from_buffer(buf)
    return [str(a.pPrinterName) for a in arr if a.pPrinterName]


def show_printer_preferences(hwnd: int, printer_name: str, initial: bytes | None = None) -> bytes | None:
    """
    Opens the printer's own Windows "Printing Preferences" window.
    Returns the customer's settings as DEVMODE bytes, or None if they cancelled.
    """
    if not IS_WINDOWS:
        raise WinPrintError("Printer preferences require Windows.")
    ws = _winspool()
    handle = ctypes.c_void_p()
    if not ws.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise WinPrintError(f"Cannot open printer '{printer_name}' on this PC.")
    try:
        size = ws.DocumentPropertiesW(hwnd, handle, printer_name, None, None, 0)
        if size <= 0:
            raise WinPrintError("The printer driver did not provide a settings window.")
        out = ctypes.create_string_buffer(size)
        mode = DM_IN_PROMPT | DM_OUT_BUFFER
        inp = None
        if initial:
            inp = ctypes.create_string_buffer(initial, max(len(initial), size))
            mode |= DM_IN_BUFFER
        rc = ws.DocumentPropertiesW(hwnd, handle, printer_name, out, inp, mode)
        if rc == IDOK:
            n = min(size, _clean_size(out.raw))
            return bytes(out.raw[:n])
        return None
    finally:
        ws.ClosePrinter(handle)


def describe_for_printer(printer_name: str, raw: bytes) -> dict:
    """describe() using the driver's own paper / paper-type names."""
    try:
        return describe(
            raw,
            dc_strings(printer_name, None, DC_PAPERNAMES), dc_numbers(printer_name, None, DC_PAPERS, ctypes.c_uint16),
            dc_strings(printer_name, None, DC_MEDIATYPENAMES), dc_numbers(printer_name, None, DC_MEDIATYPES, ctypes.c_uint32),
        )
    except Exception:
        logger.warning("Could not read driver names for summary", exc_info=True)
        return describe(raw)


def make_printer_dc(printer_name: str, raw: bytes, orientation: int | None = None) -> int:
    """
    Admin PC: creates a printer device context from the customer's DEVMODE
    (validated against this PC's driver). Orientation may be overridden;
    copies are handled by the caller. Returns an HDC handle.
    """
    if not IS_WINDOWS:
        raise WinPrintError("Printing requires Windows.")
    raw = apply_overrides(raw, orientation=orientation, copies=1)
    ws = _winspool()
    handle = ctypes.c_void_p()
    if ws.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        try:
            inp = ctypes.create_string_buffer(raw, len(raw))
            size = ws.DocumentPropertiesW(None, handle, printer_name, None, None, 0)
            if size > 0:
                out = ctypes.create_string_buffer(max(size, len(raw)))
                # Let this PC's driver merge/validate the customer's settings
                if ws.DocumentPropertiesW(None, handle, printer_name, out, inp, DM_IN_BUFFER | DM_OUT_BUFFER) == IDOK:
                    raw = bytes(out.raw[:min(len(out.raw), _clean_size(out.raw))])
        except Exception:
            logger.warning("Driver validation of DEVMODE failed; using it as received.", exc_info=True)
        finally:
            ws.ClosePrinter(handle)
    else:
        code = ctypes.GetLastError()
        logger.warning("OpenPrinter('%s') failed (Windows error %s: %s); proceeding with the "
                        "customer's DEVMODE unvalidated by this PC's driver.",
                        printer_name, code, ctypes.FormatError(code) if code else "none")
    gdi = ctypes.windll.gdi32
    gdi.CreateDCW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p]
    gdi.CreateDCW.restype = ctypes.c_void_p
    buf = ctypes.create_string_buffer(raw, len(raw))
    hdc = gdi.CreateDCW("WINSPOOL", printer_name, None, buf)
    if not hdc:
        code = ctypes.GetLastError()
        detail = f" Windows error {code}: {ctypes.FormatError(code)}" if code else ""
        raise WinPrintError(
            f"Could not open printer '{printer_name}' with the customer's settings.{detail} "
            f"This usually means the printer is off/disconnected/unreachable on the network, "
            f"its driver needs reinstalling, or the Windows Print Spooler service needs restarting."
        )
    return int(hdc)
