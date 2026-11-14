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

# Tests don't depend on a real running Redis: in-memory channel layer, and
# Celery tasks run inline (no worker/broker needed).
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
