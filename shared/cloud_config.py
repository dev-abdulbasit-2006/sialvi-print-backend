"""Single source of truth for WHERE licensing lives: the cloud service on Render.

There is no local / LAN licensing server. Admin, User and Owner all talk to this HTTPS address.
"""
CLOUD_LICENSE_URL = "https://sialvi-print-backend.onrender.com"
