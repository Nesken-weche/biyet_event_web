"""Checkout, ported from cart_tab.dart (_reserve / _release) and
customer_dashboard.dart (_handlePaymentSuccess + TixStats.addPurchase).

Order of operations is the same as the app:
  1. card part? create the Stripe payment intent first (nothing taken yet)
  2. one Firestore transaction re-checks prices, discounts, stock, wallet and
     points, then takes wallet, points and tickets
  3. card part? the browser confirms the card; the server verifies the intent
  4. save tickets + order + purchase stats in one batch
If 3 fails or is cancelled, everything from 2 is given back (release).
"""
from __future__ import annotations

import base64
import json
import math
import urllib.parse
import urllib.request

from django.conf import settings

from . import data, store
from .domain import TIX_FIELD, BuyerSnapshot, KEvent, month_key, month_label, new_ticket_id
from .pricing import STRIPE_MIN_CHARGE_CENTS, allocate_discounts, read_balance_cents, read_points, to_cents


class CheckoutError(Exception):
    """A problem with a message that can be shown to the customer. Nothing was charged."""


def _plural(n, word):
    return f"1 {word}" if n == 1 else f"{n} {word}s"


# --------------------------------------------------------------------------- #
# Locked lines
# --------------------------------------------------------------------------- #


def lock_lines(summary):
    """Freeze what the customer saw, for the reservation checks."""
    out = []
    for ln in summary.lines:
        out.append({
            "key": ln.key,
            "event_id": ln.event.id,
            "title": ln.event.title,
            "tier_id": ln.tier.tier_id,
            "tier_name": ln.tier.name,
            "has_tiers": ln.event.has_tiers,
            "unit_cents": ln.unit_cents,
            "currency": ln.currency,
            "qty": ln.qty,
            "disc": None if ln.disc is None else {
                "id": ln.disc.discount.discount_id,
                "label": ln.disc.discount.title(ln.currency),
                "off_cents": ln.disc.off_cents,
                "qty": ln.disc.qty,
                "funded_by": ln.disc.discount.funded_by,
                "app": ln.disc.discount.is_app_funded,
                "one_per_user": ln.disc.discount.one_per_user,
            },
        })
    return out


def _net_cents(line):
    d = line["disc"]
    return line["unit_cents"] * line["qty"] - (d["off_cents"] * d["qty"] if d else 0)


# --------------------------------------------------------------------------- #
# Reserve / release
# --------------------------------------------------------------------------- #


def reserve(uid, rates, lines, wallet_cents, points):
    def run(tx):
        event_ids = list(dict.fromkeys(ln["event_id"] for ln in lines))

        def title_of(eid):
            return next(ln["title"] for ln in lines if ln["event_id"] == eid)

        user = tx.get(data.USERS, uid)
        if user is None:
            raise CheckoutError("Your account could not be found.")
        events = {}
        for eid in event_ids:
            raw = tx.get(data.EVENTS, eid)
            if raw is None:
                raise CheckoutError(f"{title_of(eid)} is no longer available.")
            events[eid] = KEvent.from_map(raw, eid)

        balance = read_balance_cents(user)
        if balance < wallet_cents:
            raise CheckoutError("Your wallet balance changed. Check the total and try again.")
        user_points = read_points(user)
        if points > 0 and user_points < points:
            raise CheckoutError("Your points balance changed. Check the total and try again.")

        buyer = BuyerSnapshot.from_user(user)
        fresh = allocate_discounts(
            [(ln["key"], events[ln["event_id"]], events[ln["event_id"]].tier_by_id(ln["tier_id"]), ln["qty"])
             for ln in lines if events[ln["event_id"]].tier_by_id(ln["tier_id"]) is not None],
            buyer, BuyerSnapshot.used_keys(user))
        for ln in lines:
            now_d, had = fresh.get(ln["key"]), ln["disc"]
            same = (had is None and now_d is None) or (
                had is not None and now_d is not None and now_d.discount.discount_id == had["id"]
                and now_d.off_cents == had["off_cents"] and now_d.qty == had["qty"])
            if not same:
                raise CheckoutError(f"The discount on {ln['title']} changed. Your cart has been updated. "
                                    "Please check it and try again.")

        discount_uses, used_keys = {}, []
        for ln in lines:
            d = ln["disc"]
            if not d:
                continue
            per = discount_uses.setdefault(ln["event_id"], {})
            per[d["id"]] = per.get(d["id"], 0) + d["qty"]
            if d["one_per_user"]:
                k = f"{ln['event_id']}_{d['id']}"
                if k not in used_keys:
                    used_keys.append(k)

        quantities, sales, tier_qty, app_disc, promoter_disc = {}, {}, {}, {}, {}
        for ln in lines:
            ev = events[ln["event_id"]]
            if not ev.can_get_tix or ev.status in ("cancelled", "draft"):
                raise CheckoutError(f"{ln['title']} is not selling tickets right now.")
            tier = ev.tier_by_id(ln["tier_id"])
            if tier is None:
                raise CheckoutError(f"{ln['tier_name']} tickets for {ln['title']} are no longer available.")
            if rates.event_currency(ev) != ln["currency"]:
                raise CheckoutError(f"The currency of {ln['title']} changed. Please check your cart.")
            if to_cents(tier.price) != ln["unit_cents"]:
                raise CheckoutError(f"The price of {tier.name} for {ln['title']} changed. Your cart has been "
                                    "updated. Please check it and try again.")
            eid = ln["event_id"]
            quantities[eid] = quantities.get(eid, 0) + ln["qty"]
            d = ln["disc"]
            app_paid = bool(d and d["app"])
            disc_total = d["off_cents"] * d["qty"] if d else 0
            sales[eid] = sales.get(eid, 0.0) + (_net_cents(ln) + (disc_total if app_paid else 0)) / 100
            if d:
                m = app_disc if app_paid else promoter_disc
                m[eid] = m.get(eid, 0.0) + disc_total / 100
            per_tier = tier_qty.setdefault(eid, {})
            per_tier[tier.tier_id] = per_tier.get(tier.tier_id, 0) + ln["qty"]

        taken, new_avail, sold_out = {}, {}, {}
        for eid in event_ids:
            ev, qty, title = events[eid], quantities[eid], title_of(eid)
            if ev.sold_out:
                raise CheckoutError(f"{title} is sold out.")
            left = ev.tickets_left
            if left is not None and left < qty:
                raise CheckoutError(f"{title} is sold out." if left <= 0
                                    else f"Only {_plural(left, 'ticket')} left for {title}.")
            for tid, n in tier_qty[eid].items():
                tier = ev.tier_by_id(tid)
                label = f"{tier.name} " if ev.has_tiers else ""
                sold_msg = f"{tier.name} tickets for {title} are sold out." if ev.has_tiers else f"{title} is sold out."
                if tier.sold_out:
                    raise CheckoutError(sold_msg)
                tl = ev.tier_left(tier)
                if tl is not None and tl < n:
                    raise CheckoutError(sold_msg if tl <= 0 else f"Only {_plural(tl, label + 'ticket')} left for {title}.")
                if n > tier.max_per_order:
                    raise CheckoutError(f"You can buy up to {tier.max_per_order} {label}tickets for {title} per order.")
            avail = max(0, ev.amount_tix_avail)
            remaining = max(0, avail - qty)
            new_avail[eid], taken[eid] = remaining, avail - remaining
            counts = dict(ev.tier_sold_counts)
            if ev.has_tiers:
                for tid, n in tier_qty[eid].items():
                    counts[tid] = counts.get(tid, 0) + n
            sold_out[eid] = ev.is_sold_out_with(ev.sold_count + qty, remaining, counts)

        user_updates = {}
        if wallet_cents > 0:
            user_updates["user_balance"] = (balance - wallet_cents) / 100
        if points > 0:
            user_updates["user_total_points"] = user_points - points
        for k in used_keys:
            user_updates[f"{TIX_FIELD}.usedDiscounts.{k}"] = True
        if user_updates:
            tx.update(data.USERS, uid, user_updates)

        tiered = {}
        for eid in event_ids:
            upd = {
                "amount_tix_avail": new_avail[eid],
                "sold_count": store.INC(quantities[eid]),
                "total_sale": store.INC(sales.get(eid, 0.0)),
                "sold_out": sold_out[eid],
            }
            for did, n in discount_uses.get(eid, {}).items():
                upd[f"discount_uses.{did}"] = store.INC(n)
            if app_disc.get(eid, 0) > 0:
                upd["app_discount_total"] = store.INC(app_disc[eid])
            if promoter_disc.get(eid, 0) > 0:
                upd["promoter_discount_total"] = store.INC(promoter_disc[eid])
            if events[eid].has_tiers:
                tiered[eid] = tier_qty[eid]
                for tid, n in tier_qty[eid].items():
                    upd[f"tier_sold_counts.{tid}"] = store.INC(n)
            tx.update(data.EVENTS, eid, upd)

        return {
            "wallet_cents": wallet_cents, "points": points, "quantities": quantities, "taken": taken,
            "sales": sales, "tier_quantities": tiered, "new_balance_cents": balance - wallet_cents,
            "new_points": user_points - points, "discount_uses": discount_uses, "used_keys": used_keys,
            "buyer": buyer.to_map(), "app_disc": app_disc, "promoter_disc": promoter_disc,
        }

    return store.transaction(run)


def release(uid, r):
    """Give back wallet, points and tickets (card failed or was cancelled)."""
    if not r:
        return
    user_updates = {}
    if r["wallet_cents"] > 0:
        user_updates["user_balance"] = store.INC(r["wallet_cents"] / 100)
    if r["points"] > 0:
        user_updates["user_total_points"] = store.INC(r["points"])
    for k in r["used_keys"]:
        user_updates[f"{TIX_FIELD}.usedDiscounts.{k}"] = store.DELETE
    if user_updates:
        _retry(lambda: store.update(data.USERS, uid, user_updates))
    for eid, qty in r["quantities"].items():
        upd = {
            "amount_tix_avail": store.INC(r["taken"].get(eid, 0)),
            "sold_count": store.INC(-qty),
            "total_sale": store.INC(-r["sales"].get(eid, 0)),
            "sold_out": False,
        }
        for tid, n in r["tier_quantities"].get(eid, {}).items():
            upd[f"tier_sold_counts.{tid}"] = store.INC(-n)
        for did, n in r["discount_uses"].get(eid, {}).items():
            upd[f"discount_uses.{did}"] = store.INC(-n)
        if r["app_disc"].get(eid, 0) > 0:
            upd["app_discount_total"] = store.INC(-r["app_disc"][eid])
        if r["promoter_disc"].get(eid, 0) > 0:
            upd["promoter_discount_total"] = store.INC(-r["promoter_disc"][eid])
        _retry(lambda u=upd, e=eid: store.update(data.EVENTS, e, u))


def _retry(fn, attempts=3):
    for i in range(attempts):
        try:
            fn()
            return True
        except Exception:  # noqa: BLE001 - logged by caller in production
            if i == attempts - 1:
                return False
    return False


# --------------------------------------------------------------------------- #
# Save the order (customer_dashboard._handlePaymentSuccess)
# --------------------------------------------------------------------------- #


def save_order(uid, user, auth_email, lines, events, payment):
    """payment: method, order_total, points_used, points_amount, wallet_amount, card_amount, card_fee, buyer"""
    now = store.now()
    email = str(user.get("email") or auth_email or "")
    full_name = f"{user.get('firstName') or ''} {user.get('lastName') or ''}".strip()
    order_id = store.new_id()
    ops, items = [], []

    for ln in lines:
        e = events.get(ln["event_id"])
        if e is None:
            continue
        tid = new_ticket_id()
        d = ln["disc"]
        unit = ln["unit_cents"] / 100
        total_before = unit * ln["qty"]
        disc_total = (d["off_cents"] * d["qty"]) / 100 if d else 0.0
        total = total_before - disc_total
        tier_id = ln["tier_id"] if ln["has_tiers"] else ""
        ops.append(("set", data.TICKETS, tid, {
            # EventTicket.toMap()
            "ticket_id": tid, "event_id": e.id, "user_id": uid, "user_name": full_name, "user_email": email,
            "price": total, "referral_code": "", "purchase_date": now, "status": "valid",
            "event_details": {
                "title": e.title, "eType": e.e_type, "event_date": e.event_date, "bizName": e.biz_name,
                "bizAdd": e.biz_add, "bizCity": e.biz_city, "bizState": e.biz_state,
                "image": e.first_image_url or "",
            },
            "quantity": ln["qty"], "tier_id": "", "tier_name": "",
            # extra fields written by the dashboard
            "tierId": tier_id, "tierName": ln["tier_name"], "unitPrice": unit, "currency": ln["currency"],
            "price_before": total_before, "discount_id": d["id"] if d else "",
            "discount_label": d["label"] if d else "",
            "discount_per_ticket": d["off_cents"] / 100 if d else 0.0, "discount_qty": d["qty"] if d else 0,
            "discount_total": disc_total, "discount_funded_by": d["funded_by"] if d else "",
            "buyer_snapshot": payment["buyer"], "order_id": order_id, "created_on": store.SERVER_TS,
        }, False))
        items.append({
            "eventId": e.id, "ticketId": tid, "title": e.title, "tierId": tier_id, "tierName": ln["tier_name"],
            "quantity": ln["qty"], "unitPrice": unit, "currency": ln["currency"], "total": total,
            "discountId": d["id"] if d else "", "discountTotal": disc_total,
        })

    total_paid = payment["wallet_amount"] + payment["card_amount"]
    ops.append(("set", data.ORDERS, order_id, {
        "orderId": order_id, "discountTotal": sum(i["discountTotal"] for i in items), "uid": uid, "email": email,
        "items": items, "ticketIds": [i["ticketId"] for i in items], "paymentMethod": payment["method"],
        "orderTotal": payment["order_total"], "pointsUsed": payment["points_used"],
        "pointsAmount": payment["points_amount"], "walletAmount": payment["wallet_amount"],
        "cardAmount": payment["card_amount"], "cardFee": payment["card_fee"], "totalPaid": total_paid,
        "status": "paid", "created_on": store.SERVER_TS, "source": "web",
    }, False))

    # TixStats.addPurchase
    qty = sum(ln["qty"] for ln in lines if ln["event_id"] in events)
    if qty > 0:
        k = month_key(now)
        base = f"{TIX_FIELD}.monthly.{k}"
        stat = {
            f"{TIX_FIELD}.tixCount": store.INC(qty),
            f"{TIX_FIELD}.totalSpent": store.INC(total_paid),
            f"{base}.date": month_label(now),
            f"{base}.lastUpdated": store.SERVER_TS,
            f"{base}.eventCounts": store.INC(qty),
            f"{base}.totalSpent": store.INC(total_paid),
            f"{base}.eventIds": store.ARRAY_UNION(list(dict.fromkeys(ln["event_id"] for ln in lines))),
            f"{base}.eventTypes": store.ARRAY_UNION(list(dict.fromkeys(
                events[ln["event_id"]].e_type.strip().lower() for ln in lines
                if ln["event_id"] in events and events[ln["event_id"]].e_type.strip()))),
            f"{base}.events": store.ARRAY_UNION([
                {"eventId": ln["event_id"], "eType": events[ln["event_id"]].e_type.strip().lower()}
                for ln in lines if ln["event_id"] in events]),
        }
        if lines:
            stat[f"{TIX_FIELD}.spentCurrency"] = lines[0]["currency"]
        ops.append(("update", data.USERS, uid, stat, False))

    store.batch(ops)
    return order_id, [i["ticketId"] for i in items]


# --------------------------------------------------------------------------- #
# Stripe (card part)
# --------------------------------------------------------------------------- #


def card_enabled():
    return bool(settings.STRIPE_PAYMENT_INTENT_URL and settings.STRIPE_SECRET_KEY)


def create_payment_intent(email, cents):
    """Same Cloud Function the app calls; it also returns the publishable key."""
    if cents < STRIPE_MIN_CHARGE_CENTS:
        raise CheckoutError("Card payments must be at least $0.50.")
    body = urllib.parse.urlencode({"email": email, "amount": str(cents)}).encode()
    req = urllib.request.Request(settings.STRIPE_PAYMENT_INTENT_URL, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            j = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        raise CheckoutError("Card payment could not start. Check your connection and try again.") from exc
    secret = j.get("paymentIntent") or j.get("client_secret") or j.get("clientSecret")
    pk = j.get("publishableKey")
    if not isinstance(secret, str) or not secret.startswith("pi_") or "_secret_" not in secret:
        raise CheckoutError(f"Card payment could not start: {j.get('error') or 'no payment intent returned'}.")
    if not isinstance(pk, str) or not pk.startswith("pk_"):
        raise CheckoutError("Card payments are not set up yet (no Stripe key from the server).")
    return secret, pk, secret.split("_secret_")[0]


def verify_payment_intent(pi_id, cents):
    """Ask Stripe directly whether the intent was paid in full. Never trust the browser."""
    req = urllib.request.Request(f"https://api.stripe.com/v1/payment_intents/{urllib.parse.quote(pi_id)}")
    token = base64.b64encode(f"{settings.STRIPE_SECRET_KEY}:".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=20) as resp:
        pi = json.loads(resp.read().decode())
    return pi.get("status") == "succeeded" and int(pi.get("amount_received") or 0) >= cents


def dollars(cents):
    return round(cents / 100, 2)


def ceil_div(a, b):
    return math.ceil(a / b)
