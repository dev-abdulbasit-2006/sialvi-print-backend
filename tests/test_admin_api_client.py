import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from admin.ui import api_client as ac


def _resp(status=200, body=b"", js=None):
    r = mock.Mock(); r.status_code = status; r.content = body; r.text = body.decode() if body else ""
    if js is ValueError: r.json.side_effect = ValueError()
    else: r.json.return_value = js
    return r


class ApiClientTests(unittest.TestCase):
    def setUp(self): self.c = ac.AdminApiClient()

    def _with(self, resp):
        return mock.patch.object(ac.requests, "request", return_value=resp)

    def test_dashboard_ok(self):
        with self._with(_resp(body=b"{}", js={"today_orders": 1})):
            self.assertEqual(self.c.get_dashboard()["today_orders"], 1)

    def test_dashboard_string_json_rejected(self):
        with self._with(_resp(body=b'"oops"', js="oops")):
            with self.assertRaises(ac.ApiClientError): self.c.get_dashboard()

    def test_dashboard_html_rejected(self):
        with self._with(_resp(body=b"<html>", js=ValueError)):
            with self.assertRaises(ac.ApiClientError): self.c.get_dashboard()

    def test_http_error(self):
        with self._with(_resp(500, b"x", js={"error": "boom"})):
            with self.assertRaises(ac.ApiClientError) as cm: self.c.get_dashboard()
        self.assertEqual(cm.exception.message, "boom")

    def test_unreachable(self):
        with mock.patch.object(ac.requests, "request", side_effect=ac.requests.exceptions.ConnectionError()):
            with self.assertRaises(ac.ApiClientError): self.c.get_dashboard()

    def test_queue_shapes(self):
        with self._with(_resp(body=b"[]", js=[{"id": 1}])): self.assertEqual(len(self.c.get_queue()), 1)
        with self._with(_resp(body=b'"s"', js="s")):
            with self.assertRaises(ac.ApiClientError): self.c.get_queue()


if __name__ == "__main__": unittest.main()
