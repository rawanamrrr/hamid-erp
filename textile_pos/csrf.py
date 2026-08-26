"""What the user sees when a CSRF check fails.

Django's stock response is a bare 403 page with no navigation on it. In a browser tab
that is merely ugly — you press Back. In the packaged desktop app it is a dead end: the
window has no address bar and no Back button, so the cashier is stuck staring at
"ممنوع (403)" with no way out but killing the app.

And the failure itself is ordinary, not an attack. The common case is a page that sat
open while its CSRF cookie was replaced — the app was restarted, or the login screen was
left up overnight — so the token in the form no longer matches the one in the cookie.
Django logs it as "CSRF token from POST incorrect".

So: send the person back to the page they were on. A fresh GET issues a fresh cookie and
a matching token, and they simply try again. Only if that retry also fails do we show a
page — with a working way out of it, and an explanation.
"""
import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render

logger = logging.getLogger(__name__)

# Marks a request as "already bounced once", so a genuinely broken client (cookies
# disabled, a real forgery attempt) gets an explanation instead of an endless redirect.
RETRY_FLAG = 'csrf_retry'

RETRY_MESSAGE = 'انتهت صلاحية الصفحة بسبب طول فترة فتحها. من فضلك حاول مرة أخرى.'
BLOCKED_MESSAGE = (
    'تعذر التحقق من صحة الطلب. تأكد أن ملفات تعريف الارتباط (Cookies) مفعّلة، '
    'ثم أعد تحميل الصفحة وحاول مرة أخرى.'
)


def _wants_json(request):
    """Would this caller rather have JSON than a redirect?

    The activation screen and several POS actions post with fetch()/XHR and parse the
    reply — redirecting those produces a confusing success-looking response instead of a
    readable error.
    """
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return True
    if 'application/json' in (request.content_type or ''):
        return True
    accept = request.headers.get('accept', '')
    return 'application/json' in accept and 'text/html' not in accept


def csrf_failure(request, reason=''):
    logger.warning('CSRF failure on %s: %s', request.path, reason)

    if _wants_json(request):
        return JsonResponse({'success': False, 'message': RETRY_MESSAGE}, status=403)

    if request.GET.get(RETRY_FLAG) != '1':
        separator = '&' if request.GET else '?'
        target = f'{request.path}{separator}{RETRY_FLAG}=1'
        try:
            messages.warning(request, RETRY_MESSAGE)
        except Exception:
            # Messages need a session; if that is what broke, the page below still says
            # the same thing, so this is never worth failing the response over.
            pass
        return redirect(target)

    return render(request, 'csrf_failure.html', {'message': BLOCKED_MESSAGE}, status=403)
