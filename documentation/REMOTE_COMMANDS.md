# Remote commands (Owner → customer PCs)

What it does: from the **Owner License Manager → Remote Commands** tab you can suspend / reactivate / revoke a whole
license, disable / re-enable / remove one PC, or ask PCs to re-check now. The change is made on the licensing server
**immediately**; every PC that is online hears about it within about **30 seconds** and reports back, so you can see
per PC whether it really applied.

## How it works (plain English)
1. You press **Send Command**. The server first changes the real state (e.g. license = suspended). From that moment the
   server refuses that license at every check-in, whether or not any PC has heard about it yet.
2. The server also queues the command for each registered PC of that license (or for the one PC you chose).
3. Every PC asks the server "anything new?" every 30 s (`/api/v1/poll`). If its state differs from the server's, or a command
   is waiting, it does a normal signed check-in (the signature is verified with the public key embedded in the app) and
   then tells the server what happened (`/api/v1/ack-command`): **Applied** or **Not applied** (with a reason).
4. The tab shows: *Waiting for PCs* → *Done* (all applied) / *Needs attention* (a PC reported "not applied") / *Expired*
   (not delivered within 72 h). **Delivery Details** shows each PC.

## Commands
| Command | Needs a PC? | What the server does |
|---|---|---|
| Suspend whole license | no | license → suspended (reversible) |
| Reactivate whole license | no | suspended → active |
| Revoke license | no | **permanent** (create a new license instead) |
| Disable one PC | yes | lock that PC only |
| Re-enable one PC | yes | unlock (still refused if the license itself is not valid) |
| Remove one PC | yes | deactivate: frees its slot, history kept |
| Ask PCs to re-check now | optional | no state change; PCs check in immediately |
A reason (3+ letters) is required for every command except "re-check". It is saved in the audit log and is **never sent to the customer PC**.

## Timing and offline behaviour (honest)
* Online PC: about 30 s (poll interval). The blocking license screen then appears within ~5 s (the monitor checks every 5 s).
* Offline PC: cannot be reached. It keeps working until its signed token expires (offline grace, default 72 h), then it stops.
  When it reconnects it hears the command (if it has not expired) and is refused anyway because the server state is already changed.
* The first request after the free Render server slept can take up to ~50 s.
* Commands not delivered within `LICENSE_COMMAND_TTL_HOURS` (72) show as *Expired*. The state change itself does **not** expire.

## Settings (server environment variables, all optional)
`LICENSE_POLL_INTERVAL_SECONDS` (30, allowed 10–300) · `LICENSE_COMMAND_TTL_HOURS` (72) · `LICENSE_POLL_RATE_LIMIT` (300 poll/ack calls per minute per public IP —
a whole shop shares one IP, so this is separate from the stricter activation/check-in limit).

## Security design (why it is not "just trust the command")
* The command never carries power by itself: the PC only treats the poll answer as a hint and confirms with a **signed** check-in.
  A forged "authorized" answer can never grant access (test: `test_a_forged_poll_answer_can_never_grant_access`).
* Poll and acknowledge need the PC's own installation secret; PC B cannot acknowledge PC A's command.
* Only a logged-in owner can create commands; customer secrets are not owner credentials.
* Polling writes IP history only when the public IP changes, so 30-second polling does not flood the database.
* Every command, delivery answer and state change is in the audit log (before/after).

## Not part of this feature (decisions for you)
* **Read-only mode while suspended** (customer can still view receipts/logs but cannot take new jobs/payments, from
  `remote_license_manual_operational_guide.md`) is **not implemented**. Today a suspended PC shows the blocking
  license screen and the Admin API refuses everything, as in the version you approved earlier. Read-only mode changes that
  behaviour and needs Windows UI testing; tell me if you want it.
* The uploaded files (`central_licensing_server_implementation.py`, `client_licensing_engine_enforcement.py`,
  `owner_remote_control_management_ui.py`, `automated_end_to_end_testing.py`) were **not copied in as they are**: they would
  replace the tested licensing system and drop its protections (unsalted SHA-256 key hash used as the credential, no
  token expiry so an old "ACTIVE" answer works forever offline, unauthenticated acknowledge, no HTTPS check, no database
  transaction for the PC limit, imports names that do not exist here). Their ideas (command types, queue, acknowledge, Remote
  Control tab, 30 s polling) are implemented on top of the existing secure design.

## API (for developers)
Customer PC: `POST /api/v1/poll`, `POST /api/v1/ack-command` (body: installation_id, installation_secret, …).
Owner (Bearer token): `GET/POST /api/owner/commands`, `GET /api/owner/commands/<id>`.
Database: migration 2 adds `remote_commands` and `remote_command_deliveries` (existing databases upgrade automatically; nothing is deleted).

## Tests
`python -m unittest tests.test_remote_commands tests.test_remote_commands_client -v` (32 tests) and, on Windows with PySide6,
`python -m unittest tests.test_owner_remote_tab -v` (3 UI tests, skipped on Linux).

## Windows checklist (tick only after you do it)
- [ ] Deploy the new server (push to GitHub, Render redeploys); `/api/v1/health` still shows `postgresql`.
- [ ] Rebuild Owner + Admin + User (`installer\build_all.bat`) — old exes do not poll.
- [ ] Two PCs activated on one license. Owner → Remote Commands → *Suspend whole license*: both PCs show the license screen within ~1 minute; the tab shows *Done 2 / 2*.
- [ ] *Reactivate whole license*: both PCs come back without reinstalling or re-entering the key.
- [ ] *Disable one PC*: only that PC is blocked. *Re-enable*: it returns.
- [ ] Unplug a PC's network, suspend, plug it back: it is blocked after reconnecting (tab shows its delivery).
- [ ] Open the License dialog on a cold (sleeping) server: the window stays responsive while it waits.
