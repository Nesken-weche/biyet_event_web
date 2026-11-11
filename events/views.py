"""Events, Discover, event detail, cart/checkout, History and ticket screens."""
from __future__ import annotations

import datetime as dt
import json
import time

import qrcode
import qrcode.image.svg
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import checkout, data, store
from .context import customer, customer_required, invalidate_events
from .domain import (
    BuyerSnapshot,
    KEvent,
    favorite_types,
    format_money,
    s,
    to_date,
)
from .pricing import approx, build_summary, max_for

TYPE_LABELS = {"festival": "Festivals", "club": "Clubs", "lounge": "Lounges", "concert": "Concerts",
               "party": "Parties", "comedy": "Comedy", "sports": "Sports"}
TYPE_EMOJI = {"festival": "🎪", "club": "🪩", "lounge": "🍸", "concert": "🎵", "party": "🎉",
              "comedy": "🎭", "sports": "⚽"}
SEARCH_FIELDS = [("all", "All"), ("title", "Event"), ("venue", "Venue"), ("city", "City"), ("promoter", "Promoter")]


def type_label(t):
    t = (t or "").strip().lower()
    if t in ("", "other", "__other__"):
        return "Other"
    return TYPE_LABELS.get(t, t[:1].upper() + t[1:])


def local(d):
    return timezone.localtime(d) if d else None


def group_label(d):
    today = timezone.localdate()
    diff = (local(d).date() - today).days
    if diff < 0:
        return "Past events"
    if diff == 0:
        return "Today"
    if diff == 1:
        return "Tomorrow"
    if diff < 7:
        return "This week"
    return local(d).strftime("%B") if local(d).year == today.year else local(d).strftime("%B %Y")


def is_past_day(d):
    return local(d).date() < timezone.localdate()


# --------------------------------------------------------------------------- #
# Cart helpers (session)
# --------------------------------------------------------------------------- #


def cart_items(request):
    items = request.session.get("cart", [])
    for i in items:
        i["key"] = f"{i['event_id']}_{i['tier_id']}"
    return items


def save_cart(request, items):
    request.session["cart"] = [{"event_id": i["event_id"], "tier_id": i["tier_id"], "qty": i["qty"]} for i in items]


def in_cart(items, event_id):
    return sum(i["qty"] for i in items if i["event_id"] == event_id)


def prioritized(events, user):
    fav = favorite_types(user)
    if not fav:
        return events
    fav_list = [e for e in events if e.e_type.strip().lower() in fav]
    return fav_list + [e for e in events if e.e_type.strip().lower() not in fav]


def card_ctx(e: KEvent, items):
    """What an event card needs."""
    past = is_past_day(e.event_date)
    n = in_cart(items, e.id)
    if past:
        cta = "Ended"
    elif e.is_sold_out:
        cta = "Sold out"
    elif n:
        cta = f"In cart · {n}"
    elif e.can_get_tix:
        cta = "Get tickets"
    else:
        cta = "RSVP"
    return {
        "e": e, "past": past, "in_cart": n, "cta": cta, "date": local(e.event_date),
        "disabled": past or e.is_sold_out, "note": None if past else e.availability_note,
        "image": e.first_image_url, "emoji": TYPE_EMOJI.get(e.e_type.strip().lower(), "🎟️"),
        "type_label": e.e_type.strip().capitalize(), "media_count": len(e.media),
        "has_video": any(m["is_video"] for m in e.media),
    }


# --------------------------------------------------------------------------- #
# Events tab
# --------------------------------------------------------------------------- #


@customer_required
def events_tab(request):
    c = customer(request)
    items = cart_items(request)
    q = request.GET.get("q", "").strip()
    field = request.GET.get("field", "all")
    cat = request.GET.get("cat", "all")

    visible = [e for e in c.events() if e.is_visible]
    searched = prioritized(data.search_events(visible, q, field), c.user)

    types = c.event_types() or sorted({e.e_type.strip().lower() for e in visible if e.e_type.strip()})
    known = set(types)

    def by_cat(evs, k):
        if k == "all":
            return evs
        if k == "__other__":
            return [e for e in evs if e.e_type.strip().lower() not in known]
        return [e for e in evs if e.e_type.strip().lower() == k]

    cats = ["all", *types] + ([] if "other" in types else ["__other__"])
    chips = [{"key": k, "label": "All" if k == "all" else type_label(k), "count": len(by_cat(searched, k)),
              "on": k == cat} for k in cats]
    chips = [ch for ch in chips if ch["key"] == "all" or ch["on"] or ch["count"]]

    shown = by_cat(searched, cat)
    upcoming = sorted([e for e in shown if not is_past_day(e.event_date)], key=lambda e: e.event_date)
    past = sorted([e for e in shown if is_past_day(e.event_date)], key=lambda e: e.event_date, reverse=True)
    hot = [card_ctx(e, items) for e in upcoming if e.hot_event] if cat == "all" and len(upcoming) > 3 else []
    groups = {}
    for e in upcoming + past:
        groups.setdefault(group_label(e.event_date), []).append(card_ctx(e, items))

    return render(request, "biyet/events.html", {
        "tab": "events", "q": q, "field": field, "fields": SEARCH_FIELDS, "cat": cat, "chips": chips,
        "hot": hot, "groups": groups, "no_events": not visible, "searching": bool(q),
        "result_count": len(shown), "city": _city(c.user),
    })


def _city(user):
    city = s(user.get("userCity") or user.get("bizCity"))
    state = s(user.get("userState") or user.get("bizState"))
    return f"{city}, {state}" if city and state else city


# --------------------------------------------------------------------------- #
# Discover
# --------------------------------------------------------------------------- #


@customer_required
def discover_tab(request):
    c = customer(request)
    items = cart_items(request)
    rates, _ = c.rates_and_fee()
    types = c.event_types()
    f_types = [t for t in request.GET.getlist("type") if t]
    f_from = request.GET.get("from", "")
    f_sort = request.GET.get("sort", "")
    filtering = bool(f_types or f_from or f_sort)

    upcoming = sorted([e for e in c.events() if e.is_visible and not is_past_day(e.event_date)],
                      key=lambda e: e.event_date)

    def price_usd(e):
        p = e.lowest_price
        return 0 if p <= 0 else (rates.convert(p, e.currency, "USD") or p)

    if filtering:
        res = upcoming
        if f_types:
            res = [e for e in res if e.e_type.strip().lower() in f_types]
        if f_from:
            try:
                start = dt.date.fromisoformat(f_from)
                res = [e for e in res if local(e.event_date).date() >= start]
            except ValueError:
                pass
        if f_sort == "price_low":
            res = sorted(res, key=lambda e: (price_usd(e), e.event_date))
        elif f_sort == "price_high":
            res = sorted(res, key=lambda e: (-price_usd(e), e.event_date))
        return render(request, "biyet/discover.html", {
            "tab": "discover", "filtering": True, "results": [card_ctx(e, items) for e in res],
            "types": [(t, type_label(t)) for t in types], "f_types": f_types, "f_from": f_from, "f_sort": f_sort,
        })

    ctx = {"tab": "discover", "filtering": False, "types": [(t, type_label(t)) for t in types],
           "f_types": [], "f_from": "", "f_sort": "", "place": _city(c.user).split(",")[0]}
    if upcoming:
        today = timezone.localdate()
        wd = today.isoweekday()
        start = today if wd >= 5 else today + dt.timedelta(days=5 - wd)
        end = today + dt.timedelta(days=7 - wd + 1)
        hot = [e for e in upcoming if e.hot_event]
        top = hot[0] if hot else max(upcoming, key=lambda e: e.going_count)
        rest = [e for e in upcoming if e.id != top.id]
        purchased = {str(i.get("eventId")) for _, o in data.user_orders(c.uid) for i in (o.get("items") or [])
                     if isinstance(i, dict)}
        fav = favorite_types(c.user)
        bought_types = {e.e_type.strip().lower() for e in upcoming if e.id in purchased}
        for_you = [e for e in rest if e.id not in purchased and e.e_type.strip().lower() in (fav | bought_types)]
        known = set(types)
        vibes = {}
        for e in upcoming:
            t = e.e_type.strip().lower() or "other"
            if known and t not in known:
                t = "other"
            vibes.setdefault(t, []).append(e)
        order = [t for t in types if t in vibes] + (["other"] if "other" in vibes and "other" not in known else [])
        if not known:
            order = sorted(vibes, key=lambda t: -len(vibes[t]))
        ctx.update({
            "top": card_ctx(top, items),
            "for_you": [card_ctx(e, items) for e in for_you[:8]],
            "weekend": [card_ctx(e, items) for e in upcoming
                        if start <= local(e.event_date).date() < end and e.id != top.id],
            "trending": [card_ctx(e, items) for e in sorted([e for e in rest if e.going_count > 0],
                                                           key=lambda e: -e.going_count)[:8]],
            "free": [card_ctx(e, items) for e in rest if e.tix_price == 0][:8],
            "vibes": [{"key": t, "label": type_label(t), "count": len(vibes[t]),
                       "emoji": TYPE_EMOJI.get(t, "🎟️")} for t in order],
        })
    return render(request, "biyet/discover.html", ctx)


# --------------------------------------------------------------------------- #
# Event detail + add to cart
# --------------------------------------------------------------------------- #


@customer_required
def event_detail(request, event_id):
    c = customer(request)
    e = data.get_event(event_id)
    if e is None or not e.is_visible or (e.country and e.country != c.country):
        raise Http404
    items = cart_items(request)
    rates, _ = c.rates_and_fee()
    tiers = [{"t": t, "price": e.tier_price(t), "left": e.tier_left(t), "sold_out": e.tier_sold_out(t)}
             for t in e.tiers]
    from .domain import REPORT_REASONS
    return render(request, "biyet/event_detail.html", {
        "tab": "events", "card": card_ctx(e, items), "e": e, "tiers": tiers, "date": local(e.event_date),
        "place": ", ".join(x for x in (e.biz_city, e.biz_state) if x), "reasons": REPORT_REASONS,
        "approx_price": None if rates.event_currency(e) == (rates.currency_of_country(c.country) or "USD")
        else rates.convert(e.lowest_price, e.currency, rates.currency_of_country(c.country) or "USD", e.country),
    })


@customer_required
@require_POST
def add_to_cart(request, event_id):
    """customer_dashboard._addToCart."""
    e = data.get_event(event_id)
    if e is None:
        raise Http404
    items = cart_items(request)
    wanted = request.POST.get("tier_id")
    existing = [i for i in items if i["event_id"] == e.id]
    if wanted:
        tier = e.tier_by_id(wanted)
    elif existing:
        tier = e.tier_by_id(existing[0]["tier_id"])
    else:
        tier = next((t for t in e.tiers if not e.tier_sold_out(t)), None)
    nxt = request.POST.get("next") or "cart"

    if e.is_sold_out or tier is None or e.tier_sold_out(tier):
        messages.error(request, f"{e.title} is sold out")
        return redirect(nxt)
    key = f"{e.id}_{tier.tier_id}"
    line = next((i for i in items if i["key"] == key), None)
    have = line["qty"] if line else 0
    if have >= max_for(e, tier, items, except_key=key):
        if have and have >= tier.max_per_order:
            messages.error(request, f"You can buy up to {tier.max_per_order} {tier.name} "
                                    f"{'ticket' if tier.max_per_order == 1 else 'tickets'} per order")
        else:
            messages.error(request, f"No more tickets available for {e.title}")
        return redirect(nxt)
    if line:
        line["qty"] += 1
    else:
        items.append({"event_id": e.id, "tier_id": tier.tier_id, "qty": 1, "key": key})
    save_cart(request, items)
    messages.success(request, f"{e.title} added to cart")
    return redirect(nxt)


# --------------------------------------------------------------------------- #
# Cart
# --------------------------------------------------------------------------- #


def _cart_summary(request, c, method=None):
    items = cart_items(request)
    events = {i["event_id"]: data.get_event(i["event_id"]) for i in items}
    events = {k: v for k, v in events.items() if v}
    # Drop lines whose event or ticket type is gone (CartTab._refreshCart).
    keep = []
    for i in items:
        ev = events.get(i["event_id"])
        if ev and ev.tier_by_id(i["tier_id"]):
            keep.append(i)
        elif ev and i["tier_id"] == "general" and ev.has_tiers:
            i["tier_id"] = ev.tiers[0].tier_id
            i["key"] = f"{ev.id}_{i['tier_id']}"
            keep.append(i)
    if len(keep) != len(items):
        save_cart(request, keep)
    rates, fee = c.rates_and_fee()
    return build_summary(keep, events, c.user, rates, fee, method or request.session.get("pay_method")), rates, events


@customer_required
def cart(request):
    c = customer(request)
    sm, rates, _ = _cart_summary(request, c)
    lines = []
    for ln in sm.lines:
        net = ln.net_cents / 100
        lines.append({
            "ln": ln, "date": local(ln.event.event_date), "image": ln.event.first_image_url,
            "emoji": TYPE_EMOJI.get(ln.event.e_type.strip().lower(), "🎟️"),
            "price": format_money(ln.tier.price, ln.currency) if ln.tier.price != round(ln.tier.price)
            else ln.event.money(ln.tier.price),
            "approx": approx(rates, ln, ln.tier.price, sm.user_currency),
            "net": ln.event.money(net), "net_approx": approx(rates, ln, net, sm.user_currency),
            "gross": ln.event.money(ln.tier.price * ln.qty),
            "disc_total": ln.event.money(ln.disc.total) if ln.disc else "",
            "disc_title": ln.disc.discount.title(ln.currency) if ln.disc else "",
            "tiers": [{"t": t, "selected": t.tier_id == ln.tier.tier_id,
                       "available": max_for(ln.event, t, cart_items(request), except_key=ln.key) > 0}
                      for t in ln.event.tiers] if ln.event.has_tiers and len(ln.event.tiers) > 1 else [],
            "at_max": ln.qty >= ln.max_qty,
        })
    fmt = {
        "gross": sm.fmt_user(sm.gross_cents), "discount": sm.fmt_user(sm.discount_cents),
        "fee": sm.fmt_user(sm.service_fee_cents), "points": sm.fmt_user(sm.points_cents),
        "points_worth": sm.fmt_user(sm.points_worth_cents), "card_base": sm.fmt_usd(sm.card_base_cents),
        "card_fee": sm.fmt_usd(sm.card_fee_cents), "due": sm.fmt_user(sm.amount_due_cents),
        "wallet_local": sm.fmt_local(sm.wallet_local_cents), "card_charge": sm.fmt_usd(sm.card_charge_cents),
        "wallet_balance": sm.fmt_local(sm.wallet_balance_cents), "split": sm.fmt_user(sm.split_wallet_cents),
    }
    return render(request, "biyet/cart.html", {
        "tab": "cart", "sm": sm, "lines": lines, "fmt": fmt, "card_enabled": checkout.card_enabled(),
    })


@customer_required
@require_POST
def cart_update(request):
    c = customer(request)
    items = cart_items(request)
    action, key = request.POST.get("action"), request.POST.get("key")
    line = next((i for i in items if i["key"] == key), None)
    if action == "method":
        request.session["pay_method"] = request.POST.get("method")
        return redirect("cart")
    if line is None:
        return redirect("cart")
    ev = data.get_event(line["event_id"])
    if ev is None or action == "remove":
        items.remove(line)
    elif action in ("inc", "dec"):
        tier = ev.tier_by_id(line["tier_id"])
        mx = max_for(ev, tier, items, except_key=key) if tier else 0
        line["qty"] = max(1, min(line["qty"] + (1 if action == "inc" else -1), max(mx, 1)))
    elif action == "tier":
        tier = ev.tier_by_id(request.POST.get("tier_id", ""))
        if tier and tier.tier_id != line["tier_id"]:
            other = next((i for i in items if i is not line and i["event_id"] == ev.id
                          and i["tier_id"] == tier.tier_id), None)
            if other:
                items.remove(line)
                other["qty"] = max(1, min(other["qty"] + line["qty"],
                                          max_for(ev, tier, items, except_key=other["key"])))
            else:
                line["tier_id"], line["key"] = tier.tier_id, f"{ev.id}_{tier.tier_id}"
                line["qty"] = max(1, min(line["qty"], max_for(ev, tier, items, except_key=line["key"])))
    save_cart(request, items)
    _ = c
    return redirect("cart")


# --------------------------------------------------------------------------- #
# Checkout
# --------------------------------------------------------------------------- #

PENDING_TTL = 15 * 60


def _payment_dict(sm, reservation):
    return {
        "method": sm.method,
        "order_total": checkout.dollars(sm.order_total_cents),
        "points_used": sm.points_used,
        "points_amount": checkout.dollars(sm.points_cents),
        "wallet_amount": checkout.dollars(sm.wallet_applied_cents),
        "card_amount": checkout.dollars(sm.card_charge_cents),
        "card_fee": checkout.dollars(sm.card_fee_cents),
        "buyer": reservation["buyer"],
    }


@customer_required
@require_POST
def checkout_start(request):
    c = customer(request)
    _release_stale(request)
    sm, rates, events = _cart_summary(request, c)
    if not sm.lines:
        return JsonResponse({"ok": False, "message": "Your cart is empty."})
    if not sm.can_pay:
        return JsonResponse({"ok": False, "message": sm.cant_pay_reason})
    card_cents = sm.card_charge_cents
    if card_cents > 0 and not checkout.card_enabled():
        return JsonResponse({"ok": False, "message": "Card payments aren't set up on the web yet. "
                                                     "Use your wallet, or pay in the app."})
    lines = checkout.lock_lines(sm)
    try:
        intent = None
        if card_cents > 0:
            intent = checkout.create_payment_intent(str(c.user.get("email") or request.user.email or ""), card_cents)
        reservation = checkout.reserve(c.uid, rates, lines, sm.wallet_local_cents, sm.points_used)
    except checkout.CheckoutError as exc:
        return JsonResponse({"ok": False, "message": str(exc)})

    if card_cents == 0:
        return _finish(request, c, sm, lines, events, reservation)

    request.session["pending_checkout"] = {
        "reservation": _jsonable(reservation), "lines": lines, "payment": _payment_dict(sm, reservation),
        "pi": intent[2], "cents": card_cents, "at": time.time(),
    }
    return JsonResponse({"ok": True, "needs_card": True, "client_secret": intent[0], "publishable_key": intent[1],
                         "amount": sm.fmt_usd(card_cents)})


def _finish(request, c, sm_or_payment, lines, events, reservation):
    payment = sm_or_payment if isinstance(sm_or_payment, dict) else _payment_dict(sm_or_payment, reservation)
    try:
        order_id, ticket_ids = checkout.save_order(c.uid, c.user, request.user.email, lines, events, payment)
    except Exception:  # noqa: BLE001
        return JsonResponse({"ok": False, "message": "Paid, but your order could not be saved. "
                                                     "Please contact support."})
    save_cart(request, [])
    request.session.pop("pending_checkout", None)
    invalidate_events(c.country)
    messages.success(request, "Payment successful")
    return JsonResponse({"ok": True, "redirect": "/history/", "order_id": order_id, "tickets": ticket_ids})


@customer_required
@require_POST
def checkout_complete(request):
    c = customer(request)
    p = request.session.get("pending_checkout")
    if not p:
        return JsonResponse({"ok": False, "message": "This checkout expired. Nothing was charged."})
    try:
        paid = checkout.verify_payment_intent(p["pi"], p["cents"])
    except Exception:  # noqa: BLE001
        paid = False
    if not paid:
        checkout.release(c.uid, p["reservation"])
        request.session.pop("pending_checkout", None)
        return JsonResponse({"ok": False, "message": "Card payment wasn't completed. You were not charged."})
    events = {ln["event_id"]: data.get_event(ln["event_id"]) for ln in p["lines"]}
    return _finish(request, c, p["payment"], p["lines"], {k: v for k, v in events.items() if v}, p["reservation"])


@customer_required
@require_POST
def checkout_cancel(request):
    c = customer(request)
    p = request.session.pop("pending_checkout", None)
    if p:
        checkout.release(c.uid, p["reservation"])
    return JsonResponse({"ok": True, "message": "Payment canceled. You were not charged."})


def _release_stale(request):
    p = request.session.get("pending_checkout")
    if p and time.time() - p.get("at", 0) > PENDING_TTL:
        checkout.release(request.user.username, p["reservation"])
        request.session.pop("pending_checkout", None)


def _jsonable(o):
    return json.loads(json.dumps(o, default=lambda x: x.isoformat() if isinstance(x, dt.datetime) else str(x)))


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #

HISTORY_FILTERS = ["All", "Upcoming", "Past", "Cancelled"]


@customer_required
def history_tab(request):
    c = customer(request)
    by_id = c.event_map()
    now = dt.datetime.now(dt.timezone.utc)
    entries = []
    for oid, o in data.user_orders(c.uid):
        status = s(o.get("status"), "paid")
        for it in o.get("items") or []:
            if not isinstance(it, dict):
                continue
            ev = by_id.get(s(it.get("eventId")))
            entries.append({"order_id": oid, "event_id": s(it.get("eventId")), "ticket_id": s(it.get("ticketId")),
                            "title": s(it.get("title"), "Event"), "qty": int(it.get("quantity") or 1),
                            "total": float(it.get("total") or 0), "bought": to_date(o.get("created_on")),
                            "status": status, "event": ev, "date": ev.event_date if ev else None, "received": False})
    for tid, t in data.user_tickets(c.uid):
        if t.get("received_via_swap") is not True:
            continue
        det = t.get("event_details") if isinstance(t.get("event_details"), dict) else {}
        ev = by_id.get(s(t.get("event_id")))
        entries.append({"order_id": "", "event_id": s(t.get("event_id")), "ticket_id": tid,
                        "title": s(det.get("title"), "Event"), "qty": int(t.get("quantity") or 1),
                        "total": float(t.get("price") or 0), "bought": to_date(t.get("purchase_date")),
                        "status": "cancelled" if t.get("status") == "cancelled" else "paid", "event": ev,
                        "date": ev.event_date if ev else to_date(det.get("event_date")), "received": True})

    for e in entries:
        e["cancelled"] = e["status"] in ("cancelled", "refunded")
        e["upcoming"] = not e["cancelled"] and e["date"] is not None and e["date"] > now
        e["past"] = not e["cancelled"] and e["date"] is not None and e["date"] <= now
        e["local_date"] = local(e["date"]) if e["date"] else None
        e["venue"] = e["event"].biz_name if e["event"] else ""
        e["city"] = e["event"].biz_city if e["event"] else ""
        if e["upcoming"]:
            days = (e["local_date"].date() - timezone.localdate()).days
            e["countdown"] = (f"Today · {e['local_date']:%-I:%M %p}" if days <= 0 else
                              f"Tomorrow · {e['local_date']:%-I:%M %p}" if days == 1 else f"In {days} days")

    flt = request.GET.get("f", "All")
    match = {"Upcoming": lambda e: e["upcoming"], "Past": lambda e: e["past"],
             "Cancelled": lambda e: e["cancelled"]}.get(flt, lambda e: True)
    counts = {f: len([e for e in entries if {"Upcoming": e["upcoming"], "Past": e["past"],
                                              "Cancelled": e["cancelled"]}.get(f, True)]) for f in HISTORY_FILTERS}
    shown = [e for e in entries if match(e)]
    upcoming = sorted([e for e in shown if e["upcoming"]], key=lambda e: e["date"])
    past = sorted([e for e in shown if e["past"]], key=lambda e: e["date"], reverse=True)
    cancelled = [e for e in shown if e["cancelled"]]
    other = [e for e in shown if not (e["upcoming"] or e["past"] or e["cancelled"])]
    next_up = upcoming.pop(0) if upcoming else None
    return render(request, "biyet/history.html", {
        "tab": "history", "filters": HISTORY_FILTERS, "f": flt, "counts": counts, "total": len(entries),
        "next_up": next_up, "upcoming": upcoming, "past": past, "cancelled": cancelled, "other": other,
        "shown": len(shown),
    })


# --------------------------------------------------------------------------- #
# Ticket
# --------------------------------------------------------------------------- #

TICKET_STATUS = {"used": ("Scanned", "gold"), "cancelled": ("Cancelled", "danger"),
                 "refunded": ("Cancelled", "danger"), "transferred": ("Transferred", "muted")}


def _qr_svg(text):
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")


@customer_required
def ticket_detail(request, ticket_id):
    c = customer(request)
    t = store.get(data.TICKETS, ticket_id)
    if not t or t.get("user_id") != c.uid:
        raise Http404
    d = t.get("event_details") if isinstance(t.get("event_details"), dict) else {}
    status = s(t.get("status"), "valid")
    label, tone = TICKET_STATUS.get(status, ("Valid", "ok"))
    address = " · ".join(x for x in [s(d.get("bizAdd")), ", ".join(
        x for x in (s(d.get("bizCity")), s(d.get("bizState"))) if x)] if x)
    return render(request, "biyet/ticket.html", {
        "tab": "history", "t": t, "tid": ticket_id, "d": d, "date": local(to_date(d.get("event_date"))),
        "start_time": s(d.get("event_start_time")), "status": status, "label": label, "tone": tone,
        "usable": status == "valid", "qr": _qr_svg(ticket_id), "address": address,
        "tier": s(t.get("tierName") or t.get("tier_name")) or "General admission",
        "price": float(t.get("price") or 0), "qty": int(t.get("quantity") or 1),
    })


@customer_required
def ticket_status(request, ticket_id):
    t = store.get(data.TICKETS, ticket_id)
    if not t or t.get("user_id") != customer(request).uid:
        raise Http404
    return JsonResponse({"status": s(t.get("status"), "valid")})


_ = BuyerSnapshot
