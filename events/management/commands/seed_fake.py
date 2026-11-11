"""Fill the local fake Firestore (FIRESTORE_FAKE=true) with demo data shaped
exactly like the app's collections. Never touches real Firestore."""
import datetime as dt

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from events import store

UTC = dt.timezone.utc


def demo_data(now=None):
    now = now or dt.datetime.now(UTC)
    day = dt.timedelta(days=1)
    img = "https://images.unsplash.com/photo-{}?w=1200&q=70".format

    def event(title, etype, days, venue, city, price=0.0, **kw):
        d = (now + day * days).replace(hour=1, minute=0, second=0, microsecond=0)
        return {
            "title": title, "eType": etype, "event_date": d, "country": "USA", "bizName": venue, "bizCity": city,
            "bizState": "Indiana", "bizAdd": "100 Main St", "bizID": "promoter-1", "event_promoter": "Lakay Nights",
            "content": "Live music, good people and the best vibes in town.", "can_get_tix": True,
            "amount_tix_avail": 150, "tix_price": price, "capacity": 200, "display": True, "status": "upcoming",
            "sold_count": 12, "usr_going": [], "currency": "USD", "eMedia": [], **kw,
        }

    events = {
        "ev-kompa": event("Kompa Night Live", "club", 3, "The Skyline Terrace", "Indianapolis", 25, hot_event=True,
                          ticket_tiers=[
                              {"tier_id": "TIER-GEN1", "name": "General", "price": 25, "quantity": 120, "max_per_order": 6, "sort_order": 0},
                              {"tier_id": "TIER-VIP1", "name": "VIP", "price": 60, "quantity": 20, "max_per_order": 2, "sort_order": 1,
                               "description": "Skip the line + 1 free drink"},
                          ], tier_sold_counts={"TIER-GEN1": 10, "TIER-VIP1": 2},
                          ticket_discounts=[{"discount_id": "DSC-NEW1", "label": "New to Biyet: 20% off", "kind": "percent",
                                             "value": 20, "condition": "min_events", "min_events": 0, "max_tickets": 2,
                                             "one_per_user": True, "status": "active"}]),
        "ev-film": event("Indie Film Premiere", "concert", 6, "Old Mill Cinema", "Bloomington", 12, hot_event=True),
        "ev-market": event("Night Market & Food Trucks", "festival", 9, "Fountain Square", "Indianapolis", 0,
                           can_get_tix=True, usr_going=["a", "b", "c"]),
        "ev-pitch": event("Startup Pitch Night", "lounge", 14, "The Foundry", "Indianapolis", 5, hot_event=True),
        "ev-past": event("Summer Rooftop Jam", "party", -20, "Skyline Terrace", "Indianapolis", 15),
        "ev-sold": event("Comedy Late Show", "comedy", 4, "Laugh Factory", "Indianapolis", 20, sold_out=True),
    }
    user = {
        "uid": "demo-uid", "firstName": "Jean", "lastName": "Marc", "email": "demo@biyet.app", "u_status": "customer",
        "account_code": "123456", "true_account": True, "greeter_code": "0042", "rec_code": "e", "country": "USA",
        "userCity": "Indianapolis", "userState": "Indiana", "userZip": "46204", "userStreet1": "1 Monument Cir",
        "user_balance": 40.0, "user_total_points": 250, "created_on": now - 400 * day,
        "tixUserData": {"username": "jeanmarc", "phoneNumber": "+1 317 555 0100", "DOB": now - 9000 * day,
                        "idType": "Passport", "idPic": "https://example.invalid/id.jpg", "adminVerified": True,
                        "age": 24, "over21": True, "joinedOn": now - 400 * day, "tixCount": 1, "attendedCount": 2,
                        "favoriteEventTypes": ["club"]},
    }
    friend = {"uid": "friend-uid", "firstName": "Nadia", "lastName": "Pierre", "email": "nadia@example.com",
              "country": "USA", "true_account": True, "user_balance": 0, "user_total_points": 0,
              "userCity": "Indianapolis", "userState": "Indiana",
              "tixUserData": {"username": "nadiap", "phoneNumber": "+1 317 555 0101", "DOB": now - 9500 * day,
                              "idType": "Passport", "idPic": "https://example.invalid/id2.jpg"}}
    ticket = {
        "ticket_id": "TKT-DEMO-0001", "event_id": "ev-film", "user_id": "demo-uid", "user_name": "Jean Marc",
        "user_email": "demo@biyet.app", "price": 24.0, "referral_code": "", "purchase_date": now - 2 * day,
        "status": "valid", "quantity": 2, "tier_id": "", "tier_name": "", "tierName": "General admission",
        "event_details": {"title": "Indie Film Premiere", "eType": "concert", "event_date": events["ev-film"]["event_date"],
                          "bizName": "Old Mill Cinema", "bizAdd": "100 Main St", "bizCity": "Bloomington",
                          "bizState": "Indiana", "image": ""},
        "order_id": "order-1",
    }
    friend_ticket = {**ticket, "ticket_id": "TKT-FRND-0001", "event_id": "ev-pitch", "user_id": "friend-uid",
                     "price": 5.0, "quantity": 1,
                     "event_details": {**ticket["event_details"], "title": "Startup Pitch Night",
                                       "event_date": events["ev-pitch"]["event_date"], "bizName": "The Foundry"}}
    return {
        "developer": {"dev": {"eventListTypes": ["Club", "Concert", "Festival", "Lounge", "Party", "Comedy"],
                              "greet_num": "0100", "clientRefPromo": [{"country": "USA", "info": {"amount": 5}},
                                                                      {"country": "HAITI", "info": {"amount": 500}}],
                              "transportLine": [{"country": "USA", "state_list": [
                                  {"state": "Indiana", "transportType": ["Bus"], "cities": ["Indianapolis"]}]}]}},
        "convertion_rates": {"usd": {"country": "USA", "currency": "usd", "eventFee": 5,
                                     "to": [{"amount": 1, "country": "USA"}, {"amount": 130, "country": "HAITI"}]}},
        "user_event_list": events,
        "users": {"demo-uid": user, "friend-uid": friend},
        "event_tickets": {"TKT-DEMO-0001": ticket, "TKT-FRND-0001": friend_ticket},
        "ticket_orders": {"order-1": {"orderId": "order-1", "uid": "demo-uid", "status": "paid", "created_on": now - 2 * day,
                                      "items": [{"eventId": "ev-film", "ticketId": "TKT-DEMO-0001", "title": "Indie Film Premiere",
                                                 "quantity": 2, "total": 24.0}]}},
        "ticket_swaps": {"swap-1": {"ticket_id": "TKT-FRND-0001", "event_id": "ev-pitch", "event_title": "Startup Pitch Night",
                                    "event_date": events["ev-pitch"]["event_date"], "event_venue": "The Foundry",
                                    "event_city": "Indianapolis", "poster_id": "friend-uid", "poster_name": "Nadia P.",
                                    "poster_avatar": "#1D9E75", "type": "sell", "status": "open", "ask_price": 4,
                                    "face_value": 5, "quantity": 1, "note": "Can't make it, enjoy!", "listed_at": now - day}},
        "coupons": {"c1": {"code": "WELCOME10", "title": "10% off your first ticket", "discountType": "percent",
                           "value": 10, "active": True, "expires_on": now + 30 * day}},
        "tix_notifications": {"n1": {"title": "Kompa Night is almost sold out", "content": "Grab your seat before Friday.",
                                     "ktitle": "Kompa Night prèske fini", "type": "event", "link_id": "ev-kompa",
                                     "audience_keys": ["country:USA"], "created_on": now - dt.timedelta(hours=1),
                                     "expires_at": now + 5 * day, "is_avail": True}},
        "tix_personal_notifs": {"p1": {"uid": "demo-uid", "title": "Your ticket is ready",
                                       "content": "Indie Film Premiere · 2 tickets", "type": "ticket", "read": False,
                                       "created_on": now - 2 * day, "expires_at": now + 5 * day}},
    }


class Command(BaseCommand):
    help = "Load demo data into the local fake Firestore (FIRESTORE_FAKE=true only)."

    def handle(self, *args, **opts):
        if not settings.FIRESTORE_FAKE:
            raise CommandError("Refusing to run: set FIRESTORE_FAKE=true (this never writes to real Firestore).")
        b = store.backend()
        b.docs = demo_data()
        b._save()
        self.stdout.write(self.style.SUCCESS("Fake Firestore seeded. Open /login/ and use “Try the demo account”."))
