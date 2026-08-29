"""Server-side direct printing of the CUSTOMER RECEIPT to the main printer.

WHY THIS EXISTS
---------------
The receipt used to be printed only by the browser, via `window.print()` in
templates/sales/invoice.html. That works in a real browser — a phone on the shop's wifi
prints fine — but the packaged desktop app shows the page inside WebView2, where
`window.print()` does nothing at all: no dialog, no error, no paper. So on the till, the
one machine the receipt actually has to come out of, "طباعة الفاتورة تلقائياً" silently
printed nothing.

TWO EARLIER VERSIONS OF THIS FILE, both abandoned:
  1. A hand-drawn bitmap with its own, simplified layout — printed, but didn't match the
     real receipt (no logo, and a different field order/wording than invoice.html).
  2. Rendering the REAL invoice.html through a headless Edge/Chrome (the same engine
     "تحميل PDF" already uses) and rasterising the resulting PDF. Visually perfect, but
     unreliable in practice: launching a full browser process per receipt occasionally
     raced during Windows' file flush and produced no output at all, even after retrying
     — not an acceptable failure rate for something that has to work on every sale.

This version hand-draws the receipt again (the reliable, already-proven GDI print path
kitchen tickets already use — direct_print.print_image), but carefully matches
invoice.html field-for-field: same labels, same order, same conditions for showing each
line, and now including the shop's logo. The two must be kept in sync by hand if
invoice.html's layout changes — there is no way around that once the choice is "reliable
but separate" over "identical but fragile".
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


def _money(value):
    """Two decimals, without the trailing-zero noise Decimal gives with :g."""
    from decimal import Decimal
    try:
        return f'{Decimal(str(value or 0)):.2f}'
    except Exception:
        return str(value or 0)


def _pct(value):
    """A percentage without Decimal's trailing zeros — "14%", not "14.00%"."""
    from decimal import Decimal
    try:
        return f'{Decimal(str(value or 0)).normalize():f}'
    except Exception:
        return str(value or 0)


def _payment_method_label(order):
    """Mirrors invoice.html's own cascading if/elif exactly — same wording, same
    single-method-only detection, same 'مقسم (Split)' fallback."""
    if order.is_online_order and not order.driver_settled_at:
        return 'الدفع عند الاستلام'
    cash, wallet, instapay, visa, credit = (
        order.cash_paid or 0, order.wallet_paid or 0, order.instapay_paid or 0,
        order.visa_paid or 0, order.credit_paid or 0,
    )
    if cash > 0 and wallet == 0 and instapay == 0 and visa == 0 and credit == 0:
        return 'نقدي'
    if wallet > 0 and cash == 0 and instapay == 0 and visa == 0 and credit == 0:
        return 'محفظة'
    if instapay > 0 and cash == 0 and wallet == 0 and visa == 0 and credit == 0:
        return 'إنستا باي'
    if visa > 0 and cash == 0 and wallet == 0 and instapay == 0 and credit == 0:
        return 'فيزا'
    if credit > 0 and cash == 0 and wallet == 0 and wallet == 0 and instapay == 0 and visa == 0:
        return 'رصيد سابق'
    return 'مقسم (Split)'


def _decode_logo(sys_settings):
    """The shop's uploaded logo as a PIL Image, or None. Stored as a data: URI
    (settings.SystemSetting.logo_base64) — the same field invoice.html's <img> reads."""
    import base64
    import io as _io

    from PIL import Image

    raw = (getattr(sys_settings, 'logo_base64', '') or '').strip()
    if not raw:
        return None
    try:
        if ',' in raw:
            raw = raw.split(',', 1)[1]
        return Image.open(_io.BytesIO(base64.b64decode(raw))).convert('RGBA')
    except Exception:
        logger.warning('receipt logo could not be decoded', exc_info=True)
        return None


def render_receipt_image(order, sys_settings=None):
    """Draw the customer receipt as a 1-bit bitmap, field-for-field matching
    templates/sales/invoice.html's thermal layout — see the module docstring for why
    this draws its own bitmap instead of rendering the real template.
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
    f_item_hdr = _load_font(20)
    f_total = _load_font(38)
    f_foot = _load_font(22)

    # Generous canvas, cropped to the real content height at the end — a thermal roll has
    # no fixed page length, so printing blank space would just waste paper.
    img = Image.new('L', (TICKET_WIDTH_PX, 8000), 255)
    d = ImageDraw.Draw(img)
    y = 16
    M = TICKET_MARGIN_PX

    def centered(text, font, gap=8):
        nonlocal y
        s = _shape(text)
        w = d.textlength(s, font=font)
        d.text(((TICKET_WIDTH_PX - w) / 2, y), s, font=font, fill=0)
        y += font.size + gap

    def row(label, value, font, gap=6, indent=0, small_value_font=None):
        """Label on the right (the Arabic start edge), amount on the left — matching an
        RTL HTML table's <td>label</td><td>value</td> row."""
        nonlocal y
        vfont = small_value_font or font
        ls = _shape(label)
        vs = _shape(value)
        d.text((TICKET_WIDTH_PX - M - indent - d.textlength(ls, font=font), y),
               ls, font=font, fill=0)
        d.text((M, y), vs, font=vfont, fill=0)
        y += font.size + gap

    def rtl_line(text, font, indent=0, gap=6):
        nonlocal y
        s = _shape(text)
        d.text((TICKET_WIDTH_PX - M - indent - d.textlength(s, font=font), y),
               s, font=font, fill=0)
        y += font.size + gap

    def divider(gap=10, double=False):
        nonlocal y
        y += 4
        d.line((M, y, TICKET_WIDTH_PX - M, y), fill=0, width=2)
        if double:
            y += 5
            d.line((M, y, TICKET_WIDTH_PX - M, y), fill=0, width=2)
        y += gap

    # ── header ─────────────────────────────────────────────────────────────────
    logo = _decode_logo(sys_settings)
    if logo is not None:
        max_w, max_h = 200, 140
        ratio = min(max_w / logo.width, max_h / logo.height, 1)
        logo_resized = logo.resize((max(1, int(logo.width * ratio)), max(1, int(logo.height * ratio))))
        bg = Image.new('L', logo_resized.size, 255)
        bg.paste(logo_resized.convert('L'), mask=logo_resized.split()[3] if logo_resized.mode == 'RGBA' else None)
        img.paste(bg, (int((TICKET_WIDTH_PX - bg.width) / 2), y))
        y += bg.height + 8

    if sys_settings and sys_settings.shop_name:
        centered(sys_settings.shop_name, f_shop, gap=6)
    if sys_settings and getattr(sys_settings, 'address', ''):
        centered(sys_settings.address, f_small, gap=4)
    if sys_settings and getattr(sys_settings, 'phone', ''):
        centered(str(sys_settings.phone), f_small, gap=6)
    divider()

    # ── info ───────────────────────────────────────────────────────────────────
    row('رقم الإيصال', str(order.display_number), f_meta)
    row('التاريخ', timezone.localtime(order.created_at).strftime('%d/%m/%Y %H:%M'), f_meta)
    if get_policy('receipts.show_salesman_name'):
        salesman = getattr(order, 'salesman_name', '') or ''
        label = 'البائع' if salesman else 'الكاشير'
        who = salesman or (order.user.username if getattr(order, 'user_id', None) and order.user else '')
        if who:
            row(label, who, f_meta)
    if getattr(order, 'customer_id', None) and order.customer:
        row('العميل', f'{order.customer.first_name} {order.customer.last_name}'.strip(), f_meta)
    else:
        row('العميل', 'عميل نقدي', f_meta)
    if order.is_online_order and order.customer:
        if getattr(order.customer, 'phone', ''):
            row('هاتف التوصيل', order.customer.phone, f_meta)
        addr = getattr(order, 'shipping_address', '') or getattr(order.customer, 'address', '')
        if addr:
            row('عنوان التوصيل', addr, f_meta)
        shipment_comment = getattr(getattr(order, 'shipment', None), 'comment', '')
        if shipment_comment:
            row('ملاحظات الشحن', shipment_comment, f_meta)
    divider()

    # ── items ──────────────────────────────────────────────────────────────────
    # Column layout mirrors the real 4-column table (الصنف | الكمية | السعر | الإجمالي):
    # a wide name column on the RTL start edge, three narrower numeric columns after it.
    col_qty_w, col_price_w, col_total_w = 70, 90, 90
    col_name_w = (TICKET_WIDTH_PX - 2 * M) - col_qty_w - col_price_w - col_total_w
    x_name_right = TICKET_WIDTH_PX - M
    x_qty_center = TICKET_WIDTH_PX - M - col_name_w - col_qty_w / 2
    x_price_center = x_qty_center - col_qty_w / 2 - col_price_w / 2
    x_total_left = M

    def item_header():
        nonlocal y
        d.text((x_name_right - d.textlength(_shape('الصنف'), font=f_item_hdr), y), _shape('الصنف'), font=f_item_hdr, fill=0)
        for label, cx in (('الكمية', x_qty_center), ('السعر', x_price_center)):
            s = _shape(label)
            d.text((cx - d.textlength(s, font=f_item_hdr) / 2, y), s, font=f_item_hdr, fill=0)
        d.text((x_total_left, y), _shape('الإجمالي'), font=f_item_hdr, fill=0)
        y += f_item_hdr.size + 6

    def item_row(name, qty, price, total, font=f_item, small=False, dim=False):
        nonlocal y
        fill = 0
        ns = _shape(name)
        # Long item names wrap onto their own line above the numeric columns rather
        # than overlapping them.
        if d.textlength(ns, font=font) > col_name_w:
            d.text((x_name_right - d.textlength(ns, font=font), y), ns, font=font, fill=fill)
            y += font.size + 2
        else:
            d.text((x_name_right - d.textlength(ns, font=font), y), ns, font=font, fill=fill)
        qs, ps, ts = _shape(qty), _shape(price), _shape(total)
        d.text((x_qty_center - d.textlength(qs, font=font) / 2, y), qs, font=font, fill=fill)
        d.text((x_price_center - d.textlength(ps, font=font) / 2, y), ps, font=font, fill=fill)
        d.text((x_total_left, y), ts, font=font, fill=fill)
        y += font.size + (4 if small else 6)

    item_header()
    for line in order.receipt_line_items():
        try:
            qty = line['quantity'].normalize()
        except AttributeError:
            qty = line['quantity']
        name = line['product_name']
        if line.get('variant_label'):
            name += f" ({line['variant_label']})"
        item_row(name, f'{qty:f}', _money(line['unit_base_price']), _money(line['base_total']))
        for extra in line.get('extras') or []:
            try:
                eqty = extra['quantity'].normalize()
            except AttributeError:
                eqty = extra['quantity']
            item_row('↳ ' + extra['option'], f'{eqty:f}', _money(extra['unit_price']),
                     _money(extra['total']), font=f_small, small=True)
    divider()

    # ── totals ─────────────────────────────────────────────────────────────────
    row('المجموع:', _money(order.subtotal_amount), f_small)
    if (order.discount or 0) > 0:
        if order.discount_type == 'percent':
            disc_val = f'({_pct(order.discount)}%)'
        else:
            disc_val = f'({_money(order.discount)})'
        row('- خصم:', disc_val, f_small)
    if (getattr(order, 'delivery_cost', 0) or 0) > 0:
        row('+ خدمة توصيل:', _money(order.delivery_cost), f_small)
    if getattr(order, 'is_tailoring', False) and (getattr(order, 'tailoring_cost', 0) or 0) > 0:
        row('+ مصنعية/تفصيل:', _money(order.tailoring_cost), f_small)

    divider(double=True)

    vat = order.vat_breakdown()
    if vat:
        label = f"ض.ق.م ({_pct(vat['rate'])}%) " + ('شاملة' if vat['included'] else 'مضافة') + ':'
        row(label, _money(vat['tax']), f_small)
    svc = order.service_charge_breakdown()
    if svc:
        label = f"رسوم الخدمة ({_pct(svc['pct'])}%) " + ('مضمنة' if svc['included'] else 'مضافة') + ':'
        row(label, _money(svc['amount']), f_small)

    row('الإجمالي:', _money(order.total_amount), f_total, gap=10)

    # ── payment ────────────────────────────────────────────────────────────────
    y += 4
    d.line((M, y, TICKET_WIDTH_PX - M, y), fill=180, width=1)
    y += 8
    if not order.is_online_order or order.driver_settled_at:
        row('المدفوع:', _money(order.received_amount), f_small)
    row('طريقة الدفع:', _payment_method_label(order), f_small)

    if order.payment_method == 'custom' or (order.credit_paid or 0) > 0:
        for amount, label in (
            (order.cash_paid, '- نقدي:'), (order.wallet_paid, '- فودافون كاش:'),
            (order.instapay_paid, '- إنستا باي:'), (order.visa_paid, '- فيزا:'),
            (order.credit_paid, '- رصيد سابق:'),
        ):
            if (amount or 0) > 0:
                row(label, _money(amount), f_small, indent=12)

    if order.is_online_order and not order.driver_settled_at:
        pass  # COD: the customer owes nothing on this receipt, same as invoice.html.
    elif (order.remaining_amount or 0) > 0:
        # No warning glyph (⚠️) — the printer font (Tahoma) has no emoji coverage, which
        # rendered as an empty box rather than the actual symbol.
        row('المتبقي (عليه):', _money(order.remaining_amount), f_small)
    elif (order.remaining_amount or 0) < 0:
        row('الباقي:', _money(-order.remaining_amount), f_small)

    if getattr(order, 'is_tailoring', False):
        divider()
        centered('تفاصيل التفصيل', f_small, gap=4)
        if getattr(order, 'tailoring_type', ''):
            centered(order.tailoring_type, f_small, gap=4)
        if getattr(order, 'notes', ''):
            centered(order.notes, f_small, gap=4)

    # ── footer ─────────────────────────────────────────────────────────────────
    y += 6
    divider()
    if sys_settings and getattr(sys_settings, 'thank_you_text', ''):
        centered(sys_settings.thank_you_text, f_foot, gap=4)
    if sys_settings and getattr(sys_settings, 'return_policy', ''):
        centered(sys_settings.return_policy, f_foot, gap=4)

    # The QR is drawn straight into the bitmap — no network, so it prints offline too —
    # the same reason settings/templatetags/qr_tags.py stopped fetching it remotely.
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
