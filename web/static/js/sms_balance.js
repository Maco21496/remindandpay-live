// /static/js/sms_balance.js
(function () {
  const balanceChip = document.getElementById("sms_balance_chip");
  if (!balanceChip) return;

  function updateBalanceChip(isEnabled, balance, pauseThreshold) {
    const label = isEnabled ? String(balance ?? 0) : "Enable";
    const threshold = Number(pauseThreshold ?? 100);
    const lowCredits = isEnabled && Number(balance ?? 0) < threshold;
    balanceChip.textContent = `SMS credits: ${label}`;
    balanceChip.setAttribute(
      "aria-label",
      isEnabled ? `SMS credits ${label}` : "Enable SMS"
    );
    balanceChip.title = isEnabled ? "Open SMS billing" : "Enable SMS";
    balanceChip.href = isEnabled ? "/sms_billing" : "/settings#sms";
    balanceChip.dataset.balanceState = isEnabled ? "enabled" : "disabled";
    balanceChip.dataset.alertState = lowCredits ? "low" : "normal";
    balanceChip.style.borderColor = lowCredits ? "#dc2626" : "#e5e7eb";
    balanceChip.style.color = lowCredits ? "#b91c1c" : "";
    balanceChip.style.fontWeight = lowCredits ? "700" : "";
    if (lowCredits) {
      balanceChip.textContent = `SMS credits: ${label} • 1 alert`;
      balanceChip.title = `SMS sending paused below ${threshold} credits`;
    }
  }

  async function loadBalance() {
    try {
      const r = await fetch("/api/sms/settings", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      updateBalanceChip(Boolean(data.enabled), data.credits_balance, data.credit_send_pause_threshold);
    } catch {
      updateBalanceChip(false, 0, 100);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    updateBalanceChip(false, 0, 100);
    loadBalance();
  });
})();
