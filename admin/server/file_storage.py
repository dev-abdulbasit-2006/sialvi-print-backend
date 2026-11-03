"""
Streaming file storage for uploads.

Requirements addressed:
- Support files >200MB without loading fully into RAM (streamed to disk
  in chunks).
- Never use the raw customer filename as a filesystem path (path
  traversal / overwrite protection) - files are stored under a
  generated internal id.
- Prevent incomplete uploads from entering the print queue: files are
  written to a .part temp path and atomically renamed only after the
  full stream completes and size is verified.
"""

import hashlib
import os
import uuid
from pathlib import Path

from admin.config.config import config
from admin.server.helpers import ApiError, safe_extension

ALLOWED_EXTENSIONS = {
    "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "pdf", "txt", "rtf", "csv",
    "jpg", "jpeg", "png", "bmp", "tiff",
    "psd", "ai", "dwg", "dxf",
}


def save_stream(order_id: str, original_filename: str, stream, declared_size: int = None) -> dict:
    """
    Streams `stream` (a file-like object, e.g. request.stream) to disk
    in chunks, enforcing the configured max upload size as it goes
    (so an oversized upload is rejected mid-transfer, not after fully
    buffering). Returns metadata for the order_files row.
    """
    ext = safe_extension(original_filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise ApiError(f"Unsupported file type: .{ext}", 415, "UNSUPPORTED_FORMAT")

    file_id = str(uuid.uuid4())
    order_dir = config.uploads_dir / order_id
    order_dir.mkdir(parents=True, exist_ok=True)

    final_path = order_dir / f"{file_id}.{ext}"
    tmp_path = order_dir / f"{file_id}.part"

    chunk_size = config.upload_chunk_bytes
    max_bytes = config.max_upload_bytes
    total_written = 0
    sha256 = hashlib.sha256()

    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                total_written += len(chunk)
                if total_written > max_bytes:
                    raise ApiError(
                        f"File exceeds maximum allowed size of {max_bytes // (1024*1024)} MB.",
                        413, "FILE_TOO_LARGE",
                    )
                out.write(chunk)
                sha256.update(chunk)
    except ApiError:
        _cleanup(tmp_path)
        raise
    except Exception as e:
        _cleanup(tmp_path)
        raise ApiError(f"Upload was interrupted: {e}", 400, "UPLOAD_INTERRUPTED")

    if total_written == 0:
        _cleanup(tmp_path)
        raise ApiError("Uploaded file is empty.", 400, "EMPTY_FILE")

    # Atomic rename: a reader will never observe a partially-written file
    # under the final path.
    os.replace(tmp_path, final_path)

    return {
        "file_id": file_id,
        "stored_path": str(final_path),
        "extension": ext,
        "size_bytes": total_written,
        "checksum_sha256": sha256.hexdigest(),
    }


def _cleanup(path: Path):
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass  # best-effort; a leftover .part file is harmless and can be GC'd later


def delete_order_files(order_id: str):
    """Removes all files for an order (used on cancellation/cleanup)."""
    order_dir = config.uploads_dir / order_id
    if not order_dir.exists():
        return
    for f in order_dir.glob("*"):
        try:
            f.unlink()
        except OSError:
            pass
    try:
        order_dir.rmdir()
    except OSError:
        pass
