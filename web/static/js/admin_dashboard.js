(() => {
  const params = new URLSearchParams(window.location.search);
  const activeTab = params.get("tab") || "users";

  const sections = document.querySelectorAll("[data-admin-section]");
  const navLinks = document.querySelectorAll("[data-admin-tab]");

  function setActive(tab) {
    sections.forEach((section) => {
      section.classList.toggle("active", section.dataset.adminSection === tab);
    });
    navLinks.forEach((link) => {
      link.classList.toggle("active", link.dataset.adminTab === tab);
    });
  }

  setActive(activeTab);

  const startingCredits = document.getElementById("sms-starting-credits");
  const monthlyNumberCost = document.getElementById("sms-monthly-number-cost");
  const sendCost = document.getElementById("sms-send-cost");
  const forwardCost = document.getElementById("sms-forward-cost");
  const suspendAfterDays = document.getElementById("sms-suspend-after-days");
  const saveBtn = document.getElementById("sms-pricing-save");
  const msg = document.getElementById("sms-pricing-msg");
  const webhookRows = document.getElementById("sms-webhooks-rows");
  const webhookEmpty = document.getElementById("sms-webhooks-empty");
  const webhookMsg = document.getElementById("sms-webhooks-msg");
  const webhookRefresh = document.getElementById("sms-webhooks-refresh");
  const notificationsRows = document.getElementById("notifications-rows");
  const notificationsMsg = document.getElementById("notifications-msg");
  const notificationsRefresh = document.getElementById("notifications-refresh");
  const notificationsLogRows = document.getElementById("notifications-log-rows");
  const notificationsLogMsg = document.getElementById("notifications-log-msg");
  const notificationsLogRefresh = document.getElementById("notifications-log-refresh");

  async function loadPricing() {
    if (!startingCredits) return;
    if (msg) msg.textContent = "Loading pricing…";
    try {
      const response = await fetch("/api/admin/sms_pricing", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Failed to load pricing (${response.status})`);
      }
      const data = await response.json();
      startingCredits.value = String(data.sms_starting_credits ?? 0);
      monthlyNumberCost.value = String(data.sms_monthly_number_cost ?? 0);
      sendCost.value = String(data.sms_send_cost ?? 0);
      forwardCost.value = String(data.sms_forward_cost ?? 0);
      suspendAfterDays.value = String(data.sms_suspend_after_days ?? 0);
      if (msg) msg.textContent = "";
    } catch (error) {
      if (msg) msg.textContent = "Failed to load SMS pricing.";
      console.error(error);
    }
  }

  async function savePricing() {
    if (msg) msg.textContent = "Saving…";
    try {
      const payload = {
        sms_starting_credits: Number(startingCredits?.value ?? 0),
        sms_monthly_number_cost: Number(monthlyNumberCost?.value ?? 0),
        sms_send_cost: Number(sendCost?.value ?? 0),
        sms_forward_cost: Number(forwardCost?.value ?? 0),
        sms_suspend_after_days: Number(suspendAfterDays?.value ?? 0),
      };
      const response = await fetch("/api/admin/sms_pricing", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(`Failed to save pricing (${response.status})`);
      }
      if (msg) msg.textContent = "Pricing saved.";
    } catch (error) {
      if (msg) msg.textContent = "Failed to save SMS pricing.";
      console.error(error);
    }
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", savePricing);
  }

  if (activeTab === "sms") {
    loadPricing();
  }

  function renderWebhookRow(entry) {
    const payload = entry.payload || {};
    const status = payload.MessageStatus || payload.SmsStatus || "-";
    const toNumber = payload.To || "-";
    const segments = payload.NumSegments || "-";
    return `
      <tr>
        <td>${entry.created_at || ""}</td>
        <td>${entry.kind || ""}</td>
        <td>${status}</td>
        <td>${entry.message_sid || ""}</td>
        <td>${toNumber}</td>
        <td>${segments}</td>
      </tr>
    `;
  }

  async function loadWebhookLogs() {
    if (!webhookRows) return;
    webhookRows.innerHTML = "";
    webhookEmpty.style.display = "none";
    if (webhookMsg) webhookMsg.textContent = "Loading…";
    try {
      const response = await fetch("/admin/sms_webhooks?limit=200", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Failed to load logs (${response.status})`);
      }
      const data = await response.json();
      const logs = Array.isArray(data.logs) ? data.logs : [];
      if (!logs.length) {
        webhookEmpty.style.display = "block";
      } else {
        webhookRows.innerHTML = logs.map(renderWebhookRow).join("");
      }
      if (webhookMsg) webhookMsg.textContent = "";
    } catch (error) {
      if (webhookMsg) webhookMsg.textContent = "Failed to load webhook logs.";
      console.error(error);
    }
  }

  if (webhookRefresh) {
    webhookRefresh.addEventListener("click", loadWebhookLogs);
  }

  if (activeTab === "sms-webhooks") {
    loadWebhookLogs();
  }

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderNotificationRow(row) {
    return `
      <tr data-template-id="${row.id}">
        <td>${esc(row.event_key)}</td>
        <td><input type="checkbox" data-field="enabled" ${row.enabled ? "checked" : ""}></td>
        <td><input type="number" min="0" data-field="cooldown_minutes" value="${Number(row.cooldown_minutes ?? 0)}"></td>
        <td><input type="text" data-field="from_name" value="${esc(row.from_name || "")}"></td>
        <td><input type="text" data-field="from_email" value="${esc(row.from_email || "")}" placeholder="(env default)"></td>
        <td><input type="text" data-field="subject_template" value="${esc(row.subject_template || "")}"></td>
        <td><textarea data-field="body_template" rows="3" style="min-width:260px;">${esc(row.body_template || "")}</textarea></td>
        <td>
          <button class="btn btn-primary" type="button" data-action="save-notification">Save</button>
          <button class="btn" type="button" data-action="test-notification">Test</button>
        </td>
      </tr>
    `;
  }

  async function loadNotifications() {
    if (!notificationsRows) return;
    notificationsRows.innerHTML = "";
    if (notificationsMsg) notificationsMsg.textContent = "Loading…";
    try {
      const response = await fetch("/admin/notifications/templates", { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load templates (${response.status})`);
      const data = await response.json();
      const templates = Array.isArray(data.templates) ? data.templates : [];
      notificationsRows.innerHTML = templates.map(renderNotificationRow).join("");
      if (notificationsMsg) notificationsMsg.textContent = "";
    } catch (error) {
      if (notificationsMsg) notificationsMsg.textContent = "Failed to load notification templates.";
      console.error(error);
    }
  }

  async function saveNotificationRow(rowEl) {
    const id = Number(rowEl?.dataset?.templateId || 0);
    if (!id) return;
    const payload = {};
    rowEl.querySelectorAll("[data-field]").forEach((field) => {
      const key = field.dataset.field;
      if (!key) return;
      if (field.type === "checkbox") {
        payload[key] = field.checked ? 1 : 0;
      } else if (field.type === "number") {
        payload[key] = Number(field.value || 0);
      } else {
        payload[key] = field.value;
      }
    });
    if (notificationsMsg) notificationsMsg.textContent = `Saving ${id}…`;
    try {
      const response = await fetch(`/admin/notifications/templates/${id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(`Failed to update template (${response.status})`);
      if (notificationsMsg) notificationsMsg.textContent = "Template saved.";
    } catch (error) {
      if (notificationsMsg) notificationsMsg.textContent = "Failed to save template.";
      console.error(error);
    }
  }

  async function testNotificationRow(rowEl) {
    const id = Number(rowEl?.dataset?.templateId || 0);
    if (!id) return;
    if (notificationsMsg) notificationsMsg.textContent = `Queueing test ${id}…`;
    try {
      const response = await fetch(`/admin/notifications/templates/${id}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!response.ok) throw new Error(`Failed to queue test (${response.status})`);
      if (notificationsMsg) notificationsMsg.textContent = "Test notification queued.";
      await loadNotificationLogs();
    } catch (error) {
      if (notificationsMsg) notificationsMsg.textContent = "Failed to queue test notification.";
      console.error(error);
    }
  }

  function renderNotificationLogRow(row) {
    return `
      <tr>
        <td>${esc(row.created_at || "")}</td>
        <td>${esc(row.user_email || "")}</td>
        <td>${esc(row.event_key || "")}</td>
        <td>${esc(row.status || "")}</td>
        <td>${esc(row.dedupe_key || "")}</td>
      </tr>
    `;
  }

  async function loadNotificationLogs() {
    if (!notificationsLogRows) return;
    notificationsLogRows.innerHTML = "";
    if (notificationsLogMsg) notificationsLogMsg.textContent = "Loading…";
    try {
      const response = await fetch("/admin/notifications/log?limit=100", { cache: "no-store" });
      if (!response.ok) throw new Error(`Failed to load logs (${response.status})`);
      const data = await response.json();
      const logs = Array.isArray(data.logs) ? data.logs : [];
      notificationsLogRows.innerHTML = logs.map(renderNotificationLogRow).join("");
      if (notificationsLogMsg) notificationsLogMsg.textContent = "";
    } catch (error) {
      if (notificationsLogMsg) notificationsLogMsg.textContent = "Failed to load notification log.";
      console.error(error);
    }
  }

  if (notificationsRefresh) {
    notificationsRefresh.addEventListener("click", loadNotifications);
  }
  if (notificationsRows) {
    notificationsRows.addEventListener("click", (event) => {
      const button = event.target.closest("[data-action='save-notification']");
      const testButton = event.target.closest("[data-action='test-notification']");
      const row = (button || testButton)?.closest("tr[data-template-id]");
      if (!row) return;
      if (button) saveNotificationRow(row);
      if (testButton) testNotificationRow(row);
    });
  }
  if (notificationsLogRefresh) {
    notificationsLogRefresh.addEventListener("click", loadNotificationLogs);
  }
  if (activeTab === "notifications") {
    loadNotifications();
    loadNotificationLogs();
  }
})();
