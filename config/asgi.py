"""
ASGI config for config project.

Routes HTTP to regular Django and WebSocket to the Channels consumers — both in
the same ASGI process (Daphne). It must call django.setup() and resolve the
Django app (get_asgi_application) BEFORE importing anything that touches
models/apps (the consumers' routing), otherwise AppRegistryNotReady is raised.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

import django
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django.setup()

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.people.ws_auth import JWTAuthMiddleware  # noqa: E402
from apps.production.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
    }
)
