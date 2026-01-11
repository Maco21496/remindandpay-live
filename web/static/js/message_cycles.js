// FINAL VERSION OF /static/js/message_cycles.js
(function () {
  // ----- DOM refs (match templates/message_templates.html) -----
  // left column
  const planListEl        = document.getElementById("cycle_list");
  const deletePlanBtn     = document.getElementById("cycle_delete_btn");
  const newPlanBtn        = document.getElementById("cycle_new_btn");

  // right column
  const planNameEl        = document.getElementById("steps_cycle_name");
  const planDescEl        = document.getElementById("steps_cycle_desc");
  const triggerListEl     = document.getElementById("steps_list");
  const addTriggerBtn     = document.getElementById("step_add_btn");

  // modals (IDs from the HTML)
  const modalAdd          = document.getElementById("modal_add_step");
  const addOffsetInput    = document.getElementById("add_offset_days");
  const addTemplateSelect = document.getElementById("add_template_key");
  const addErrorEl        = document.getElementById("add_error");
  const addSaveBtn        = document.getElementById("add_step_save");

  const modalEdit         = document.getElementById("modal_edit_step");
  const editOffsetInput   = document.getElementById("edit_offset_days");
  const editTemplateSel   = document.getElementById("edit_template_key");
  const editErrorEl       = document.getElementById("edit_error");
  const editSaveBtn       = document.getElementById("edit_step_save");

  // ----- state -----
  let plans        = [];   // GET /api/chasing_plans
  let activePlanId = null;
  let activePlan   = null; // GET /api/chasing_plans/:id
  let templates    = [];   // active email templates for dropdowns
  let editingTriggerId = null;

  // ----- helpers -----
  async function safeFetchJson(url, opts = {}) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      throw new Error(`${opts.method || "GET"} ${url} failed ${r.status}: ${body}`);
    }
    return await r.json();
  }

  function escapeHtml(str) {
    return String(str || "").replace(/[<>&"]/g, (s) => {
      const map = { "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" };
      return map[s] || s;
    });
  }

  function openModal(el) { el?.classList.remove("hidden"); }
  function closeModal(el) { el?.classList.add("hidden"); }

  function truncate(str, maxLen) {
    const s = String(str || "");
    return s.length <= maxLen ? s : s.slice(0, maxLen - 1) + "…";
  }

  // ----- render: left sidebar list of plans -----
  function renderPlansSidebar() {
    if (!planListEl) return;

    planListEl.innerHTML = plans.map((plan) => {
      const offsets = Array.isArray(plan.offsets) ? plan.offsets : [];
      const triggerCount = Number(plan.step_count || 0);
      return `
        <button
          class="mc-cycle-row ${String(plan.id) === String(activePlanId) ? "is-active" : ""}"
          data-id="${plan.id}">
          <div class="mc-cycle-row-top">
            <div class="mc-cycle-row-name">${escapeHtml(plan.name || "")}</div>
            <div class="mc-cycle-row-menu">⋯</div>
          </div>
          <div class="mc-cycle-row-bottom">
            <span class="mc-cycle-row-meta">
              ${triggerCount} trigger${triggerCount === 1 ? "" : "s"}${
                offsets.length ? ` • days ${escapeHtml(offsets.join(", "))}` : ""
              }
            </span>
          </div>
        </button>
      `;
    }).join("");

    const enabled = activePlanId != null;
    if (deletePlanBtn) deletePlanBtn.disabled = !enabled;
    if (addTriggerBtn) addTriggerBtn.disabled = !enabled;
  }

  // ----- render: right panel with triggers for the active plan -----
  function renderTriggersPanel() {
    if (!planNameEl || !planDescEl || !triggerListEl) return;

    if (!activePlan) {
      planNameEl.textContent = "No plan selected";
      planDescEl.textContent = "Select a plan on the left.";
      triggerListEl.innerHTML = "";
      return;
    }

    const triggerArr = activePlan.steps || [];
    planNameEl.textContent = activePlan.name || "(unnamed plan)";
    planDescEl.textContent = `${triggerArr.length} trigger(s) in this plan`;

    triggerListEl.innerHTML = triggerArr.map((tr) => {
      const trigNum = tr.order_index || 0;
      const dayLabel = `Trigger ${trigNum} — ${tr.offset_days} days overdue`;
      const subject  = tr.template_subject || "(no subject)";
      const tkey     = tr.template_key || "(no key)";
      const bodyPreviewRaw = tr.template_body_text || "";
      const bodyPreview    = bodyPreviewRaw ? truncate(bodyPreviewRaw, 400) : "(no body)";
      const toneChip = tr.template_tag ? `<span class="mc-chip mc-chip--tone">${escapeHtml(tr.template_tag)}</span>` : "";
      const chanChip = `<span class="mc-chip mc-chip--chan">${escapeHtml(tr.channel || "email")}</span>`;

      return `
        <div class="mc-step-card" data-trigger-id="${tr.id}">
          <div class="mc-step-head js-trigger-toggle" data-trigger-id="${tr.id}">
            <div class="mc-step-head-left">
              <div class="mc-step-title">
                <span class="mc-step-arrow">›</span>
                <span>${escapeHtml(dayLabel)}</span>
              </div>
              <div class="mc-step-chips">${chanChip}${toneChip}</div>
            </div>
            <div class="mc-step-head-right">
              <button class="mc-btn mc-btn--ghost mc-btn--xs js-trigger-replace" data-trigger-id="${tr.id}">Replace</button>
              <button class="mc-btn mc-btn--ghost mc-btn--danger mc-btn--xs js-trigger-del" data-trigger-id="${tr.id}">Delete</button>
            </div>
          </div>
          <div class="mc-step-body">
            <div class="mc-step-row">
              <div class="mc-step-label">Subject:</div>
              <div class="mc-step-value">${escapeHtml(subject)}</div>
            </div>
            <div class="mc-step-row">
              <div class="mc-step-label">Template key:</div>
              <div class="mc-step-value">${escapeHtml(tkey)}</div>
            </div>
            <div class="mc-step-row">
              <div class="mc-step-label">Message preview:</div>
              <div class="mc-step-value mc-step-preview">${escapeHtml(bodyPreview)}</div>
            </div>
          </div>
        </div>
      `;
    }).join("");

    // collapsed by default
    triggerListEl.querySelectorAll(".mc-step-body").forEach((body) => {
      body.style.display = "none";
    });
  }

  // expand/collapse trigger preview
  triggerListEl?.addEventListener("click", (e) => {
    const head = e.target.closest(".js-trigger-toggle[data-trigger-id]");
    if (!head) return;
    const card  = head.closest(".mc-step-card");
    const body  = card?.querySelector(".mc-step-body");
    const arrow = card?.querySelector(".mc-step-arrow");
    if (!body) return;
    const isOpen = body.style.display !== "none";
    body.style.display = isOpen ? "none" : "block";
    if (arrow) arrow.textContent = isOpen ? "›" : "⌄";
  });

  // ----- data loaders -----
  async function loadPlansList() {
    plans = await safeFetchJson("/api/chasing_plans");
    renderPlansSidebar();
  }

  async function loadPlanDetail(planId) {
    activePlan = await safeFetchJson(`/api/chasing_plans/${encodeURIComponent(planId)}`);
    renderTriggersPanel();
  }

  async function loadTemplates() {
    templates = await safeFetchJson("/api/reminder_templates?channel=email&active=true");
    const opts = templates.map((t) => {
      const subj = t.subject || "(no subject)";
      return `<option value="${escapeHtml(t.key)}">${escapeHtml(subj)} — ${escapeHtml(t.key)}</option>`;
    }).join("");
    if (addTemplateSelect) addTemplateSelect.innerHTML = `<option value="">(choose template)</option>` + opts;
    if (editTemplateSel)  editTemplateSel.innerHTML  = `<option value="">(choose template)</option>` + opts;
  }

  // ----- sidebar click: choose a plan -----
  planListEl?.addEventListener("click", async (e) => {
    const row = e.target.closest(".mc-cycle-row[data-id]");
    if (!row) return;
    activePlanId = row.getAttribute("data-id");
    renderPlansSidebar();
    await loadPlanDetail(activePlanId);
    if (deletePlanBtn) deletePlanBtn.disabled = false;
    if (addTriggerBtn) addTriggerBtn.disabled = false;
  });

  // ----- new plan -----
  newPlanBtn?.addEventListener("click", async () => {
    const name = prompt("Name for new plan?");
    if (!name || !name.trim()) return;
    const body = { name: name.trim() };
    try {
      const created = await safeFetchJson("/api/chasing_plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      await loadPlansList();
      activePlanId = created.id;
      renderPlansSidebar();
      await loadPlanDetail(activePlanId);
      if (deletePlanBtn) deletePlanBtn.disabled = false;
      if (addTriggerBtn) addTriggerBtn.disabled = false;
    } catch (err) {
      alert("Failed to create plan");
      console.error(err);
    }
  });

  // ----- delete plan -----
  deletePlanBtn?.addEventListener("click", async () => {
    if (!activePlanId) return;
    if (!confirm("Delete this entire plan (all triggers)?")) return;
    try {
      await safeFetchJson(`/api/chasing_plans/${encodeURIComponent(activePlanId)}`, { method: "DELETE" });
      activePlanId = null;
      activePlan   = null;
      await loadPlansList();
      renderTriggersPanel();
      if (deletePlanBtn) deletePlanBtn.disabled = true;
      if (addTriggerBtn) addTriggerBtn.disabled = true;
    } catch (err) {
      alert("Failed to delete plan");
      console.error(err);
    }
  });

  // ----- open Add Trigger modal -----
  addTriggerBtn?.addEventListener("click", () => {
    if (!activePlanId || !modalAdd) return;
    if (addOffsetInput)    addOffsetInput.value = "";
    if (addTemplateSelect) addTemplateSelect.value = "";
    if (addErrorEl) { addErrorEl.classList.add("hidden"); addErrorEl.textContent = ""; }
    openModal(modalAdd);
  });

  // close Add Trigger modal
  modalAdd?.addEventListener("click", (e) => {
    if (e.target.matches("[data-close-add]")) closeModal(modalAdd);
  });

  // save Add Trigger
  addSaveBtn?.addEventListener("click", async () => {
    if (!activePlanId) return;
    const offset = Number(addOffsetInput?.value || 0);
    const tkey   = addTemplateSelect?.value || "";
    if (!tkey) {
      if (addErrorEl) { addErrorEl.textContent = "Choose a template."; addErrorEl.classList.remove("hidden"); }
      return;
    }
    if (addErrorEl) addErrorEl.classList.add("hidden");
    try {
      await safeFetchJson(`/api/chasing_plans/${encodeURIComponent(activePlanId)}/steps`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ offset_days: offset, template_key: tkey, channel: "email" }),
      });
      closeModal(modalAdd);
      await loadPlanDetail(activePlanId);
    } catch (err) {
      console.error(err);
      if (addErrorEl) { addErrorEl.textContent = err.message || "Failed to add trigger."; addErrorEl.classList.remove("hidden"); }
    }
  });

  // ----- trigger actions (Replace / Delete) -----
  triggerListEl?.addEventListener("click", async (e) => {
    const replBtn = e.target.closest(".js-trigger-replace[data-trigger-id]");
    if (replBtn) { openEditTriggerModal(replBtn.getAttribute("data-trigger-id")); return; }
    const delBtn = e.target.closest(".js-trigger-del[data-trigger-id]");
    if (delBtn) {
      const tid = delBtn.getAttribute("data-trigger-id");
      if (!confirm("Delete this trigger?")) return;
      try {
        await safeFetchJson(`/api/chasing_plans/${encodeURIComponent(activePlanId)}/steps/${encodeURIComponent(tid)}`, { method: "DELETE" });
        await loadPlanDetail(activePlanId);
      } catch (err2) {
        alert("Failed to delete trigger");
        console.error(err2);
      }
    }
  });

  // ----- open Edit/Replace Trigger modal -----
  function openEditTriggerModal(triggerId) {
    if (!activePlan || !modalEdit) return;
    const trigger = (activePlan.steps || []).find((t) => String(t.id) === String(triggerId));
    if (!trigger) return;
    editingTriggerId = triggerId;
    if (editOffsetInput) editOffsetInput.value = String(trigger.offset_days || 0);
    if (editTemplateSel) editTemplateSel.value = trigger.template_key || "";
    if (editErrorEl) { editErrorEl.classList.add("hidden"); editErrorEl.textContent = ""; }
    openModal(modalEdit);
  }

  // close Edit Trigger modal
  modalEdit?.addEventListener("click", (e) => {
    if (e.target.matches("[data-close-edit]")) closeModal(modalEdit);
  });

  // save Edit Trigger
  editSaveBtn?.addEventListener("click", async () => {
    if (!activePlanId || !editingTriggerId) return;
    const newOffset = Number(editOffsetInput?.value || 0);
    const newKey    = editTemplateSel?.value || "";
    if (!newKey) {
      if (editErrorEl) { editErrorEl.textContent = "Choose a template."; editErrorEl.classList.remove("hidden"); }
      return;
    }
    if (editErrorEl) editErrorEl.classList.add("hidden");
    try {
      await safeFetchJson(`/api/chasing_plans/${encodeURIComponent(activePlanId)}/steps/${encodeURIComponent(editingTriggerId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ offset_days: newOffset, template_key: newKey, channel: "email" }),
      });
      closeModal(modalEdit);
      await loadPlanDetail(activePlanId);
    } catch (err) {
      console.error(err);
      if (editErrorEl) { editErrorEl.textContent = err.message || "Failed to update trigger."; editErrorEl.classList.remove("hidden"); }
    }
  });

  // ----- init -----
  async function init() {
    try {
      await loadTemplates();   // fill dropdowns in modals
      await loadPlansList();   // left sidebar
      renderTriggersPanel();   // right pane (none selected yet)
    } catch (err) {
      console.error("Init failed:", err);
    }
  }

  init();

  // Match the HTML’s event name from the tab switcher
  window.addEventListener("cycles_tab_activated", () => {
    loadPlansList();
    if (activePlanId != null) {
      loadPlanDetail(activePlanId);
    } else {
      renderTriggersPanel();
    }
  });
})();
