import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
    from admin.ui.screens import payments
    HAVE_QT = True
except Exception:
    HAVE_QT = False

ROW = {"payment_id": "p1", "order_id": "34c6d5b0aaaa", "computer_name": "PC", "file_names": "task.pdf",
       "printing_type": "2ee", "printer_name": "EPSON L3150 Series", "total_pages": 2, "amount": 0}


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class PaymentsScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def make(self):
        with mock.patch.object(payments.api_client, "get_pending_payments", return_value=[ROW]):
            return payments.PaymentsScreen()

    def test_only_printer_and_amount_are_highlighted(self):
        w = self.make()
        for c in range(7):
            highlighted = w.table.item(0, c).background().style() != Qt.NoBrush
            self.assertEqual(highlighted, c in (4, 6), f"column {c}")

    def test_confirm_shows_no_question_or_success_popup(self):
        w = self.make()
        res = {"receipt_no": 19, "print_jobs_created": 1}
        with mock.patch.object(payments.api_client, "verify_payment", return_value=res) as v, \
             mock.patch.object(payments.api_client, "get_pending_payments", return_value=[]), \
             mock.patch.object(QMessageBox, "question") as q, mock.patch.object(QMessageBox, "information") as i:
            w.confirm("p1", "CASH")
        v.assert_called_once_with("p1", "CASH")
        q.assert_not_called(); i.assert_not_called()
        self.assertIn("Receipt #000019", w.count.text())

    def test_failure_still_shows_error(self):
        w = self.make()
        with mock.patch.object(payments.api_client, "verify_payment", side_effect=payments.ApiClientError("boom")), \
             mock.patch.object(QMessageBox, "critical") as c:
            w.confirm("p1", "CASH")
        c.assert_called_once()


if __name__ == "__main__": unittest.main()
