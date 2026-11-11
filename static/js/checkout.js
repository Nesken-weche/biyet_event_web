// Checkout: wallet/points are settled on the server; a card part is confirmed
// here with Stripe.js, then verified by the server before the order is saved.
(function () {
  const btn = document.getElementById("pay-btn");
  if (!btn) return;
  const statusEl = document.getElementById("pay-status");
  const dialog = document.getElementById("card-dialog");
  const csrf = document.querySelector("[name=csrfmiddlewaretoken]").value;
  const label = btn.textContent;

  const post = (url) => fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf } })
    .then((r) => r.json());
  const busy = (text) => { btn.disabled = true; btn.textContent = text; };
  const idle = (msg) => { btn.disabled = false; btn.textContent = label; if (msg) statusEl.textContent = msg; };

  btn.addEventListener("click", async () => {
    statusEl.textContent = "";
    busy("Holding your tickets…");
    let data;
    try { data = await post(btn.dataset.start); } catch (_) { return idle("No connection. You were not charged."); }
    if (!data.ok) return idle(data.message);
    if (!data.needs_card) return location.assign(data.redirect);
    busy("Waiting for your card…");
    payByCard(data);
  });

  function payByCard(data) {
    if (!window.Stripe) { post(btn.dataset.cancel); return idle("Card form didn't load. You were not charged."); }
    const stripe = window.Stripe(data.publishable_key);
    const elements = stripe.elements({ appearance: { theme: "night" } });
    const card = elements.create("card", { style: { base: { color: "#ece8f4", fontSize: "16px" } } });
    document.getElementById("card-amount").textContent = data.amount;
    const errEl = document.getElementById("card-error");
    errEl.textContent = "";
    dialog.showModal();
    card.mount("#card-element");
    const payBtn = document.getElementById("card-pay");
    const cancelBtn = document.getElementById("card-cancel");

    const cleanup = () => { card.destroy(); dialog.close(); payBtn.onclick = cancelBtn.onclick = null; };
    cancelBtn.onclick = async () => {
      cleanup();
      const r = await post(btn.dataset.cancel);
      idle(r.message);
    };
    payBtn.onclick = async () => {
      payBtn.disabled = true;
      const { error } = await stripe.confirmCardPayment(data.client_secret, { payment_method: { card } });
      payBtn.disabled = false;
      if (error) { errEl.textContent = error.message; return; }  // stay open so they can fix the card
      cleanup();
      busy("Confirming your tickets…");
      const r = await post(btn.dataset.complete);
      if (r.ok) location.assign(r.redirect); else idle(r.message);
    };
  }
})();
