"""Cart math, ported from cart_tab.dart. All money is in integer cents."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .domain import BuyerSnapshot, CurrencyRates, KEvent, format_money, format_price_in

DEFAULT_SERVICE_FEE = 0.05
MIN_REDEEM_CENTS = 100          # points apply once worth $1.00
CARD_FEE_RATE = 0.03
CARD_FEE_FIXED_CENTS = 30
MIN_CARD_BASE_CENTS = 20
STRIPE_MIN_CHARGE_CENTS = 50


def dround(x):
    """Dart's round(): halves away from zero (Python rounds halves to even)."""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def to_cents(v):
    return dround(v * 100)


@dataclass
class LineDisc:
    discount: object
    off_cents: int
    qty: int

    @property
    def total_cents(self):
        return self.off_cents * self.qty

    @property
    def total(self):
        return self.total_cents / 100

    def same(self, o):
        return o is not None and o.discount.discount_id == self.discount.discount_id and \
            o.off_cents == self.off_cents and o.qty == self.qty


@dataclass
class CartLine:
    """One cart line: event + ticket type."""
    event: KEvent
    tier: object
    qty: int
    disc: LineDisc | None = None
    max_qty: int = 0
    locked_hint: str | None = None
    currency: str = "USD"

    @property
    def key(self):
        return f"{self.event.id}_{self.tier.tier_id}"

    @property
    def unit_cents(self):
        return to_cents(self.tier.price)

    @property
    def net_cents(self):
        return self.unit_cents * self.qty - (self.disc.total_cents if self.disc else 0)


def allocate_discounts(lines, buyer, used_keys):
    """_allocateDiscounts: best discount per line; limits shared per event."""
    out, left = {}, {}
    for key, ev, tier, qty in lines:
        d = ev.best_discount(tier, buyer, used_keys=used_keys)
        if d is None:
            continue
        k = d.used_key(ev.id)
        if k not in left:
            n = d.max_tickets if d.max_tickets > 0 else None
            uses = ev.discount_uses_left(d)
            if uses is not None:
                n = uses if n is None else min(n, uses)
            left[k] = n
        room = left[k]
        n = qty if room is None else min(qty, room)
        off = dround(d.off_per_ticket(tier.price) * 100)
        if n <= 0 or off <= 0:
            continue
        if room is not None:
            left[k] = room - n
        out[key] = LineDisc(d, off, n)
    return out


def max_for(ev, tier, cart_items, except_key=None):
    """_maxFor: per-order max, tier left, venue left minus other lines."""
    if tier.sold_out:
        return 0
    mx = tier.max_per_order
    tl = ev.tier_left(tier)
    if tl is not None:
        mx = min(mx, tl)
    el = ev.tickets_left
    if el is not None:
        others = sum(i["qty"] for i in cart_items if i["event_id"] == ev.id and i["key"] != except_key)
        mx = min(mx, el - others)
    return max(0, mx)


@dataclass
class Summary:
    lines: list = field(default_factory=list)
    user_currency: str = "USD"
    user_per_usd: float = 1.0
    fee_rate: float = DEFAULT_SERVICE_FEE
    gross_cents: int = 0
    subtotal_cents: int = 0
    discount_cents: int = 0
    service_fee_cents: int = 0
    points_worth_cents: int = 0
    points_cents: int = 0
    points_used: int = 0
    user_points: int = 0
    min_points: int = 0
    order_total_cents: int = 0
    wallet_balance_cents: int = 0     # in user currency
    wallet_usd_cents: int = 0
    method: str = "card"
    available: dict = field(default_factory=dict)
    wallet_applied_cents: int = 0     # USD
    wallet_local_cents: int = 0       # user currency
    split_wallet_cents: int = 0
    card_base_cents: int = 0
    card_fee_cents: int = 0
    card_charge_cents: int = 0
    can_pay: bool = True
    cant_pay_reason: str = ""

    @property
    def amount_due_cents(self):
        return self.wallet_applied_cents + self.card_charge_cents

    def fmt_user(self, usd_cents):
        return format_money(dround(usd_cents * self.user_per_usd) / 100, self.user_currency)

    def fmt_local(self, local_cents):
        return format_money(local_cents / 100, self.user_currency)

    @staticmethod
    def fmt_usd(cents):
        return f"${cents / 100:.2f}"

    @property
    def fee_label(self):
        p = self.fee_rate * 100
        return f"{p:.0f}%" if p == round(p) else f"{p:.1f}%"

    @property
    def button_text(self):
        if self.amount_due_cents == 0:
            return "Complete order"
        if self.method == "wallet":
            return f"Pay {self.fmt_local(self.wallet_local_cents)} from wallet"
        if self.method == "split":
            return f"Pay {self.fmt_local(self.wallet_local_cents)} wallet + {self.fmt_usd(self.card_charge_cents)} card"
        return f"Pay {self.fmt_usd(self.card_charge_cents)} with card"


def read_balance_cents(user):
    raw = (user or {}).get("user_balance")
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        return to_cents(float(raw))
    if isinstance(raw, str):
        try:
            return to_cents(float(raw))
        except ValueError:
            return 0
    return 0


def read_points(user):
    raw = (user or {}).get("user_total_points")
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        return int(math.floor(raw))
    if isinstance(raw, str):
        try:
            return int(math.floor(float(raw)))
        except ValueError:
            return 0
    return 0


def user_country(user):
    c = str((user or {}).get("country") or "").strip().upper()
    return c or "USA"


def build_summary(items, events, user, rates: CurrencyRates, fee_rate, chosen_method=None):
    """items: [{"event_id","tier_id","qty","key"}] from the session cart."""
    sm = Summary(fee_rate=fee_rate if fee_rate is not None else DEFAULT_SERVICE_FEE)
    country = user_country(user)
    sm.user_currency = rates.currency_of_country(country) or "USD"
    sm.user_per_usd = rates.per_base(sm.user_currency) or 1.0
    buyer = BuyerSnapshot.from_user(user)
    used = BuyerSnapshot.used_keys(user)

    lines = []
    for it in items:
        ev = events.get(it["event_id"])
        tier = ev.tier_by_id(it["tier_id"]) if ev else None
        if not ev or not tier:
            continue
        line = CartLine(ev, tier, it["qty"], currency=rates.event_currency(ev))
        line.max_qty = max_for(ev, tier, items, except_key=it["key"])
        lines.append(line)
    discs = allocate_discounts([(ln.key, ln.event, ln.tier, ln.qty) for ln in lines], buyer, used)
    for ln in lines:
        ln.disc = discs.get(ln.key)
        if ln.disc is None:
            locked = ln.event.locked_discounts(ln.tier, buyer)
            locked.sort(key=lambda d: 0 if d.condition == "min_events" else 1)
            if locked and locked[0].condition != "new_user":
                ln.locked_hint = locked[0].unlock_hint(buyer, ln.currency)
    sm.lines = lines

    def usd_cents(amount, currency, country_):
        v = rates.convert(amount, currency, "USD", country=country_)
        return dround((amount if v is None else v) * 100)

    sm.gross_cents = sum(usd_cents(ln.tier.price * ln.qty, ln.currency, ln.event.country) for ln in lines)
    sm.subtotal_cents = sum(
        usd_cents(ln.tier.price * ln.qty - (ln.disc.total if ln.disc else 0), ln.currency, ln.event.country)
        for ln in lines)
    sm.discount_cents = max(0, sm.gross_cents - sm.subtotal_cents)
    sm.service_fee_cents = dround(sm.subtotal_cents * sm.fee_rate)
    before_points = sm.subtotal_cents + sm.service_fee_cents

    sm.user_points = read_points(user)
    sm.points_worth_cents = int(math.floor(sm.user_points / sm.user_per_usd))
    can_use = sm.points_worth_cents >= MIN_REDEEM_CENTS
    sm.points_cents = min(sm.points_worth_cents, before_points) if can_use else 0
    sm.points_used = 0 if sm.points_cents == 0 else min(sm.user_points, math.ceil(sm.points_cents * sm.user_per_usd))
    sm.min_points = math.ceil(MIN_REDEEM_CENTS * sm.user_per_usd)
    sm.order_total_cents = before_points - sm.points_cents

    sm.wallet_balance_cents = read_balance_cents(user)
    sm.wallet_usd_cents = int(math.floor(sm.wallet_balance_cents / sm.user_per_usd))
    covers_all = sm.wallet_usd_cents >= sm.order_total_cents
    sm.split_wallet_cents = max(0, min(sm.wallet_usd_cents, sm.order_total_cents - MIN_CARD_BASE_CENTS))
    sm.available = {
        "wallet": covers_all,
        "split": (not covers_all) and sm.split_wallet_cents > 0,
        "card": True,
    }
    if chosen_method in sm.available and sm.available[chosen_method]:
        sm.method = chosen_method
    else:
        sm.method = next(m for m in ("wallet", "split", "card") if sm.available[m])
    sm.wallet_applied_cents = {"wallet": sm.order_total_cents, "split": sm.split_wallet_cents, "card": 0}[sm.method]
    sm.wallet_local_cents = 0 if sm.wallet_applied_cents == 0 else \
        min(sm.wallet_balance_cents, math.ceil(sm.wallet_applied_cents * sm.user_per_usd))
    sm.card_base_cents = sm.order_total_cents - sm.wallet_applied_cents
    sm.card_fee_cents = dround(sm.card_base_cents * CARD_FEE_RATE) + CARD_FEE_FIXED_CENTS if sm.card_base_cents > 0 else 0
    sm.card_charge_cents = sm.card_base_cents + sm.card_fee_cents

    unsupported = sorted({ln.currency for ln in lines if not rates.supports_currency(ln.currency)})
    if not rates.supports_country(country):
        sm.can_pay, sm.cant_pay_reason = False, "Payments aren't available in your country yet."
    elif unsupported:
        sm.can_pay = False
        sm.cant_pay_reason = f"Tickets priced in {', '.join(unsupported)} can't be paid here yet. Remove them to continue."
    return sm


def approx(rates, line, amount, user_currency):
    if line.currency == user_currency:
        return None
    v = rates.convert(amount, line.currency, user_currency, country=line.event.country)
    return None if v is None else f"≈ {format_money(v, user_currency)}"


def line_money(line, amount):
    return format_money(amount, line.currency) if amount != round(amount) else format_price_in(amount, line.currency)
