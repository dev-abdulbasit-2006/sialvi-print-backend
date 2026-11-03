"""Kiosk stylesheet for the customer (User) app. Premium light theme lives in theme_user.py;
shared/theme.py (Admin/Owner) is untouched."""
from user.ui.theme_user import build_user_stylesheet

STYLESHEET = build_user_stylesheet()
