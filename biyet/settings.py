"""Django settings for Biyet — event ticketing PWA backed by Firebase Auth."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "events",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "events.context.TimezoneMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "biyet.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "events.context_processors.firebase_config",
            ],
        },
    },
]

WSGI_APPLICATION = "biyet.wsgi.application"

# Heroku Postgres sets DATABASE_URL. Without it (your computer) SQLite is used.
# Django only keeps sign-in sessions and its auth table here; all app data
# lives in Firestore.
if os.environ.get("DATABASE_URL"):
    import dj_database_url

    DATABASES = {"default": dj_database_url.config(conn_max_age=600, ssl_require=True)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTHENTICATION_BACKENDS = [
    "events.auth_backend.FirebaseBackend",
    "django.contrib.auth.backends.ModelBackend",  # keeps /admin/ password login working
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "events"

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "America/Indiana/Indianapolis")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Firebase ---------------------------------------------------------------
# Server side: path to a service-account JSON file (Firebase console →
# Project settings → Service accounts → Generate new private key).
FIREBASE_CREDENTIALS = os.environ.get("FIREBASE_CREDENTIALS", "")

# Client side: the web app config (Project settings → General → Your apps).
FIREBASE_WEB_CONFIG = {
    "apiKey": os.environ.get("FIREBASE_API_KEY", ""),
    "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
    "projectId": os.environ.get("FIREBASE_PROJECT_ID", ""),
    "storageBucket": os.environ.get("FIREBASE_STORAGE_BUCKET", ""),
    "messagingSenderId": os.environ.get("FIREBASE_MESSAGING_SENDER_ID", ""),
    "appId": os.environ.get("FIREBASE_APP_ID", ""),
}
# Web push: Firebase console → Project settings → Cloud Messaging →
# Web Push certificates → "Generate key pair" (the public key).
FIREBASE_VAPID_KEY = os.environ.get("FIREBASE_VAPID_KEY", "")

# Local demo / tests: an in-memory Firestore (saved to fake_firestore.json).
FIRESTORE_FAKE = env_bool("FIRESTORE_FAKE", False)

# --- Card payments (optional) ----------------------------------------------
# Same Cloud Function the app uses to create the PaymentIntent, plus the Stripe
# secret key so the server can confirm the card was charged before saving
# the order. Leave either empty to accept wallet payments only on the web.
STRIPE_PAYMENT_INTENT_URL = os.environ.get(
    "STRIPE_PAYMENT_INTENT_URL",
    "https://us-central1-kobey-e0eca.cloudfunctions.net/GicheStripePaymentIntent",
)
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")

SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"

# Errors go to the Heroku log (heroku logs --tail).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}

if not DEBUG:
    if SECRET_KEY == "dev-insecure-change-me":
        raise RuntimeError("Set DJANGO_SECRET_KEY before running with DJANGO_DEBUG=false.")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # Heroku's router ends HTTPS
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "3600"))
    SECURE_CONTENT_TYPE_NOSNIFF = True