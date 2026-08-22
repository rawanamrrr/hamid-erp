"""
DIAGNOSTIC TEST ONLY — not called from any production code path.

Purpose: determine whether the Xprinter's auto-cutter fails to fire because of
(A) the printer hardware, (B) the Windows driver/port configuration, or (C) the
app's printing code — by sending a small RAW ESC/POS test receipt DIRECTLY to the
printer's Windows print queue (RAW datatype), bypassing the driver's normal
page-rendering pipeline entirely. If the cutter fires here, the printer and the
raw ESC/POS cut command both work fine, and the problem is specifically that the
app's real printing flow (browser window.print() -> Windows GDI driver) never
gets that command down to the printer, i.e. it's a driver-setting / GDI-print
issue, not a hardware or "app is missing a cut command" issue (there's no
ESC/POS code in the app today — see sales/printer_utils.py, which is a dead
stub).

Usage:
    venv\\Scripts\\python.exe scripts\\test_xprinter_cut.py                 # list printers
    venv\\Scripts\\python.exe scripts\\test_xprinter_cut.py "Xprinter XP-58" # run the test

Requires pywin32 (not currently a project dependency — installed only in this
venv for this diagnostic; not added to requirements.txt since this script is
never imported by the app).
"""
import sys

try:
    import win32print
except ImportError:
    print("pywin32 is not installed. Install it just for this test with:")
    print("    venv\\Scripts\\python.exe -m pip install pywin32")
    sys.exit(1)

ESC = b'\x1b'
GS = b'\x1d'

INIT = ESC + b'@'                 # ESC @  — reset printer state
LF = b'\n'
CUT_FULL = GS + b'V' + b'\x00'    # GS V 0 — full cut, no feed
CUT_FULL_FEED = GS + b'V' + b'\x42' + b'\x00'  # GS V 66 0 — feed then full cut (most common on Xprinter/Epson-compatible firmware)


def list_printers():
    print("Installed Windows printers:")
    for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS):
        print(f"  - {p[2]}")
    print("\nDefault printer:", win32print.GetDefaultPrinter())
    print('\nRun again with the exact printer name in quotes, e.g.:')
    print('    python scripts\\test_xprinter_cut.py "XP-58"')


def send_raw(printer_name: str, data: bytes):
    h = win32print.OpenPrinter(printer_name)
    try:
        job = win32print.StartDocPrinter(h, 1, ("XprinterCutTest", None, "RAW"))
        try:
            win32print.StartPagePrinter(h)
            win32print.WritePrinter(h, data)
            win32print.EndPagePrinter(h)
        finally:
            win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)
    print(f"Sent {len(data)} raw bytes to '{printer_name}'.")


def build_test_payload():
    lines = [
        b"XPRINTER CUT TEST",
        b"------------------",
        b"If the paper cuts",
        b"after this line,",
        b"the printer + cut",
        b"command both work.",
        b"------------------",
    ]
    payload = INIT
    for line in lines:
        payload += line + LF
    payload += LF * 3          # feed clear of the cutter blade before cutting
    payload += CUT_FULL_FEED   # GS V 66 0 — the command actually being tested
    return payload


if __name__ == '__main__':
    if len(sys.argv) < 2:
        list_printers()
        sys.exit(0)

    printer_name = sys.argv[1]
    print(f"Sending raw ESC/POS test receipt + cut command to: {printer_name}")
    send_raw(printer_name, build_test_payload())
    print("\nDone. Watch the printer:")
    print("  - Text printed AND paper cut automatically  -> printer + ESC/POS cut command are fine.")
    print("    The problem is the app's browser-print path never sending this command")
    print("    (see the production-fix guidance after this test).")
    print("  - Text printed but did NOT cut               -> try re-running with CUT_FULL instead of")
    print("    CUT_FULL_FEED (edit the script), and separately check the Xprinter driver's")
    print("    'Auto Cut' setting in Windows Printing Preferences -> Advanced/Options.")
    print("  - Nothing printed at all / error               -> Windows driver or port configuration issue")
    print("    (wrong port, printer offline, spooler stuck, etc.) — not an ESC/POS command issue.")
