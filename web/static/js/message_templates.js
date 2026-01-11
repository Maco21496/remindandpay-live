// FINAL VERSION OF /static/js/message_templates.js
(function () {
  // ----- DOM refs -----
  const rowsEl     = document.getElementById("t_rows");
  const emptyEl    = document.getElementById("t_empty");
  const tagSel     = document.getElementById("t_tag");
  const activeSel  = document.getElementById("t_active");
  const searchInp  = document.getElementById("t_search");
  const newBtn     = document.getElementById("t_new");
  const editorEl   = document.getElementById("t_editor"); // no longer used for inline edit

  // local cache
  let latestTemplates = [];

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

  function fmtUpdated(ts) {
    if (!ts) return "";
    const d = new Date(ts);
    if (Number.isNaN(d.getTime())) return escapeHtml(ts);
    const yyyy = d.getFullYear();
    const mm   = String(d.getMonth()+1).padStart(2,"0");
    const dd   = String(d.getDate()).padStart(2,"0");
    const hh   = String(d.getHours()).padStart(2,"0");
    const mi   = String(d.getMinutes()).padStart(2,"0");
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
  }

  function pillType(tagVal) {
    return (tagVal === "custom")
      ? `<span class="msg-pill msg-pill--custom">Custom</span>`
      : `<span class="msg-pill msg-pill--default">Default</span>`;
  }

  function statusLabel(isActive) { return isActive ? "Active" : "Archived"; }

  function buildQuery() {
    const params = new URLSearchParams();
    params.set("channel", "email");
    const tagVal = tagSel?.value || "";
    if (tagVal) params.set("tag", tagVal);
    const actVal = activeSel?.value || "";
    if (actVal === "1")      params.set("active", "true");
    else if (actVal === "0") params.set("active", "false");
    const qVal = (searchInp?.value || "").trim();
    if (qVal) params.set("q", qVal);
    return `/api/reminder_templates?${params.toString()}`;
  }

  function renderTable() {
    if (!Array.isArray(latestTemplates) || latestTemplates.length === 0) {
      rowsEl.innerHTML = "";
      if (emptyEl) emptyEl.style.display = "block";
      return;
    }
    if (emptyEl) emptyEl.style.display = "none";

    rowsEl.innerHTML = latestTemplates.map((t) => {
      const labelName   = t.name || "(unnamed template)";
      const updated     = fmtUpdated(t.updated_at);
      const st          = statusLabel(t.is_active);
      const pill        = pillType(t.tag);
      const subjSafe    = escapeHtml(t.subject || "");

      return `
        <tr class="msg-row" data-template-id="${t.id}">
          <td class="msg-col-name">
            <div class="msg-col-top">
              <span class="msg-col-label">${escapeHtml(labelName)}</span>
              ${pill}
            </div>
            <div class="msg-col-subject">${subjSafe}</div>
          </td>
          <td class="msg-col-status">${escapeHtml(st)}</td>
          <td class="msg-col-updated">${escapeHtml(updated)}</td>
          <td class="msg-col-edit" style="text-align:right;">
            <button class="msg-btn msg-btn--ghost msg-btn--xs js-t-edit" data-id="${t.id}">Edit</button>
          </td>
        </tr>
      `;
    }).join("");
  }

  async function loadAndRenderTemplates() {
    try {
      latestTemplates = await safeFetchJson(buildQuery());
      renderTable();
    } catch (err) {
      console.error("Failed to load templates:", err);
      latestTemplates = [];
      renderTable();
    }
  }

  // ---------- Open wizard for EDIT (Step 2) ----------
  rowsEl?.addEventListener("click", (e) => {
    const btn = e.target.closest(".js-t-edit[data-id]");
    if (!btn) return;
    const id = btn.getAttribute("data-id");
    const tmpl = latestTemplates.find(x => String(x.id) === String(id));
    if (!tmpl) return;

    if (window.TemplateWizard && typeof window.TemplateWizard.openForEdit === "function") {
      window.TemplateWizard.openForEdit(tmpl);
    } else {
      console.error("TemplateWizard (openForEdit) not available");
    }
  });

  // ---------- Open wizard for NEW ----------
  newBtn?.addEventListener("click", () => {
    if (window.TemplateWizard && typeof window.TemplateWizard.open === "function") {
      window.TemplateWizard.open();
    } else {
      console.error("TemplateWizard not loaded");
    }
  });

  // Refresh after modal save/close (the wizard dispatches this event)
  window.addEventListener("templates_tab_activated", () => loadAndRenderTemplates());
  if (location.hash === "#templates") loadAndRenderTemplates();
})();
