// /static/js/sms_settings.js
(function () {
  const $ = (id) => document.getElementById(id);

  const enabledSel = $("sms_enabled");
  const bundleInput = $("sms_bundle_size");
  const creditsInput = $("sms_credits");
  const phoneNumberInput = $("sms_phone_number");
  const phoneSidInput = $("sms_phone_sid");
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
    toggle(bundleInput, isEnabled);
    toggle(forwardingSel, isEnabled);
    toggle(forwardToInput, isEnabled);
    toggle(saveBtn, isEnabled);
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

  async function loadSmsSettings() {
    try {
      const r = await fetch("/api/sms/settings", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();

      currentEnabled = Boolean(data.enabled);
      setSelectValue(enabledSel, data.enabled ? "true" : "false");
      setSelectValue(forwardingSel, data.forwarding_enabled ? "true" : "false");

      if (bundleInput) bundleInput.value = String(data.bundle_size ?? 1000);
      if (creditsInput) creditsInput.value = String(data.credits_balance ?? 0);

      if (phoneNumberInput) phoneNumberInput.value = data.twilio_phone_number || "";
      if (phoneSidInput) phoneSidInput.value = data.twilio_phone_sid || "";
      if (forwardToInput) forwardToInput.value = data.forward_to_phone || "";
      setFieldsEnabled(currentEnabled);
      if (msg) msg.textContent = "";
    } catch {
      if (msg) msg.textContent = "Failed to load SMS settings.";
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
        const t = await r.text().catch(() => "");
        throw new Error(`Enable failed ${r.status} ${t}`);
      }
      const data = await r.json();
      currentEnabled = Boolean(data.enabled);
      setSelectValue(enabledSel, data.enabled ? "true" : "false");
      if (creditsInput) creditsInput.value = String(data.credits_balance ?? 0);
      setFieldsEnabled(currentEnabled);
      closeEnableModal();
      if (msg) msg.textContent = "SMS enabled.";
    } catch {
      if (enableMsg) enableMsg.textContent = "Enable failed.";
      setSelectValue(enabledSel, "false");
      currentEnabled = false;
      setFieldsEnabled(false);
    }
  }

  async function saveSmsSettings() {
    if (msg) msg.textContent = "Saving…";
    try {
      const payload = {
        enabled: enabledSel ? enabledSel.value === "true" : undefined,
        bundle_size: bundleInput ? Number(bundleInput.value) : undefined,
        twilio_phone_number: phoneNumberInput ? phoneNumberInput.value : undefined,
        twilio_phone_sid: phoneSidInput ? phoneSidInput.value : undefined,
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
    if (document.getElementById("tab_sms")?.style.display === "block") {
      loadSmsSettings();
    }
  });
})();
