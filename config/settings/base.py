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
    "daphne",  # precisa vir antes de django.contrib.staticfiles (convenção Channels: substitui
    # o runserver padrão por um que também serve WebSocket em dev)
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
# Liga só em deploys de teste onde o IP do frontend na rede local não é fixo
# (então não dá pra listar em CORS_ALLOWED_ORIGINS de antemão). Mantém False
# por padrão — nunca ligar isso com um deploy exposto à internet.
CORS_ALLOW_ALL_ORIGINS = config("CORS_ALLOW_ALL_ORIGINS", default=False, cast=bool)

# Redis: camada de canais do Channels (WebSocket) e broker/result backend do Celery — índices
# de DB diferentes (0 e 1) na mesma instância pra não misturar as duas filas. Default aponta
# pro Redis local (fora de Docker); docker-compose.yml/swarm-stack.yml sobrescrevem para o
# hostname do serviço `redis` dentro da rede do container.
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

# Substitui o `python manage.py sync_reconciliation_leads` manual por uma execução periódica
# via Celery Beat — a task só chama o mesmo management command (nenhuma lógica duplicada).
CELERY_BEAT_SCHEDULE = {
    "sync-reconciliation-leads": {
        "task": "apps.reconciliation.tasks.sync_reconciliation_leads",
        "schedule": crontab(minute="*/15"),
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
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Redis API",
    "DESCRIPTION": "API da plataforma de gestão da Igreja Luz às Nações (ILAN).",
    "VERSION": "1.0.0",
}
