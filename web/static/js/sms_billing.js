(function () {
  const rowsEl = document.getElementById("sms_billing_rows");
  const emptyEl = document.getElementById("sms_billing_empty");
  const balanceEl = document.getElementById("sms_billing_balance");
  const prevBtn = document.getElementById("sms_billing_prev");
  const nextBtn = document.getElementById("sms_billing_next");
  const pageEl = document.getElementById("sms_billing_page");
  const refreshBtn = document.getElementById("sms_billing_refresh");

  const topupButtons = Array.from(document.querySelectorAll("[data-topup-package]"));
  const topupStatusEl = document.getElementById("sms_topup_status");

  function setTopupLoading(loading, message = "") {
    topupButtons.forEach((btn) => {
      btn.disabled = loading;
      if (loading && btn.dataset.originalText == null) {
        btn.dataset.originalText = btn.textContent || "";
      }
      if (!loading && btn.dataset.originalText != null) {
        btn.textContent = btn.dataset.originalText;
      }
    });
    if (topupStatusEl) topupStatusEl.textContent = message;
  }

  async function startTopup(packageKey, button) {
    setTopupLoading(true, "Redirecting to Stripe Checkout…");
    if (button) button.textContent = "Loading…";
    try {
      const resp = await fetch("/api/billing/stripe/checkout-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ package_key: String(packageKey) }),
      });
      if (!resp.ok) {
        let message = `Top-up failed (${resp.status})`;
        try {
          const err = await resp.json();
          if (err?.detail) message = String(err.detail);
        } catch {}
        throw new Error(message);
      }
      const data = await resp.json();
      if (!data?.checkout_url) throw new Error("No checkout URL returned");
      window.location.href = data.checkout_url;
    } catch (err) {
      setTopupLoading(false, err?.message || "Unable to start checkout");
    }
  }

  if (!rowsEl) return;

  const fmtDT = (iso) => (window.AppDate && AppDate.formatDateTime)
    ? AppDate.formatDateTime(iso)
    : (new Date(iso)).toLocaleString();

  let offset = 0;
  const limit = 50;

  function renderRow(entry) {
    const details = entry.details || {};
    const segments = details.segments ?? "-";
    const direction = entry.entry_type === "debit" ? "Outbound" : "Credit";
    const to = details.to || "-";
    const credits = entry.entry_type === "debit" ? `-${entry.amount}` : `+${entry.amount}`;
    const balance = entry.balance_after ?? "";

    return `
      <tr>
        <td>${fmtDT(entry.created_at)}</td>
        <td>${direction}</td>
        <td>${to}</td>
        <td>${segments}</td>
        <td style="text-align:right;">${credits}</td>
        <td style="text-align:right;">${balance}</td>
      </tr>
    `;
  }

  async function loadLedger() {
    rowsEl.innerHTML = "";
    emptyEl.style.display = "none";
    pageEl.textContent = "Loading…";
    try {
      const r = await fetch(`/api/sms/ledger?limit=${limit}&offset=${offset}`, { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      balanceEl.textContent = String(data.balance ?? 0);
      const entries = Array.isArray(data.entries) ? data.entries : [];
      if (!entries.length) {
        emptyEl.style.display = "block";
        pageEl.textContent = "";
      } else {
        rowsEl.innerHTML = entries.map(renderRow).join("");
        pageEl.textContent = `Showing ${offset + 1}-${offset + entries.length}`;
      }
      prevBtn.disabled = offset === 0;
      nextBtn.disabled = entries.length < limit;
    } catch {
      emptyEl.style.display = "block";
      pageEl.textContent = "Failed to load";
      balanceEl.textContent = "0";
      prevBtn.disabled = true;
      nextBtn.disabled = true;
    }
  }

  prevBtn?.addEventListener("click", () => {
    offset = Math.max(0, offset - limit);
    loadLedger();
  });

  nextBtn?.addEventListener("click", () => {
    offset += limit;
    loadLedger();
  });

  refreshBtn?.addEventListener("click", () => {
    loadLedger();
  });

  topupButtons.forEach((btn) => {
    btn.addEventListener("click", () => startTopup(btn.dataset.topupPackage, btn));
  });

  document.addEventListener("DOMContentLoaded", () => {
    loadLedger();
  });
})();
