from .base import *  # noqa: F401,F403

DEBUG = False

# Fast, isolated DB for the test suite — independent of whatever DATABASE_URL points at.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Testes não dependem de um Redis real rodando: camada de canais em memória, e
# tasks do Celery executam inline (sem precisar de worker/broker).
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
