// /static/js/sms_settings.js
(function () {
  const $ = (id) => document.getElementById(id);

  const enabledSel = $("sms_enabled");
  const deliverySel = $("sms_delivery_mode");
  const bundleInput = $("sms_bundle_size");
  const creditsInput = $("sms_credits");
  const accountSidInput = $("sms_account_sid");
  const authTokenInput = $("sms_auth_token");
  const phoneNumberInput = $("sms_phone_number");
  const phoneSidInput = $("sms_phone_sid");
  const forwardingSel = $("sms_forwarding_enabled");
  const forwardToInput = $("sms_forward_to");
  const msg = $("sms_msg");
  const saveBtn = $("sms_save");
  const accountHint = $("sms_account_hint");
  const authHint = $("sms_auth_hint");

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

  function updateHints(data) {
    if (accountHint) {
      accountHint.textContent = data.has_account_sid
        ? "Account SID saved."
        : "Not configured yet.";
    }
    if (authHint) {
      authHint.textContent = data.has_auth_token
        ? "Auth token saved."
        : "Not configured yet.";
    }
  }

  async function loadSmsSettings() {
    try {
      const r = await fetch("/api/sms/settings", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      const data = await r.json();

      setSelectValue(enabledSel, data.enabled ? "true" : "false");
      setSelectValue(deliverySel, data.delivery_mode || "email");
      setSelectValue(forwardingSel, data.forwarding_enabled ? "true" : "false");

      if (bundleInput) bundleInput.value = String(data.bundle_size ?? 1000);
      if (creditsInput) creditsInput.value = String(data.credits_balance ?? 0);

      if (phoneNumberInput) phoneNumberInput.value = data.twilio_phone_number || "";
      if (phoneSidInput) phoneSidInput.value = data.twilio_phone_sid || "";
      if (forwardToInput) forwardToInput.value = data.forward_to_phone || "";

      updateHints(data);
      if (msg) msg.textContent = "";
    } catch {
      if (msg) msg.textContent = "Failed to load SMS settings.";
    }
  }

  async function saveSmsSettings() {
    if (msg) msg.textContent = "Saving…";
    try {
      const payload = {
        enabled: enabledSel ? enabledSel.value === "true" : undefined,
        delivery_mode: deliverySel ? deliverySel.value : undefined,
        bundle_size: bundleInput ? Number(bundleInput.value) : undefined,
        twilio_account_sid: accountSidInput ? accountSidInput.value : undefined,
        twilio_auth_token: authTokenInput ? authTokenInput.value : undefined,
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
      updateHints(data);
      if (msg) msg.textContent = "Saved.";
    } catch {
      if (msg) msg.textContent = "Save failed.";
    }
  }

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
