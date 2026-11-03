"""Command-line test client that proves real activation and enforcement.

  python -m licensing.client_demo --server https://license.example.com --public-key key.pem activate SIALVI-....
  python -m licensing.client_demo ... status      # offline decision from the stored token
  python -m licensing.client_demo ... checkin     # renew from the server
  python -m licensing.client_demo ... deactivate  # free this PC's slot
Use --state-dir to simulate several PCs on one machine. HTTPS only (cloud licensing).
"""
import argparse
import sys
from pathlib import Path

from shared.cloud_config import CLOUD_LICENSE_URL
from shared.license_client import LicenseClient


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default=CLOUD_LICENSE_URL)
    ap.add_argument("--public-key", required=True, help="file with the server public key (python -m licensing.server show-public-key)")
    ap.add_argument("--state-dir", default=str(Path.home() / ".sialvi_license_demo"))
    ap.add_argument("--device-name")
    ap.add_argument("cmd", choices=["activate", "checkin", "status", "deactivate"])
    ap.add_argument("serial_key", nargs="?")
    a = ap.parse_args(argv)
    client = LicenseClient(a.server, a.state_dir, Path(a.public_key).read_text(), "demo-1.0")
    if a.cmd == "activate":
        if not a.serial_key:
            sys.exit("activate needs the serial key")
        st = client.activate(a.serial_key, a.device_name)
    elif a.cmd == "checkin":
        st = client.refresh()
    elif a.cmd == "deactivate":
        print("deactivated" if client.deactivate() else "deactivation refused")
        return
    else:
        st = client.check_authorization()
    print(("AUTHORIZED" if st.authorized else "REFUSED"), st.code, "-", st.message)
    sys.exit(0 if st.authorized else 1)


if __name__ == "__main__":
    main()
