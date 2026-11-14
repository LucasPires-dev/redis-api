"""JWT authentication for WebSocket connections.

The rest of the API uses `rest_framework_simplejwt.authentication.JWTAuthentication`
(`Authorization: Bearer <token>` header), but the browser's JS `WebSocket` doesn't
allow custom headers on the handshake — so the token comes in the query string
(`?token=<access>`), the same pattern other WebSocket+JWT libs use. Reuses
`JWTAuthentication` to validate the token and resolve the `Person`, instead of
reimplementing the validation/lookup logic.
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


@database_sync_to_async
def _resolve_user(token: str):
    try:
        auth = JWTAuthentication()
        validated_token = auth.get_validated_token(token)
        return auth.get_user(validated_token)
    except (InvalidToken, TokenError):
        return AnonymousUser()


class JWTAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        token = parse_qs(query_string).get("token", [None])[0]
        scope["user"] = await _resolve_user(token) if token else AnonymousUser()
        return await self.app(scope, receive, send)
