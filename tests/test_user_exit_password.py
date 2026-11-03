"""Tests for the User-app exit password (logic only; the Qt dialog needs Windows/PySide6)."""
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from user import security  # noqa: E402


class FakeStore(dict):
    def set(self, k, v):
        self[k] = v


class ExitPasswordTests(unittest.TestCase):
    def test_no_password_initially(self):
        self.assertFalse(security.has_exit_password(FakeStore()))
        self.assertFalse(security.verify_exit_password("anything", FakeStore()))

    def test_set_and_verify(self):
        st = FakeStore()
        security.set_exit_password("shop1234", st)
        self.assertTrue(security.has_exit_password(st))
        self.assertTrue(security.verify_exit_password("shop1234", st))
        self.assertFalse(security.verify_exit_password("wrong", st))
        self.assertFalse(security.verify_exit_password("", st))

    def test_hash_is_not_plaintext_and_salted(self):
        a, b = FakeStore(), FakeStore()
        security.set_exit_password("same-pass", a)
        security.set_exit_password("same-pass", b)
        self.assertNotIn("same-pass", repr(a))
        self.assertNotEqual(a[security.HASH_KEY], b[security.HASH_KEY])

    def test_too_short_rejected(self):
        with self.assertRaises(ValueError):
            security.set_exit_password("abc", FakeStore())

    def test_corrupt_salt_fails_closed(self):
        st = FakeStore({security.HASH_KEY: "x", security.SALT_KEY: "!!notbase64"})
        self.assertFalse(security.verify_exit_password("x", st))

    def test_close_wiring_present_in_source(self):
        src = open(os.path.join(ROOT, "user", "ui", "main_window.py"), encoding="utf-8").read()
        self.assertIn("def closeEvent", src)
        self.assertIn("event.ignore()", src)
        self.assertIn("WindowMinimizeButtonHint", src)
        self.assertIn("WindowMaximizeButtonHint", src)   # maximize is allowed (customer requirement); closing still needs the password
        run = open(os.path.join(ROOT, "user", "run_user.py"), encoding="utf-8").read()
        self.assertIn("ensure_exit_password_exists", run)
        self.assertIn("commitDataRequest", run)
        self.assertIn("showMaximized", run)


if __name__ == "__main__":
    unittest.main()
