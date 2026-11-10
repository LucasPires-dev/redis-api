from decouple import config

from .base import *  # noqa: F401,F403

DEBUG = False

# Defaults assume real HTTPS in front (Traefik/nginx with a certificate).
# For a test deploy on a local network without TLS, override these three via
# .env (SECURE_SSL_REDIRECT=False SESSION_COOKIE_SECURE=False
# CSRF_COOKIE_SECURE=False) — with them True and no HTTPS, the browser doesn't
# even store the session cookie (admin becomes unreachable) and the SSL
# redirect loops.
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=True, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=True, cast=bool)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 7, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
