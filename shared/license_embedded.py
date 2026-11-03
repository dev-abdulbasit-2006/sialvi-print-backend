"""Build-time licensing settings baked into the Admin and User apps (cloud licensing on Render).

The server address is fixed to the cloud service. PUBLIC_KEY_PEM must be the server's Ed25519 PUBLIC key
(NOT an RSA key). It is filled in by:   python licensing/embed_config.py --fetch
Until then it is empty and the apps refuse to run (fail closed) and installer/build_all.bat stops.
Only the PUBLIC key is stored: it can verify tokens, never create them.
"""
SERVER_URL = "https://sialvi-print-backend.onrender.com"
LICENSE_SERVER_URL = SERVER_URL        # alias for code that uses the longer name
PUBLIC_KEY_PEM = ""
