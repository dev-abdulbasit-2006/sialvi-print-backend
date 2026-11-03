# SialviPrint network setup — User-PC printer routing

SialviPrint now separates **printer ownership** from the Admin server.

## Recommended shop layout

- **Admin PC:** connected only to the shop router by Wi-Fi or Ethernet.
- **User/kiosk PCs:** connected to the same router by Wi-Fi or Ethernet.
- **Printers:** may be USB/local, Wi-Fi, or Ethernet/LAN.
- A printer does **not** have to be installed on the Admin PC.

The important rule is:

> If Windows on a User PC has a printer installed and available, that printer belongs to that User station and can be selected by the User app.

## New print flow

```text
USER PC
  |
  | Windows local printer enumeration
  v
USER APP
  |
  | selected printer name + station_id
  v
ADMIN SERVER
  |
  | payment verified
  | convert Office/design file to PDF when required
  | authenticated HTTP print command
  v
USER PC PRINT AGENT :8767
  |
  | Windows local spooler
  v
SELECTED PRINTER ON THAT USER PC
```

The Admin PC no longer opens the selected User printer with `win32print`.

## Printer connection types

All of these are supported as long as Windows has the printer installed:

1. **USB/local printer**
   - Example: Epson L3150 connected by USB to User PC.
2. **Wi-Fi printer**
   - Example: Epson L3150 connected to the same router as the User PC.
3. **Ethernet/LAN printer**
   - Example: HP LaserJet connected to the router by Ethernet.

The application does not need to know which physical connection was used. Windows owns that detail.

## Admin-to-User ports

| Port | Purpose |
|---|---|
| TCP 8765 | Admin Flask API |
| UDP 8766 | Admin discovery |
| TCP 8767 | User print agent |

`AdminSetup.exe` creates the Admin firewall rule for TCP 8765 and UDP 8766.

`UserSetup.exe` creates the User firewall rule for TCP 8767.

Use a **Private/Domain Windows network profile** for the shop LAN. Public-network firewall exposure should not be enabled unless the shop deliberately requires it.

## Authentication

The existing `X-LAN-Token` is reused.

The User print agent additionally checks:

- the exact `station_id`;
- the selected Windows printer name;
- the authenticated request;
- the print job ID.

The User agent is not an unauthenticated open printer server.

## Printer sharing is NOT required

Windows printer sharing is **not required** by the new architecture.

For example:

```text
User PC 1
  └── USB ── Epson L3150

Admin PC
  └── router only
```

This is valid.

The Admin sends the prepared document to User PC 1, and User PC 1 sends it to its own local Epson queue.

Likewise:

```text
User PC 2
  └── Wi-Fi ── Canon G3010
```

is valid without sharing the Canon printer to the Admin PC.

## Same printer name on two User PCs

This is explicitly supported.

```text
User PC 1: Epson L3150
User PC 2: Epson L3150
```

The order stores both:

- `station_id`
- a per-station secret (`station_key`)
- `printer_name`
- `station_host`
- `station_port`

Therefore `Epson L3150` on PC 1 is not confused with `Epson L3150` on PC 2.

## DHCP / IP changes

The User application registers its print-agent address with the Admin server and refreshes it periodically.

If the User PC receives a new DHCP address while it is running, the station registration updates the address for its active orders.

## Offline behavior

If the User PC is offline when Admin verifies payment:

- the job remains in the normal queue;
- the Admin does **not** silently choose another printer;
- the queue retries according to the existing retry rules;
- if the retry limit is reached, the job becomes FAILED with the actual routing error.

If the selected printer is offline/unavailable on the User PC:

- the User agent rejects/fails the print;
- the job is retried;
- another printer is never selected automatically.

## External software

The Admin remains the document-preparation authority:

- LibreOffice: Office/text conversion and page counting.
- ImageMagick/Ghostscript: PSD/AI conversion.
- ODA File Converter: DWG conversion.
- ezdxf: DXF processing.

The User PC does **not** need those conversion tools for remote printing. It only needs the SialviPrint User installer and the Windows printer's own driver.

The User installer now bundles the Python-side Windows printer integration required for:

- local printer enumeration;
- native Printing Preferences;
- final PDF/image spool submission.


## Offline wired LAN

Internet and Wi-Fi are not required. Firewall rules now apply to all profiles but only from LAN/private addresses.
Details: `OFFLINE_LAN_SETUP.md`.
