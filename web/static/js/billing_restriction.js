(function () {
  async function loadBillingRestrictionBanner() {
    try {
      const r = await fetch('/api/settings/billing', { cache: 'no-store' });
      if (!r.ok) return;
      const b = await r.json();
      const status = String(b.subscription_status || '').toLowerCase();
      const expired = status === 'trial_expired' || status === 'past_due' || status === 'canceled';
      if (!expired) return;

      const bar = document.createElement('div');
      bar.style.cssText = 'background:#fff4e5;border:1px solid #f5d7a1;color:#7a4b00;padding:10px 14px;margin:10px auto;max-width:1200px;border-radius:10px;display:flex;justify-content:space-between;gap:10px;align-items:center;';
      bar.innerHTML = '<span>Your trial has ended and sending is restricted. Activate membership to re-enable email and SMS sending.</span><a href="/settings#billing" style="font-weight:600;text-decoration:underline;">Go to Billing</a>';

      const host = document.getElementById('customer-tabbar');
      if (host && host.parentNode) {
        host.parentNode.insertBefore(bar, host.nextSibling);
      }
    } catch (e) {
      // ignore
    }
  }

  document.addEventListener('DOMContentLoaded', loadBillingRestrictionBanner);
})();
