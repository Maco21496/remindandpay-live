(function () {
  const rowsEl = document.getElementById("sms_billing_rows");
  const emptyEl = document.getElementById("sms_billing_empty");
  const balanceEl = document.getElementById("sms_billing_balance");
  const prevBtn = document.getElementById("sms_billing_prev");
  const nextBtn = document.getElementById("sms_billing_next");
  const pageEl = document.getElementById("sms_billing_page");
  const refreshBtn = document.getElementById("sms_billing_refresh");

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

  document.addEventListener("DOMContentLoaded", () => {
    loadLedger();
  });
})();
