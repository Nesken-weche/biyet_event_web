"""Per-request access to the signed-in customer's Firestore data, and the
gates the Flutter AuthGate / dashboard apply before showing the dashboard."""
from __future__ import annotations

import functools
import zoneinfo

from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import redirect
from django.utils import timezone

from . import data
from .domain import TIX_FIELD, missing_address, missing_profile


def client_ip(request):
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")


class Customer:
    def __init__(self, request):
        self.uid = request.user.username
        self.request = request
        self._user = None
        self.loaded = False

    def load(self, ensure=False):
        if ensure:
            self._user, _ = data.ensure_tix(self.uid, client_ip(self.request))
        else:
            self._user = data.get_user(self.uid)
        self.loaded = True
        return self._user

    @property
    def user(self):
        if not self.loaded:
            self.load()
        return self._user or {}

    @property
    def tix(self):
        t = self.user.get(TIX_FIELD)
        return t if isinstance(t, dict) else {}

    @property
    def country(self):
        return str(self.user.get("country") or "")

    def events(self):
        key = f"events:{self.country}"
        evs = cache.get(key)
        if evs is None:
            evs = data.events_for_country(self.country)
            cache.set(key, evs, 30)
        return evs

    def event_map(self):
        return {e.id: e for e in self.events()}

    def event_types(self):
        types = cache.get("event_types")
        if types is None:
            types = data.event_types()
            cache.set("event_types", types, 300)
        return types

    def rates_and_fee(self):
        key = f"rates:{self.country}"
        v = cache.get(key)
        if v is None:
            v = data.rates_and_fee(self.country)
            cache.set(key, v, 300)
        return v

    @property
    def full_name(self):
        return f"{self.user.get('firstName') or ''} {self.user.get('lastName') or ''}".strip()

    @property
    def initials(self):
        f, l_ = str(self.user.get("firstName") or ""), str(self.user.get("lastName") or "")
        return ((f[:1] + l_[:1]).upper()) or "?"

    @property
    def username(self):
        return str(self.tix.get("username") or "").strip()


def customer(request) -> Customer:
    if not hasattr(request, "_biyet_customer"):
        request._biyet_customer = Customer(request)
    return request._biyet_customer


def invalidate_events(country):
    cache.delete(f"events:{country}")


def customer_required(view=None, *, allow_incomplete=False):
    """Signed in + has a users/{uid} customer doc + verified + profile done."""

    def deco(fn):
        @login_required
        @functools.wraps(fn)
        def wrapper(request, *args, **kwargs):
            c = customer(request)
            user = c.load(ensure=not request.session.get("tix_ensured"))
            request.session["tix_ensured"] = True
            if not user:
                logout(request)
                return redirect("/login/?e=no_customer")
            if user.get("be_req_del") is True:
                logout(request)
                return redirect("/login/?e=deleted")
            if user.get("is_blocked") is True:
                logout(request)
                return redirect("/login/?e=blocked")
            if user.get("true_account") is False:
                return redirect("verify_code")
            if not allow_incomplete and (missing_profile(c.tix) or missing_address(user)):
                return redirect("profile")
            return fn(request, *args, **kwargs)

        return wrapper

    return deco(view) if view else deco


class TimezoneMiddleware:
    """Show times in the visitor's own zone (set by app.js in the `tz` cookie)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tz = request.COOKIES.get("tz")
        try:
            timezone.activate(zoneinfo.ZoneInfo(tz)) if tz else timezone.deactivate()
        except Exception:  # noqa: BLE001
            timezone.deactivate()
        return self.get_response(request)
