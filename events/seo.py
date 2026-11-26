"""Search engines: public home page, robots.txt, sitemap.xml, canonical host,
and schema.org Event data for event pages."""
from __future__ import annotations

import datetime as dt
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import render
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.http import require_GET

from . import data, store
from .domain import KEvent

COUNTRY_CODES = {"USA": "US", "US": "US", "HAITI": "HT", "HT": "HT", "CANADA": "CA", "CA": "CA"}
COUNTRY_NAMES = {"USA": "United States", "HAITI": "Haiti", "CANADA": "Canada"}

# Pages only a signed-in customer can use: keep them out of search results.
PRIVATE_PATHS = ["/cart/", "/history/", "/tickets/", "/swap/", "/me/", "/notifications/", "/rewards/",
                 "/settings/", "/profile/", "/verify/", "/auth/", "/logout/", "/demo-login/", "/admin/",
                 "/discover/", "/events/*/add/", "/events/*/report/"]


def site_url():
    return settings.SITE_URL.rstrip("/")


def absolute(path):
    return site_url() + path


# --------------------------------------------------------------------------- #
# Canonical host: biyet-xxxx.herokuapp.com → https://www.biyetevent.com
# --------------------------------------------------------------------------- #


class CanonicalHostMiddleware:
    """301-redirects other hosts (e.g. the *.herokuapp.com address) to SITE_URL,
    so Google sees one site instead of two copies."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.host = urlsplit(settings.SITE_URL).netloc.lower()

    def __call__(self, request):
        host = request.get_host().split(":")[0].lower()
        if (not settings.DEBUG and self.host and host != self.host
                and host.endswith(".herokuapp.com")):
            return HttpResponsePermanentRedirect(site_url() + request.get_full_path())
        return self.get_response(request)


# --------------------------------------------------------------------------- #
# Public events (all countries), cached
# --------------------------------------------------------------------------- #


def public_events():
    evs = cache.get("public_events")
    if evs is None:
        evs = [KEvent.from_map(d, i) for i, d in store.all_docs(data.EVENTS)]
        cache.set("public_events", evs, 600)
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)
    return sorted([e for e in evs if e.is_visible and e.event_date >= now], key=lambda e: e.event_date)


def public_home(request):
    """What visitors and Google see at biyetevent.com (signed-in customers get
    their Events tab instead)."""
    from .views import card_ctx

    events = public_events()
    countries = sorted({e.country.strip().upper() for e in events if e.country.strip()})
    pick = request.GET.get("country", "").strip().upper()
    shown = [e for e in events if not pick or e.country.strip().upper() == pick]
    website_ld = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Organization", "name": "Biyet Event", "url": absolute("/"),
             "logo": settings.BRAND_LOGO_URL,
             "sameAs": ["https://play.google.com/store/apps/details?id=com.kenutek.giche"]},
            {"@type": "WebSite", "name": "Biyet Event", "alternateName": "Kote plezi a ye", "url": absolute("/")},
            {"@type": "ItemList", "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "url": absolute(f"/events/{e.id}/"), "name": e.title}
                for i, e in enumerate(shown[:30])]},
        ],
    }
    return render(request, "biyet/public_home.html", {
        "cards": [card_ctx(e, []) for e in shown[:60]], "public": True,
        "countries": [(c, COUNTRY_NAMES.get(c, c.title())) for c in countries], "pick": pick,
        "jsonld": _ld(website_ld),
    })


# --------------------------------------------------------------------------- #
# robots.txt + sitemap.xml
# --------------------------------------------------------------------------- #


@require_GET
def robots_txt(request):
    lines = ["User-agent: *", "Allow: /"] + [f"Disallow: {p}" for p in PRIVATE_PATHS]
    lines += ["", f"Sitemap: {absolute('/sitemap.xml')}"]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@require_GET
def sitemap_xml(request):
    urls = [(absolute("/"), None, "daily", "1.0"),
            (absolute("/signup/"), None, "monthly", "0.5"),
            (absolute("/login/"), None, "monthly", "0.3")]
    for e in public_events():
        urls.append((absolute(f"/events/{e.id}/"), None, "daily", "0.8"))
    body = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, freq, prio in urls:
        body.append(f"  <url><loc>{escape(loc)}</loc><changefreq>{freq}</changefreq><priority>{prio}</priority></url>")
    body.append("</urlset>")
    return HttpResponse("\n".join(body), content_type="application/xml")


# --------------------------------------------------------------------------- #
# schema.org Event (Google event rich results)
# --------------------------------------------------------------------------- #


def _ld(obj):
    """JSON-LD that is safe to put inside <script>."""
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def event_jsonld(e: KEvent):
    url = absolute(f"/events/{e.id}/")
    images = [m["url"] for m in e.media if not m["is_video"]] or [settings.BRAND_LOGO_URL]
    country = e.country.strip().upper()
    address = {"@type": "PostalAddress"}
    if e.biz_add:
        address["streetAddress"] = e.biz_add
    if e.biz_city:
        address["addressLocality"] = e.biz_city
    if e.biz_state:
        address["addressRegion"] = e.biz_state
    if country:
        address["addressCountry"] = COUNTRY_CODES.get(country, country)
    offers = []
    if e.can_get_tix:
        for t in e.tiers:
            offers.append({
                "@type": "Offer", "name": t.name, "price": f"{t.price:.2f}", "priceCurrency": e.currency or "USD",
                "availability": "https://schema.org/SoldOut" if e.tier_sold_out(t) else "https://schema.org/InStock",
                "url": url, "validFrom": timezone.localtime(e.event_date - dt.timedelta(days=60)).isoformat(),
            })
    obj = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": e.title,
        "startDate": e.event_date.isoformat(),
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "eventStatus": "https://schema.org/EventCancelled" if e.status == "cancelled"
        else "https://schema.org/EventScheduled",
        "location": {"@type": "Place", "name": e.biz_name or e.biz_city or "Venue", "address": address},
        "image": images[:3],
        "description": (e.content.strip() or ", ".join(x for x in (e.biz_name, e.biz_add, e.biz_city) if x))[:500],
        "url": url,
    }
    if offers:
        obj["offers"] = offers
    if e.event_promoter:
        obj["organizer"] = {"@type": "Organization", "name": e.event_promoter}
    return _ld(obj)
