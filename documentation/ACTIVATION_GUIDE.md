# Activation guide (cloud licensing) and the "not recognized the next day" bug

## 1. What was wrong
The cloud licensing service stored licenses, registered PCs and the owner session in a **SQLite file on Render's free tier**. That disk is
temporary: every time the service restarts, redeploys, or goes to sleep after ~15 idle minutes and wakes up again, the file is gone.
So the day after you activated, the server had forgotten your PCs: the PC said "not recognized, activate again", the serial key was
unknown, and the Owner app showed no licenses or had to sign in again. The apps were behaving correctly; the server lost its memory.

**Fix:** the server can now store everything in a persistent **PostgreSQL** database (`DATABASE_URL`). With it, restarts and sleeping
no longer matter. Verified with real PostgreSQL in tests (restart on an empty disk keeps licenses, PCs, the owner login and the PC limit).
Not verified: your live Render + hosted PostgreSQL combination (this environment cannot reach them).

## 2. One-time server setup
1. **Create a free PostgreSQL database** at a provider with a persistent free plan, for example Neon (neon.tech) or Supabase. (Render's own free
   database is time-limited; check each provider's current free-tier terms.) Create a project, then copy the **connection string**. It looks like
   `postgresql://user:password@host/dbname?sslmode=require`. Treat it as a secret.
2. **Render dashboard -> your service -> Environment.** Make sure these five secrets exist (Key = name, Value = text only, no quotes):

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the PostgreSQL connection string from step 1 |
   | `LICENSE_KEY_PEPPER` | from `python -m licensing.server render-secrets` |
   | `LICENSE_SIGNING_KEY_PEM` | from `python -m licensing.server render-secrets` |
   | `LICENSE_OWNER_USERNAME` | the owner login you choose |
   | `LICENSE_OWNER_PASSWORD` | your password, at least 10 characters |

   Never paste these values into chats. If the signing key ever changes, every installed app must be rebuilt.
3. **Push this project's `app.py`, `requirements.txt`, `render.yaml`, `licensing/`, `shared/` to GitHub** (Render builds from there) and wait for **Live**.
4. **Check it:** open `https://sialvi-print-backend.onrender.com/api/v1/health` (wait up to a minute). You must see `"storage":"postgresql"` and `"persistent":true`.
   If it says `"sqlite"`, `DATABASE_URL` is missing or misspelled and licenses will be lost again.
5. **Prove it survives:** in Render use the **Manual Deploy / Restart** option, wait for Live, open the health URL again: still `postgresql`.

## 3. Build the apps (Windows, project folder)
```
python licensing\embed_config.py --fetch
installer\build_all.bat
```
`--fetch` stores the server's public key in the apps; the build checks it against the live server. Installers are written to `installer\Output\`
(`AdminSetup.exe`, `UserSetup.exe`, `OwnerSetup.exe`). Only Admin and User go to customers.

## 4. Activate step by step
1. **Owner PC:** install `OwnerSetup.exe`, open it, sign in with `LICENSE_OWNER_USERNAME` / `LICENSE_OWNER_PASSWORD`.
   The first sign-in can take up to a minute (the free host wakes up). The title bar line should say `data: postgresql`.
   A red banner about a TEMPORARY disk means step 2.1/2.2 above is not done yet.
2. **Create the license:** Licenses tab -> create -> customer name and the number of PCs allowed -> **copy the serial key now** (shown once).
3. **Admin PC:** install `AdminSetup.exe`, open it. The License Required window appears. Paste the serial key, check the PC name, click **Activate**.
4. **User PCs:** install `UserSetup.exe` on each customer PC, open it, enter **the same serial key**, click **Activate**. Each PC uses one slot of the PC limit.
5. **Check:** in the Owner app, Installations tab lists every PC with its last check-in.
6. **Next day:** open the apps again. They must start without asking for the key. PCs renew their license about every 6 hours while online
   and keep working offline for up to 72 hours.

## 5. Owner controls (take effect at the PC's next check-in)
Lock/unlock one PC, suspend/revoke/restore a license, raise the PC limit, deactivate a PC to free its slot. A PC that is offline keeps working
until its saved license expires (up to 72 hours), then follows the server's decision.

## 6. If a PC shows "not registered with the licensing server" or "not recognized"
| Cause | What to do |
|---|---|
| The server lost its data (`/api/v1/health` shows `"sqlite"`) | Finish section 2, then create new licenses in the Owner app and activate again |
| You deleted/deactivated that PC in the Owner app | Activate again (check the PC limit) |
| You rebuilt the server with a different `LICENSE_SIGNING_KEY_PEM` | Rebuild the apps (`--fetch`) and reinstall |
| License was revoked or expired | Restore/renew it in the Owner app, then press **Check again** on the PC |

Other messages: "Cannot reach the licensing server" = no internet or the free host is waking up (wait a minute, press Check again).
"Serial key is not valid" = typo, or the key was lost with the old temporary database. "PC limit reached" = raise the limit or deactivate a PC.

## 7. After switching to PostgreSQL
Everything created before the switch lived on the temporary disk and is already gone. Create the licenses again in the Owner app and re-activate
each PC once. From then on activation lasts.

## 8. Still your responsibility
Keep a private backup of the five secrets. Keep regular database backups according to your PostgreSQL provider's options (ask me to add an
automated export if you want one). The remote command queue with delivery confirmation is still not implemented.
