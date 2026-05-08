"""ASGI config: HTTP + WebSocket via Django Channels."""
import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.development")

django_asgi_app = get_asgi_application()

# Import websocket routes only after Django is set up (so apps are loaded).
from apps.chat.routing import websocket_urlpatterns as chat_ws  # noqa: E402
from apps.documents.routing import websocket_urlpatterns as doc_ws  # noqa: E402
from common.channels_auth import JWTAuthMiddlewareStack  # noqa: E402

websocket_urlpatterns = doc_ws + chat_ws

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns))
        ),
    }
)
