"""
QThread workers so the kiosk UI never freezes during a large (>200MB)
file upload or while waiting on a network round-trip (requirement 19:
"the system must remain responsive during large-file transfers").
"""

from PySide6.QtCore import QThread, Signal

from user.network.api_client import api_client, ApiClientError


class UploadWorker(QThread):
    progress = Signal(str, int, int)     # file_id_placeholder(local temp key), bytes_sent, total_bytes
    finished_ok = Signal(str, dict)       # local temp key, response body {file_id, page_count, status}
    failed = Signal(str, str)             # local temp key, error message

    def __init__(self, order_id, file_path, sequence_index, local_key, parent=None):
        super().__init__(parent)
        self.order_id = order_id
        self.file_path = file_path
        self.sequence_index = sequence_index
        self.local_key = local_key

    def run(self):
        try:
            result = api_client.upload_file(
                self.order_id, self.file_path, self.sequence_index,
                on_progress=lambda sent, total: self.progress.emit(self.local_key, sent, total),
            )
            self.finished_ok.emit(self.local_key, result)
        except ApiClientError as e:
            self.failed.emit(self.local_key, e.message)
        except Exception as e:
            self.failed.emit(self.local_key, f"Unexpected error during upload: {e}")


class SetOptionsWorker(QThread):
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, order_id, printer_id, profile_id, copies, orientation, page_range, parent=None, print_settings=None):
        super().__init__(parent)
        self.args = (order_id, printer_id, profile_id, copies, orientation, page_range, print_settings)

    def run(self):
        try:
            result = api_client.set_options(*self.args)
            self.finished_ok.emit(result)
        except ApiClientError as e:
            self.failed.emit(e.message)
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")


class PayWorker(QThread):
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, order_id, parent=None):
        super().__init__(parent)
        self.order_id = order_id

    def run(self):
        try:
            result = api_client.pay(self.order_id)
            self.finished_ok.emit(result)
        except ApiClientError as e:
            self.failed.emit(e.message)
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")
