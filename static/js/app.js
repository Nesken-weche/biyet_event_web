// PWA shell + small helpers.
(function () {
  // Time zone for server-rendered times.
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (tz && !document.cookie.includes("tz=" + encodeURIComponent(tz))) {
      document.cookie = "tz=" + encodeURIComponent(tz) + ";path=/;max-age=31536000;samesite=lax";
    }
  } catch (_) { /* ignore */ }

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).then((reg) => {
        const urls = [...document.querySelectorAll("a[data-precache]")].map((a) => a.href).filter((h) => !h.endsWith("#"));
        if (urls.length) {
          const send = (sw) => sw && sw.postMessage({ type: "precache", urls });
          reg.active ? send(reg.active) : navigator.serviceWorker.ready.then((r) => send(r.active));
        }
      }).catch(() => {});
    });
  }

  // Sign-out without Firebase on the page (e.g. demo mode).
  document.querySelectorAll("[data-firebase-logout]").forEach((f) =>
    f.addEventListener("submit", () => navigator.serviceWorker?.controller?.postMessage({ type: "logout" })));

  // Install button:
  //  - Android      → open the Biyet Event app on Google Play
  //  - iPhone/iPad  → show "Share → Add to Home Screen" (Apple has no install prompt)
  //  - everything else (desktop Chrome/Edge…) → install the PWA
  const PLAY_STORE = "https://play.google.com/store/apps/details?id=com.kenutek.giche";
  const ua = navigator.userAgent || "";
  const isAndroid = navigator.userAgentData?.platform === "Android" || /Android/i.test(ua);
  const isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  let deferred;
  const install = document.getElementById("install-btn");

  if (install && !standalone) {
    if (isAndroid) {
      install.textContent = "Get the app";
      install.hidden = false;
      install.addEventListener("click", () => { window.location.href = PLAY_STORE; });
    } else if (isIOS) {
      install.textContent = "Install app";
      install.hidden = false;
      install.addEventListener("click", () => {
        let tip = document.getElementById("ios-install-tip");
        if (!tip) {
          tip = document.createElement("div");
          tip.id = "ios-install-tip";
          tip.className = "ios-tip";
          tip.setAttribute("role", "dialog");
          tip.innerHTML = "<strong>Install Biyet Event</strong><span>Tap <b>Share</b> " +
            "<span aria-hidden='true'>⬆︎</span> at the bottom of Safari, then <b>Add to Home Screen</b>.</span>" +
            "<button type='button' class='linklike'>Got it</button>";
          tip.querySelector("button").addEventListener("click", () => tip.remove());
          document.body.appendChild(tip);
        }
      });
    } else {
      window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; install.hidden = false; });
      install.addEventListener("click", async () => {
        if (!deferred) return;
        deferred.prompt();
        await deferred.userChoice;
        deferred = null;
        install.hidden = true;
      });
    }
  }

  const banner = document.getElementById("offline-banner");
  const net = () => { if (banner) banner.hidden = navigator.onLine; };
  window.addEventListener("online", net); window.addEventListener("offline", net); net();

  // Copy buttons.
  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); const t = b.textContent; b.textContent = "Copied ✓"; setTimeout(() => (b.textContent = t), 1500); } catch (_) {}
  }));

  // Share sheet (falls back to copying the text).
  document.querySelectorAll("[data-share]").forEach((b) => b.addEventListener("click", async () => {
    const text = b.dataset.share;
    if (navigator.share) { try { await navigator.share({ title: b.dataset.shareTitle, text }); } catch (_) {} return; }
    try { await navigator.clipboard.writeText(text); b.textContent = "Invite copied ✓"; } catch (_) {}
  }));

  // Ticket: switch to "Scanned" as soon as staff scan it.
  const ticket = document.getElementById("ticket");
  if (ticket && ticket.dataset.status === "valid") {
    const timer = setInterval(async () => {
      if (!navigator.onLine || document.hidden) return;
      try {
        const r = await fetch(ticket.dataset.statusUrl, { credentials: "same-origin" });
        const { status } = await r.json();
        if (status !== "valid") { clearInterval(timer); location.reload(); }
      } catch (_) {}
    }, 15000);
  }
})();