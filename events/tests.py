import datetime as dt
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from . import checkout, data, store
from .domain import CurrencyRates, KEvent, format_price_in
from .management.commands.seed_fake import demo_data
from .pricing import build_summary

User = get_user_model()


@override_settings(FIRESTORE_FAKE=True, STRIPE_SECRET_KEY="")
class Base(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        self.db = store.reset_fake(demo_data())
        self.user = User.objects.create_user("demo-uid", email="demo@biyet.app")
        self.client.force_login(self.user)

    def doc(self, col, i):
        return store.get(col, i)

    def add(self, event_id, tier_id=None):
        return self.client.post(f"/events/{event_id}/add/", {"tier_id": tier_id or "", "next": "/cart/"})


class DomainTests(TestCase):
    def test_price_format_matches_app(self):
        self.assertEqual(format_price_in(45, "USD"), "$45")
        self.assertEqual(format_price_in(12.5, "usd"), "$12.50")
        self.assertEqual(format_price_in(1300, "HTG"), "1,300 HTG")

    def test_rates_and_event_currency(self):
        rates = CurrencyRates.from_docs([{"country": "USA", "currency": "usd",
                                          "to": [{"amount": 1, "country": "USA"}, {"amount": 130, "country": "HAITI"}]}])
        self.assertEqual(rates.currency_of_country("haiti"), "HTG")
        self.assertAlmostEqual(rates.convert(1300, "HTG", "USD"), 10)
        e = KEvent.from_map({"title": "x", "country": "USA", "currency": "HTG"}, "e")
        self.assertEqual(rates.event_currency(e), "USD")  # HTG isn't allowed for a USA event

    def test_tiers_and_capacity(self):
        e = KEvent.from_map(demo_data()["user_event_list"]["ev-kompa"], "ev-kompa")
        vip = e.tier_by_id("TIER-VIP1")
        self.assertEqual(e.tier_left(vip), 18)
        self.assertEqual(e.price_label, "From $25")


class PricingTests(Base):
    def summary(self, items, method=None):
        evs = {i["event_id"]: data.get_event(i["event_id"]) for i in items}
        rates, fee = data.rates_and_fee("USA")
        for i in items:
            i["key"] = f"{i['event_id']}_{i['tier_id']}"
        return build_summary(items, evs, data.get_user("demo-uid"), rates, fee, method)

    def test_fee_points_and_wallet(self):
        # $12 x 2 = $24, fee 5% = $1.20, points 250 = $2.50 → $22.70; wallet $40 covers it.
        sm = self.summary([{"event_id": "ev-film", "tier_id": "general", "qty": 2}])
        self.assertEqual(sm.gross_cents, 2400)
        self.assertEqual(sm.service_fee_cents, 120)
        self.assertEqual(sm.points_cents, 250)
        self.assertEqual(sm.points_used, 250)
        self.assertEqual(sm.order_total_cents, 2270)
        self.assertEqual(sm.method, "wallet")
        self.assertEqual(sm.card_charge_cents, 0)

    def test_card_fee_on_card_part_only(self):
        sm = self.summary([{"event_id": "ev-film", "tier_id": "general", "qty": 2}], method="card")
        self.assertEqual(sm.card_fee_cents, round(2270 * 0.03) + 30)
        store.update("users", "demo-uid", {"user_balance": 10})
        sm = self.summary([{"event_id": "ev-film", "tier_id": "general", "qty": 2}])
        self.assertEqual(sm.method, "split")
        self.assertEqual(sm.wallet_applied_cents, 1000)
        self.assertEqual(sm.card_base_cents, 1270)

    def test_discount_limited_per_order(self):
        sm = self.summary([{"event_id": "ev-kompa", "tier_id": "TIER-GEN1", "qty": 3}])
        ln = sm.lines[0]
        self.assertEqual((ln.disc.off_cents, ln.disc.qty), (500, 2))  # 20% of $25 on max 2 tickets
        self.assertEqual(sm.discount_cents, 1000)


class CheckoutTests(Base):
    def start(self):
        return self.client.post("/cart/checkout/").json()

    def test_wallet_checkout_writes_like_the_app(self):
        self.add("ev-film")
        self.add("ev-film")
        r = self.start()
        self.assertTrue(r["ok"], r)
        tid = r["tickets"][0]
        t = self.doc("event_tickets", tid)
        self.assertEqual((t["user_id"], t["status"], t["quantity"], t["event_id"]), ("demo-uid", "valid", 2, "ev-film"))
        self.assertTrue(tid.startswith("TKT-"))
        order = self.doc("ticket_orders", r["order_id"])
        self.assertEqual(order["paymentMethod"], "wallet")
        self.assertEqual(order["items"][0]["ticketId"], tid)
        ev = self.doc("user_event_list", "ev-film")
        self.assertEqual((ev["amount_tix_avail"], ev["sold_count"]), (148, 14))
        u = self.doc("users", "demo-uid")
        self.assertAlmostEqual(u["user_balance"], 40 - 22.70)
        self.assertEqual(u["user_total_points"], 0)
        self.assertEqual(u["tixUserData"]["tixCount"], 3)
        self.assertEqual(self.client.session["cart"], [])

    def test_tiers_and_discount_saved(self):
        self.add("ev-kompa", "TIER-GEN1")
        r = self.start()
        self.assertTrue(r["ok"], r)
        ev = self.doc("user_event_list", "ev-kompa")
        self.assertEqual(ev["tier_sold_counts"]["TIER-GEN1"], 11)
        self.assertEqual(ev["discount_uses"]["DSC-NEW1"], 1)
        u = self.doc("users", "demo-uid")
        self.assertTrue(u["tixUserData"]["usedDiscounts"]["ev-kompa_DSC-NEW1"])
        t = self.doc("event_tickets", r["tickets"][0])
        self.assertEqual((t["discount_id"], t["price"], t["tierName"]), ("DSC-NEW1", 20.0, "General"))

    def test_price_change_is_rejected_and_nothing_taken(self):
        self.add("ev-film")
        rates, fee = data.rates_and_fee("USA")
        items = [{**i, "key": f"{i['event_id']}_{i['tier_id']}"} for i in self.client.session["cart"]]
        sm = build_summary(items, {"ev-film": data.get_event("ev-film")}, data.get_user("demo-uid"), rates, fee)
        locked = checkout.lock_lines(sm)
        store.update("user_event_list", "ev-film", {"tix_price": 99})  # promoter changes the price
        with self.assertRaises(checkout.CheckoutError) as cm:
            checkout.reserve("demo-uid", rates, locked, sm.wallet_local_cents, sm.points_used)
        self.assertIn("price", str(cm.exception))
        self.assertEqual(self.doc("users", "demo-uid")["user_balance"], 40.0)
        self.assertEqual(self.doc("user_event_list", "ev-film")["sold_count"], 12)

    def test_sold_out_blocks_add(self):
        self.add("ev-sold")
        self.assertEqual(self.client.session.get("cart", []), [])

    def test_release_puts_everything_back(self):
        self.add("ev-kompa", "TIER-VIP1")
        rates, fee = data.rates_and_fee("USA")
        items = [{**i, "key": f"{i['event_id']}_{i['tier_id']}"} for i in self.client.session["cart"]]
        sm = build_summary(items, {"ev-kompa": data.get_event("ev-kompa")}, data.get_user("demo-uid"), rates, fee)
        r = checkout.reserve("demo-uid", rates, checkout.lock_lines(sm), sm.wallet_local_cents, sm.points_used)
        self.assertEqual(self.doc("user_event_list", "ev-kompa")["sold_count"], 13)
        checkout.release("demo-uid", r)
        ev, u = self.doc("user_event_list", "ev-kompa"), self.doc("users", "demo-uid")
        self.assertEqual((ev["sold_count"], ev["tier_sold_counts"]["TIER-VIP1"], ev["amount_tix_avail"]), (12, 2, 150))
        self.assertAlmostEqual(u["user_balance"], 40)
        self.assertEqual(u["user_total_points"], 250)
        self.assertNotIn("ev-kompa_DSC-NEW1", u["tixUserData"].get("usedDiscounts", {}))

    def test_card_needs_stripe_config(self):
        store.update("users", "demo-uid", {"user_balance": 0, "user_total_points": 0})
        self.add("ev-film")
        r = self.start()
        self.assertFalse(r["ok"])
        self.assertIn("Card payments", r["message"])


class TicketAndHistoryTests(Base):
    def test_history_and_ticket(self):
        self.assertContains(self.client.get("/history/"), "Indie Film Premiere")
        r = self.client.get("/tickets/TKT-DEMO-0001/")
        self.assertContains(r, "<svg")
        self.assertEqual(self.client.get("/tickets/TKT-DEMO-0001/status/").json(), {"status": "valid"})

    def test_cannot_open_someone_elses_ticket(self):
        self.assertEqual(self.client.get("/tickets/TKT-FRND-0001/").status_code, 404)


class SwapTests(Base):
    def test_claim_then_poster_accepts(self):
        r = self.client.post("/swap/swap-1/action/", {"action": "claim"})
        self.assertEqual(self.doc("ticket_swaps", "swap-1")["status"], "pending")
        # The friend (poster) accepts.
        friend = User.objects.create_user("friend-uid")
        self.client.force_login(friend)
        self.client.post("/swap/swap-1/action/", {"action": "accept"})
        sw = self.doc("ticket_swaps", "swap-1")
        self.assertEqual(sw["status"], "closed")
        new = self.doc("event_tickets", sw["new_ticket_id"])
        self.assertEqual((new["user_id"], new["received_via_swap"], new["price"]), ("demo-uid", True, 4))
        self.assertEqual(self.doc("event_tickets", "TKT-FRND-0001")["status"], "transferred")
        self.assertEqual(self.doc("users", "friend-uid")["tixUserData"]["swapSoldCount"], 1)
        self.assertEqual(self.doc("users", "demo-uid")["tixUserData"]["swapBoughtCount"], 1)
        _ = r

    def test_list_ticket_capped_at_face_value(self):
        self.client.post("/swap/new/", {"ticket": "TKT-DEMO-0001", "type": "sell", "price": "50"})
        self.assertFalse([d for d in self.db.docs["ticket_swaps"].values() if d["ticket_id"] == "TKT-DEMO-0001"])
        self.client.post("/swap/new/", {"ticket": "TKT-DEMO-0001", "type": "sell", "price": "20"})
        listed = [d for d in self.db.docs["ticket_swaps"].values() if d["ticket_id"] == "TKT-DEMO-0001"]
        self.assertEqual(listed[0]["ask_price"], 20)


class AccountTests(Base):
    def test_notifications_mark_all_read(self):
        self.assertContains(self.client.get("/notifications/"), "Kompa Night")
        self.client.post("/notifications/read/", {"all": "1"})
        self.assertTrue(self.doc("tix_personal_notifs", "p1")["read"])
        self.assertTrue(self.doc("users", "demo-uid")["tixUserData"]["readNotifs"]["n1"])

    def test_profile_gate(self):
        store.update("users", "demo-uid", {"tixUserData.idPic": ""})
        self.assertRedirects(self.client.get("/"), "/profile/", fetch_redirect_response=False)

    def test_settings_favorites(self):
        self.client.post("/settings/", {"fav": "concert"})
        self.assertEqual(set(self.doc("users", "demo-uid")["tixUserData"]["favoriteEventTypes"]), {"club", "concert"})

    def test_report_event(self):
        self.client.post("/events/ev-kompa/report/", {"reason": "wrong_price"})
        rep = self.doc("reports", "demo-uid_event_ev-kompa")
        self.assertEqual((rep["reason"], rep["owner_id"]), ("wrong_price", "promoter-1"))

    def test_delete_account_sets_flag(self):
        self.client.post("/me/delete/")
        self.assertTrue(self.doc("users", "demo-uid")["be_req_del"])


@override_settings(FIRESTORE_FAKE=True)
class SignupTests(TestCase):
    def setUp(self):
        self.db = store.reset_fake(demo_data())

    @mock.patch("events.firebase.verify_id_token")
    def test_signup_code_and_referral_reward(self, verify):
        verify.return_value = {"uid": "new-uid", "email": "new@example.com", "name": ""}
        r = self.client.post("/auth/signup/", json.dumps({"idToken": "t", "firstName": "Ana", "lastName": "Joseph",
                                                          "country": "USA", "referral": "0042"}),
                             content_type="application/json").json()
        self.assertTrue(r["ok"], r)
        u = store.get("users", "new-uid")
        self.assertEqual((u["greeter_code"], u["rec_code"], u["user_balance"], u["true_account"]), ("0101", "0042", 5.0, False))
        self.assertTrue(any(m["to"] == ["new@example.com"] for m in self.db.docs["mail"].values()))
        self.assertRedirects(self.client.get("/"), "/verify/", fetch_redirect_response=False)
        self.client.post("/verify/", {"code": "000000"})
        self.assertFalse(store.get("users", "new-uid")["true_account"])
        self.client.post("/verify/", {"code": u["account_code"]})
        self.assertTrue(store.get("users", "new-uid")["true_account"])
        self.assertAlmostEqual(store.get("users", "demo-uid")["user_balance"], 41.0)  # 20% of $5
        self.assertEqual(store.get("users", "demo-uid")["greeter_counts"], 1)

    @mock.patch("events.firebase.verify_id_token")
    def test_bad_referral(self, verify):
        verify.return_value = {"uid": "x-uid", "email": "x@example.com"}
        r = self.client.post("/auth/signup/", json.dumps({"idToken": "t", "firstName": "A", "lastName": "B",
                                                          "country": "USA", "referral": "9999"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.json()["bad_referral"])

    @mock.patch("events.firebase.verify_id_token", return_value={"uid": "promoter-only", "email": "p@x.com"})
    def test_non_customer_is_signed_out(self, _):
        self.client.post("/auth/firebase/", json.dumps({"idToken": "t"}), content_type="application/json")
        self.assertRedirects(self.client.get("/"), "/login/?e=no_customer", fetch_redirect_response=False)


class PWATests(TestCase):
    def test_manifest_and_sw(self):
        self.assertEqual(json.loads(self.client.get("/manifest.webmanifest").content)["display"], "standalone")
        self.assertEqual(self.client.get("/sw.js")["Service-Worker-Allowed"], "/")


_ = dt
