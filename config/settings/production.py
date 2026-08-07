from decouple import config

from .base import *  # noqa: F401,F403

DEBUG = False

# Defaults assumem HTTPS de verdade na frente (Traefik/nginx com certificado).
# Para um deploy de teste em rede local sem TLS, sobrescreva essas três via
# .env (SECURE_SSL_REDIRECT=False SESSION_COOKIE_SECURE=False
# CSRF_COOKIE_SECURE=False) — com elas True e sem HTTPS, o navegador nem
# guarda o cookie de sessão (admin fica inacessível) e o redirect de SSL faz
# loop.
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=True, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=True, cast=bool)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 7, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
