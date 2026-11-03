"""Run / manage the licensing server.

  python -m licensing.server create-owner --username owner      (asks for a password)
  python -m licensing.server run [--host 0.0.0.0 --port 8780] [--cert c.pem --key k.pem]

Production: put it behind HTTPS (reverse proxy such as Caddy/nginx, or --cert/--key) and set
LICENSE_TRUSTED_PROXIES if a proxy forwards the client IP. See documentation/LICENSING_FEATURE1.md.
"""
import argparse
import getpass
import os
import sys

from licensing.server.app import create_app
from licensing.server.config import Settings
from licensing.server.service import LicenseError


def _read_password(prompt: str) -> str:
    """Read a password, with a fallback for terminals where getpass breaks (e.g. Windows PowerShell)."""
    env_pw = os.environ.get("LICENSE_OWNER_PASSWORD")
    if env_pw:
        return env_pw
    try:
        return getpass.getpass(prompt)
    except (KeyboardInterrupt, EOFError, OSError, ImportError):
        print()
        print("Note: password input is not supported in this terminal — typing visibly.")
        return input(prompt)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="licensing.server")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-owner", help="create an owner account (first-time setup)")
    c.add_argument("--username", required=True)
    r = sub.add_parser("run", help="start the licensing server")
    r.add_argument("--host"); r.add_argument("--port", type=int)
    r.add_argument("--cert"); r.add_argument("--key")
    r.add_argument("--allow-http", action="store_true", help="allow plain HTTP on a non-loopback address (NOT recommended)")
    sub.add_parser("show-public-key", help="print the public key to give to client installations")
    sub.add_parser("render-secrets", help="generate NEW LICENSE_KEY_PEPPER + LICENSE_SIGNING_KEY_PEM values to paste into the hosting dashboard (writes nothing)")
    args = ap.parse_args(argv)

    if args.cmd == "render-secrets":          # must not create a data dir or touch an existing identity
        import base64, secrets as _secrets
        from licensing.server.security import generate_private_key_pem, load_private_key
        from shared.license_token import public_key_pem
        pem = generate_private_key_pem()
        print("Paste these into the hosting dashboard as SECRET environment variables. Store a private backup;\n"
              "losing the signing key invalidates every installed app, leaking it lets anyone forge licenses.\n")
        print("LICENSE_KEY_PEPPER=" + _secrets.token_hex(32))
        print("LICENSE_SIGNING_KEY_PEM=" + base64.b64encode(pem).decode("ascii"))
        print("\n# Public key (safe to share; this is what the apps embed):\n" + public_key_pem(load_private_key(pem)))
        return

    settings = Settings.from_env()
    app = create_app(settings)
    svc = app.extensions["license_service"]

    if args.cmd == "create-owner":
        pw = _read_password("Owner password (min 10 chars): ")
        pw2 = _read_password("Repeat password: ")
        if pw != pw2:
            sys.exit("Passwords do not match.")
        try:
            svc.create_owner(args.username, pw)
        except LicenseError as e:
            sys.exit(e.message)
        print("Owner created.")
    elif args.cmd == "show-public-key":
        from shared.license_token import public_key_pem
        print(public_key_pem(svc.signing_key))
    else:
        host, port = args.host or settings.host, args.port or settings.port
        tls = bool(args.cert and args.key)
        if host not in ("127.0.0.1", "localhost", "::1") and not tls and not args.allow_http and not settings.trusted_proxies:
            sys.exit("Refusing to listen on a public address without HTTPS. Use --cert/--key, put a TLS reverse "
                     "proxy in front and set LICENSE_TRUSTED_PROXIES, or pass --allow-http for a private test.")
        try:
            from waitress import serve
            if tls:
                sys.exit("--cert/--key need the built-in server; run behind a TLS reverse proxy when using waitress.")
            serve(app, host=host, port=port)
        except ImportError:
            app.run(host=host, port=port, ssl_context=(args.cert, args.key) if tls else None, threaded=True)


if __name__ == "__main__":
    main()
