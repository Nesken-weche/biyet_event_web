# Biyet Event — *Kote plezi a ye* (web PWA)

The customer dashboard of the Biyet Event app, as an installable web app.
It runs on **the same Firebase project and the same Firestore data** as the
Flutter app, so events, tickets, orders, swaps, wallet, points and
notifications are shared between phone and web.

**Stack:** Django 5 · firebase-admin (Auth + Firestore + Storage) · Firebase JS
SDK for sign-in · service-worker PWA · WhiteNoise.

## What's included

| Screen | Same as the app | Firestore |
|---|---|---|
| Events | search by event/venue/city/promoter, category chips from `developer.eventListTypes`, Hot carousel, Today/Tomorrow/This week groups, favourites first | `user_event_list` (user's country) |
| Event detail | media gallery, ticket tiers, availability, report this event | `user_event_list`, `reports` |
| Cart & checkout | tiers, per-order and venue limits, discounts (best one, per-order / total limits, one per user), service fee from `convertion_rates.eventFee`, points (from $1), wallet / wallet + card / card, card fee 3% + $0.30, HTG ↔ USD | `users`, `user_event_list`, `event_tickets`, `ticket_orders` |
| History | All / Upcoming / Past / Cancelled, Next up ticket, tickets received via Swap | `ticket_orders`, `event_tickets` |
| Ticket | QR code (ticket id), live "Scanned" status, opens offline | `event_tickets` |
| Discover | top pick, for you, this weekend, trending, free, browse by vibe, filters (date, type, price sort) | `user_event_list`, `ticket_orders` |
| Swap | browse / my swaps, list free / sell (capped at face value) / trade, claim, withdraw, decline, remove, transfer (new ticket, old one marked transferred, swap stats) | `ticket_swaps`, `event_tickets`, `users.tixUserData` |
| Menu | balance, points, upcoming tickets, travel card, sign out, delete account (`be_req_del`) | `users` |
| Notifications | announcements by audience key + personal, 4 languages, mark read / all read | `tix_notifications`, `tix_personal_notifs` |
| Referrals & rewards | referral code + share sheet, points, coupons | `users.greeter_code`, `coupons` |
| Settings | favourite event types, notifications on/off | `users.tixUserData` |
| Profile | required fields gate, ID photo upload, USA / Haiti (department → arrondissement → commune) / other address, change history, `adminVerified` reset | `users`, `users/{uid}/tixProfileHistory`, Storage `tix_ids/` |
| Sign up / sign in / code | email + password, country, referral code, `greeter_code` from `developer.greet_num`, sign-up bonus, 6-digit code via `mail`, referrer reward (20%) | `users`, `developer`, `mail` |

**Not included (on purpose):** social room and the version check.
**Not yet on the web:** Transportation (bus booking) — the menu shows the Travel
card where `developer.transportLine` covers the user, pointing to the app.

The money and stock logic is a line-by-line port of `cart_tab.dart`
(`_reserve` / `_release`) and `customer_dashboard.dart`
(`_handlePaymentSuccess`, `TixStats.addPurchase`), so web orders look exactly
like app orders (plus `source: "web"` on the order).

## Run it

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env              # macOS/Linux: cp
python manage.py migrate
python manage.py runserver
```

### Try it without touching real data

Set `FIRESTORE_FAKE=true` in `.env`, then:

```powershell
python manage.py seed_fake
python manage.py runserver
```

Open http://localhost:8000/login/ → **Try the demo account**. The fake
database is saved in `fake_firestore.json`. `seed_fake` refuses to run unless
`FIRESTORE_FAKE=true`, so it can never write to your real project.

### Use your real Firebase project

1. `.env`: `FIRESTORE_FAKE=false`, `FIREBASE_CREDENTIALS=./firebase-service-account.json`,
   and the web config (`FIREBASE_API_KEY`, `FIREBASE_AUTH_DOMAIN`,
   `FIREBASE_PROJECT_ID`, `FIREBASE_STORAGE_BUCKET`, …).
2. Firebase console → Authentication → Settings → **Authorized domains**: add
   `localhost` and your web domain.
3. Sign in with an existing customer account from the app.

The server uses the service account, so Firestore security rules don't apply
to it; every write is checked in Django the same way the app checks it.

### Card payments

Card checkout uses the same Cloud Function as the app
(`GicheStripePaymentIntent`) to create the PaymentIntent and Stripe.js to take
the card. Set `STRIPE_SECRET_KEY` (the secret key of the same Stripe account)
so the server can confirm the charge before saving the order. Without it the
web accepts wallet and points only and tells customers card payments are app-only.

## Tests

```powershell
$env:FIRESTORE_FAKE="true"; python manage.py test events
```

25 tests on the fake Firestore: pricing (fee, points, wallet, split, card
fee, discount limits), wallet checkout writes, price-change rejection, release,
sold out, Swap transfer, notifications, profile gate, settings, reports,
sign-up + code + referral reward.

## Deploy

PWAs need HTTPS. `DJANGO_DEBUG=false`, set `DJANGO_SECRET_KEY`,
`DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, then
`python manage.py collectstatic --noinput && gunicorn biyet.wsgi`. Bump
`VERSION` in `static/js/sw.js` after changing it.

## Branding

Logo mark `static/img/logo-mark.svg`, icons in `static/icons/`, tagline
**Kote plezi a ye**. Replace those files to use your own artwork.
