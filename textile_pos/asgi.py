"""
ASGI config for textile_pos project.

Serves HTTP via Django as before, plus websocket routes for the cafe's live
KDS / waiter table-map / delivery screens (restaurant.routing).

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'textile_pos.settings')

django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

import restaurant.routing  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AuthMiddlewareStack(
        URLRouter(restaurant.routing.websocket_urlpatterns)
    ),
})
