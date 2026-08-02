"""Autenticação de conexões WebSocket via JWT.

O restante da API usa `rest_framework_simplejwt.authentication.JWTAuthentication`
(header `Authorization: Bearer <token>`), mas o JS `WebSocket` do navegador não
permite setar headers customizados no handshake — por isso o token vem via
query string (`?token=<access>`), mesmo padrão usado por outras libs de
WebSocket+JWT. Reaproveita `JWTAuthentication` para validar o token e resolver
a `Person`, em vez de reimplementar a lógica de validação/lookup.
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
