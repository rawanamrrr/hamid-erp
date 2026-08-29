"""Server-side direct printing of the CUSTOMER RECEIPT to the main printer.

WHY THIS EXISTS
---------------
The receipt used to be printed only by the browser, via `window.print()` in
templates/sales/invoice.html. That works in a real browser — a phone on the shop's wifi
prints fine — but the packaged desktop app shows the page inside WebView2, where
`window.print()` does nothing at all: no dialog, no error, no paper. So on the till, the
one machine the receipt actually has to come out of, "طباعة الفاتورة تلقائياً" silently
printed nothing.

Kitchen tickets never had that problem, because they are rasterised and sent straight to
a named printer server-side (restaurant/direct_print.py). The receipt now takes the same
route, for the same reason. The browser preview stays as the fallback for when no printer
is configured, or on a machine that cannot print directly.

The drawing helpers, the 80mm geometry and the Arabic shaping all come from direct_print
so both documents look like they came from the same shop.
"""
import logging

from .direct_print import (
    TICKET_MARGIN_PX,
    TICKET_WIDTH_PX,
    _load_font,
    _shape,
    is_available,
    print_image,
)

logger = logging.getLogger(__name__)


def _pct(value):
    """A percentage without Decimal's trailing zeros — "14%", not "14.00%"."""
    from decimal import Decimal
    try:
        return f'{Decimal(str(value or 0)).normalize():f}'
    except Exception:
        return str(value or 0)


def _money(value):
    """Two decimals, without the trailing-zero noise Decimal gives with :g."""
    from decimal import Decimal
    try:
        return f'{Decimal(str(value or 0)):.2f}'
    except Exception:
        return str(value or 0)


def render_receipt_image(order, sys_settings=None):
    """Draw the customer receipt as a 1-bit bitmap, mirroring templates/sales/invoice.html.

    Laid out for the 80mm roll directly rather than converted from the HTML: same width,
    same order of sections, same Arabic shaping as the kitchen ticket.
    """
    from decimal import Decimal

    from django.utils import timezone
    from PIL import Image, ImageDraw

    from settings.models import SystemSetting
    from settings.policies import get_policy

    if sys_settings is None:
        sys_settings = SystemSetting.objects.first()

    f_shop = _load_font(38)
    f_small = _load_font(22)
    f_meta = _load_font(24)
    f_item = _load_font(26)
    f_total = _load_font(38)
    f_foot = _load_font(22)

    # Generous canvas, cropped to the real content height at the end — a thermal roll has
    # no fixed page length, so printing blank space would just waste paper.
    img = Image.new('L', (TICKET_WIDTH_PX, 6000), 255)
    d = ImageDraw.Draw(img)
    y = 16
    M = TICKET_MARGIN_PX

    def centered(text, font, gap=8):
        nonlocal y
        s = _shape(text)
        w = d.textlength(s, font=font)
        d.text(((TICKET_WIDTH_PX - w) / 2, y), s, font=font, fill=0)
        y += font.size + gap

    def row(label, value, font, gap=6, indent=0):
        """Label on the right (the Arabic start edge), amount on the left."""
        nonlocal y
        ls = _shape(label)
        vs = _shape(value)
        d.text((TICKET_WIDTH_PX - M - indent - d.textlength(ls, font=font), y),
               ls, font=font, fill=0)
        d.text((M + indent, y), vs, font=font, fill=0)
        y += font.size + gap

    def rtl_line(text, font, indent=0, gap=6):
        nonlocal y
        s = _shape(text)
        d.text((TICKET_WIDTH_PX - M - indent - d.textlength(s, font=font), y),
               s, font=font, fill=0)
        y += font.size + gap

    def divider(gap=10):
        nonlocal y
        y += 4
        d.line((M, y, TICKET_WIDTH_PX - M, y), fill=0, width=2)
        y += gap

    # ── header ─────────────────────────────────────────────────────────────────
    if sys_settings and sys_settings.shop_name:
        centered(sys_settings.shop_name, f_shop, gap=6)
    if sys_settings and getattr(sys_settings, 'address', ''):
        centered(sys_settings.address, f_small, gap=4)
    if sys_settings and getattr(sys_settings, 'phone', ''):
        centered(str(sys_settings.phone), f_small, gap=6)
    divider()

    row('رقم الفاتورة', str(order.display_number), f_meta)
    row('التاريخ', timezone.localtime(order.created_at).strftime('%d/%m/%Y %H:%M'), f_meta)
    if get_policy('receipts.show_salesman_name'):
        who = getattr(order, 'salesman_name', '') or (
            order.user.username if getattr(order, 'user_id', None) and order.user else '')
        if who:
            row('الكاشير', who, f_meta)
    if getattr(order, 'customer_id', None) and order.customer:
        row('العميل', f'{order.customer.first_name} {order.customer.last_name}'.strip(), f_meta)
    else:
        row('العميل', 'عميل نقدي', f_meta)
    if getattr(order, 'table_id', None) and order.table:
        row('ترابيزة', str(order.table.number), f_meta)
    divider()

    # ── items ──────────────────────────────────────────────────────────────────
    for line in order.receipt_line_items():
        name = line['product_name']
        if line.get('variant_label'):
            name += f" ({line['variant_label']})"
        # Decimal('1.00') formats as "1.00" even with :f, which reads as clutter on a
        # receipt; normalize() drops the trailing zeros so a whole number prints as "1".
        try:
            qty = line['quantity'].normalize()
        except AttributeError:
            qty = line['quantity']
        rtl_line(name, f_item, gap=2)
        row(f'{qty:f} × {_money(line["unit_base_price"])}',
            _money(line['base_total']), f_small, gap=6, indent=16)
        for extra in line.get('extras') or []:
            row(f"+ {extra['option']}", _money(extra['total']), f_small, gap=4, indent=32)
    divider()

    # ── money ──────────────────────────────────────────────────────────────────
    row('الإجمالي الفرعي', _money(order.subtotal_amount), f_small)
    if (order.discount or 0) > 0:
        row('الخصم', _money(order.discount), f_small)
    if (getattr(order, 'delivery_cost', 0) or 0) > 0:
        row('التوصيل', _money(order.delivery_cost), f_small)

    svc = order.service_charge_breakdown()
    if svc and svc['amount']:
        label = f"خدمة {_pct(svc['pct'])}%" + (' (شاملة)' if svc['included'] else '')
        row(label, _money(svc['amount']), f_small)

    vat = order.vat_breakdown()
    if vat and vat['tax']:
        label = f"ضريبة {_pct(vat['rate'])}%" + (' (شاملة)' if vat['included'] else '')
        row(label, _money(vat['tax']), f_small)

    divider()
    row('الإجمالي', _money(order.total_amount), f_total, gap=10)
    divider()

    row('المدفوع', _money(order.received_amount), f_small)
    remaining = order.remaining_amount or Decimal('0')
    if remaining > 0:
        row('المتبقي', _money(remaining), f_small)
    elif remaining < 0:
        row('الباقي للعميل', _money(-remaining), f_small)

    # ── footer ─────────────────────────────────────────────────────────────────
    y += 6
    divider()
    if sys_settings and getattr(sys_settings, 'thank_you_text', ''):
        centered(sys_settings.thank_you_text, f_foot, gap=4)
    if sys_settings and getattr(sys_settings, 'return_policy', ''):
        centered(sys_settings.return_policy, f_foot, gap=4)

    # The QR is drawn into the bitmap itself, so it prints with no internet — the same
    # reason settings/templatetags/qr_tags.py stopped fetching it from a web service.
    if (sys_settings and getattr(sys_settings, 'show_qr', False)
            and getattr(sys_settings, 'qr_link', '')):
        try:
            import qrcode
            qr = qrcode.QRCode(box_size=4, border=1,
                               error_correction=qrcode.constants.ERROR_CORRECT_M)
            qr.add_data(sys_settings.qr_link)
            qr.make(fit=True)
            code = qr.make_image(fill_color='black', back_color='white').convert('L')
            y += 8
            img.paste(code, (int((TICKET_WIDTH_PX - code.width) / 2), y))
            y += code.height + 8
        except Exception:
            # A decorative code must never cost the customer their receipt.
            logger.warning('receipt QR could not be drawn', exc_info=True)

    centered(timezone.localtime(order.created_at).strftime('%d-%m-%Y %H:%M:%S'), f_foot, gap=4)

    y += 24   # feed a little so the cut doesn't slice the last line
    return img.crop((0, 0, TICKET_WIDTH_PX, min(y, img.height))).convert('1')


def print_receipt(order, printer_override=None):
    """Render + print the customer receipt. Returns (ok, message); never raises.

    Returns ok=False rather than raising whenever this machine cannot print directly, so
    the caller can fall back to the browser preview instead of losing the receipt.
    Printing must also never be able to fail a sale: by the time this runs the order is
    already committed, and a jammed or offline printer is not a reason to lose it.
    """
    from settings.models import SystemSetting

    settings_obj = SystemSetting.objects.first()
    printer = (printer_override or '').strip()
    if not printer:
        printer = (getattr(settings_obj, 'printer_name', '') or '').strip()
    if not printer:
        return False, 'لم يتم تحديد طابعة أساسية في الإعدادات'
    if not is_available():
        return False, 'الطباعة المباشرة غير متاحة على هذا الجهاز'

    try:
        image = render_receipt_image(order, settings_obj)
        print_image(printer, image, doc_name=f'Invoice #{order.display_number}')
        return True, f'تم إرسال الفاتورة إلى {printer}'
    except Exception as exc:
        logger.warning('receipt direct print failed: %s', exc, exc_info=True)
        return False, str(exc)


def receipt_printer_ready():
    """Whether this machine can actually put a receipt on paper right now.

    Checked before handing the job to the background thread below, so the POS can be told
    straight away whether it still needs to fall back to the browser preview.
    """
    from settings.models import SystemSetting

    settings_obj = SystemSetting.objects.first()
    if not (getattr(settings_obj, 'printer_name', '') or '').strip():
        return False
    return is_available()


def print_receipt_async(order):
    """Fire-and-forget, like the kitchen ticket: spooling can block for seconds on a busy
    printer, and the cashier's 'دفع وطباعة' must not wait on that."""
    import threading

    def _run():
        try:
            print_receipt(order)
        except Exception:
            logger.warning('receipt print thread crashed', exc_info=True)

    threading.Thread(target=_run, daemon=True).start()
