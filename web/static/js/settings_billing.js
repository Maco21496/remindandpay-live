(function () {
  const trialDaysEl = document.getElementById('billing_trial_days');
  const trialEndsEl = document.getElementById('billing_trial_ends_at');
  const trialLeftEl = document.getElementById('billing_trial_days_left');
  const subStatusEl = document.getElementById('billing_subscription_status');
  const msgEl = document.getElementById('billing_msg');

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
      subStatusEl.textContent = data.subscription_status || 'none';
      if (msgEl) msgEl.textContent = '';
    } catch (err) {
      if (msgEl) msgEl.textContent = 'Failed to load billing status';
    }
  }

  window.addEventListener('billing_settings_tab_activated', loadBilling);
})();
