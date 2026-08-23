class LocalhostSecureCookieMiddleware:
    """Desktop EXE build only (wired in via production_settings.py).

    The native app window (pywebview/WebView2) loads http://localhost:PORT — WebView2
    doesn't always send a SameSite=Lax cookie back the way a normal browser tab does
    (its internal navigation isn't always recognized as a "top-level" navigation), so the
    CSRF cookie Django set on page load was silently never coming back on the next POST,
    failing CSRF verification on EVERY form submission in the desktop app: login, but
    also creating an order from the waiter/cashier screen, which is why that looked like
    a crash — it was a raw Django 403 page rendering inside the app's single window.

    A global SESSION_COOKIE_SECURE=True/SameSite=None fix was tried once — it does work
    around the WebView2 quirk, but Secure cookies are only ever sent back over HTTPS,
    with a narrow Chromium exception for the literal `localhost` origin. That exception
    does NOT cover a phone/tablet on the same WiFi hitting a real LAN IP
    (http://192.168.x.x:PORT is not "localhost" to any browser) — so the blanket fix
    silently broke every other device on the network from ever getting a session cookie
    at all.

    This middleware instead rewrites the Set-Cookie attributes AFTER the fact, only for
    requests whose Host header is literally `localhost` or `127.0.0.1` — i.e. only the
    desktop app's own window. Every other client (a LAN IP, which is what a phone/tablet
    always uses) keeps Django's normal Secure=False/SameSite=Lax cookies untouched.
    """

    LOCAL_HOSTS = {'localhost', '127.0.0.1'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        host = request.get_host().split(':')[0].lower()
        if host not in self.LOCAL_HOSTS:
            return response

        from django.conf import settings
        for cookie_name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
            if cookie_name in response.cookies:
                response.cookies[cookie_name]['samesite'] = 'None'
                response.cookies[cookie_name]['secure'] = True

        return response
