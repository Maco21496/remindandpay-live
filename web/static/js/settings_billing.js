(function () {
  const trialDaysEl = document.getElementById('billing_trial_days');
  const trialEndsEl = document.getElementById('billing_trial_ends_at');
  const trialLeftEl = document.getElementById('billing_trial_days_left');
  const subStatusEl = document.getElementById('billing_subscription_status');
  const msgEl = document.getElementById('billing_msg');
  const subscribeBtn = document.getElementById('billing_subscribe_btn');
  const activeBadge = document.getElementById('billing_active_badge');
  const invRows = document.getElementById('billing_invoices_rows');
  const invEmpty = document.getElementById('billing_invoices_empty');
  const invRefresh = document.getElementById('billing_invoices_refresh');
  const invMsg = document.getElementById('billing_invoices_msg');

  function fmtMoney(amountMinor, currency) {
    if (amountMinor == null) return '-';
    const n = Number(amountMinor) / 100;
    const c = (currency || 'GBP').toUpperCase();
    return `${c} ${n.toFixed(2)}`;
  }

  function fmtDate(epochSec) {
    if (!epochSec) return '-';
    return new Date(Number(epochSec) * 1000).toLocaleString();
  }

  async function startSubscription() {
    if (msgEl) msgEl.textContent = 'Redirecting to Stripe…';
    if (subscribeBtn) subscribeBtn.disabled = true;
    try {
      const r = await fetch('/api/billing/stripe/subscription-checkout-session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`Failed (${r.status}) ${txt}`);
      }
      const data = await r.json();
      if (!data || !data.checkout_url) throw new Error('No checkout URL returned');
      window.location.assign(data.checkout_url);
    } catch (err) {
      if (msgEl) msgEl.textContent = 'Unable to start subscription checkout';
      if (subscribeBtn) subscribeBtn.disabled = false;
    }
  }

  async function loadInvoices() {
    if (!invRows) return;
    invRows.innerHTML = '';
    if (invMsg) invMsg.textContent = 'Loading…';
    if (invEmpty) invEmpty.style.display = 'none';
    try {
      const r = await fetch('/api/settings/billing/invoices?limit=30', { cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      const rows = Array.isArray(data.invoices) ? data.invoices : [];
      if (!rows.length) {
        if (invEmpty) invEmpty.style.display = 'block';
      } else {
        invRows.innerHTML = rows.map((x) => {
          const view = x.hosted_invoice_url ? `<a href="${x.hosted_invoice_url}" target="_blank" rel="noopener">View</a>` : '-';
          const pdf = x.invoice_pdf ? `<a href="${x.invoice_pdf}" target="_blank" rel="noopener">PDF</a>` : '-';
          const kindLabel = (x.kind === 'membership') ? 'subscription' : (x.kind || '-');
          return `<tr><td>${fmtDate(x.created)}</td><td>${kindLabel}</td><td>${x.status || '-'}</td><td>${fmtMoney(x.amount_due, x.currency)}</td><td>${view} ${pdf}</td></tr>`;
        }).join('');
      }
      if (invMsg) invMsg.textContent = '';
    } catch {
      if (invMsg) invMsg.textContent = 'Failed to load invoices';
      if (invEmpty) invEmpty.style.display = 'block';
    }
  }

  async function loadBilling() {
    if (!trialDaysEl) return;
    if (msgEl) msgEl.textContent = 'Loading billing…';
    try {
      const r = await fetch('/api/settings/billing', { cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();

      trialDaysEl.textContent = String(data.trial_days_assigned ?? 0);
      trialEndsEl.textContent = data.trial_ends_at || '-';
      trialLeftEl.textContent = String(data.trial_days_left ?? 0);
      const status = data.subscription_status || 'none';
      subStatusEl.textContent = status;

      const active = String(status).toLowerCase() === 'active';
      if (subscribeBtn) subscribeBtn.style.display = active ? 'none' : 'inline-flex';
      if (activeBadge) activeBadge.style.display = active ? 'inline-flex' : 'none';

      if (msgEl) msgEl.textContent = '';
    } catch (err) {
      if (msgEl) msgEl.textContent = 'Failed to load billing status';
    }
    await loadInvoices();
  }

  subscribeBtn?.addEventListener('click', startSubscription);
  invRefresh?.addEventListener('click', loadInvoices);
  window.addEventListener('billing_settings_tab_activated', loadBilling);
})();
