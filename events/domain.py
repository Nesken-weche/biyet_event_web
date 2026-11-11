"""Python port of the Flutter models (k_event.dart, currency_rates.dart,
ticket_swap.dart, tix_profile.dart) so the web app reads and writes the same
Firestore fields with the same rules as the mobile app."""
from __future__ import annotations

import datetime as dt
import math
import re
import secrets
import unicodedata
from dataclasses import dataclass, field

UTC = dt.timezone.utc
_CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


# --------------------------------------------------------------------------- #
# Converters (same fallbacks as the Dart helpers)
# --------------------------------------------------------------------------- #


def to_date(raw, fallback=None):
    if isinstance(raw, dt.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str) and raw:
        try:
            d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=UTC)
        except ValueError:
            pass
    return fallback


def to_float(raw, fallback=0.0):
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return fallback
    return fallback


def to_int(raw, fallback=0):
    if isinstance(raw, bool):
        return fallback
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(float(raw))
        except ValueError:
            return fallback
    return fallback


def s(raw, fallback=""):
    return fallback if raw is None else str(raw)


def lst(raw):
    return raw if isinstance(raw, list) else []


def normalize(text):
    """Lower-case and strip accents so "petion" matches "Pétion-Ville"."""
    text = unicodedata.normalize("NFKD", (text or "").lower().strip())
    return "".join(c for c in text if not unicodedata.combining(c))


def _group_thousands(whole: str) -> str:
    return re.sub(r"\B(?=(\d{3})+(?!\d))", ",", whole)


def format_price_in(v, currency):
    """"$45" / "$12.50" for USD, "1,300 HTG" otherwise (k_event.formatPriceIn)."""
    c = (currency or "").strip().upper()
    fixed = f"{v:.0f}" if v == round(v) else f"{v:.2f}"
    whole, _, dec = fixed.partition(".")
    n = _group_thousands(whole) + (f".{dec}" if dec else "")
    return f"${n}" if c in ("", "USD") else f"{n} {c}"


def format_money(amount, currency):
    """$1,234.50 / 1,234.50 HTG (CurrencyRates.format)."""
    c = (currency or "").strip().upper()
    whole, _, dec = f"{amount:.2f}".partition(".")
    txt = f"{_group_thousands(whole)}.{dec}"
    return f"${txt}" if c in ("", "USD") else f"{txt} {c}"


def short_code(prefix, parts=1):
    def part():
        return "".join(secrets.choice(_CODE_CHARS) for _ in range(4))

    return prefix + "-" + "-".join(part() for _ in range(parts))


def new_ticket_id():
    return short_code("TKT", 2)


AVATAR_COLORS = ["#E8192C", "#534AB7", "#1D9E75", "#D85A30", "#D4537E", "#185FA5", "#854F0B", "#639922", "#D4AF6A"]


def avatar_color_from_uid(uid):
    return AVATAR_COLORS[sum(ord(c) for c in uid) % len(AVATAR_COLORS)]


# --------------------------------------------------------------------------- #
# BuyerSnapshot
# --------------------------------------------------------------------------- #


@dataclass
class BuyerSnapshot:
    taken_at: dt.datetime
    joined_on: dt.datetime | None = None
    tickets_bought: int = 0
    attended_count: int = 0

    def is_new_user(self, days, now=None):
        if self.tickets_bought > 0 or self.joined_on is None:
            return False
        return ((now or self.taken_at) - self.joined_on).days < days

    @classmethod
    def from_user(cls, user, at=None):
        u = user or {}
        tix = u.get("tixUserData") if isinstance(u.get("tixUserData"), dict) else {}
        joined = tix.get("joinedOn") or u.get("created_on") or tix.get("created_on")
        return cls(
            taken_at=at or dt.datetime.now(UTC),
            joined_on=to_date(joined),
            tickets_bought=to_int(tix.get("tixCount")),
            attended_count=to_int(tix.get("attendedCount")),
        )

    @staticmethod
    def used_keys(user):
        tix = (user or {}).get("tixUserData")
        used = tix.get("usedDiscounts") if isinstance(tix, dict) else None
        return {str(k) for k, v in used.items() if v is True} if isinstance(used, dict) else set()

    def to_map(self):
        return {
            "joined_on": self.joined_on,
            "tickets_bought": self.tickets_bought,
            "attended_count": self.attended_count,
            "taken_at": self.taken_at,
        }


# --------------------------------------------------------------------------- #
# TicketTier / EventDiscount
# --------------------------------------------------------------------------- #


@dataclass
class TicketTier:
    GENERAL_ID = "general"

    tier_id: str
    name: str
    price: float
    description: str = ""
    quantity: int = 0
    max_per_order: int = 10
    sold_out: bool = False
    sort_order: int = 0

    @property
    def is_free(self):
        return self.price <= 0

    @classmethod
    def from_map(cls, m):
        return cls(
            tier_id=s(m.get("tier_id")),
            name=s(m.get("name")),
            price=to_float(m.get("price")),
            description=s(m.get("description")),
            quantity=to_int(m.get("quantity")),
            max_per_order=to_int(m.get("max_per_order"), 10),
            sold_out=m.get("sold_out") is True,
            sort_order=to_int(m.get("sort_order")),
        )


@dataclass
class EventDiscount:
    PERCENT, AMOUNT = "percent", "amount"
    EVERYONE, NEW_USER, MIN_EVENTS = "everyone", "new_user", "min_events"
    FUNDED_PROMOTER, FUNDED_APP = "promoter", "app"

    discount_id: str
    kind: str
    value: float
    label: str = ""
    condition: str = "everyone"
    new_user_days: int = 30
    min_events: int = 0
    tier_ids: list = field(default_factory=list)
    max_tickets: int = 0
    max_uses: int = 0
    one_per_user: bool = True
    starts_at: dt.datetime | None = None
    ends_at: dt.datetime | None = None
    status: str = "active"
    funded_by: str = "promoter"

    @property
    def is_percent(self):
        return self.kind == self.PERCENT

    @property
    def is_app_funded(self):
        return self.funded_by == self.FUNDED_APP

    def is_live_at(self, now):
        return (
            self.status == "active"
            and (self.starts_at is None or now >= self.starts_at)
            and (self.ends_at is None or now < self.ends_at)
        )

    def applies_to_tier(self, tier_id):
        return not self.tier_ids or tier_id in self.tier_ids

    def used_key(self, event_id):
        return f"{event_id}_{self.discount_id}"

    def off_per_ticket(self, price):
        if price <= 0 or self.value <= 0:
            return 0.0
        off = price * min(self.value, 100) / 100 if self.is_percent else self.value
        return round(min(off, price) * 100) / 100

    def qualifies(self, buyer: BuyerSnapshot, now=None):
        if self.condition == self.NEW_USER:
            return buyer.is_new_user(self.new_user_days, now=now)
        if self.condition == self.MIN_EVENTS:
            return buyer.attended_count >= self.min_events
        return True

    def value_label(self, currency):
        if self.is_percent:
            v = self.value
            return (f"{v:.0f}" if v == round(v) else str(v)) + "%"
        return format_price_in(self.value, currency)

    def unlock_hint(self, buyer, currency):
        v = self.value_label(currency)
        if self.condition == self.MIN_EVENTS:
            left = max(1, self.min_events - buyer.attended_count)
            return f"Attend {left} more {'event' if left == 1 else 'events'} to unlock {v} off"
        return f"{v} off for new users"

    def title(self, currency):
        return self.label.strip() or f"{self.value_label(currency)} off"

    @classmethod
    def from_map(cls, m):
        cond = s(m.get("condition"))
        created_role = s(m.get("created_role"), "promoter")
        funded = s(m.get("funded_by"), cls.FUNDED_APP if created_role == "admin" else cls.FUNDED_PROMOTER)
        return cls(
            discount_id=s(m.get("discount_id")),
            label=s(m.get("label")),
            kind=cls.AMOUNT if s(m.get("kind"), "percent") == cls.AMOUNT else cls.PERCENT,
            value=to_float(m.get("value")),
            condition=cond if cond in (cls.NEW_USER, cls.MIN_EVENTS) else cls.EVERYONE,
            new_user_days=to_int(m.get("new_user_days"), 30),
            min_events=to_int(m.get("min_events")),
            tier_ids=[str(x) for x in lst(m.get("tier_ids"))],
            max_tickets=to_int(m.get("max_tickets")),
            max_uses=to_int(m.get("max_uses")),
            one_per_user=m.get("one_per_user") is not False,
            starts_at=to_date(m.get("starts_at")),
            ends_at=to_date(m.get("ends_at")),
            status="ended" if s(m.get("status"), "active") == "ended" else "active",
            funded_by=cls.FUNDED_APP if funded == cls.FUNDED_APP else cls.FUNDED_PROMOTER,
        )


# --------------------------------------------------------------------------- #
# KEvent (fields the customer dashboard uses)
# --------------------------------------------------------------------------- #

MAX_EXTRAS_RATE = 0.10


@dataclass
class KEvent:
    id: str
    title: str
    e_type: str
    event_date: dt.datetime
    content: str = ""
    imp_note: str = ""
    country: str = ""
    biz_id: str = ""
    biz_name: str = ""
    biz_add: str = ""
    biz_city: str = ""
    biz_state: str = ""
    e_media: list = field(default_factory=list)
    usr_going: list = field(default_factory=list)
    can_get_tix: bool = False
    amount_tix_avail: int = 0
    tix_price: float = 0.0
    capacity: int = 0
    allow_extras: bool = False
    max_extras: int = 0
    sold_out: bool = False
    display: bool = True
    hot_event: bool = False
    event_promoter: str = ""
    status: str = "upcoming"
    sold_count: int = 0
    event_start_time: str = ""
    ticket_tiers: list = field(default_factory=list)
    tier_sold_counts: dict = field(default_factory=dict)
    currency: str = "USD"
    ticket_discounts: list = field(default_factory=list)
    discount_uses: dict = field(default_factory=dict)

    @classmethod
    def from_map(cls, m, doc_id):
        tiers = [TicketTier.from_map(t) for t in lst(m.get("ticket_tiers")) if isinstance(t, dict)]
        tiers.sort(key=lambda t: t.sort_order)
        discounts = [EventDiscount.from_map(d) for d in lst(m.get("ticket_discounts")) if isinstance(d, dict)]
        cur = s(m.get("currency")).strip().upper() or "USD"
        return cls(
            id=doc_id,
            title=s(m.get("title")),
            e_type=s(m.get("eType")),
            event_date=to_date(m.get("event_date"), dt.datetime.now(UTC)),
            content=s(m.get("content")),
            imp_note=s(m.get("imp_note")),
            country=s(m.get("country")),
            biz_id=s(m.get("bizID")),
            biz_name=s(m.get("bizName")),
            biz_add=s(m.get("bizAdd")),
            biz_city=s(m.get("bizCity")),
            biz_state=s(m.get("bizState")),
            e_media=lst(m.get("eMedia")),
            usr_going=lst(m.get("usr_going")),
            can_get_tix=m.get("can_get_tix") is True,
            amount_tix_avail=to_int(m.get("amount_tix_avail")),
            tix_price=to_float(m.get("tix_price")),
            capacity=to_int(m.get("capacity")),
            allow_extras=m.get("allowExtras") is True,
            max_extras=to_int(m.get("max_extras")),
            sold_out=m.get("sold_out") is True,
            display=m.get("display") is not False,
            hot_event=m.get("hot_event") is True,
            event_promoter=s(m.get("event_promoter")),
            status=s(m.get("status"), "upcoming"),
            sold_count=to_int(m.get("sold_count")),
            event_start_time=s(m.get("event_start_time")),
            ticket_tiers=tiers,
            tier_sold_counts={str(k): to_int(v) for k, v in (m.get("tier_sold_counts") or {}).items()}
            if isinstance(m.get("tier_sold_counts"), dict) else {},
            currency=cur,
            ticket_discounts=[d for d in discounts if d.discount_id],
            discount_uses={str(k): to_int(v) for k, v in (m.get("discount_uses") or {}).items()}
            if isinstance(m.get("discount_uses"), dict) else {},
        )

    # ── Visibility ──
    @property
    def is_visible(self):
        return self.display and self.status != "draft"

    @property
    def is_past(self):
        return self.event_date.date() < dt.datetime.now(self.event_date.tzinfo).date()

    # ── Capacity ──
    @property
    def extras_allowed(self):
        if not self.allow_extras or self.capacity <= 0:
            return 0
        return min(self.max_extras, math.floor(self.capacity * MAX_EXTRAS_RATE))

    @property
    def sell_limit(self):
        return self.capacity + self.extras_allowed if self.capacity > 0 else None

    @property
    def has_tiers(self):
        return bool(self.ticket_tiers)

    @property
    def tickets_left(self):
        limit = self.sell_limit
        by_cap = None if limit is None else max(0, limit - self.sold_count)
        if self.allow_extras or self.has_tiers:
            return by_cap
        regular = max(0, self.amount_tix_avail)
        return regular if by_cap is None else min(regular, by_cap)

    @property
    def is_sold_out(self):
        left = self.tickets_left
        return (
            self.sold_out
            or (left is not None and left <= 0)
            or (self.has_tiers and all(self.tier_sold_out(t) for t in self.ticket_tiers))
        )

    # ── Tiers ──
    @property
    def tiers(self):
        if self.has_tiers:
            return self.ticket_tiers
        return [TicketTier(TicketTier.GENERAL_ID, "General admission", self.tix_price)]

    def tier_by_id(self, tier_id):
        return next((t for t in self.tiers if t.tier_id == tier_id), None)

    def tier_sold(self, t):
        return self.tier_sold_counts.get(t.tier_id, 0) if self.has_tiers else self.sold_count

    def tier_left(self, t):
        by_tier = max(0, t.quantity - self.tier_sold(t)) if t.quantity > 0 else None
        by_event = self.tickets_left
        if by_tier is None:
            return by_event
        if by_event is None:
            return by_tier
        return min(by_tier, by_event)

    def tier_sold_out(self, t):
        if t.sold_out:
            return True
        left = self.tier_left(t)
        return left is not None and left <= 0

    @property
    def lowest_price(self):
        open_ = [t for t in self.tiers if not self.tier_sold_out(t)]
        return min(t.price for t in (open_ or self.tiers))

    @property
    def price_label(self):
        low = self.lowest_price
        varied = len({t.price for t in self.tiers}) > 1
        if low <= 0 and not varied:
            return "Free"
        money = format_price_in(low, self.currency)
        return f"From {money}" if varied else money

    def money(self, v):
        return format_price_in(v, self.currency)

    def tier_price(self, t):
        return "Free" if t.is_free else self.money(t.price)

    # ── Media ──
    @property
    def media(self):
        out = []
        for m in self.e_media:
            if not isinstance(m, dict) or not s(m.get("url")):
                continue
            kind = s(m.get("type")).lower()
            if kind in ("image", "video"):
                out.append({"url": s(m.get("url")), "is_video": kind == "video"})
        return out

    @property
    def first_image_url(self):
        return next((m["url"] for m in self.media if not m["is_video"]), None)

    @property
    def going_count(self):
        return self.sold_count + len(self.usr_going)

    @property
    def availability_note(self):
        left = self.tickets_left
        if self.is_sold_out or left is None or left > 20:
            return None
        return f"Only {left} left"

    @property
    def venue_line(self):
        return ", ".join(x for x in (self.biz_name, self.biz_city) if x)

    # ── Discounts ──
    def discount_uses_left(self, d):
        return max(0, d.max_uses - self.discount_uses.get(d.discount_id, 0)) if d.max_uses > 0 else None

    def live_discounts_for(self, tier, now=None):
        now = now or dt.datetime.now(UTC)
        return [
            d for d in self.ticket_discounts
            if d.is_live_at(now) and d.applies_to_tier(tier.tier_id)
            and (self.discount_uses_left(d) is None or self.discount_uses_left(d) > 0)
        ]

    def best_discount(self, tier, buyer, used_keys=frozenset(), now=None):
        best, best_off = None, 0.0
        for d in self.live_discounts_for(tier, now):
            if not d.qualifies(buyer, now=now):
                continue
            if d.one_per_user and d.used_key(self.id) in used_keys:
                continue
            off = d.off_per_ticket(tier.price)
            if off > best_off:
                best, best_off = d, off
        return best

    def locked_discounts(self, tier, buyer, now=None):
        return [d for d in self.live_discounts_for(tier, now) if not d.qualifies(buyer, now=now)]

    def is_sold_out_with(self, sold_count, amount_tix_avail, tier_sold_counts):
        """KEvent.copyWith(...).isSoldOut used by checkout."""
        import copy as _copy

        e = _copy.copy(self)
        e.sold_count, e.amount_tix_avail, e.tier_sold_counts = sold_count, amount_tix_avail, tier_sold_counts
        return e.is_sold_out


# --------------------------------------------------------------------------- #
# CurrencyRates (currency_rates.dart)
# --------------------------------------------------------------------------- #

KNOWN_CURRENCIES = {"USA": "USD", "HAITI": "HTG"}


def norm(x):
    return (x or "").strip().upper()


class CurrencyRates:
    BASE = "USD"

    def __init__(self, per_base, currency_of, by_country):
        self.base_currency = self.BASE
        self._per_base = per_base          # COUNTRY -> local per 1 USD
        self._currency_of = currency_of    # COUNTRY -> code
        self._by_country = by_country      # COUNTRY -> (currency, {COUNTRY: amount})

    @classmethod
    def from_docs(cls, docs):
        per_base, currency_of, by_country = {}, {}, {}
        base_docs = [d for d in docs if norm(s(d.get("currency"))) == cls.BASE]
        if base_docs:
            d = base_docs[0]
            doc_country = norm(s(d.get("country")))
            for t in lst(d.get("to")):
                if not isinstance(t, dict):
                    continue
                c, a = norm(s(t.get("country"))), t.get("amount")
                if not c or not isinstance(a, (int, float)) or a <= 0:
                    continue
                per_base[c] = float(a)
                cur = norm(s(t.get("currency")))
                currency_of[c] = cur or KNOWN_CURRENCIES.get(c) or (cls.BASE if c == doc_country else "")
        for d in docs:
            country, currency = norm(s(d.get("country"))), norm(s(d.get("currency")))
            if not country or not currency:
                continue
            to = {}
            for t in lst(d.get("to")):
                if isinstance(t, dict):
                    c, a = norm(s(t.get("country"))), t.get("amount")
                    if c and isinstance(a, (int, float)) and a > 0:
                        to[c] = float(a)
            by_country[country] = (currency, to)
        for d in docs:
            c, cur = norm(s(d.get("country"))), norm(s(d.get("currency")))
            if c in per_base and not currency_of.get(c) and cur:
                currency_of[c] = cur
        if not per_base:
            per_base["USA"], currency_of["USA"] = 1.0, "USD"
        currency_of = {k: v for k, v in currency_of.items() if v}
        return cls(per_base, currency_of, by_country)

    def supports_country(self, country):
        return norm(country) in self._currency_of

    def currency_of_country(self, country):
        return self._currency_of.get(norm(country))

    def currencies_for_country(self, country):
        local = self.currency_of_country(country)
        if local is None:
            return []
        return [local] if local == self.base_currency else [local, self.base_currency]

    def supports_currency(self, currency):
        c = norm(currency)
        return c == self.base_currency or c in self._currency_of.values()

    def per_base(self, currency):
        c = norm(currency)
        if c == self.base_currency:
            return 1.0
        for country, code in self._currency_of.items():
            if code == c:
                return self._per_base.get(country)
        return None

    def per_unit_in(self, country, currency):
        doc = self._by_country.get(norm(country))
        if doc is None:
            return None
        c = norm(currency)
        if not c:
            return None
        doc_cur, to = doc
        if c == doc_cur:
            return 1.0
        for k, amount in to.items():
            cur = self._currency_of.get(k) or KNOWN_CURRENCIES.get(k) or (self._by_country.get(k) or (None,))[0]
            if cur == c:
                return amount
        return None

    def convert(self, amount, frm, to, country=None):
        if norm(frm) == norm(to):
            return amount
        if country and country.strip():
            a, b = self.per_unit_in(country, frm), self.per_unit_in(country, to)
            if a is not None and b is not None:
                return amount / a * b
        a, b = self.per_base(frm), self.per_base(to)
        if a is None or b is None:
            return None
        return amount / a * b

    def event_currency(self, e: KEvent):
        """CartTab._eventCurrency: the event's currency if allowed for its country."""
        cur, country = norm(e.currency), norm(e.country)
        if not country:
            return cur or "USD"
        allowed = self.currencies_for_country(country)
        if not allowed:
            return cur or "USD"
        return cur if cur in allowed else allowed[0]


# --------------------------------------------------------------------------- #
# Event fee (customer_dashboard._loadEventFee)
# --------------------------------------------------------------------------- #


def event_fee_rate(rate_docs, country):
    def as_rate(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            return None
        return v / 100 if v > 1 else float(v)

    c = norm(country)
    if c:
        for d in rate_docs:
            if norm(s(d.get("country"))) == c:
                r = as_rate(d.get("eventFee"))
                if r is not None:
                    return r
    for d in rate_docs:
        for t in lst(d.get("to")):
            if isinstance(t, dict) and norm(s(t.get("country"))) == c:
                r = as_rate(t.get("eventFee"))
                if r is not None:
                    return r
    for d in rate_docs:
        r = as_rate(d.get("eventFee"))
        if r is not None:
            return r
    return None


# --------------------------------------------------------------------------- #
# Profile (tix_profile.dart)
# --------------------------------------------------------------------------- #

TIX_FIELD = "tixUserData"
ID_TYPES = ["National ID (CIN)", "Passport", "Driver's license", "Other"]
REQUIRED_FIELDS = {
    "username": "Username",
    "phoneNumber": "Phone number",
    "DOB": "Date of birth",
    "idType": "ID type",
    "idPic": "Photo of your ID",
}


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def is_usa(country):
    return norm(s(country)) in ("USA", "US", "UNITED STATES")


def is_haiti(country):
    return norm(s(country)) in ("HAITI", "HT")


def missing_profile(tix):
    if tix is None:
        return list(REQUIRED_FIELDS.values())
    return [label for key, label in REQUIRED_FIELDS.items() if _blank(tix.get(key))]


def missing_address(user):
    if not user:
        return []
    haiti = is_haiti(user.get("country"))
    out = []
    if _blank(user.get("userState")):
        out.append("Department" if haiti else "State")
    if _blank(user.get("userCity")):
        out.append("Commune" if haiti else "City")
    return out


def age_on(dob: dt.date, today: dt.date | None = None):
    today = today or dt.date.today()
    age = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        age -= 1
    return age


def title_case(text):
    return " ".join(w[0].upper() + w[1:] for w in (text or "").strip().split() if w)


def favorite_types(user):
    tix = (user or {}).get(TIX_FIELD)
    raw = tix.get("favoriteEventTypes") if isinstance(tix, dict) and "favoriteEventTypes" in tix else None
    if raw is None:
        old = (user or {}).get("tixSettings")
        raw = old.get("favoriteEventTypes") if isinstance(old, dict) else None
    return {str(x).strip().lower() for x in raw} if isinstance(raw, list) else set()


def notifications_enabled(user):
    tix = (user or {}).get(TIX_FIELD)
    if isinstance(tix, dict) and "notificationsEnabled" in tix:
        return tix["notificationsEnabled"] is True
    old = (user or {}).get("tixSettings")
    return isinstance(old, dict) and old.get("notificationsEnabled") is True


def public_name(user):
    u = user or {}
    first, last = s(u.get("firstName")).strip(), s(u.get("lastName")).strip()
    username = s(u.get("username")).strip()
    if first:
        return f"{first} {last[0].upper()}." if last else first
    return username or "Kobey user"


def month_key(d):
    return f"{d.year}-{d.month:02d}"


def month_label(d):
    return f"{d.month:02d}-{d.year}"


def money_short(v):
    return f"${v:.0f}" if v == round(v) else f"${v:.2f}"


US_STATES = [
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
    "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas",
    "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
]

HAITI_DEPARTMENTS = ["Artibonite", "Centre", "Grand'Anse", "Nippes", "Nord", "Nord-Est", "Nord-Ouest", "Ouest", "Sud", "Sud-Est"]

HAITI_STATES = HAITI_DEPARTMENTS

# report.dart: ReportType.event reasons
REPORT_REASONS = [
    ("scam", "Scam or fake event"),
    ("misleading", "Misleading information"),
    ("offensive_image", "Offensive image or text"),
    ("wrong_price", "Wrong price"),
    ("other", "Something else"),
]
