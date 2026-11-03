"""Conversion/rendering for Adobe and AutoCAD source files.

Supported source formats:
- PSD / AI: ImageMagick (with the required delegates, such as Ghostscript for AI)
- DXF: ezdxf's native drawing renderer -> PDF
- DWG: ODA File Converter -> DXF -> ezdxf -> PDF

The module deliberately does not guess a page count.  Every supported design file
is converted to a PDF first; the normal PDF page counter and silent PDF printer then
operate on that exact rendered output.
"""

import glob
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("design_file_converter")

DESIGN_EXTS = {".psd", ".ai", ".dwg", ".dxf"}
ADOBE_EXTS = {".psd", ".ai"}
CAD_EXTS = {".dwg", ".dxf"}


def _first_existing(paths):
    for raw in paths:
        p = Path(raw)
        if p.is_file():
            return str(p)
    return None


def find_imagemagick() -> str | None:
    """Find ImageMagick's magick executable without requiring PATH setup."""
    found = shutil.which("magick") or shutil.which("magick.exe")
    if found:
        return found
    if sys.platform == "win32":
        matches = []
        for pattern in (
            r"C:\\Program Files\\ImageMagick-*\\magick.exe",
            r"C:\\Program Files (x86)\\ImageMagick-*\\magick.exe",
        ):
            matches.extend(glob.glob(pattern))
        return _first_existing(sorted(matches, reverse=True))
    return None


def find_oda_converter() -> str | None:
    """Find ODA File Converter on Windows, or a PATH override."""
    override = os.environ.get("ODA_FILE_CONVERTER_PATH", "").strip().strip('"')
    if override:
        if Path(override).is_file():
            return override
        raise RuntimeError(
            f"ODA_FILE_CONVERTER_PATH points to a missing file: {override}"
        )

    found = shutil.which("ODAFileConverter") or shutil.which("ODAFileConverter.exe")
    if found:
        return found

    if sys.platform == "win32":
        matches = []
        for pattern in (
            r"C:\\Program Files\\ODA\\ODAFileConverter*\\ODAFileConverter.exe",
            r"C:\\Program Files (x86)\\ODA\\ODAFileConverter*\\ODAFileConverter.exe",
        ):
            matches.extend(glob.glob(pattern))
        return _first_existing(sorted(matches, reverse=True))
    return None


def convert_design_to_pdf(file_path: str, extension: str | None = None):
    """Return ``(TemporaryDirectory, pdf_path)`` for a design/CAD source.

    The caller owns the returned TemporaryDirectory and must keep it alive until
    printing/page-counting is finished.
    """
    ext = (extension or Path(file_path).suffix).lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in DESIGN_EXTS:
        raise RuntimeError(f"No design-file converter is defined for '{ext}'.")

    tmp = tempfile.TemporaryDirectory(prefix="sialviprint_design_")
    try:
        if ext in ADOBE_EXTS:
            pdf_path = _convert_adobe(file_path, ext, Path(tmp.name))
        elif ext == ".dwg":
            dxf_path = _convert_dwg_to_dxf(file_path, Path(tmp.name))
            pdf_path = _render_dxf_to_pdf(dxf_path, Path(tmp.name))
        else:  # .dxf
            pdf_path = _render_dxf_to_pdf(file_path, Path(tmp.name))
        return tmp, pdf_path
    except Exception:
        tmp.cleanup()
        raise


def _convert_adobe(file_path: str, ext: str, tmp_dir: Path) -> str:
    magick = find_imagemagick()
    if not magick:
        raise RuntimeError(
            f".{ext.lstrip('.').upper()} files require ImageMagick on the Admin PC. "
            "Install ImageMagick and restart SialviPrint. For .AI files, the "
            "ImageMagick installation must also include its PostScript/Ghostscript delegate."
        )

    out = tmp_dir / "design.pdf"
    # 300 DPI gives a print-quality rasterisation for PSD and AI while keeping
    # the normal PDF printer pipeline unchanged. ImageMagick's AI reader uses
    # its PostScript delegate where required.
    cmd = [
        magick,
        "-density", "300",
        file_path,
        "-background", "white",
        "-alpha", "remove",
        "-alpha", "off",
        "-flatten",
        str(out),
    ]
    try:
        completed = subprocess.run(
            cmd,
            check=True,
            timeout=240,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise RuntimeError("ImageMagick was found but could not be started.")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f".{ext.lstrip('.').upper()} conversion to PDF timed out.")
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode(errors="ignore").strip()[:500]
        raise RuntimeError(
            f"ImageMagick could not convert .{ext.lstrip('.').upper()} to PDF"
            f"{': ' + detail if detail else '.'}"
        )

    if not out.is_file() or out.stat().st_size == 0:
        detail = completed.stderr.decode(errors="ignore").strip()[:300]
        raise RuntimeError(
            f"ImageMagick finished without producing a usable PDF"
            f"{': ' + detail if detail else '.'}"
        )
    return str(out)


def _convert_dwg_to_dxf(file_path: str, tmp_dir: Path) -> str:
    converter = find_oda_converter()
    if not converter:
        raise RuntimeError(
            ".DWG files require the ODA File Converter on the Admin PC. "
            "Install/configure it, or set ODA_FILE_CONVERTER_PATH to "
            "ODAFileConverter.exe, then restart SialviPrint."
        )

    source_dir = tmp_dir / "dwg_source"
    target_dir = tmp_dir / "dxf_output"
    source_dir.mkdir()
    target_dir.mkdir()
    source = source_dir / Path(file_path).name
    shutil.copy2(file_path, source)

    # ODA's documented CLI takes source dir, target dir, output version/type,
    # recursive flag and audit flag. R2018/ACAD2018 is a broadly compatible
    # DXF target for the next stage (ezdxf).
    cmd = [converter, str(source_dir), str(target_dir), "ACAD2018", "DXF", "0", "1"]
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            timeout=240,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise RuntimeError("ODA File Converter could not be started.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("DWG conversion to DXF timed out.")

    produced = target_dir / (source.stem + ".dxf")
    if completed.returncode != 0 or not produced.is_file():
        detail = (
            completed.stderr.decode(errors="ignore").strip()
            or completed.stdout.decode(errors="ignore").strip()
        )[:500]
        raise RuntimeError(
            "ODA File Converter could not convert this DWG to DXF"
            f"{': ' + detail if detail else '.'}"
        )
    return str(produced)


def _render_dxf_to_pdf(file_path: str, tmp_dir: Path) -> str:
    try:
        import ezdxf
        from ezdxf.addons.drawing import matplotlib
    except ImportError as e:
        raise RuntimeError(
            "DXF/CAD rendering requires the ezdxf package in the Admin build. "
            "Rebuild SialviPrint from the updated requirements.txt."
        ) from e

    try:
        doc = ezdxf.readfile(file_path)
    except Exception as e:
        raise RuntimeError(f"Could not read AutoCAD DXF data: {e}") from e

    # Prefer the model space because it is the common denominator for DXF files
    # produced by CAD programs and does not depend on a customer's printer/plotter
    # configuration being embedded in the source file.
    try:
        model = doc.modelspace()
        pdf_path = tmp_dir / "cad.pdf"
        matplotlib.qsave(model, str(pdf_path), dpi=300)
    except Exception as e:
        raise RuntimeError(f"Could not render AutoCAD drawing to PDF: {e}") from e

    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError("CAD renderer did not produce a usable PDF.")
    return str(pdf_path)
