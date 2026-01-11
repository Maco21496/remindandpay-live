// FINAL VERSION OF /static/js/message_template_wizard.js
(function () {
  // Public API (attached at end)
  const TemplateWizard = {};

  // ---- modal elements (present in the HTML file) ----
  const wrap  = document.getElementById("tmpl_wizard");
  const body  = document.getElementById("tw_body");
  const btnX  = document.getElementById("tw_close");
  const btnBk = document.getElementById("tw_back");
  const btnNx = document.getElementById("tw_next");
  const step1 = document.getElementById("tw_st1");
  const step2 = document.getElementById("tw_st2");
  const tplS1 = document.getElementById("tpl_tw_step1");
  const tplS2 = document.getElementById("tpl_tw_step2");
  const title = document.getElementById("tw_title");

  // state
  const S = {
    mode: "new",             // "new" | "edit"
    lockBasics: false,       // when true (edit mode) Back is disabled
    editId: null,            // template id when editing
    step: 1,
    form: {
      tag: "gentle",
      channel: "email",
      is_active: true,
      // key will be auto-generated from name
    },
    // track last-focused content field for placeholder insertion
    lastFocusId: null,
  };

  // ---- helpers ----
  function show(el){ el && el.classList.remove("hidden"); }
  function hide(el){ el && el.classList.add("hidden"); }

  async function safeFetchJson(url, opts = {}) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      const text = await r.text().catch(()=> "");
      const err  = new Error(`${opts.method || "GET"} ${url} failed ${r.status}: ${text}`);
      err.status = r.status;
      err.text   = text;
      throw err;
    }
    return await r.json();
  }

  function slugify(s) {
    return String(s || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 64);
  }

  function markStepper() {
    [step1, step2].forEach(n => n && n.classList.remove("is-active","is-complete"));
    if (S.step === 1) step1?.classList.add("is-active");
    if (S.step === 2) { step1?.classList.add("is-complete"); step2?.classList.add("is-active"); }

    // Back button rules: disabled on step 1, and also disabled entirely in edit mode
    if (btnBk) btnBk.disabled = (S.step === 1) || (S.mode === "edit" && S.lockBasics);

    if (btnNx) {
      btnNx.textContent = (S.mode === "edit")
        ? "Save changes"
        : (S.step === 2 ? "Create template" : "Next");
    }

    if (title) {
      title.textContent = (S.mode === "edit") ? "Edit message template" : "New message template";
    }
  }

  // -------- Placeholder palette --------
  const PLACEHOLDERS = [
    { token: "customer_name",                 label: "Customer name",       aliases: ["customer.name"] },
    { token: "invoice_count",                 label: "Invoice count" },
    { token: "overdue_total",                 label: "Overdue total" },
    { token: "oldest_days_overdue",           label: "Oldest days overdue" },
    { token: "oldest_invoice.invoice_number", label: "Oldest invoice number" },
    { token: "oldest_invoice.outstanding_str",label: "Oldest invoice amount" },
    { token: "invoice.invoice_number",        label: "Invoice number" },
    { token: "invoice.amount_due",            label: "Invoice amount due" },
    { token: "days_overdue",                  label: "Days overdue" },
    { token: "payment_link",                  label: "Payment link" },
    { token: "invoices_table",                label: "Invoices table (HTML)" },
    { token: "pay_url",                       label: "Pay URL" }
  ];

  function paletteHtml() {
    return `
      <div class="msg-field">
        <label class="msg-label">Placeholders</label>
        <div id="tw_ph_wrap" style="display:flex;flex-wrap:wrap;gap:6px;">
          ${PLACEHOLDERS.map(p => `
            <button
              type="button"
              class="msg-btn msg-btn--ghost msg-btn--xs tw-ph"
              data-token="{{ ${p.token} }}"
              title="Insert {{ ${p.token} }}"
            >
              ${p.label}
            </button>
          `).join("")}
        </div>
        <div class="muted" style="margin-top:4px;">
          Click to insert the selected placeholder at the cursor in Subject or Body.
        </div>
      </div>
    `;
  }

  function insertAtCursor(el, text) {
    if (!el) return;
    el.focus();
    const start = el.selectionStart ?? el.value.length;
    const end   = el.selectionEnd   ?? el.value.length;
    const before = el.value.slice(0, start);
    const after  = el.value.slice(end);
    el.value = before + text + after;
    const pos = start + text.length;
    if (typeof el.setSelectionRange === "function") el.setSelectionRange(pos, pos);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function wirePalette() {
    const wrap = document.getElementById("tw_ph_wrap");
    if (!wrap) return;
    wrap.addEventListener("click", (e) => {
      const btn = e.target.closest(".tw-ph[data-token]");
      if (!btn) return;
      const token = btn.getAttribute("data-token");
      const targetId = S.lastFocusId || "tw_body_text"; // default to text body
      insertAtCursor(document.getElementById(targetId), token);
    });
  }

  // -------- render steps --------
  function render() {
    markStepper();
    if (!body) return;

    if (S.step === 1) {
      body.innerHTML = tplS1.innerHTML;

      // refs
      const tag  = document.getElementById("tw_tag");
      const name = document.getElementById("tw_name");
      const ch   = document.getElementById("tw_channel");
      const act  = document.getElementById("tw_active");

      // set values
      tag.value = S.form.tag || "gentle";
      name.value = S.form.name || "";
      ch.value = S.form.channel || "email";
      act.value = S.form.is_active ? "1" : "0";

      // when name changes, keep an internal key suggestion
      name.addEventListener("input", () => {
        S.form.key = slugify(name.value || "");
      });

      // in edit mode we keep basics locked (visible for context, but not the flow)
      if (S.mode === "edit") {
        tag.disabled = true; ch.disabled = true; name.disabled = true; act.disabled = false;
      }

    } else {
      body.innerHTML = `
        ${tplS2.innerHTML}
        ${paletteHtml()}
      `;

      // restore any existing content
      document.getElementById("tw_subject").value   = S.form.subject   || "";
      document.getElementById("tw_body_text").value = S.form.body_text || "";
      document.getElementById("tw_body_html").value = S.form.body_html || "";

      // track last-focused field for placeholder insertion
      ["tw_subject","tw_body_text","tw_body_html"].forEach(id => {
        const el = document.getElementById(id);
        el?.addEventListener("focus", () => { S.lastFocusId = id; });
      });

      wirePalette();
    }
  }

  function openNew() {
    S.mode = "new";
    S.lockBasics = false;
    S.editId = null;
    S.step = 1;
    S.form = { tag:"gentle", channel:"email", is_active:true };
    render();
    show(wrap);
  }

  // Open the wizard pre-populated for editing; jump straight to Step 2
  function openForEdit(tmpl) {
    // tmpl is the object from the list (id, name, subject, body_text, body_html, is_active, tag, channel, etc.)
    S.mode = "edit";
    S.lockBasics = true;     // keep Back disabled; we start on step 2
    S.editId = tmpl.id;
    S.step = 2;

    S.form = {
      tag: tmpl.tag || "gentle",
      name: tmpl.name || "",
      channel: tmpl.channel || "email",
      is_active: !!tmpl.is_active,
      key: tmpl.key || "",
      subject: tmpl.subject || "",
      body_text: tmpl.body_text || "",
      body_html: tmpl.body_html || ""
    };

    render();
    show(wrap);
  }

  function close() { hide(wrap); }

  function collectStep1() {
    const tag = document.getElementById("tw_tag")?.value || "gentle";
    const name= (document.getElementById("tw_name")?.value || "").trim();
    const ch  = document.getElementById("tw_channel")?.value || "email";
    const act = document.getElementById("tw_active")?.value === "1";

    if (!name) { alert("Enter a label/name."); return false; }

    S.form.tag = tag;
    S.form.name = name;
    S.form.channel = ch;
    S.form.is_active = act;

    // Auto-generate key, do not expose in UI
    S.form.key = slugify(name) || "message_template";
    return true;
  }

  async function createTemplateWithRetry() {
    // Try up to 5 variants if key clashes (status 409 or server mentions 'key')
    let attempt = 0;
    let base = S.form.key;
    while (attempt < 5) {
      const key = attempt === 0 ? base : `${base}_${attempt+1}`;
      const payload = {
        key,
        channel:   S.form.channel,
        tag:       S.form.tag,
        name:      S.form.name,
        subject:   S.form.subject   || "",
        body_html: S.form.body_html || "",
        body_text: S.form.body_text || "",
        is_active: !!S.form.is_active,
      };
      try {
        const res = await safeFetchJson("/api/reminder_templates", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        return res; // success
      } catch (e) {
        const text = (e.text || "").toLowerCase();
        const conflict = e.status === 409 || text.includes("key") || text.includes("unique");
        if (!conflict) throw e;
        attempt += 1;
      }
    }
    throw new Error("Could not find a unique key for this name. Please tweak the label and try again.");
  }

  async function saveEdit() {
    const payload = {
      subject:   S.form.subject   || "",
      body_html: S.form.body_html || "",
      body_text: S.form.body_text || "",
      is_active: !!S.form.is_active,
    };
    return safeFetchJson(`/api/reminder_templates/${encodeURIComponent(S.editId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async function nextHandler() {
    if (S.mode === "new" && S.step === 1) {
      if (!collectStep1()) return;
      S.step = 2; render(); return;
    }

    // collect step 2
    S.form.subject   = (document.getElementById("tw_subject")?.value || "").trim();
    S.form.body_text = (document.getElementById("tw_body_text")?.value || "").trim();
    S.form.body_html = (document.getElementById("tw_body_html")?.value || "").trim();

    const errEl = document.getElementById("tw_err");
    if (errEl) { errEl.classList.add("hidden"); errEl.textContent = ""; }

    try {
      if (S.mode === "edit") {
        await saveEdit();
      } else {
        await createTemplateWithRetry();
      }
      close();
      // ask the list to refresh if available
      window.dispatchEvent(new Event("templates_tab_activated"));
    } catch (e) {
      console.error(e);
      if (errEl) {
        errEl.textContent = e.message || (S.mode === "edit" ? "Failed to save changes." : "Failed to create template.");
        errEl.classList.remove("hidden");
      } else {
        alert(S.mode === "edit" ? "Failed to save changes" : "Failed to create template");
      }
    }
  }

  // wire modal controls
  btnX?.addEventListener("click", close);
  btnBk?.addEventListener("click", () => {
    if (S.mode === "edit" && S.lockBasics) return; // locked in edit mode
    if (S.step > 1) { S.step -= 1; render(); }
  });
  btnNx?.addEventListener("click", nextHandler);

  // close on Escape
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !wrap.classList.contains("hidden")) close();
  });

  // public API
  TemplateWizard.open = openNew;
  TemplateWizard.openForEdit = openForEdit;
  window.TemplateWizard = TemplateWizard;
})();
