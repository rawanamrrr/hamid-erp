"""Server-side direct printing of kitchen tickets to a specific printer.

WHY THIS EXISTS
---------------
Everything else in the app prints through the browser (`window.print()`), which is fine
for the customer invoice: the cashier is standing at the cashier printer anyway. It does
NOT work for kitchen tickets, for two reasons:

  1. A browser gives JavaScript no way to choose WHICH printer to use — the person picks
     it in the print dialog. So an invoice and a kitchen ticket printed from the same
     machine both land on the same (default) printer.
  2. A ticket has to appear in the KITCHEN the moment an order is sent, with nobody
     clicking anything.

So kitchen tickets are rendered and printed here, on the server (the cashier PC), sent
straight to a named printer — no browser, no dialog, and a different printer from the
invoice.

WHY AN IMAGE, NOT ESC/POS TEXT
------------------------------
Thermal printers print Arabic text only if the printer's own firmware has an Arabic
codepage (CP864), which many cheap units either lack or implement badly. Rasterising the
ticket to a bitmap and letting the Windows driver print it sidesteps that entirely:
Windows shapes and draws the Arabic, and the printer just prints dots. Works on any
printer that has a Windows driver.

Arabic needs two transformations before drawing, or it renders as disconnected letters in
reverse order: arabic_reshaper (letter joining) then python-bidi (RTL reordering).
"""
import logging
import threading

logger = logging.getLogger(__name__)

# 80mm thermal head at 203dpi ≈ 576 printable dots. Matches the @page size the HTML
# ticket template uses, so both routes produce the same physical width.
TICKET_WIDTH_PX = 576
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\tahoma.ttf",     # ships with Windows, good Arabic coverage
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
]


def is_available():
    """True only if this machine can actually print directly (Windows + the libraries).

    Callers use this to fall back to the browser-preview route instead of failing.
    """
    try:
        import arabic_reshaper  # noqa: F401
        import win32print  # noqa: F401
        import win32ui  # noqa: F401
        from bidi.algorithm import get_display  # noqa: F401
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
        return True
    except Exception:
        return False


def _shape(text):
    """Arabic letter-joining + RTL reordering. Latin/digits pass through untouched."""
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(str(text)))


def _load_font(size):
    from PIL import ImageFont
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_ticket_image(order, items, station_name='تذكرة المطبخ'):
    """Draw the kitchen ticket as a 1-bit bitmap, mirroring the layout of
    templates/restaurant/kitchen_ticket.html so both routes look the same."""
    from PIL import Image, ImageDraw

    f_station = _load_font(26)
    f_number = _load_font(64)
    f_meta = _load_font(26)
    f_item = _load_font(34)
    f_mod = _load_font(24)
    f_footer = _load_font(24)

    # Generous canvas, cropped to the real content height at the end — a thermal roll
    # has no fixed page length, so printing blank space would just waste paper.
    img = Image.new('L', (TICKET_WIDTH_PX, 3000), 255)
    d = ImageDraw.Draw(img)
    y = 16
    M = 14   # side margin

    def centered(text, font, gap=10):
        nonlocal y
        s = _shape(text)
        w = d.textlength(s, font=font)
        d.text(((TICKET_WIDTH_PX - w) / 2, y), s, font=font, fill=0)
        y += font.size + gap

    def rtl_line(text, font, indent=0, gap=8):
        """Right-aligned — the natural start edge for Arabic."""
        nonlocal y
        s = _shape(text)
        w = d.textlength(s, font=font)
        d.text((TICKET_WIDTH_PX - M - indent - w, y), s, font=font, fill=0)
        y += font.size + gap

    def divider(gap=12):
        nonlocal y
        y += 4
        d.line((M, y, TICKET_WIDTH_PX - M, y), fill=0, width=3)
        y += gap

    centered(station_name, f_station, gap=4)
    centered(str(order.display_number), f_number, gap=6)

    if getattr(order, 'table_id', None) and order.table:
        centered(f'ترابيزة {order.table.number}', f_meta, gap=4)
    else:
        centered(order.get_order_type_display(), f_meta, gap=4)

    from django.utils import timezone
    centered(timezone.localtime(order.created_at).strftime('%Y-%m-%d %H:%M'), f_meta, gap=6)
    divider()

    for item in items:
        # Decimal('1.00') formats as "1.00" even with :g (unlike float), which reads as
        # clutter on a kitchen ticket — normalize() drops the trailing zeros so a whole
        # number prints as "1" while "1.5" still prints as "1.5".
        try:
            qty = item.quantity.normalize()
        except AttributeError:
            qty = item.quantity
        label = f'{qty:f} × {item.product.name if item.product else ""}'
        if getattr(item, 'variant_id', None) and item.variant:
            label += f' ({item.variant.label})'
        rtl_line(label, f_item, gap=4)

        mods = [m.get('option', '') for m in (item.modifiers or []) if m.get('option')]
        if mods:
            rtl_line('، '.join(mods), f_mod, indent=24, gap=4)
        if getattr(item, 'note', ''):
            rtl_line(f'* {item.note}', f_mod, indent=24, gap=4)
        y += 8

    divider()
    who = ''
    if getattr(order, 'waiter_id', None) and order.waiter:
        who = order.waiter.get_full_name() or order.waiter.username
    elif getattr(order, 'user_id', None) and order.user:
        who = order.user.get_full_name() or order.user.username
    if who:
        centered(who, f_footer, gap=4)

    y += 24   # feed a little so the cut doesn't slice the last line
    return img.crop((0, 0, TICKET_WIDTH_PX, min(y, img.height))).convert('1')


def print_image(printer_name, image, doc_name='Kitchen Ticket'):
    """Send a bitmap to a named Windows printer through its driver (GDI)."""
    import win32con
    import win32ui
    from PIL import ImageWin

    hdc = win32ui.CreateDC()
    hdc.CreatePrinterDC(printer_name)
    try:
        hdc.StartDoc(doc_name)
        try:
            hdc.StartPage()
            # Our bitmap is authored at 203dpi; the driver's DC may report a different
            # resolution, so scale to keep the physical width correct on paper.
            scale = hdc.GetDeviceCaps(win32con.LOGPIXELSX) / 203.0
            w, h = int(image.width * scale), int(image.height * scale)
            ImageWin.Dib(image.convert('RGB')).draw(hdc.GetHandleOutput(), (0, 0, w, h))
            hdc.EndPage()
        finally:
            hdc.EndDoc()
    finally:
        hdc.DeleteDC()


def print_kitchen_ticket(order, items, station_name='تذكرة المطبخ', printer_override=None):
    """Render + print one ticket. Returns (ok, message); never raises.

    `printer_override` targets a specific station's printer (KitchenStation.printer_target);
    without it the ticket goes to the branch-wide kitchen printer from system settings.

    Printing must never be able to fail an order: the sale is already committed by the
    time this runs, and a jammed/offline printer is not a reason to lose it.
    """
    from settings.models import SystemSetting

    printer = (printer_override or '').strip()
    if not printer:
        settings_obj = SystemSetting.objects.first()
        printer = (getattr(settings_obj, 'kitchen_printer_name', '') or '').strip()
    if not printer:
        return False, 'لم يتم تحديد طابعة للمطبخ'
    if not is_available():
        return False, 'الطباعة المباشرة غير متاحة على هذا الجهاز'

    try:
        image = render_ticket_image(order, items, station_name)
        print_image(printer, image, doc_name=f'Kitchen Ticket #{order.display_number}')
        return True, f'تم إرسال التذكرة إلى {printer}'
    except Exception as exc:
        logger.warning('kitchen ticket direct print failed: %s', exc, exc_info=True)
        return False, str(exc)


def print_kitchen_ticket_async(order, items, station_name='تذكرة المطبخ', printer_override=None):
    """Fire-and-forget: spooling can block for seconds on a busy/offline printer, and
    the waiter's 'send to kitchen' request must not wait on that."""
    items = list(items)   # evaluate the queryset before leaving the request's DB context

    def _run():
        try:
            print_kitchen_ticket(order, items, station_name, printer_override)
        except Exception:
            logger.warning('kitchen ticket print thread crashed', exc_info=True)

    threading.Thread(target=_run, daemon=True).start()
