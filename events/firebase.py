"""Thin wrapper around firebase-admin so the rest of the app never touches it directly."""
import json
import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)
_app = None


class FirebaseNotConfigured(RuntimeError):
    pass


def get_app():
    """Initialise firebase_admin once, lazily (so tests and manage.py work without credentials)."""
    global _app
    if _app is not None:
        return _app

    import firebase_admin
    from firebase_admin import credentials

    cred_source = settings.FIREBASE_CREDENTIALS
    b64 = os.environ.get("FIREBASE_CREDENTIALS_B64", "").strip()
    if b64:
        # Heroku-friendly: the whole service-account file as one base64 line.
        import base64

        cred_source = base64.b64decode(b64).decode("utf-8")
    if not cred_source:
        raise FirebaseNotConfigured(
            "Set FIREBASE_CREDENTIALS (file path or JSON) or FIREBASE_CREDENTIALS_B64.")

    # Accept either a file path or the raw JSON (handy on hosts that only offer env vars).
    if cred_source.strip().startswith("{"):
        cred = credentials.Certificate(json.loads(cred_source))
    elif os.path.exists(cred_source):
        cred = credentials.Certificate(cred_source)
    else:
        raise FirebaseNotConfigured(f"Firebase credentials file not found: {cred_source}")

    _app = firebase_admin.initialize_app(cred)
    return _app


def verify_id_token(id_token):
    """Return the decoded token dict, or None if the token is invalid/expired/revoked."""
    from firebase_admin import auth

    app = get_app()
    try:
        return auth.verify_id_token(id_token, app=app, check_revoked=True)
    except Exception as exc:  # firebase_admin raises several distinct error types
        logger.info("Rejected Firebase ID token: %s", exc)
        return None