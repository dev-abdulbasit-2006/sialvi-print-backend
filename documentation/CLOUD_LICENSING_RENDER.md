# Cloud licensing on Render

Everything licensing-related talks to ONE address: `https://sialvi-print-backend.onrender.com`
(`shared/cloud_config.py`). Admin, User and Owner have no local-server mode.

```
Owner app  ->  HTTPS  ->  Render (gunicorn app:app = licensing/server)  <-  HTTPS  <-  Admin / User apps
                                 Ed25519-signed tokens, SQLite DB, audit log
```

## 1. Read this first: three things that were wrong in the uploaded archive

1. **The deployed `app.py` was a stub.** It had one route, `/api/client/checkin`, that answered `ACTIVE` for ANY hardware id and ANY key
   ("Add your license check logic here"). That is not licensing: anybody could have used any string. The desktop apps could not even talk to it
   (they call `/api/v1/activate|checkin|deactivate` and require a signed token). Root `app.py` now serves the real server (`licensing/server`).
   **You must push this change to the GitHub repo that Render deploys, or the live site stays a stub.**
2. **`shared/license_embedded.py` could not work.** It defined `LICENSE_SERVER_URL` / `PUBLIC_KEY`, but the code reads `SERVER_URL` / `PUBLIC_KEY_PEM`,
   and the key was a placeholder (`MIIBIjAN...`, which is the header of an **RSA** key). The licensing tokens are signed with **Ed25519**; an RSA
   key can never verify them. The file now has the right names; the key is empty until you run `embed_config.py --fetch` (apps refuse to run with an empty key).
3. **There are three unrelated backends in the repo.** (a) the real one, `licensing/server` (now the Render entry); (b) the old stub; (c) `functions/main.py`
   (Firebase / Firestore), which has no signatures, no PC limit, returns unsigned "commands", marks them SENT before any acknowledgement and answers CORS `*`.
   The apps do not use (c). It is left untouched; do not deploy it for real customers. `firestore.rules` (deny everything) is fine.

## 2. The free-tier problem you must solve before real customers

Render's **free web service has an ephemeral disk** and **sleeps after ~15 minutes idle** (first request afterwards takes ~30-60 s).

| What | Consequence | Status |
|---|---|---|
| Signing key + key pepper | Used to be files in the data dir, lost on restart, so every installed app would break | **Fixed**: they now come from environment secrets (`LICENSE_SIGNING_KEY_PEM`, `LICENSE_KEY_PEPPER`) |
| SQLite database (licenses, devices, owner login, audit) | **Lost on every restart, redeploy and wake-up from sleep** (this caused "installation not recognized" the next day) | **FIXED:** set `DATABASE_URL` to a persistent PostgreSQL database (see `documentation/ACTIVATION_GUIDE.md`). Without it the server still falls back to SQLite and the Owner app shows a red warning. |
| Cold start | First activation/check-in after idle can take up to ~1 min | Mitigated: client timeouts are (10 s connect, 45 s read), tools retry; PCs keep working offline for the grace period (72 h) |

Without `DATABASE_URL` treat the service as a **test environment**: licenses you create can vanish. With it, the check `/api/v1/health` shows `"storage":"postgresql"`.

## 3. One-time setup

1. Generate the secrets on your PC (prints, writes nothing):  `python -m licensing.server render-secrets`
   Keep a private backup of both values. Losing the signing key invalidates every installed app; leaking it lets anyone forge licenses.
2. Push the repo (with the new `app.py`, `requirements.txt`, `render.yaml`) to GitHub. In Render: the service uses
   Build `pip install -r requirements.txt`, Start `gunicorn app:app --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT`, Health check `/api/v1/health`.
3. In the Render dashboard add the secret environment variables `DATABASE_URL`, `LICENSE_KEY_PEPPER` and `LICENSE_SIGNING_KEY_PEM` (from step 1).
   `LICENSE_TRUSTED_PROXIES` in `render.yaml` assumes Render's proxy peers are in the private ranges: **unverified**; check the Owner "last IP" values after deploying
   (if they all show a private address, the setting is wrong; if the setting is too wide, client IPs could be spoofed).
4. Owner login: Render's free tier has **no shell**, so add two more secret variables, `LICENSE_OWNER_USERNAME` and `LICENSE_OWNER_PASSWORD` (min 10 characters).
   The service creates that owner at start-up if it does not exist (never logs the password). Because the free database is wiped on restart, this is what
   re-creates your login each time. Never paste secrets into chats or e-mails.
5. Open `https://sialvi-print-backend.onrender.com/api/v1/health` (wait for the cold start) and `/api/v1/public-key`.

## 4. Building the apps (Windows)

```
python licensing\embed_config.py --fetch          # writes shared\license_embedded.py with the cloud public key
python licensing\embed_config.py --check --online # the build runs this; it stops if the key is missing, RSA, or differs from the live server
installer\build_all.bat
```
Compare the fingerprint printed by `--fetch` with Owner License Manager > App Builds.

## 5. Checks that are covered by tests (Linux, in-process) vs not verified

Covered: HTTPS-only clients, embed validation (rejects http / localhost / IPs / private names / RSA / placeholders), fail-closed gate, build scripts, env-secret
signing key, `render-secrets`, root `app.py` rejecting random keys, script invocation as the build runs it.
**Not verified:** the live Render service (this environment cannot reach it), cold-start behavior, real TLS, Windows builds, installed apps activating against Render.
