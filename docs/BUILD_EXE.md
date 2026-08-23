# Building the desktop EXE (Windows)

The POS ships as a self-contained Windows folder the customer double-clicks — no Python install
needed. Built with PyInstaller (onedir). WeasyPrint/GTK is **not** bundled; PDF endpoints fall
back to browser print (receipts already print via the browser).

## Build steps

```bat
:: 1. Collect static files (WhiteNoise serves them inside the bundle)
set DJANGO_SETTINGS_MODULE=textile_pos.production_settings
python manage.py collectstatic --noinput

:: 2. Build
pyinstaller textile_pos.spec --noconfirm
```

Output: **`dist/DigiFlow/`** — ship this whole folder. The customer runs **`dist/DigiFlow/DigiFlow.exe`**.

## What the EXE does (pos_launcher.py)
1. Creates a writable **`data/`** folder next to `DigiFlow.exe` for `db.sqlite3` + uploaded media.
   **Customer data lives here, outside the bundle — it survives program updates.**
2. Runs `migrate` on every launch (first run creates the DB; updates upgrade the schema in place).
3. Serves the app with **waitress** on `http://localhost:8085` (and the LAN IP for other devices).
4. Opens the browser automatically.

## Shipping an update / bug fix to a customer
1. Edit source here, rebuild (`collectstatic` + `pyinstaller`).
2. Send the new `dist/DigiFlow/` folder; the customer replaces their program folder **but keeps their
   own `data/` folder**. On launch, `migrate` upgrades their existing DB — no data loss.

## Per-install setup (first deployment)
- Create the master/owner user (or push a `CREATE_MASTER_USER` token via the activation page).
- Licensing: sell time with single-use `EXTEND_SUBSCRIPTION` tokens; the per-store signing key
  is on the activation page (master-only).
- For multiple concurrent cashiers, switch the DB to PostgreSQL (see `POSTGRES_CUTOVER.md`).

## Notes / gotchas
- `production_settings.py` sets `DEBUG=False`, WhiteNoise, and reads `DJANGO_SQLITE_NAME` /
  `DJANGO_MEDIA_ROOT` from the launcher.
- Antivirus/SmartScreen may warn on an unsigned EXE — code-sign it for distribution.
- To re-enable real PDF export, install the GTK runtime on the target and remove `weasyprint`
  from the spec's `excludes`.

---

## Native window (pywebview) + installer

The app now renders in a **native window** (pywebview/WebView2 — own window, icon, taskbar
entry; not a browser). Build prerequisites: `pip install pywebview` (pulls pythonnet).

### Build the installer (one-click Setup.exe)
1. `python manage.py collectstatic --noinput` (DJANGO_SETTINGS_MODULE=textile_pos.production_settings)
2. `pyinstaller textile_pos.spec --noconfirm`        → dist\POS\
3. `ISCC.exe installer.iss`                          → installer_output\POS-Setup.exe

Inno Setup compiler (`ISCC.exe`): install via `winget install JRSoftware.InnoSetup`.

`POS-Setup.exe` is what you give customers: it installs to Program Files, adds Start-menu +
desktop shortcuts with the app icon, registers an uninstaller, and launches the app. The
customer's `data\` folder (database + media) is **preserved on uninstall and reinstall**.

### Notes
- `app_icon.ico` is a placeholder — replace it with your real logo (same filename) and rebuild.
- Build needs Python 3.10.1+ ideally; on 3.10.0 the spec monkeypatches a `dis` bug so it still builds.

---

## Licensing tokens (asymmetric) + onboarding a customer

Tokens are signed with **ECDSA**: you hold the PRIVATE key, the app ships only the PUBLIC key.
Customers can verify tokens but **cannot forge** them, and tokens work on any device (no shared
secret to match). Verified: dev signs, any install verifies, a machine without the private key
cannot sign.

**The private key** lives in `license_private_key.pem` — it is **gitignored and NOT bundled in the
EXE**. Keep it safe; back it up. Place it next to `DigiFlow.exe` (or in the project root) on the ONE
machine where you generate tokens. If you lose it you must rotate to a new keypair (regenerate +
update `PUBLIC_KEY_HEX` in licensing/signing.py + rebuild). To rotate:
`python -c "from ecdsa import SigningKey,NIST256p; sk=SigningKey.generate(curve=NIST256p); open('license_private_key.pem','w').write(sk.to_string().hex()); print('PUBLIC_KEY_HEX =', sk.get_verifying_key().to_string().hex())"`

**Onboard a new customer (create their master account):**
1. Customer installs + runs the EXE → empty system.
2. You open your token generator (dev admin), enter a `store_id` (e.g. the customer's name) and a
   `CREATE_MASTER_USER` value `username|password` (or `username|password|email`) → get a token.
3. Send the token to the customer. They click "تفعيل النظام / إنشاء حساب المالك" on the login page,
   paste it, submit → master account created. They log in.
4. Sell time later with `EXTEND_SUBSCRIPTION` tokens (value = number of days) the same way.
