"""Reads the dashboard needs, from the same collections as the Flutter app."""
from __future__ import annotations

import datetime as dt

from . import store
from .domain import (
    TIX_FIELD,
    CurrencyRates,
    KEvent,
    age_on,
    event_fee_rate,
    normalize,
    s,
    to_date,
)

USERS = "users"
EVENTS = "user_event_list"
TICKETS = "event_tickets"
ORDERS = "ticket_orders"
SWAPS = "ticket_swaps"
RATES = "convertion_rates"
COUPONS = "coupons"
ANNOUNCEMENTS = "tix_notifications"
PERSONAL_NOTIFS = "tix_personal_notifs"
PROFILE_HISTORY = "tixProfileHistory"


def get_user(uid):
    return store.get(USERS, uid) if uid else None


def ensure_tix(uid, ip=""):
    """TixProfile.ensure: make sure users/{uid}.tixUserData exists and is current."""
    user = get_user(uid)
    if user is None:
        return None, None
    raw = user.get(TIX_FIELD)
    if not isinstance(raw, dict):
        tix = {
            "fcmToken": "",
            "username": s(user.get("username")).strip(),
            "currentIP": ip,
            "DOB": None,
            "idPic": "",
            "idType": "",
            "age": None,
            "over21": False,
            "phoneNumber": s(user.get("phoneNumber") or user.get("phone")).strip(),
            "adminVerified": False,
            "joinedOn": user.get("created_on") or store.now(),
            "attendedCount": 0,
            "attendedEventIds": [],
        }
        store.set(USERS, uid, {TIX_FIELD: {**tix, "created_on": store.SERVER_TS}}, merge=True)
        user[TIX_FIELD] = tix
        return user, tix

    tix = dict(raw)
    updates = {}
    if ip and ip != tix.get("currentIP"):
        updates["currentIP"] = ip
    if tix.get("joinedOn") is None:
        updates["joinedOn"] = user.get("created_on") or tix.get("created_on") or store.now()
    dob = to_date(tix.get("DOB"))
    if dob is not None:
        age = age_on(dob.date())
        if tix.get("age") != age:
            updates["age"] = age
        if tix.get("over21") != (age >= 21):
            updates["over21"] = age >= 21
    if updates:
        store.update(USERS, uid, {f"{TIX_FIELD}.{k}": v for k, v in updates.items()})
        tix.update(updates)
        user[TIX_FIELD] = tix
    return user, tix


def events_for_country(country):
    if not country:
        return []
    return [KEvent.from_map(d, doc_id) for doc_id, d in store.where(EVENTS, [("country", "==", country)])]


def get_event(event_id):
    d = store.get(EVENTS, event_id)
    return KEvent.from_map(d, event_id) if d else None


def developer_doc():
    for name in ("developer", "developper"):
        rows = store.where(name, limit=1)
        if rows:
            return rows[0][1]
    return {}


def event_types():
    raw = developer_doc().get("eventListTypes")
    if not isinstance(raw, list):
        return []
    seen = []
    for t in raw:
        t = str(t).strip().lower()
        if t and t not in seen:
            seen.append(t)
    return seen


def rate_docs():
    return [d for _, d in store.all_docs(RATES)]


def rates_and_fee(country):
    docs = rate_docs()
    return CurrencyRates.from_docs(docs), event_fee_rate(docs, country)


def transport_line():
    tl = developer_doc().get("transportLine")
    return tl if isinstance(tl, list) else []


def user_tickets(uid):
    return store.where(TICKETS, [("user_id", "==", uid)])


def user_orders(uid):
    return store.where(ORDERS, [("uid", "==", uid)])


def upcoming_tickets(uid, limit=3):
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for tid, t in user_tickets(uid):
        details = t.get("event_details") if isinstance(t.get("event_details"), dict) else {}
        d = to_date(details.get("event_date"))
        if t.get("status") == "valid" and d and d > now:
            rows.append({"id": tid, "title": s(details.get("title"), "Event"), "date": d,
                         "venue": s(details.get("bizName")), "quantity": t.get("quantity") or 1})
    rows.sort(key=lambda r: r["date"])
    return rows[:limit]


def search_events(events, query, field):
    keys = {
        "all": ("title", "biz_name", "biz_city", "event_promoter"),
        "title": ("title",),
        "venue": ("biz_name",),
        "city": ("biz_city",),
        "promoter": ("event_promoter",),
    }.get(field, ("title", "biz_name", "biz_city", "event_promoter"))
    terms = normalize(query).split()
    if not terms:
        return events
    out = []
    for e in events:
        hay = " ".join(normalize(getattr(e, k)) for k in keys)
        if all(t in hay for t in terms):
            out.append(e)
    return out


def transport_for(user):
    """customer_dashboard._transport: types available where the user lives."""
    country = normalize(s(user.get("country")))
    state = normalize(s(user.get("userState") or user.get("bizState")))
    city = normalize(s(user.get("userCity") or user.get("bizCity")))
    if not country or not state:
        return [], False
    for c in transport_line():
        if not isinstance(c, dict) or normalize(s(c.get("country"))) != country:
            continue
        for st in c.get("state_list") or []:
            if not isinstance(st, dict) or normalize(s(st.get("state"))) != state:
                continue
            types = [str(x).strip() for x in (st.get("transportType") or []) if str(x).strip()]
            in_city = bool(city) and any(
                normalize(s(ct.get("city") if isinstance(ct, dict) else ct)) == city for ct in st.get("cities") or []
            )
            return types, in_city
    return [], False
