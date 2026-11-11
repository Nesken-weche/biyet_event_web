from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

from . import firebase


class FirebaseBackend(BaseBackend):
    """Authenticate a Django user from a Firebase ID token.

    The Firebase UID becomes the Django username, so each Firebase account maps
    to exactly one Django user regardless of sign-in provider.
    """

    def authenticate(self, request, firebase_id_token=None, **kwargs):
        if not firebase_id_token:
            return None
        decoded = firebase.verify_id_token(firebase_id_token)
        if not decoded:
            return None

        User = get_user_model()
        uid = decoded["uid"]
        email = decoded.get("email", "") or ""
        name = decoded.get("name", "") or ""

        user, created = User.objects.get_or_create(username=uid, defaults={"email": email})
        changed = False
        if email and user.email != email:
            user.email, changed = email, True
        if name and not user.first_name:
            first, _, last = name.partition(" ")
            user.first_name, user.last_name, changed = first[:150], last[:150], True
        if created:
            user.set_unusable_password()
            changed = True
        if changed:
            user.save()
        return user if user.is_active else None

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
