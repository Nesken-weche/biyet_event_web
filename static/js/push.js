// Web push: gets this browser's FCM token and saves it as
// users/{uid}.tixUserData.fcmToken (POST /me/device-token/).
//
// Phone app equivalent: TixProfile.fcmToken() + the Settings switch.
//  - Already allowed → refresh the token quietly on each visit (like ensure()).
//  - Settings switch → ask permission (must come from a tap), then save it.
import { initializeApp, getApps } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getMessaging, getToken, onMessage, isSupported } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging.js";

const root = document.getElementById("push-config");
const config = JSON.parse(document.getElementById("firebase-config").textContent);
const csrf = () => document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";
const app = getApps()[0] || initializeApp(config);
const SAVED = "biyet.fcmToken";

async function currentToken() {
  if (!(await isSupported())) throw new Error("unsupported");
  const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  return getToken(getMessaging(app), { vapidKey: root.dataset.vapid, serviceWorkerRegistration: registration });
}

async function save(token, enable = false) {
  const res = await fetch(root.dataset.url, {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
    body: JSON.stringify({ token, enable }),
  });
  if (!res.ok) throw new Error("save failed");
  try { localStorage.setItem(SAVED, token); } catch (_) { /* private mode */ }
}

/** Called from a tap (Settings switch). Resolves to "on", "blocked" or "unsupported". */
window.biyetEnablePush = async function () {
  if (!("Notification" in window) || !(await isSupported())) return "unsupported";
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return "blocked";
  const token = await currentToken();
  if (!token) return "unsupported";
  await save(token, true);
  return "on";
};

// Quiet refresh: only when the person already said yes, never prompts.
(async () => {
  try {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const token = await currentToken();
    let saved = null;
    try { saved = localStorage.getItem(SAVED); } catch (_) { /* ignore */ }
    if (token && token !== saved) await save(token);

    // While the page is open, FCM delivers here instead of the service worker.
    onMessage(getMessaging(app), (msg) => {
      const n = msg.notification || {};
      const title = n.title || msg.data?.title;
      if (!title) return;
      navigator.serviceWorker.ready.then((reg) => reg.showNotification(title, {
        body: n.body || msg.data?.body || "", icon: "/static/icons/icon-192.png",
        image: n.image, data: { link: msg.fcmOptions?.link || msg.data?.link || "/" },
      }));
    });
  } catch (_) { /* unsupported browser or blocked: nothing to do */ }
})();