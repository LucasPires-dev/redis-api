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
