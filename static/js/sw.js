/* Biyet service worker.
 * - Static assets: cache-first.
 * - Pages: network-first, falling back to the cached copy, then /offline/.
 *   Ticket pages are kept so they still open at the venue door without signal.
 */
const VERSION = "biyet-v4";
const STATIC_CACHE = `${VERSION}-static`;
const PAGE_CACHE = `${VERSION}-pages`;
const STATIC_URL = "__STATIC_URL__";

const SHELL = [
  "/offline/",
  "/history/",
  `${STATIC_URL}css/app.css`,
  `${STATIC_URL}js/app.js`,
  `${STATIC_URL}icons/icon-192.png`,
  `${STATIC_URL}img/logo-mark.svg`,
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      // Add individually so one failure (e.g. /me/tickets/ redirecting when signed out) doesn't abort install.
      Promise.all(SHELL.map((url) => cache.add(new Request(url, { credentials: "same-origin" })).catch(() => {})))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "logout") {
    // Pages are per-user; drop them so the next person on this device can't see them.
    event.waitUntil(caches.delete(PAGE_CACHE));
    return;
  }
  if (event.data?.type === "precache" && Array.isArray(event.data.urls)) {
    event.waitUntil(
      caches.open(PAGE_CACHE).then((cache) =>
        Promise.all(event.data.urls.map((u) =>
          fetch(u, { credentials: "same-origin" }).then((r) => r.ok && !r.redirected && cache.put(u, r)).catch(() => {})
        ))
      )
    );
  }
});

const NEVER_CACHE = ["/admin/", "/auth/", "/logout/", "/login/", "/signup/", "/verify/", "/demo-login/", "/cart/", "/sw.js"];

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (NEVER_CACHE.some((p) => url.pathname.startsWith(p))) return;

  if (url.pathname.startsWith(STATIC_URL)) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(STATIC_CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }))
    );
    return;
  }

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).then((res) => {
        if (res.ok && !res.redirected) {
          const copy = res.clone();
          caches.open(PAGE_CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(async () =>
        (await caches.match(req, { ignoreSearch: true })) ||
        (await caches.match("/offline/")) ||
        new Response("Offline", { status: 503, headers: { "Content-Type": "text/plain" } })
      )
    );
  }
});

// ── Web push (FCM) ───────────────────────────────────────────────────────
// Background messages arrive here. FCM sends JSON like
// { notification: {title, body, image}, data: {...}, fcmOptions: {link} }.
self.addEventListener("push", (event) => {
  let msg = {};
  try { msg = event.data ? event.data.json() : {}; } catch (_) { msg = { notification: { body: event.data?.text() } }; }
  const n = msg.notification || {};
  const d = msg.data || {};
  const title = n.title || d.title || "Biyet Event";
  // Links by notification type, same targets as the app's notification taps.
  const byType = { swap: "/swap/", ticket: "/history/", refund: "/history/", event: "/",
                   referral: "/rewards/", coupon: "/rewards/?tab=rewards", account: "/profile/" };
  const link = msg.fcmOptions?.link || d.link || byType[d.type] || "/notifications/";
  event.waitUntil(self.registration.showNotification(title, {
    body: n.body || d.body || "",
    icon: `${STATIC_URL}icons/icon-192.png`,
    badge: `${STATIC_URL}icons/icon-192.png`,
    image: n.image || d.image,
    data: { link },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.link || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    const same = wins.find((w) => w.url === url);
    if (same) return same.focus();
    return self.clients.openWindow(url);
  }));
});