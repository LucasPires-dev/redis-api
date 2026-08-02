"""
ASGI config for config project.

Roteia HTTP pro Django normal e WebSocket pros consumers do Channels — ambos no
mesmo processo ASGI (Daphne). Precisa chamar django.setup() e resolver o app
Django (get_asgi_application) ANTES de importar qualquer coisa que toque
models/apps (routing dos consumers), senão estoura AppRegistryNotReady.

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
