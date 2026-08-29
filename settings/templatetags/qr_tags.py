"""Render the receipt QR code locally instead of fetching it from the internet.

The invoice templates used to point <img src> at https://api.qrserver.com. A cafe till is
frequently offline — and the packaged desktop build is often on a LAN with no route out at
all — so the receipt printed with a broken-image box and the words "QR Code" where the code
should have been.

This draws the same code with the `qrcode` package that is already a dependency and hands
the template a `data:` URI, so the image is part of the document before it is printed. That
matters beyond being offline: the thermal receipt auto-prints as soon as it renders, and a
remote image can easily still be in flight at that moment.
"""
import base64
import io
import logging

from django import template
from django.core.cache import cache
from django.utils.safestring import mark_safe

logger = logging.getLogger(__name__)
register = template.Library()

# A QR for the same link is identical every time, and a receipt is printed constantly.
_CACHE_SECONDS = 60 * 60 * 24

# Part of the cache key. Bump it whenever the drawing below changes, or installs that
# already cached an image keep serving the old one for a day — which is exactly what
# happened when the code was first resized.
_RENDER_VERSION = 2


@register.filter
def qr_data_uri(link, size=90):
    """A `data:image/png;base64,...` QR for `link`, or '' when there is nothing to encode.

    Returns an empty string rather than raising: a receipt that cannot draw its QR must
    still print. The caller decides what to show in its place.
    """
    link = (link or '').strip()
    if not link:
        return ''

    key = 'qr_data_uri:%s:%s:%s' % (_RENDER_VERSION, size, link)
    cached = cache.get(key)
    if cached is not None:
        return mark_safe(cached)

    try:
        import qrcode

        # qrcode.make() defaults to box_size=10, border=4 — nearly a megabyte of PNG for
        # something printed at 80px on a thermal receipt, carried inside every invoice and
        # every PDF. These settings put a scannable code in a few hundred bytes.
        qr = qrcode.QRCode(box_size=3, border=2,
                           error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(link)
        qr.make(fit=True)
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG', optimize=True)
        uri = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode('ascii')
    except Exception:
        # Never let a decorative code take the receipt down with it.
        logger.warning('Could not render the receipt QR code', exc_info=True)
        return ''

    cache.set(key, uri, _CACHE_SECONDS)
    return mark_safe(uri)
