"""Swap, Notifications, Rewards & referrals, Settings, Profile, the menu (drawer),
reports, sign-in / sign-up / code verification and the PWA endpoints."""
from __future__ import annotations

import datetime as dt
import json
import secrets
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from . import data, firebase, store
from .context import customer, customer_required
from .domain import (
    HAITI_STATES,
    ID_TYPES,
    REPORT_REASONS,
    TIX_FIELD,
    US_STATES,
    age_on,
    avatar_color_from_uid,
    favorite_types,
    is_haiti,
    is_usa,
    missing_address,
    missing_profile,
    money_short,
    month_key,
    month_label,
    new_ticket_id,
    norm,
    notifications_enabled,
    public_name,
    s,
    title_case,
    to_date,
)
from .views import local, type_label

# Biyet Event logo: nav bar, browser-tab icon, share previews, app icon
BRAND_ICON_URL = "https://localplugz.s3.us-east-2.amazonaws.com/featured_images/cf79b589-40d0-4cb3-8662-275abb9a4feb.png"
ANDROID_STORE_URL = "https://play.google.com/store/apps/details?id=com.kenutek.giche"
IOS_STORE_URL = "https://apps.apple.com/app/idXXXXXXXXXX"


# --------------------------------------------------------------------------- #
# Swap (swap_tab.dart)
# --------------------------------------------------------------------------- #

CAP_AT_FACE_VALUE = True


def _swap(doc_id, m):
    d = dict(m)
    d["id"] = doc_id
    d["event_date"] = to_date(m.get("event_date"))
    d["listed_at"] = to_date(m.get("listed_at"))
    d["closed_at"] = to_date(m.get("closed_at"))
    d["local_date"] = local(d["event_date"]) if d["event_date"] else None
    t = s(m.get("type"), "free")
    d["type"] = t if t in ("free", "sell", "exchange") else "free"
    d["status"] = s(m.get("status"), "open")
    ask, face = float(m.get("ask_price") or 0), float(m.get("face_value") or 0)
    d["price_text"] = {"free": "Free", "sell": money_short(ask), "exchange": "Trade"}[d["type"]]
    d["type_text"] = {"free": "Free", "sell": "For sale", "exchange": "Trade"}[d["type"]]
    note = None
    if d["type"] == "sell" and face > 0:
        diff = ask - face
        note = "At face value" if abs(diff) < 0.01 else (
            f"{money_short(-diff)} below face value" if diff < 0 else f"{money_short(diff)} above face value")
    d["price_note"] = note
    d["is_over"] = d["event_date"] is not None and d["event_date"] < dt.datetime.now(dt.timezone.utc)
    return d


@customer_required
def swap_tab(request):
    c = customer(request)
    view = request.GET.get("v", "browse")
    flt = request.GET.get("type", "")
    open_ = [_swap(i, m) for i, m in store.where(data.SWAPS, [("status", "==", "open")])]
    posted = [_swap(i, m) for i, m in store.where(data.SWAPS, [("poster_id", "==", c.uid)])]
    claimed = [_swap(i, m) for i, m in store.where(data.SWAPS, [("claimed_by_uid", "==", c.uid)])]
    for lst_ in (open_, posted, claimed):
        lst_.sort(key=lambda x: x["listed_at"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    browsable = [x for x in open_ if not x["is_over"]]
    counts = {"all": len(browsable), **{t: len([x for x in browsable if x["type"] == t])
                                     for t in ("free", "sell", "exchange")}}
    posted_live = [x for x in posted if x["status"] != "cancelled"]
    needs = [x for x in posted_live if x["status"] == "pending"]
    done = sorted([x for x in posted_live if x["status"] == "closed"] + [x for x in claimed if x["status"] == "closed"],
                  key=lambda x: x["closed_at"] or x["listed_at"], reverse=True)
    return render(request, "biyet/swap.html", {
        "tab": "swap", "v": view, "flt": flt, "counts": counts, "me": c.uid,
        "browse": [x for x in browsable if not flt or x["type"] == flt],
        "needs": needs, "listed": [x for x in posted_live if x["status"] == "open" and not x["is_over"]],
        "requested": [x for x in claimed if x["status"] == "pending"], "done": done,
        "badge": len(needs),
    })


@customer_required
def swap_new(request):
    c = customer(request)
    listed = {s(m.get("ticket_id")) for _, m in store.where(data.SWAPS, [("poster_id", "==", c.uid)])
              if m.get("status") in ("open", "pending")}
    now = dt.datetime.now(dt.timezone.utc)
    tickets = []
    for tid, t in data.user_tickets(c.uid):
        det = t.get("event_details") if isinstance(t.get("event_details"), dict) else {}
        d = to_date(det.get("event_date"))
        if t.get("status") == "valid" and tid not in listed and d and d > now:
            tickets.append({"id": tid, "t": t, "d": det, "date": local(d), "price": float(t.get("price") or 0)})
    tickets.sort(key=lambda x: x["date"])
    error = None
    form = {"type": "free", "ticket": tickets[0]["id"] if len(tickets) == 1 else "", "price": "", "exchange": "",
            "note": ""}
    if request.method == "POST":
        form = {k: request.POST.get(k, "").strip() for k in ("type", "ticket", "price", "exchange", "note")}
        sel = next((x for x in tickets if x["id"] == form["ticket"]), None)
        try:
            price = float(form["price"] or 0)
        except ValueError:
            price = 0
        if sel is None:
            error = "Choose the ticket you want to list."
        elif form["type"] == "sell" and price <= 0:
            error = "Enter your asking price."
        elif form["type"] == "sell" and CAP_AT_FACE_VALUE and sel["price"] > 0 and price > sel["price"]:
            error = f"You can't ask more than you paid ({money_short(sel['price'])})."
        elif form["type"] == "exchange" and not form["exchange"]:
            error = "Say what you want in exchange."
        if not error:
            t, d = sel["t"], sel["d"]
            store.add(data.SWAPS, {
                "ticket_id": sel["id"], "event_id": s(t.get("event_id")), "event_title": s(d.get("title"), "Event"),
                "event_date": to_date(d.get("event_date")), "event_venue": s(d.get("bizName")),
                "event_city": s(d.get("bizCity")), "event_image": s(d.get("image")), "poster_id": c.uid,
                "poster_name": public_name(c.user), "poster_avatar": avatar_color_from_uid(c.uid),
                "type": form["type"] if form["type"] in ("free", "sell", "exchange") else "free", "status": "open",
                "ask_price": price if form["type"] == "sell" else 0, "face_value": sel["price"],
                "exchange_for": form["exchange"] if form["type"] == "exchange" else "", "note": form["note"],
                "quantity": int(t.get("quantity") or 1), "listed_at": store.now(), "claimed_at": None,
                "claimed_by_uid": None, "claimed_by_name": None, "closed_at": None, "new_ticket_id": None,
            })
            messages.success(request, "Your ticket is listed.")
            return redirect("/swap/?v=mine")
    return render(request, "biyet/swap_new.html", {"tab": "swap", "tickets": tickets, "form": form, "error": error})


class SwapError(Exception):
    pass


REOPEN = {"status": "open", "claimed_by_uid": None, "claimed_by_name": None, "claimed_at": None}


def _swap_stats(uid, sold, swap, amount):
    now = store.now()
    k = f"{TIX_FIELD}.monthly.{month_key(now)}"
    side, low = ("Sold", "sold") if sold else ("Bought", "bought")
    qty = int(swap.get("quantity") or 1)
    upd = {
        f"{TIX_FIELD}.swap{side}Count": store.INC(qty), f"{TIX_FIELD}.swap{side}Amount": store.INC(amount),
        f"{k}.date": month_label(now), f"{k}.lastUpdated": store.SERVER_TS,
        f"{k}.swaps.{low}Count": store.INC(qty), f"{k}.swaps.{low}Amount": store.INC(amount),
        f"{k}.swaps.eventIds": store.ARRAY_UNION([s(swap.get("event_id"))]),
    }
    if swap.get("type"):
        upd[f"{k}.swaps.{swap['type']}Count"] = store.INC(1)
    upd[f"{k}.swaps.swapIds"] = store.ARRAY_UNION([swap["_id"]])
    return upd


@customer_required
@require_POST
def swap_action(request, swap_id):
    c = customer(request)
    action = request.POST.get("action")
    me = c.uid

    def run(tx):
        sw = tx.get(data.SWAPS, swap_id)
        if sw is None:
            raise SwapError("This listing no longer exists.")
        sw["_id"] = swap_id
        if action == "claim":
            if sw.get("status") != "open":
                raise SwapError("Someone else already asked for this ticket.")
            if sw.get("poster_id") == me:
                raise SwapError("You can't claim your own listing.")
            tx.update(data.SWAPS, swap_id, {"status": "pending", "claimed_by_uid": me,
                                            "claimed_by_name": public_name(c.user), "claimed_at": store.SERVER_TS})
            return f"Request sent. {s(sw.get('poster_name'), 'They')} will answer soon."
        if action == "withdraw":
            if sw.get("status") != "pending" or sw.get("claimed_by_uid") != me:
                raise SwapError("This request is no longer active.")
            tx.update(data.SWAPS, swap_id, REOPEN)
            return "Request withdrawn."
        if sw.get("poster_id") != me:
            raise SwapError("This isn't your listing.")
        if action == "decline":
            if sw.get("status") != "pending":
                raise SwapError("This request is no longer active.")
            tx.update(data.SWAPS, swap_id, REOPEN)
            return "Request declined. Your listing is open again."
        if action == "cancel":
            tx.update(data.SWAPS, swap_id, {"status": "cancelled"})
            return "Listing removed."
        if action == "accept":
            if sw.get("status") != "pending":
                raise SwapError("This request is no longer active.")
            claimer = s(sw.get("claimed_by_uid"))
            if not claimer:
                raise SwapError("This request is no longer active.")
            old = tx.get(data.TICKETS, s(sw.get("ticket_id")))
            if old is None or old.get("user_id") != me:
                raise SwapError("You no longer own this ticket.")
            if old.get("status") != "valid":
                raise SwapError("This ticket was already used or cancelled.")
            new_id = new_ticket_id()
            is_sell = sw.get("type") == "sell"
            price = float(sw.get("ask_price") or 0) if is_sell else 0
            tx.set(data.TICKETS, new_id, {
                **old, "ticket_id": new_id, "user_id": claimer, "user_name": s(sw.get("claimed_by_name")),
                "user_email": "", "status": "valid", "price": price, "purchase_date": store.now(), "order_id": "",
                "received_via_swap": True, "swap_id": swap_id, "transferred_from_ticket": s(sw.get("ticket_id")),
                "transferred_from_uid": me, "created_on": store.SERVER_TS,
            })
            tx.update(data.TICKETS, s(sw.get("ticket_id")), {"status": "transferred", "transferred_to_ticket": new_id,
                                                             "transferred_at": store.SERVER_TS})
            tx.update(data.SWAPS, swap_id, {"status": "closed", "new_ticket_id": new_id,
                                            "closed_at": store.SERVER_TS})
            tx.update(data.USERS, me, _swap_stats(me, True, sw, price))
            tx.update(data.USERS, claimer, _swap_stats(claimer, False, sw, price))
            return f"Ticket transferred to {s(sw.get('claimed_by_name'), 'the buyer')}."
        raise SwapError("Unknown action.")

    try:
        messages.success(request, store.transaction(run))
    except SwapError as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "/swap/")


# --------------------------------------------------------------------------- #
# Notifications (notifications_screen.dart + tix_notification.dart)
# --------------------------------------------------------------------------- #


def lang_code(pref):
    p = s(pref).strip().lower()
    if p.startswith("fr"):
        return "fr"
    if p.startswith(("kr", "cr")) or p == "ht":
        return "ht"
    if p.startswith(("es", "sp")):
        return "es"
    return "en"


def _pick(m, lang, base):
    keys = {"fr": "f" + base, "ht": "k" + base, "es": "s" + base}
    want = s(m.get(keys.get(lang, base))).strip()
    if want:
        return want
    for k in (base, "k" + base, "f" + base, "s" + base):
        if s(m.get(k)).strip():
            return s(m.get(k)).strip()
    return ""


def audience_keys(uid, user):
    def f(k):
        return s(user.get(k)).strip()

    keys = ["all", f"uid:{uid}"]
    if f("country"):
        keys.append(f"country:{f('country').upper()}")
    if f("userState"):
        keys.append(f"state:{f('userState')}")
    if f("userCity"):
        keys.append(f"city:{f('userCity')}")
    keys += [f"type:{t}" for t in sorted(favorite_types(user))]
    return list(dict.fromkeys(keys))[:30]


def notif_items(uid, user):
    now = dt.datetime.now(dt.timezone.utc)
    tix = user.get(TIX_FIELD) if isinstance(user.get(TIX_FIELD), dict) else {}
    read = tix.get("readNotifs") if isinstance(tix.get("readNotifs"), dict) else {}
    lang = lang_code(user.get("prefLang"))
    items = []
    try:
        anns = store.where(data.ANNOUNCEMENTS, [("audience_keys", "array_contains_any", audience_keys(uid, user)),
                                                ("expires_at", ">", now)], order_by="expires_at", descending=True,
                           limit=50)
    except Exception:  # noqa: BLE001 - missing index etc.
        anns = []
    for i, m in anns:
        if m.get("is_avail") is False:
            continue
        items.append({"id": i, "personal": False, "title": _pick(m, lang, "title"), "body": _pick(m, lang, "content"),
                      "image": s(m.get("image") or m.get("posImg")), "type": s(m.get("type"), "promo"),
                      "link": s(m.get("link_id")), "pinned": m.get("pinned") is True, "read": read.get(i) is True,
                      "created": to_date(m.get("created_on"), now)})
    try:
        pers = store.where(data.PERSONAL_NOTIFS, [("uid", "==", uid), ("expires_at", ">", now)],
                           order_by="expires_at", descending=True, limit=50)
    except Exception:  # noqa: BLE001
        pers = []
    for i, m in pers:
        items.append({"id": i, "personal": True, "title": _pick(m, lang, "title"), "body": _pick(m, lang, "content"),
                      "image": s(m.get("image")), "type": s(m.get("type"), "general"), "link": s(m.get("link_id")),
                      "pinned": False, "read": m.get("read") is True, "created": to_date(m.get("created_on"), now)})
    items.sort(key=lambda x: x["created"], reverse=True)
    items.sort(key=lambda x: not x["pinned"])
    return items


NOTIF_LINKS = {"swap": "/swap/", "ticket": "/history/", "refund": "/history/", "event": "/",
               "referral": "/rewards/", "coupon": "/rewards/?tab=rewards", "account": "/profile/"}
NOTIF_ICON = {"swap": "⇄", "ticket": "🎟️", "refund": "↩", "referral": "👥", "coupon": "🏷", "account": "✔",
              "event": "🎉", "promo": "📣", "bus": "🚌"}


@customer_required
def notifications(request):
    c = customer(request)
    items = notif_items(c.uid, c.user)
    flt = request.GET.get("f", "all")
    shown = [i for i in items if flt == "all" or (flt == "personal") == i["personal"]]
    today = timezone.localdate()
    for i in shown:
        d = local(i["created"]).date()
        i["group"] = "Pinned" if i["pinned"] else "Today" if d == today else \
            "This week" if d > today - dt.timedelta(days=7) else "Earlier"
        i["icon"] = NOTIF_ICON.get(i["type"], "🔔")
        i["href"] = NOTIF_LINKS.get(i["type"], "")
    return render(request, "biyet/notifications.html", {
        "tab": "menu", "items": shown, "f": flt, "unread": len([i for i in items if not i["read"]]),
        "count_all": len(items), "count_personal": len([i for i in items if i["personal"]]),
        "count_news": len([i for i in items if not i["personal"]]),
    })


@customer_required
@require_POST
def notif_read(request):
    c = customer(request)
    ids = request.POST.getlist("id")
    if request.POST.get("all"):
        items = [i for i in notif_items(c.uid, c.user) if not i["read"]]
    else:
        items = [i for i in notif_items(c.uid, c.user) if i["id"] in ids and not i["read"]]
    ops, user_upd = [], {}
    for i in items:
        if i["personal"]:
            ops.append(("update", data.PERSONAL_NOTIFS, i["id"], {"read": True}, False))
        else:
            user_upd[f"{TIX_FIELD}.readNotifs.{i['id']}"] = True
    if user_upd:
        ops.append(("update", data.USERS, c.uid, user_upd, False))
    if ops:
        store.batch(ops)
    nxt = request.POST.get("next", "")
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "notifications")


def unread_count(c):
    try:
        return len([i for i in notif_items(c.uid, c.user) if not i["read"]])
    except Exception:  # noqa: BLE001
        return 0


# --------------------------------------------------------------------------- #
# Rewards & referrals
# --------------------------------------------------------------------------- #


@customer_required
def rewards(request):
    c = customer(request)
    country = norm(s(c.user.get("country")))
    coupons = []
    for cid, m in store.where(data.COUPONS, [("active", "==", True)]):
        uids = [str(x) for x in (m.get("uids") or [])]
        if uids and c.uid not in uids:
            continue
        cc = norm(s(m.get("country")))
        if cc and cc != country:
            continue
        code = s(m.get("code"))
        if not code:
            continue
        exp = to_date(m.get("expires_on"))
        used = c.uid in (m.get("usedBy") or [])
        expired = exp is not None and exp < dt.datetime.now(dt.timezone.utc)
        v = float(m.get("value") or 0)
        vtxt = f"{v:.0f}" if v == round(v) else str(v)
        label = (f"{vtxt}{' ' + s(m.get('currency')) if s(m.get('currency')) else ''} OFF"
                 if s(m.get("discountType")) == "amount" else f"{vtxt}% OFF")
        coupons.append({"id": cid, "code": code, "title": s(m.get("title")) or label, "desc": s(m.get("description")),
                        "label": label, "expires": local(exp) if exp else None, "used": used, "expired": expired,
                        "usable": not used and not expired})
    coupons.sort(key=lambda x: (not x["usable"], x["expires"] or dt.datetime.max.replace(tzinfo=dt.timezone.utc)))
    pts = c.user.get("user_total_points") or c.user.get("totalPoints") or c.user.get("points") or 0
    try:
        pts = int(float(pts))
    except (TypeError, ValueError):
        pts = 0
    code = s(c.user.get("greeter_code")).strip()
    invite = ("Join me on Biyet Event and get your tickets for the best parties, concerts and festivals!\n\n"
              + (f"Use my code {code} when you sign up.\n\n" if code else "")
              + f"Android: {ANDROID_STORE_URL}\niPhone: {IOS_STORE_URL}\nWeb: {request.build_absolute_uri('/signup/')}")
    return render(request, "biyet/rewards.html", {
        "tab": "menu", "sub": request.GET.get("tab", "referral"), "coupons": coupons, "points": pts,
        "code": code, "invite": invite,
    })


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


@customer_required
def settings_view(request):
    c = customer(request)
    types = c.event_types()
    fav = favorite_types(c.user)
    if request.method == "POST":
        if "fav" in request.POST:
            t = request.POST["fav"]
            fav = fav ^ {t} if t in types else fav
            values = {"favoriteEventTypes": sorted(fav)}
        else:
            values = {"notificationsEnabled": request.POST.get("notifications") == "on"}
        store.set(data.USERS, c.uid, {TIX_FIELD: {**values, "settingsUpdated_on": store.SERVER_TS}}, merge=True)
        return redirect("settings")
    return render(request, "biyet/settings.html", {
        "tab": "menu", "types": [{"key": t, "label": t[:1].upper() + t[1:], "on": t in fav} for t in types],
        "notifications": notifications_enabled(c.user),
    })


@customer_required(allow_incomplete=True)
@require_POST
def device_token(request):
    """Saves this browser's FCM token, like TixProfile.fcmToken() + ensure()
    and the Settings switch do in the app.

    tixUserData.fcmToken  = this device's token (the latest device wins, the
                            same as when the phone app refreshes it)
    tixUserData.fcmTokens = every device's token, so a backend can send to
                            phone AND desktop instead of only the last one
    """
    c = customer(request)
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False}, status=400)
    token = s(payload.get("token")).strip()
    # FCM tokens are long base64url-ish strings; reject anything else.
    if not (100 <= len(token) <= 4096) or any(ch.isspace() for ch in token):
        return JsonResponse({"ok": False, "error": "Invalid token"}, status=400)
    updates = {
        f"{TIX_FIELD}.fcmToken": token,
        f"{TIX_FIELD}.fcmTokens": store.ARRAY_UNION([token]),
        f"{TIX_FIELD}.fcmPlatform": "web",
        f"{TIX_FIELD}.fcmUpdated_on": store.SERVER_TS,
    }
    if payload.get("enable"):  # turned on from Settings → also flip the setting
        updates[f"{TIX_FIELD}.notificationsEnabled"] = True
        updates[f"{TIX_FIELD}.settingsUpdated_on"] = store.SERVER_TS
    store.update(data.USERS, c.uid, updates)
    return JsonResponse({"ok": True})


# --------------------------------------------------------------------------- #
# Profile (tix_profile.dart)
# --------------------------------------------------------------------------- #

_HT = None


def ht_states():
    global _HT
    if _HT is None:
        from pathlib import Path

        _HT = json.loads((Path(__file__).parent / "ht_state.json").read_text(encoding="utf-8"))
    return _HT


def _upload_id_pic(uid, f):
    path = f"tix_ids/{uid}/{int(dt.datetime.now().timestamp() * 1000)}.jpg"
    if settings.FIRESTORE_FAKE:
        return f"https://example.invalid/{path}"
    from urllib.parse import quote

    from firebase_admin import storage

    bucket = storage.bucket(settings.FIREBASE_WEB_CONFIG["storageBucket"], app=firebase.get_app())
    blob = bucket.blob(path)
    token = str(uuid.uuid4())
    blob.metadata = {"firebaseStorageDownloadTokens": token}
    blob.upload_from_file(f, content_type=f.content_type or "image/jpeg")
    return f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{quote(path, safe='')}?alt=media&token={token}"


@customer_required(allow_incomplete=True)
def profile(request):
    c = customer(request)
    user, tix = c.user, dict(c.tix)
    usa, haiti = is_usa(user.get("country")), is_haiti(user.get("country"))
    dob = to_date(tix.get("DOB"))
    form = {
        "username": s(tix.get("username")), "phoneNumber": s(tix.get("phoneNumber")),
        "DOB": local(dob).date().isoformat() if dob else "", "idType": s(tix.get("idType")),
        "street": s(user.get("userStreet1")), "city": s(user.get("userCity")), "state": s(user.get("userState")),
        "zip": s(user.get("userZip")), "arr": s(user.get("uArr")),
    }
    errors = {}
    if request.method == "POST":
        form = {k: request.POST.get(k, "").strip() for k in form}
        if len(form["username"]) < 3:
            errors["username"] = "At least 3 characters"
        if len("".join(ch for ch in form["phoneNumber"] if ch.isdigit())) < 7:
            errors["phoneNumber"] = "Enter a valid phone number"
        try:
            dob_d = dt.date.fromisoformat(form["DOB"])
        except ValueError:
            dob_d = None
            errors["DOB"] = "Select your date of birth"
        if form["idType"] not in ID_TYPES:
            errors["idType"] = "Choose an ID type"
        pic = request.FILES.get("idPic")
        if not pic and not s(tix.get("idPic")):
            errors["idPic"] = "Add a photo of your ID"
        if haiti:
            dept = next((d for d in ht_states() if d["name"] == form["state"]), None)
            if not dept:
                errors["state"] = "Choose a department"
            elif form["arr"] not in dept["arrs"]:
                errors["arr"] = "Choose an arrondissement"
            elif form["city"] not in dept["arrs"][form["arr"]]:
                errors["city"] = "Choose a commune"
            address = {"userState": form["state"], "uArr": form["arr"], "userCity": form["city"], "userZip": ""}
        else:
            if not form["city"]:
                errors["city"] = "City is required"
            if not form["state"]:
                errors["state"] = "State is required"
            if usa and not (form["zip"].isdigit() and len(form["zip"]) == 5):
                errors["zip"] = "5 digits"
            if usa and not form["street"]:
                errors["street"] = "Enter your address"
            address = {"userCity": title_case(form["city"]), "userState": title_case(form["state"])}
            if usa:
                address.update({"userStreet1": form["street"], "userZip": form["zip"]})
        if not errors:
            id_pic = _upload_id_pic(c.uid, pic) if pic else s(tix.get("idPic"))
            age = age_on(dob_d)
            nxt = {"username": form["username"], "phoneNumber": form["phoneNumber"],
                   "DOB": dt.datetime(dob_d.year, dob_d.month, dob_d.day, tzinfo=dt.timezone.utc),
                   "idType": form["idType"], "idPic": id_pic, "age": age, "over21": age >= 21}

            def same(k, a, b):
                if k == "DOB":
                    da, db = to_date(a), to_date(b)
                    return (da.date() if da else None) == (db.date() if db else None)
                return s(a) == s(b)

            changes = {k: {"from": tix.get(k), "to": v} for k, v in nxt.items() if not same(k, tix.get(k), v)}
            changes.update({k: {"from": user.get(k), "to": v} for k, v in address.items() if not same(k, user.get(k), v)})
            if not changes:
                messages.info(request, "Nothing changed.")
            else:
                store.batch([
                    ("set", data.USERS, c.uid, {TIX_FIELD: {**nxt, "adminVerified": False,
                                                            "updated_on": store.SERVER_TS}, **address}, True),
                    ("set", f"{data.USERS}/{c.uid}/{data.PROFILE_HISTORY}", store.new_id(), {
                        "uid": c.uid, "changedFields": list(changes), "changes": changes,
                        "wasAdminVerified": tix.get("adminVerified") is True, "platform": "web",
                        "ip": s(tix.get("currentIP")), "updated_on": store.SERVER_TS}, False),
                ])
                messages.success(request, "Profile saved.")
                return redirect("events")
    missing = missing_profile(c.tix) + missing_address(user)
    return render(request, "biyet/profile.html", {
        "tab": "menu", "form": form, "errors": errors, "id_types": ID_TYPES, "usa": usa, "haiti": haiti,
        "us_states": US_STATES, "ht": ht_states(), "ht_json": json.dumps(ht_states()),
        "verified": tix.get("adminVerified") is True, "id_pic": s(tix.get("idPic")), "missing": missing,
        "blocking": bool(missing), "age": age_on(local(dob).date()) if dob else None,
    })


# --------------------------------------------------------------------------- #
# Menu (drawer)
# --------------------------------------------------------------------------- #


@customer_required
def menu(request):
    c = customer(request)
    u = c.user
    bal = u.get("user_balance") or u.get("wallet_balance") or u.get("balance") or 0
    try:
        bal = float(bal)
    except (TypeError, ValueError):
        bal = 0.0
    rates, _ = c.rates_and_fee()
    cur = rates.currency_of_country(c.country) or "USD"
    pts = u.get("user_total_points") or 0
    try:
        pts = int(float(pts))
    except (TypeError, ValueError):
        pts = 0
    created = to_date(u.get("created_on") or u.get("memberSince"))
    dob = to_date(c.tix.get("DOB"))
    age = age_on(local(dob).date()) if dob else c.tix.get("age")
    types, in_city = data.transport_for(u)
    from .domain import format_money
    return render(request, "biyet/menu.html", {
        "tab": "menu", "name": f"@{c.username}" if c.username else (c.full_name or "No name set"),
        "handle": "  ·  ".join(x for x in [("" if c.username else s(u.get("email")).split("@")[0]),
                                          f"{age} yrs" if age else "",
                                          f"Member since {created.year}" if created else ""] if x),
        "initials": c.initials, "balance": format_money(bal, cur), "points": f"{pts:,}",
        "upcoming": [{**t, "local": local(t["date"])} for t in data.upcoming_tickets(c.uid)],
        "unread": unread_count(c), "transport": types, "transport_city": s(u.get("userCity")) if in_city else "",
    })


# --------------------------------------------------------------------------- #
# Report a problem (report.dart)
# --------------------------------------------------------------------------- #


@customer_required
@require_POST
def report_event(request, event_id):
    c = customer(request)
    e = data.get_event(event_id)
    if e is None:
        raise Http404
    reason = next((r for r in REPORT_REASONS if r[0] == request.POST.get("reason")), None)
    details = request.POST.get("details", "").strip()
    nxt = reverse("event_detail", args=[event_id])
    if reason is None:
        messages.error(request, "Pick a reason.")
    elif reason[0] == "other" and not details:
        messages.error(request, "Please tell us what happened.")
    elif len(details) > 1000:
        messages.error(request, "Please keep it under 1,000 characters.")
    else:
        d = local(e.event_date)
        store.set("reports", f"{c.uid}_event_{e.id}", {
            "reporter_id": c.uid, "reporter_email": s(request.user.email), "reporter_name": c.full_name,
            "target_type": "event", "target_id": e.id, "target_name": e.title, "owner_id": e.biz_id,
            "owner_name": e.event_promoter or e.biz_name,
            "context": " · ".join(x for x in (f"{d.month}/{d.day}/{d.year}", e.biz_name, e.biz_city) if x),
            "reason": reason[0], "reason_label": reason[1], "details": details, "status": "open",
            "action_taken": "", "admin_note": "", "created_on": store.SERVER_TS,
        })
        messages.success(request, "Thanks. Our team will review your report.")
    return redirect(nxt)


@customer_required(allow_incomplete=True)
@require_POST
def delete_account(request):
    c = customer(request)
    store.set(data.USERS, c.uid, {"be_req_del": True}, merge=True)
    logout(request)
    return redirect("/login/?e=deleted")


# --------------------------------------------------------------------------- #
# Auth: Firebase sign-in, web sign-up, 6-digit code
# --------------------------------------------------------------------------- #

LOGIN_ERRORS = {
    "no_customer": "This sign-in isn't a customer account. Promoters and bus companies use the app.",
    "deleted": "Your account is being deleted. Contact support if this is a mistake.",
    "blocked": "This account has been blocked. Contact support.",
}


def login_view(request):
    if request.user.is_authenticated and not request.GET.get("e"):
        return redirect(request.GET.get("next") or "events")
    return render(request, "login.html", {"next": request.GET.get("next", ""), "mode": "signin",
                                          "error": LOGIN_ERRORS.get(request.GET.get("e", "")),
                                          "demo": settings.FIRESTORE_FAKE})


def signup_view(request):
    countries = []
    dev = data.developer_doc()
    for p in dev.get("clientRefPromo") or []:
        if isinstance(p, dict) and s(p.get("country")).strip():
            c = s(p.get("country")).strip().upper()
            if c not in countries:
                countries.append(c)
    countries = countries or ["USA", "HAITI", "CANADA"]
    names = {"USA": "🇺🇸  United States", "HAITI": "🇭🇹  Haiti", "CANADA": "🇨🇦  Canada"}
    return render(request, "login.html", {"mode": "signup", "next": "", "languages": LANGUAGES,
                                          "countries": [(c, names.get(c, c)) for c in countries],
                                          "demo": settings.FIRESTORE_FAKE})


def _session_from_token(request, payload):
    token = payload.get("idToken")
    if not token:
        return None, JsonResponse({"ok": False, "error": "Missing idToken"}, status=400)
    try:
        user = authenticate(request, firebase_id_token=token)
    except firebase.FirebaseNotConfigured as exc:
        return None, JsonResponse({"ok": False, "error": str(exc)}, status=503)
    if user is None:
        return None, JsonResponse({"ok": False, "error": "Invalid or expired token"}, status=401)
    login(request, user, backend="events.auth_backend.FirebaseBackend")
    request.session["tix_ensured"] = False
    return user, None


@require_POST
def firebase_session(request):
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    user, err = _session_from_token(request, payload)
    if err:
        return err
    nxt = payload.get("next") or ""
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = reverse("events")
    return JsonResponse({"ok": True, "redirect": nxt})


def _developer_ref():
    for name in ("developer", "developper"):
        rows = store.where(name, limit=1)
        if rows:
            return name, rows[0][0], rows[0][1]
    return None, None, {}


def _new_user_balance(dev, country):
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    for p in dev.get("clientRefPromo") or []:
        if isinstance(p, dict) and country and s(p.get("country")).strip().upper() == country.upper():
            return num((p.get("info") or {}).get("amount"))
    return num(dev.get("new_user_bal"))


def _send_code_mail(email, name, code):
    store.add("mail", {"to": [email], "message": {
        "subject": f"Your Biyet Event verification code: {code}",
        "text": f"Hi{' ' + name if name else ''},\n\nYour Biyet Event verification code is {code}.\n"
                "Enter it to activate your account.\n\nIf you did not ask for this, ignore this email.",
        "html": '<div style="font-family:Arial,sans-serif;background:#000;color:#fff;padding:24px;border-radius:12px">'
                '<h2 style="margin:0 0 4px">Biyet <span style="color:#FF6A1A">Event</span></h2>'
                f'<p>Hi{" " + name if name else ""},</p><p>Your verification code is:</p>'
                f'<p style="font-size:32px;font-weight:bold;letter-spacing:8px;color:#FF6A1A">{code}</p></div>'},
        "created_on": store.SERVER_TS})


LANGUAGES = ["English", "Kreyòl", "Français", "Español"]


def user_profile_defaults(uid, now):
    """Every field UserProfile (the shared users model) reads, with a safe
    default, so accounts made in Biyet open fine in the other app too.
    Never stores the password (upass stays empty; Firebase Auth holds it)."""
    return {
        "id": uid, "username": "", "userImage": "",
        "userStreet1": "", "userStreet2": "", "userCity": "", "userState": "", "userZip": "", "uArr": "",
        "user_total_order": 0, "user_total_tips": 0, "user_total_driver_tips": 0,
        "user_total_resto_tips": 0, "user_total_donations": 0,
        "userStatus": "customer", "user_role": "normal", "fcmToken": "",
        "staff_send_text": False, "send_text_date": now, "user_order": False, "user_order_date": now,
        "user_adds": [], "user_credit": 0.0, "upass": "", "newem": False,
        "has_credit_line": False, "credit_line_amount": 0.0, "money_owed": 0.0, "first_order": True,
        "usr_circle": [], "sent_req": [], "rec_req": [], "secureCart": False,
        "vendorId": "", "isVendor": False, "vendorReq": False, "vendorApproved": False,
    }


@require_POST
def signup_create(request):
    """signup_screen.dart (customer): the browser has already created the
    Firebase Auth account; this writes users/{uid} exactly like the app."""
    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)
    user, err = _session_from_token(request, payload)
    if err:
        return err
    uid = user.username
    if data.get_user(uid):
        return JsonResponse({"ok": True, "redirect": reverse("events")})
    first, last = s(payload.get("firstName")).strip(), s(payload.get("lastName")).strip()
    country = s(payload.get("country")).strip().upper()
    referral = s(payload.get("referral")).strip()
    if not first or not last or not country:
        return JsonResponse({"ok": False, "error": "Fill in your name and country."}, status=400)
    if referral and not store.where(data.USERS, [("greeter_code", "==", referral)], limit=1):
        return JsonResponse({"ok": False, "error": "That referral code doesn't exist. Check it, or leave it empty.",
                             "bad_referral": True}, status=400)

    col, dev_id, dev = _developer_ref()
    greeter = ""
    if dev_id:
        def nxt(tx):
            d = tx.get(col, dev_id) or {}
            try:
                n = int(str(d.get("greet_num") or "0")) + 1
            except ValueError:
                n = 1
            code = str(n).zfill(4)
            tx.update(col, dev_id, {"greet_num": code})
            return code
        try:
            greeter = store.transaction(nxt)
        except Exception:  # noqa: BLE001
            greeter = ""
    code = str(100000 + secrets.randbelow(900000))
    email = user.email
    pref_lang = s(payload.get("prefLang")).strip() or ("Kreyòl" if country == "HAITI" else "English")
    if pref_lang not in LANGUAGES:
        pref_lang = "English"
    now = store.now()
    store.set(data.USERS, uid, {
        **user_profile_defaults(uid, now),
        "uid": uid, "firstName": first, "lastName": last, "email": email, "u_status": "normal",
        "account_code": code, "true_account": False, "admin_verified": False, "greeter_code": greeter,
        "rec_code": referral or "e", "greeter_counts": 0, "mySignUps": [], "is_blocked": False, "rq_del": False,
        "user_balance": float(_new_user_balance(dev, country)), "user_total_points": 0, "created_on": now,
        "country": country, "city": "", "state": "", "user_phone": "", "prefLang": pref_lang,
        # Names the dashboard, profile gate and delete flow read (see README):
        "phoneNumber": "", "be_req_del": False,
    })
    _send_code_mail(email, first, code)
    return JsonResponse({"ok": True, "redirect": reverse("verify_code")})


@login_required
def verify_code(request):
    """code_page.dart: 6-digit account code, then the referral reward."""
    uid = request.user.username
    u = data.get_user(uid)
    if not u:
        logout(request)
        return redirect("/login/?e=no_customer")
    if u.get("true_account") is not False:
        return redirect("events")
    email = s(u.get("email") or request.user.email)
    at = email.find("@")
    masked = (email[:2] + "•" * max(3, at - 2) + email[at:]) if at > 2 else email
    error = None
    if request.method == "POST":
        if request.POST.get("resend"):
            _send_code_mail(email, s(u.get("firstName")), s(u.get("account_code")))
            messages.info(request, f"Code sent again to {masked}")
            return redirect("verify_code")
        code = "".join(ch for ch in request.POST.get("code", "") if ch.isdigit())
        if len(code) != 6:
            error = "Please enter the full 6-digit code."
        elif s(u.get("account_code")).strip() != code:
            error = "That code doesn't match. Check your email and try again."
        else:
            greeter = None
            rec = s(u.get("rec_code")).strip()
            if rec and rec != "e":
                rows = store.where(data.USERS, [("greeter_code", "==", rec)], limit=1)
                if rows and rows[0][0] != uid:
                    greeter = rows[0]
            sign_ups = list(u.get("mySignUps") or [])
            reward = greeter is not None and greeter[0] not in sign_ups
            if reward:
                sign_ups.append(greeter[0])
            store.update(data.USERS, uid, {"true_account": True, "mySignUps": sign_ups})
            if reward:
                _, _, dev = _developer_ref()
                country = s(u.get("country")).strip() or s(greeter[1].get("country")).strip()
                amt = 0.0
                for p in dev.get("clientRefPromo") or []:
                    if isinstance(p, dict) and s(p.get("country")).strip().lower() == country.lower():
                        try:
                            amt = float((p.get("info") or {}).get("amount") or 0) * 0.2
                        except (TypeError, ValueError):
                            amt = 0.0
                try:
                    store.update(data.USERS, greeter[0], {"greeter_counts": store.INC(1),
                                                          "user_balance": store.INC(round(amt, 2))})
                except Exception:  # noqa: BLE001
                    pass
            messages.success(request, "Account verified. Welcome to Biyet Event!")
            return redirect("profile")
    return render(request, "biyet/verify.html", {"masked": masked, "error": error})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


def demo_login(request):
    """Local demo mode only (FIRESTORE_FAKE): sign in as the seeded customer."""
    if not settings.FIRESTORE_FAKE:
        raise Http404
    from django.contrib.auth import get_user_model

    u, _ = get_user_model().objects.get_or_create(username="demo-uid", defaults={"email": "demo@biyet.app"})
    login(request, u, backend="django.contrib.auth.backends.ModelBackend")
    request.session["tix_ensured"] = False
    return redirect("events")


# --------------------------------------------------------------------------- #
# PWA
# --------------------------------------------------------------------------- #


@require_GET
def manifest(request):
    body = {
        "name": "Biyet Event — Kote plezi a ye", "short_name": "Biyet Event",
        "description": "Kote plezi a ye. Find events, grab tickets, swap seats.",
        "start_url": "/?source=pwa", "scope": "/", "display": "standalone", "orientation": "portrait",
        "background_color": "#0f0d1a", "theme_color": "#ff5a36",
        "icons": [
            {"src": BRAND_ICON_URL, "sizes": "any", "type": "image/png", "purpose": "any"},
            {"src": static("icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": static("icons/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
        "shortcuts": [{"name": "My tickets", "url": "/history/"}, {"name": "Swap", "url": "/swap/"}],
        # Android Chrome: offer the real app from Google Play instead of the PWA.
        "related_applications": [{"platform": "play", "id": "com.kenutek.giche", "url": ANDROID_STORE_URL}],
        "prefer_related_applications": True,
    }
    return HttpResponse(json.dumps(body), content_type="application/manifest+json")


@require_GET
@never_cache
def service_worker(request):
    js = (settings.BASE_DIR / "static" / "js" / "sw.js").read_text()
    resp = HttpResponse(js.replace("__STATIC_URL__", "/" + settings.STATIC_URL.strip("/") + "/"),
                        content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    return resp


def favicon(request):
    """/favicon.ico: browsers, Google and link previews ask for this path
    directly, even before reading the page."""
    from django.http import HttpResponseRedirect

    resp = HttpResponseRedirect(BRAND_ICON_URL)
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


def offline(request):
    return render(request, "offline.html")


_ = (HAITI_STATES, type_label)