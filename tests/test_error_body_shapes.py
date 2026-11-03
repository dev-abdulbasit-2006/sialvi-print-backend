import os, sys, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from user.network.api_client import UserApiClient, ApiClientError


def _r(status, js=None, bad=False):
    r = mock.Mock(); r.status_code = status; r.content = b"x"
    if bad: r.json.side_effect = ValueError()
    else: r.json.return_value = js
    return r


class UserErrorBodyTests(unittest.TestCase):
    """Regression: {"error": "not found"} (string) used to crash with 'str' object has no attribute 'get'."""
    def check(self, resp, expect):
        with self.assertRaises(ApiClientError) as cm: UserApiClient()._handle_response(resp)
        self.assertIn(expect, str(cm.exception))

    def test_string_error_value(self): self.check(_r(404, {"error": "not found"}), "not found")
    def test_dict_error_value(self): self.check(_r(400, {"error": {"message": "bad"}}), "bad")
    def test_plain_json_string(self): self.check(_r(500, "boom"), "boom")
    def test_list_body(self): self.check(_r(500, ["x"]), "500")
    def test_html_body(self): self.check(_r(502, bad=True), "502")
    def test_404_hint(self): self.check(_r(404, {"error": "not found"}), "old copy")


if __name__ == "__main__": unittest.main()
