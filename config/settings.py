"""
Django settings for the iSgela project.

Every environment-specific value is read from the environment (via a .env file
in development) using python-decouple. Nothing secret is hardcoded here.
"""

import sys
from pathlib import Path

from decouple import Csv, config

from .db import parse_database_url, supabase_url_from_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# Whether the test runner is what is executing us. Several settings below depend
# on this, so it is established first.
RUNNING_TESTS = len(sys.argv) > 1 and sys.argv[1] == "test"

# Opt in to running the suite against the real Postgres database instead of the
# in-memory SQLite default. See the database section below.
TEST_ON_POSTGRES = config("TEST_ON_POSTGRES", default=False, cast=bool)


# --------------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------------- #

if RUNNING_TESTS:
    # A fixed throwaway key under the test runner only, so that `manage.py test`
    # works on a fresh clone and in CI without any configuration. Signatures
    # made with it never outlive the test database.
    SECRET_KEY = config("SECRET_KEY", default="insecure-key-used-only-by-the-test-suite")
else:
    # No default: the project should refuse to boot rather than run on a key
    # that has been committed to source control.
    SECRET_KEY = config("SECRET_KEY")

DEBUG = config("DEBUG", default=False, cast=bool)

ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())


# --------------------------------------------------------------------------- #
# Applications
# --------------------------------------------------------------------------- #

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

LOCAL_APPS = [
    # Users, roles, and the parent/student relationship.
    "accounts",
    # Shared domain models, infrastructure, and the role dashboards.
    "core",
    # Papers, memorandums, and the AI marking engine.
    "marking",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves collected static files from the application process. Needed because
    # gunicorn will not do it, and without it the Django admin renders unstyled.
    # Memorandum authoring is admin-only for now, so that is not cosmetic.
    # In development Django's staticfiles app handles this instead.
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
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# --------------------------------------------------------------------------- #
# Database (Supabase Postgres)
# --------------------------------------------------------------------------- #

# The test suite runs against a local in-memory SQLite database by default.
#
# Two reasons: the suite covers view logic and role permissions rather than
# anything Postgres-specific, and running it against a hosted Supabase instance
# takes minutes rather than seconds, which is the difference between running
# tests on every change and not running them at all.
#
# DATABASE_URL is not even read in that case, so `manage.py test` works on a
# fresh clone and in CI with no configuration at all.
#
# To run the same suite against Postgres:
#   TEST_ON_POSTGRES=True python manage.py test
# Against Supabase specifically, add --keepdb: its connection pooler holds a
# session open, which stops Django from dropping the test database afterwards.
if RUNNING_TESTS and not TEST_ON_POSTGRES:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }
else:
    DATABASES = {
        "default": parse_database_url(
            config("DATABASE_URL"),
            conn_max_age=config("DB_CONN_MAX_AGE", default=0, cast=int),
        )
    }


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #

AUTH_USER_MODEL = "accounts.User"

if RUNNING_TESTS:
    # Test-only. The suite creates a lot of accounts, and the default hasher is
    # deliberately slow, which is correct for real passwords and pointless for
    # throwaway fixtures. Never applied outside the test runner.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

    # The marking tests deliberately exercise every failure path, and each one
    # logs a warning. Silencing them keeps a passing run readable; tests that
    # care about a specific log line use assertLogs, which is unaffected.
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"null": {"class": "logging.NullHandler"}},
        "loggers": {
            "marking": {"handlers": ["null"], "level": "WARNING", "propagate": False},
            "core": {"handlers": ["null"], "level": "WARNING", "propagate": False},
        },
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation."
        "UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
# core:home reads request.user.role and forwards to that role's dashboard, so
# one redirect target works for all three roles.
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "accounts:login"


# --------------------------------------------------------------------------- #
# Internationalization
# --------------------------------------------------------------------------- #

LANGUAGE_CODE = "en-za"

TIME_ZONE = config("TIME_ZONE", default="Africa/Johannesburg")

USE_I18N = True

USE_TZ = True


# --------------------------------------------------------------------------- #
# Static files
# --------------------------------------------------------------------------- #

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --------------------------------------------------------------------------- #
# File storage (Supabase Storage)
# --------------------------------------------------------------------------- #

# The project reference is already in DATABASE_URL, either as the username on a
# pooler connection (postgres.<ref>) or in the direct host (db.<ref>.supabase.co).
# Deriving the URL from it by default means one less variable to keep in sync,
# and no way for the storage bucket and the database to point at different
# projects. Set SUPABASE_URL explicitly to override.
SUPABASE_URL = config("SUPABASE_URL", default="") or supabase_url_from_database_url(
    config("DATABASE_URL", default="")
)

# The service_role key, from Supabase → Project Settings → API. It bypasses
# row-level security, so it stays server-side only and never reaches a template.
SUPABASE_SERVICE_KEY = config("SUPABASE_SERVICE_KEY", default="")

SUPABASE_STORAGE_BUCKET = config("SUPABASE_STORAGE_BUCKET", default="papers")

SUPABASE_SIGNED_URL_EXPIRY = config("SUPABASE_SIGNED_URL_EXPIRY", default=3600, cast=int)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # Compresses and fingerprints static files at collectstatic time so they
        # can be served with long cache headers. The plain backend is used under
        # test, where no manifest has been built.
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if RUNNING_TESTS
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
    # Uploaded papers. Deliberately separate from "default" so that nothing
    # else can accidentally end up in the same bucket.
    "papers": {
        "BACKEND": "core.storage.SupabaseStorage",
        "OPTIONS": {
            "bucket": SUPABASE_STORAGE_BUCKET,
            "base_url": SUPABASE_URL,
            "service_key": SUPABASE_SERVICE_KEY,
            "signed_url_expiry": SUPABASE_SIGNED_URL_EXPIRY,
        },
    },
}

if RUNNING_TESTS:
    # Tests must never touch the network or leave objects in the real bucket.
    STORAGES["papers"] = {"BACKEND": "django.core.files.storage.InMemoryStorage"}


# --------------------------------------------------------------------------- #
# Uploads and image processing
# --------------------------------------------------------------------------- #

# A phone photo of an A4 page. Large enough that handwriting stays legible to
# the vision model, small enough to be kind to a metered mobile connection.
MARKING_IMAGE_MAX_DIMENSION = config("MARKING_IMAGE_MAX_DIMENSION", default=1600, cast=int)
MARKING_IMAGE_JPEG_QUALITY = config("MARKING_IMAGE_JPEG_QUALITY", default=80, cast=int)

# Rejected before any processing happens, so a huge upload cannot tie up a worker.
MARKING_MAX_UPLOAD_BYTES = config(
    "MARKING_MAX_UPLOAD_BYTES", default=15 * 1024 * 1024, cast=int
)

# Push anything above 2.5 MB to a temp file instead of holding it in memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2621440
DATA_UPLOAD_MAX_MEMORY_SIZE = MARKING_MAX_UPLOAD_BYTES + 1048576


# --------------------------------------------------------------------------- #
# OpenRouter (AI marking)
# --------------------------------------------------------------------------- #

OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default="")

if RUNNING_TESTS:
    # Forced, not defaulted. The suite mocks every OpenRouter call, and this
    # guarantees two things: it runs with no configuration at all (CI, fresh
    # clone), and a test that forgets to mock cannot spend real quota against a
    # developer's key. It would get a 401 instead.
    OPENROUTER_API_KEY = "sk-or-test-key-never-used-against-the-real-api"

OPENROUTER_BASE_URL = config(
    "OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1"
)

# Model slugs move, and free variants appear and disappear. Keeping this in the
# environment means switching model is a config change, not a code change.
OPENROUTER_MODEL = config("OPENROUTER_MODEL", default="qwen/qwen2.5-vl-72b-instruct")

# Tried in order when the primary model is rate-limited or unavailable, which on
# a free tier is a routine event rather than an exceptional one.
OPENROUTER_FALLBACK_MODELS = config("OPENROUTER_FALLBACK_MODELS", default="", cast=Csv())

OPENROUTER_TIMEOUT = config("OPENROUTER_TIMEOUT", default=120, cast=int)

OPENROUTER_MAX_TOKENS = config("OPENROUTER_MAX_TOKENS", default=2000, cast=int)

# Sent as HTTP-Referer and X-Title so usage is identifiable in the OpenRouter
# dashboard. Optional, and purely for our own observability.
OPENROUTER_APP_URL = config("OPENROUTER_APP_URL", default="https://github.com/isgela")
OPENROUTER_APP_TITLE = config("OPENROUTER_APP_TITLE", default="iSgela")
