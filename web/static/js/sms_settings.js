// /static/js/sms_settings.js
(function () {
  const $ = (id) => document.getElementById(id);

  const enabledSel = $("sms_enabled");
  const bundleInput = $("sms_bundle_size");
  const creditsInput = $("sms_credits");
  const dedicatedNumberValue = $("sms_dedicated_number_value");
  const nextRenewalValue = $("sms_next_renewal_value");
  const monthlyRenewalCostValue = $("sms_monthly_renewal_cost_value");
  const renewalOverdueRow = $("sms_renewal_overdue_row");
  const renewalOverdueValue = $("sms_renewal_overdue_value");
  const releasedRow = $("sms_released_row");
  const releasedValue = $("sms_released_value");
  const forwardingSel = $("sms_forwarding_enabled");
  const forwardToInput = $("sms_forward_to");
  const msg = $("sms_msg");
  const saveBtn = $("sms_save");
  const enableModal = $("sms_enable_modal");
  const enableClose = $("sms_enable_close");
  const enableCancel = $("sms_enable_cancel");
  const enableConfirm = $("sms_enable_confirm");
  const enableAccept = $("sms_enable_accept");
  const enableMsg = $("sms_enable_msg");
  const enableTerms = $("sms_enable_terms");
  const balanceChip = $("sms_balance_chip");
  const enabledHint = $("sms_enabled_hint");

  let currentEnabled = false;
  let pricingSnapshot = null;

  function setSelectValue(sel, val) {
    if (!sel) return;
    const v = String(val ?? "");
    const opts = Array.from(sel.options || []);
    opts.forEach((o) => (o.selected = false));
    const match = opts.find((o) => o.value === v);
    if (match) match.selected = true;
    sel.value = v;
    sel.setAttribute("value", v);
  }

  function setFieldsEnabled(isEnabled) {
    const toggle = (el, enabled) => {
      if (!el) return;
      el.disabled = !enabled;
    };
    toggle(forwardingSel, isEnabled);
    toggle(forwardToInput, isEnabled);
    toggle(saveBtn, isEnabled);
  }

  function lockEnabledToggle(isEnabled) {
    if (!enabledSel) return;
    enabledSel.disabled = isEnabled;
    if (enabledHint) {
      enabledHint.textContent = isEnabled
        ? "SMS is active and cannot be turned off."
        : "Turning on will provision your dedicated number.";
    }
  }

  function updateBalanceChip(isEnabled, balance, pauseThreshold) {
    if (!balanceChip) return;
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
  const fmtDT = (iso) => (window.AppDate && AppDate.formatDateTime)
    ? AppDate.formatDateTime(iso)
    : (iso ? (new Date(iso)).toLocaleString() : "-");

  function activateSmsTab() {
    const smsTabBtn = document.querySelector('#set_tabs .tab[data-tab="sms"]');
    smsTabBtn?.click();
  }

  function updateTermsList(snapshot) {
    if (!enableTerms) return;
    if (!snapshot) {
      enableTerms.innerHTML = "<li>Unable to load pricing.</li>";
      return;
    }
    enableTerms.innerHTML = `
      <li>${snapshot.sms_starting_credits} free SMS credits on activation.</li>
      <li>${snapshot.sms_monthly_number_cost} credits per month for your dedicated number.</li>
      <li>${snapshot.sms_send_cost} credits per SMS send.</li>
      <li>${snapshot.sms_forward_cost} credits per SMS forwarded reply.</li>
      <li>Number suspended after ${snapshot.sms_suspend_after_days} days of insufficient balance.</li>
    `;
  }

  function openEnableModal() {
    if (!enableModal) return;
    if (enableAccept) enableAccept.checked = false;
    if (enableMsg) enableMsg.textContent = "";
    enableModal.style.display = "block";
  }

  function closeEnableModal() {
    if (!enableModal) return;
    enableModal.style.display = "none";
  }

  async function readErrorDetail(response) {
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (payload && typeof payload === "object") {
      if (typeof payload.detail === "string" && payload.detail.trim()) return payload.detail.trim();
      if (typeof payload.message === "string" && payload.message.trim()) return payload.message.trim();
    }
    const text = await response.text().catch(() => "");
    return text.trim() || `Request failed (${response.status}).`;
  }

  async function loadPricing() {
    try {
      const r = await fetch("/api/sms/pricing", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      pricingSnapshot = await r.json();
      updateTermsList(pricingSnapshot);
    } catch {
      pricingSnapshot = null;
      updateTermsList(null);
    }
  }

  async function loadSmsSettings({ silent = false } = {}) {
    try {
      const r = await fetch("/api/sms/settings", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();

      currentEnabled = Boolean(data.enabled);
      setSelectValue(enabledSel, data.enabled ? "true" : "false");
      setSelectValue(forwardingSel, data.forwarding_enabled ? "true" : "false");

      if (bundleInput) bundleInput.value = String(data.bundle_size ?? 1000);
      if (creditsInput) creditsInput.value = String(data.credits_balance ?? 0);

      if (dedicatedNumberValue) dedicatedNumberValue.textContent = data.twilio_phone_number || "Not assigned";
      if (nextRenewalValue) nextRenewalValue.textContent = data.next_number_charge_at ? fmtDT(data.next_number_charge_at) : "Not scheduled";
      if (monthlyRenewalCostValue) monthlyRenewalCostValue.textContent = Number.isFinite(data.sms_monthly_number_cost) ? `${data.sms_monthly_number_cost} credits` : "-";
      if (renewalOverdueRow) renewalOverdueRow.style.display = data.past_due_since ? "" : "none";
      if (renewalOverdueValue) renewalOverdueValue.textContent = data.past_due_since ? fmtDT(data.past_due_since) : "-";
      const showReleased = Boolean(data.released_at) && !(data.enabled && data.twilio_phone_number);
      if (releasedRow) releasedRow.style.display = showReleased ? "" : "none";
      if (releasedValue) releasedValue.textContent = showReleased ? `Released on ${fmtDT(data.released_at)}${data.release_reason ? ` (${data.release_reason})` : ""}` : "-";
      if (forwardToInput) forwardToInput.value = data.forward_to_phone || "";
      setFieldsEnabled(currentEnabled);
      lockEnabledToggle(currentEnabled);
      updateBalanceChip(currentEnabled, data.credits_balance, data.credit_send_pause_threshold);
      if (msg) msg.textContent = "";
    } catch {
      if (msg && !silent) msg.textContent = "Failed to load SMS settings.";
    }
  }

  async function enableSms() {
    if (!enableAccept?.checked) {
      if (enableMsg) enableMsg.textContent = "Please accept the terms to continue.";
      return;
    }
    if (enableMsg) enableMsg.textContent = "Enabling…";
    try {
      if (!pricingSnapshot) {
        await loadPricing();
      }
      const r = await fetch("/api/sms/enable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          accepted: true,
          terms_version: "v1",
          pricing_snapshot: pricingSnapshot,
        }),
      });
      if (!r.ok) {
        const detail = await readErrorDetail(r);
        throw new Error(detail);
      }
      const data = await r.json();
      currentEnabled = Boolean(data.enabled);
      setSelectValue(enabledSel, data.enabled ? "true" : "false");
      if (creditsInput) creditsInput.value = String(data.credits_balance ?? 0);
      setFieldsEnabled(currentEnabled);
      lockEnabledToggle(currentEnabled);
      updateBalanceChip(currentEnabled, data.credits_balance, data.credit_send_pause_threshold);
      closeEnableModal();
      if (msg) msg.textContent = "SMS enabled.";
    } catch (err) {
      if (enableMsg) {
        const detail = err?.message || "Enable failed.";
        enableMsg.textContent = detail;
        if (/top up|deposit|insufficient|need at least/i.test(detail)) {
          const link = document.createElement("a");
          link.href = "/sms_billing";
          link.textContent = "Add SMS credits";
          link.style.marginLeft = "8px";
          enableMsg.appendChild(link);
        }
      }
      setSelectValue(enabledSel, "false");
      currentEnabled = false;
      setFieldsEnabled(false);
      lockEnabledToggle(false);
      updateBalanceChip(false, 0, 100);
    }
  }

  async function saveSmsSettings() {
    if (msg) msg.textContent = "Saving…";
    try {
      const payload = {
        enabled: enabledSel ? enabledSel.value === "true" : undefined,
        bundle_size: bundleInput ? Number(bundleInput.value) : undefined,
        forwarding_enabled: forwardingSel ? forwardingSel.value === "true" : undefined,
        forward_to_phone: forwardToInput ? forwardToInput.value : undefined,
      };

      const r = await fetch("/api/sms/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const t = await r.text().catch(() => "");
        throw new Error(`Save failed ${r.status} ${t}`);
      }
      const data = await r.json();
      if (creditsInput) creditsInput.value = String(data.credits_balance ?? 0);
      updateBalanceChip(Boolean(data.enabled), data.credits_balance, data.credit_send_pause_threshold);
      if (msg) msg.textContent = "Saved.";
    } catch {
      if (msg) msg.textContent = "Save failed.";
    }
  }

  enabledSel?.addEventListener("change", () => {
    const wantEnabled = enabledSel.value === "true";
    if (wantEnabled && !currentEnabled) {
      setSelectValue(enabledSel, "false");
      loadPricing();
      openEnableModal();
    } else {
      currentEnabled = wantEnabled;
      setFieldsEnabled(currentEnabled);
    }
  });

  enableConfirm?.addEventListener("click", enableSms);
  enableCancel?.addEventListener("click", () => {
    closeEnableModal();
  });
  enableClose?.addEventListener("click", () => {
    closeEnableModal();
  });

  saveBtn?.addEventListener("click", saveSmsSettings);

  window.addEventListener("sms_settings_tab_activated", () => {
    loadSmsSettings();
  });

  document.addEventListener("DOMContentLoaded", () => {
    updateBalanceChip(false, 0);
    balanceChip?.addEventListener("click", (e) => {
      if (balanceChip.dataset.balanceState === "disabled") {
        e.preventDefault();
        activateSmsTab();
        loadPricing();
        openEnableModal();
      }
    });
    loadSmsSettings({ silent: true });
    if (document.getElementById("tab_sms")?.style.display === "block") {
      loadSmsSettings();
    }
  });
})();
