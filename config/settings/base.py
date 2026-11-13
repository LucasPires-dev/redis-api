from datetime import timedelta
from pathlib import Path

import dj_database_url
from celery.schedules import crontab
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="", cast=Csv())

AUTH_USER_MODEL = "people.Person"

INSTALLED_APPS = [
    "daphne",  # must come before django.contrib.staticfiles (Channels convention: replaces
    # the default runserver with one that also serves WebSocket in dev)
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "corsheaders",
    "django_filters",
    "apps.people",
    "apps.churches",
    "apps.reconciliation",
    "apps.production",
    "apps.courses",
    "apps.payments",
    "apps.notifications",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": dj_database_url.parse(
        config("DATABASE_URL"),
        conn_max_age=config("DB_CONN_MAX_AGE", default=0, cast=int),
    )
}
# Supabase's pooler (port 6543) is PgBouncer in transaction mode, which does not
# support psycopg3's server-side prepared statements. Disable them to avoid
# "prepared statement already exists" errors under the pooler.
DATABASES["default"].setdefault("OPTIONS", {})
DATABASES["default"]["OPTIONS"]["prepare_threshold"] = None

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="", cast=Csv())
# Enable only on test deploys where the frontend's IP on the local network isn't
# fixed (so it can't be listed in CORS_ALLOWED_ORIGINS up front). Keep it False
# by default — never enable this on a deploy exposed to the internet.
CORS_ALLOW_ALL_ORIGINS = config("CORS_ALLOW_ALL_ORIGINS", default=False, cast=bool)

# Redis: the Channels channel layer (WebSocket) and Celery's broker/result backend — different
# DB indexes (0 and 1) on the same instance so the two queues don't mix. The default points
# to the local Redis (outside Docker); docker-compose.yml/swarm-stack.yml override it with
# the `redis` service hostname inside the container network.
REDIS_URL = config("REDIS_URL", default="redis://127.0.0.1:6379/0")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

CELERY_BROKER_URL = config("CELERY_BROKER_URL", default="redis://127.0.0.1:6379/1")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

# Reconciliação: service-account read access to the Google Sheet fed by the
# "novo convertido" Google Form. GOOGLE_SHEETS_CREDENTIALS_JSON is the service
# account key JSON, base64-encoded (survives ephemeral-filesystem hosting the
# same way SECRET_KEY/DATABASE_URL already do — no file to mount).
GOOGLE_SHEETS_CREDENTIALS_JSON = config("GOOGLE_SHEETS_CREDENTIALS_JSON", default="")
GOOGLE_SHEETS_SPREADSHEET_ID = config("GOOGLE_SHEETS_SPREADSHEET_ID", default="")
GOOGLE_SHEETS_RANGE = config("GOOGLE_SHEETS_RANGE", default="Respostas ao formulário 1!A:Z")

# Replaces the manual `python manage.py sync_reconciliation_leads` with a periodic run
# via Celery Beat — the task just calls the same management command (no duplicated logic).
CELERY_BEAT_SCHEDULE = {
    "sync-reconciliation-leads": {
        "task": "apps.reconciliation.tasks.sync_reconciliation_leads",
        "schedule": crontab(minute="*/15"),
    },
    # Notifications (apps/notifications/reminders.py). Each reminder is sent once.
    "notifications-hourly": {
        "task": "apps.notifications.tasks.send_hourly_reminders",
        "schedule": crontab(minute=5),
    },
    "notifications-daily": {
        "task": "apps.notifications.tasks.send_daily_reminders",
        "schedule": crontab(minute=0, hour=8),
    },
    "notifications-weekly": {
        "task": "apps.notifications.tasks.send_weekly_reminders",
        "schedule": crontab(minute=30, hour=8, day_of_week="mon"),
    },
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Public sign-up and invite codes are guessable/abusable without a limit.
    "DEFAULT_THROTTLE_RATES": {"register": "10/hour", "campus_invite": "30/hour"},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

ASAAS_API_KEY = config("ASAAS_API_KEY", default="")
ASAAS_SANDBOX = config("ASAAS_SANDBOX", default=True, cast=bool)
# Token set in the Asaas panel for the webhook (sent in the `asaas-access-token`
# header). Empty = the webhook rejects every call (fail closed).
ASAAS_WEBHOOK_TOKEN = config("ASAAS_WEBHOOK_TOKEN", default="")

SPECTACULAR_SETTINGS = {
    "TITLE": "Redis API",
    "DESCRIPTION": "API da plataforma de gestão da Igreja Luz às Nações (ILAN).",
    "VERSION": "1.0.0",
}
