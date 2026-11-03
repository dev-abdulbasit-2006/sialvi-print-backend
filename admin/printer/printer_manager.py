"""
Windows printer integration.

Uses pywin32 (win32print / win32api) to talk to the real Windows
printing subsystem - no fake printer names, no simulated capabilities.

On non-Windows platforms (used only for local development of the
Flask/API layer before deployment), this module falls back to a
clearly-labeled stub so the rest of the codebase can still be exercised
without pywin32 installed. That stub must NEVER ship in the production
build - build_windows.py asserts win32print is importable before
packaging.
"""

import logging
import json
import subprocess
import sys
import tempfile
import threading
import requests
import time
from pathlib import Path

from admin.config.config import config
from admin.printer import silent_print

logger = logging.getLogger("printer_manager")

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import win32print
    import win32api


class PrinterError(Exception):
    pass


# Types printed by rendering them ourselves onto the EXACT printer chosen (by name)
PDF_EXTS = {".pdf"}
OFFICE_EXTS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".txt", ".csv"}
DESIGN_EXTS = {".psd", ".ai", ".dwg", ".dxf"}

# Only one job at a time may touch the Windows default printer (legacy fallback only)
_DEFAULT_PRINTER_LOCK = threading.Lock()


class PrinterManager:
    """
    Wraps Windows printer discovery, capability inspection, and job
    submission. All methods raise PrinterError with a human-readable
    message on failure - callers must not leak raw pywin32 exceptions
    to the User application.
    """

    def list_printers(self):
        """
        Returns real printers currently registered on this Windows
        machine (local + network-connected), each as:
            {system_name, display_name, is_network}
        """
        if not IS_WINDOWS:
            logger.warning("PrinterManager running on non-Windows platform; "
                            "returning empty list. This must not happen in production.")
            return []

        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        try:
            raw = win32print.EnumPrinters(flags)
        except Exception as e:
            raise PrinterError(f"Failed to enumerate Windows printers: {e}")

        results = []
        for flags_, description, name, comment in raw:
            results.append({
                "system_name": name,
                "display_name": name,
                "is_network": "\\\\" in name,
            })
        return results

    def get_capabilities(self, printer_name: str) -> dict:
        """
        Inspects the real driver's DEVMODE to determine supported
        paper sizes, color capability, and duplex support.
        Raises PrinterError if the printer cannot be opened.
        """
        if not IS_WINDOWS:
            raise PrinterError("Printer capability detection requires Windows.")

        try:
            handle = win32print.OpenPrinter(printer_name)
        except Exception as e:
            raise PrinterError(f"Cannot open printer '{printer_name}': {e}")

        try:
            devmode = win32print.GetPrinter(handle, 8)  # DEVMODE structure
            info2 = win32print.GetPrinter(handle, 2)

            color_capable = bool(getattr(devmode, "Color", 1) or True)
            # DC_COLORDEVICE via DeviceCapabilities gives the authoritative answer
            try:
                color_capable = bool(win32print.DeviceCapabilities(
                    printer_name, info2.get("pPortName", ""), win32print.DC_COLORDEVICES if hasattr(win32print, "DC_COLORDEVICES") else 1
                ))
            except Exception:
                pass  # keep devmode-derived guess if DeviceCapabilities unsupported by driver

            try:
                duplex_capable = win32print.DeviceCapabilities(
                    printer_name, info2.get("pPortName", ""), win32print.DC_DUPLEX
                ) == 1
            except Exception:
                duplex_capable = False

            try:
                paper_names_raw = win32print.DeviceCapabilities(
                    printer_name, info2.get("pPortName", ""), win32print.DC_PAPERNAMES
                )
                paper_sizes = [p.strip("\x00").strip() for p in paper_names_raw]
            except Exception:
                paper_sizes = []

            return {
                "printer_name": printer_name,
                "color_capable": color_capable,
                "duplex_capable": duplex_capable,
                "paper_sizes": paper_sizes,
            }
        finally:
            win32print.ClosePrinter(handle)

    def supports_profile(self, printer_name: str, paper_size: str, color_mode: str) -> tuple[bool, str]:
        """
        Returns (supported: bool, reason: str). (No online/offline check - by design.) Used by the order API
        to reject unsupported configurations before they ever reach
        the printer, per requirement section 6.
        """
        try:
            caps = self.get_capabilities(printer_name)
        except PrinterError as e:
            return False, str(e)

        if color_mode == "COLOR" and not caps["color_capable"]:
            return False, f"Printer '{printer_name}' does not support color printing."

        if caps["paper_sizes"] and not any(paper_size.upper() in p.upper() for p in caps["paper_sizes"]):
            return False, f"Printer '{printer_name}' does not support paper size {paper_size}."

        return True, ""

    def get_print_options(self, printer_name: str, profile_paper: str = "", profile_media: str = "") -> dict:
        """Paper sizes / paper types / qualities offered in the User app's Print Properties popup."""
        from admin.printer import silent_print
        return silent_print.get_print_options(printer_name, profile_paper, profile_media)

    def submit_file(self, printer_name: str, file_path: str, copies: int = 1,
                     orientation: str = "AUTO", print_settings: dict = None,
                     page_range: str = "", profile_paper: str = "", color_mode: str = "") -> int:
        """
        Submits a file to the Windows print spooler using ShellExecute's
        'print' verb, which routes through the file's associated
        application/driver (correct rendering for PDF, DOCX, images,
        etc. without reinventing per-format rendering).

        Returns the Windows spooler job id where obtainable, else -1.
        Raises PrinterError on failure - caller marks the print_job FAILED.
        """
        if not IS_WINDOWS:
            raise PrinterError("Print submission requires Windows.")

        # Validate the printer is currently known to Windows BEFORE spending
        # time converting/rendering the file - a stale or renamed printer
        # name should fail fast with a clear reason, not deep inside GDI
        # after wasted work (requirement section 7, step 1).
        self._validate_printer_exists(printer_name)

        # ------------------------------------------------------------------
        # EVERY job is sent to the exact printer chosen, addressed BY NAME.
        # (The old approach - switch the Windows *default* printer, ShellExecute
        # "print", switch back - is asynchronous: the PDF/Word program often
        # printed AFTER the default was restored, so a job for e.g. the
        # Epson 3150 came out of the 7710. That path is now only a last resort.)
        # ------------------------------------------------------------------
        logger.info("Submitting '%s' to printer '%s' (copies=%s)", Path(file_path).name, printer_name, copies)
        from admin.printer import silent_print
        suffix = Path(file_path).suffix.lower()

        # NOTE: `print_settings` is populated for almost every order (paper
        # size/quality/paper type), not only Glossy/Matte/Luster jobs - so
        # checking it for truthiness here used to route ANY Office file with
        # settings attached (e.g. a plain DOCX order) straight into the
        # silent-print engine on the raw .docx file, skipping the PDF
        # conversion below entirely and failing with "Silent printing does
        # not support '.docx' files." Only a real customer DEVMODE
        # (devmode_b64, from the User PC's Printing Preferences window)
        # actually requires bypassing conversion - everything else must
        # still be checked against IMAGE_EXTS/PDF_EXTS/OFFICE_EXTS normally.
        has_devmode = bool(print_settings and print_settings.get("devmode_b64"))
        if has_devmode or suffix in silent_print.IMAGE_EXTS or suffix in PDF_EXTS:
            return self._print_silent(printer_name, file_path, copies, orientation,
                                      page_range, print_settings, profile_paper, color_mode)

        if suffix in OFFICE_EXTS:
            pdf_dir = None
            try:
                pdf_dir, pdf_path = self._convert_to_pdf(file_path)
            except PrinterError as e:
                logger.warning("Could not convert %s to PDF (%s); using the application print route.",
                               Path(file_path).name, e)
            if pdf_dir is not None:
                try:
                    return self._print_silent(printer_name, pdf_path, copies, orientation,
                                              page_range, print_settings, profile_paper, color_mode)
                finally:
                    try:
                        pdf_dir.cleanup()
                    except Exception:
                        pass

        if suffix in DESIGN_EXTS:
            # Adobe/CAD files must use the same conversion path used for page
            # counting. Do not fall back to Windows' file association because
            # that can open an interactive application on the Admin PC and
            # bypass the customer's selected print settings.
            pdf_dir = None
            try:
                from admin.server.design_file_converter import convert_design_to_pdf
                pdf_dir, pdf_path = convert_design_to_pdf(file_path, suffix)
                return self._print_silent(printer_name, pdf_path, copies, orientation,
                                          page_range, print_settings, profile_paper, color_mode)
            except Exception as e:
                raise PrinterError(f"Could not prepare {suffix.upper().lstrip('.') } file for printing: {e}") from e
            finally:
                if pdf_dir is not None:
                    try:
                        pdf_dir.cleanup()
                    except Exception:
                        pass

        return self._print_via_application(printer_name, file_path, copies)

    def submit_remote_file(self, station_host: str, station_port: int, station_id: str, station_key: str,
                           printer_name: str, file_path: str, job_id: str, copies: int = 1,
                           orientation: str = "AUTO", print_settings: dict | None = None,
                           page_range: str = "", profile_paper: str = "", color_mode: str = "") -> int:
        """
        Prepare the customer's document on the Admin PC, then stream the
        resulting PDF/image to the authenticated print agent on the originating
        User PC. The Admin PC never opens the User printer in Windows.
        """
        if not station_host:
            raise PrinterError("Originating User PC is not currently reachable.")
        if not station_id:
            raise PrinterError("Originating User station identity is missing.")
        if not station_key:
            raise PrinterError("Originating User station authentication key is missing.")
        if not printer_name:
            raise PrinterError("Selected User-PC printer identity is missing.")

        source = Path(file_path)
        suffix = source.suffix.lower()
        temp_dir = None
        prepared = str(source)

        # The Admin remains the single conversion/page-counting authority.
        # User PCs receive only PDF/images, so they do not need LibreOffice,
        # ImageMagick, ODA, or Adobe/CAD conversion dependencies.
        if suffix in OFFICE_EXTS:
            temp_dir, prepared = self._convert_to_pdf(file_path)
        elif suffix in DESIGN_EXTS:
            try:
                from admin.server.design_file_converter import convert_design_to_pdf
                temp_dir, prepared = convert_design_to_pdf(file_path, suffix)
            except Exception as e:
                raise PrinterError(
                    f"Could not prepare {suffix.upper().lstrip('.')} file for remote printing: {e}"
                ) from e
        elif suffix not in PDF_EXTS and suffix not in silent_print.IMAGE_EXTS:
            raise PrinterError(
                f"Cannot route '{source.name}' remotely because no prepared print format is available."
            )

        settings_json = json.dumps(print_settings or {}, separators=(",", ":"))
        headers = {
            "X-LAN-Token": config.lan_shared_token,
            "X-Station-ID": station_id,
            "X-Station-Key": station_key,
            "X-Job-ID": str(job_id),
            "X-Printer-Name": printer_name,
            "X-Copies": str(max(1, int(copies))),
            "X-Orientation": (orientation or "AUTO").upper(),
            "X-Page-Range": page_range or "",
            "X-Profile-Paper": profile_paper or "",
            "X-Color-Mode": color_mode or "",
            "X-Print-Settings": settings_json,
            "X-Filename": source.name if suffix in PDF_EXTS or suffix in silent_print.IMAGE_EXTS else Path(prepared).name,
            "Content-Type": "application/octet-stream",
        }

        url = f"http://{station_host}:{int(station_port or 8767)}/agent/print"
        logger.info(
            "Routing job %s from Admin to User station=%s host=%s printer=%r",
            job_id, station_id, station_host, printer_name,
        )
        try:
            with open(prepared, "rb") as fh:
                response = requests.post(
                    url,
                    data=fh,
                    headers=headers,
                    timeout=(5, 300),
                )
        except requests.RequestException as e:
            raise PrinterError(
                f"User PC {station_id} is unreachable at {station_host}:{station_port}: {e}"
            ) from e
        finally:
            if temp_dir is not None:
                try:
                    temp_dir.cleanup()
                except Exception:
                    pass

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            message = payload.get("error") or f"User print agent rejected the job (HTTP {response.status_code})."
            raise PrinterError(message)

        if payload.get("status") != "SUBMITTED":
            raise PrinterError(payload.get("error") or "User print agent did not confirm the print submission.")
        return int(payload.get("windows_job_id") or -1)

    # ------------------------------------------------------------------
    def _print_silent(self, printer_name, file_path, copies, orientation, page_range, print_settings,
                      profile_paper, color_mode=""):
        from admin.printer import silent_print
        try:
            silent_print.print_file(printer_name, file_path, copies=copies, orientation=orientation,
                                    page_range=page_range, settings=print_settings or {},
                                    profile_paper=profile_paper, color_mode=color_mode)
        except silent_print.SilentPrintError as e:
            raise PrinterError(str(e))
        return -1

    @staticmethod
    def _convert_to_pdf(file_path: str):
        """Office/text file -> PDF with LibreOffice (same engine used to count its pages)."""
        from admin.server.page_counter import LIBREOFFICE_BIN
        if not LIBREOFFICE_BIN:
            raise PrinterError("LibreOffice is not installed on the Admin PC.")
        tmp = tempfile.TemporaryDirectory()
        try:
            subprocess.run([LIBREOFFICE_BIN, "--headless", "--convert-to", "pdf", "--outdir", tmp.name, file_path],
                           check=True, timeout=180, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            produced = list(Path(tmp.name).glob("*.pdf"))
            if not produced:
                raise PrinterError("LibreOffice did not produce a PDF.")
            return tmp, str(produced[0])
        except subprocess.TimeoutExpired:
            tmp.cleanup()
            raise PrinterError("Conversion to PDF timed out.")
        except subprocess.CalledProcessError as e:
            tmp.cleanup()
            raise PrinterError(f"LibreOffice conversion failed: {e.stderr.decode(errors='ignore')[:200]}")
        except PrinterError:
            tmp.cleanup()
            raise

    def _validate_printer_exists(self, printer_name: str):
        """Fails fast, with a clear reason, if this exact printer name isn't
        currently known to Windows - e.g. renamed, uninstalled, or a stale
        row in our own database - rather than failing deep inside GDI after
        conversion/rendering work has already been spent."""
        try:
            live = {p["system_name"] for p in self.list_printers()}
        except PrinterError:
            return  # enumeration itself failed; let the real print attempt surface that error
        if live and printer_name not in live:
            raise PrinterError(
                f"'{printer_name}' is not currently installed/visible on this Admin PC "
                f"(Windows reports: {', '.join(sorted(live)) or 'no printers'}). "
                f"Check the printer is connected/powered on, or re-add it in Windows if it "
                f"was renamed or reinstalled."
            )

    def _spooler_job_ids(self, printer_name: str) -> set:
        try:
            h = win32print.OpenPrinter(printer_name)
            try:
                return {j["JobId"] for j in win32print.EnumJobs(h, 0, -1, 1)}
            finally:
                win32print.ClosePrinter(h)
        except Exception:
            return set()

    def _print_via_application(self, printer_name: str, file_path: str, copies: int) -> int:
        """
        Last resort (file types we cannot render ourselves, and no LibreOffice).
        1) 'printto' verb: the application is told the printer NAME explicitly,
           so the Windows default printer is never touched.
        2) Otherwise the default printer is switched, but it is kept switched until
           the job has really reached THIS printer's queue (not restored early).
        """
        with _DEFAULT_PRINTER_LOCK:
            try:
                for _ in range(max(1, copies)):
                    win32api.ShellExecute(0, "printto", file_path, f'"{printer_name}"', ".", 0)
                return -1
            except Exception as e:
                logger.info("'printto' not available for %s (%s); using default-printer route.",
                            Path(file_path).name, e)

            try:
                previous_default = win32print.GetDefaultPrinter()
                before = self._spooler_job_ids(printer_name)
                win32print.SetDefaultPrinter(printer_name)
                try:
                    for _ in range(max(1, copies)):
                        win32api.ShellExecute(0, "print", file_path, None, ".", 0)
                    deadline = time.time() + 40
                    while time.time() < deadline:
                        if len(self._spooler_job_ids(printer_name) - before) >= max(1, copies):
                            break
                        time.sleep(0.5)
                    else:
                        logger.warning("No new spooler job appeared on '%s' within 40s.", printer_name)
                finally:
                    win32print.SetDefaultPrinter(previous_default)
            except Exception as e:
                raise PrinterError(f"Failed to submit print job to '{printer_name}': {e}")
        return -1


printer_manager = PrinterManager()
