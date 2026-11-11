// Firebase Authentication (email + password, like the app) → Django session.
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import {
  getAuth, signInWithEmailAndPassword, createUserWithEmailAndPassword,
  sendPasswordResetEmail, sendEmailVerification, signOut,
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

const config = JSON.parse(document.getElementById("firebase-config").textContent);
const auth = getAuth(initializeApp(config));
const csrf = () => document.querySelector("[name=csrfmiddlewaretoken]")?.value || "";

document.querySelectorAll("[data-firebase-logout]").forEach((form) => {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    navigator.serviceWorker?.controller?.postMessage({ type: "logout" });
    try { await signOut(auth); } catch (_) { /* ignore */ }
    form.submit();
  });
});

const root = document.getElementById("auth-root");
if (root) {
  const errorEl = document.getElementById("auth-error");
  const form = document.getElementById("email-form");
  const signup = root.dataset.mode === "signup";
  const show = (msg) => { errorEl.textContent = msg; errorEl.hidden = false; };
  // Kreyòl by default for Haiti, until the person picks a language themselves.
  const lang = form.querySelector("[data-lang]");
  let langTouched = false;
  lang?.addEventListener("change", () => (langTouched = true));
  form.querySelector("[name=country]")?.addEventListener("change", (e) => {
    if (lang && !langTouched) lang.value = e.target.value === "HAITI" ? "Kreyòl" : "English";
  });
  const friendly = (err) => ({
    "auth/invalid-credential": "Email or password is incorrect.",
    "auth/wrong-password": "Email or password is incorrect.",
    "auth/user-not-found": "Email or password is incorrect.",
    "auth/email-already-in-use": "An account already exists with that email.",
    "auth/weak-password": "Password is too weak. Use at least 6 characters.",
    "auth/invalid-email": "Please enter a valid email address.",
    "auth/network-request-failed": "Network error — check your connection.",
  }[err.code] || err.message || "Something went wrong. Please try again.");

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body),
    });
    return res.json();
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.hidden = true;
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    const f = Object.fromEntries(new FormData(form));
    try {
      if (signup) {
        if (f.password !== f.confirm) throw { message: "Passwords don't match." };
        const cred = await createUserWithEmailAndPassword(auth, f.email.trim(), f.password);
        const data = await post(root.dataset.signupUrl, {
          idToken: await cred.user.getIdToken(true),
          firstName: f.firstName, lastName: f.lastName, country: f.country, prefLang: f.prefLang,
          referral: (f.referral || "").trim(),
        });
        if (!data.ok) {
          if (data.bad_referral) await cred.user.delete();  // same as the app: fix the code and retry
          throw { message: data.error };
        }
        try { await sendEmailVerification(cred.user); } catch (_) { /* optional */ }
        location.assign(data.redirect);
      } else {
        const cred = await signInWithEmailAndPassword(auth, f.email.trim(), f.password);
        const data = await post(root.dataset.sessionUrl, { idToken: await cred.user.getIdToken(true), next: root.dataset.next });
        if (!data.ok) throw { message: data.error || "Could not sign in." };
        location.assign(data.redirect);
      }
    } catch (err) {
      show(friendly(err));
      btn.disabled = false;
    }
  });

  document.getElementById("reset-btn")?.addEventListener("click", async () => {
    const email = form.email.value.trim();
    if (!email) return show("Enter your email first, then tap “Forgot password?”.");
    try { await sendPasswordResetEmail(auth, email); show("Reset link sent — check your inbox."); }
    catch (err) { show(friendly(err)); }
  });
}