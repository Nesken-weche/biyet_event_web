from django.conf import settings
from django.core.cache import cache


def firebase_config(request):
    cfg = settings.FIREBASE_WEB_CONFIG
    out = {
        "firebase_web_config": cfg,
        "firebase_enabled": bool(cfg.get("apiKey") and cfg.get("projectId")),
        "push_enabled": bool(cfg.get("apiKey") and cfg.get("messagingSenderId") and settings.FIREBASE_VAPID_KEY),
        "vapid_key": settings.FIREBASE_VAPID_KEY,
        "cart_count": 0,
        "initials": "",
        "notif_dot": False,
    }
    if getattr(request, "user", None) is None or not request.user.is_authenticated:
        return out
    out["cart_count"] = sum(i.get("qty", 0) for i in request.session.get("cart", []))
    c = getattr(request, "_biyet_customer", None)
    if c is not None and c.loaded:
        out["initials"] = c.initials
        key = f"unread:{c.uid}"
        n = cache.get(key)
        if n is None:
            from .views_account import unread_count

            n = unread_count(c)
            cache.set(key, n, 60)
        out["notif_dot"] = n > 0
    return out