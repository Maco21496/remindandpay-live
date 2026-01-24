// /static/js/sms_balance.js
(function () {
  const balanceChip = document.getElementById("sms_balance_chip");
  if (!balanceChip) return;

  function updateBalanceChip(isEnabled, balance) {
    const label = isEnabled ? String(balance ?? 0) : "Enable";
    balanceChip.textContent = `SMS credits: ${label}`;
    balanceChip.setAttribute(
      "aria-label",
      isEnabled ? `SMS credits ${label}` : "Enable SMS"
    );
    balanceChip.title = isEnabled ? "Open SMS activity" : "Enable SMS";
    balanceChip.href = isEnabled ? "/schedule#sms-activity" : "/settings#sms";
    balanceChip.dataset.balanceState = isEnabled ? "enabled" : "disabled";
  }

  async function loadBalance() {
    try {
      const r = await fetch("/api/sms/settings", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();
      updateBalanceChip(Boolean(data.enabled), data.credits_balance);
    } catch {
      updateBalanceChip(false, 0);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    updateBalanceChip(false, 0);
    loadBalance();
  });
})();
