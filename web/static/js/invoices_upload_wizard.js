// /static/js/invoices_upload_wizard.js
// Depends on: el(), listUploadPresets(), loadUploadPreset() from invoices.js

/* ---------------------------
   Wizard state + constants
---------------------------- */
const wiz = {
  dlg: null, step: 1, file: null,
  sniff: { delimiter: ",", hasHeader: true, headers: [], columnCount: 26 },
  mapping: {},
  options: { assignMode: "per_row", defaultCustomerId: null, dateFormat: null, autoCreate: false },
  dryRunResult: null
};

// One global, safe from double-declare warnings
const DATE_FORMATS = window.__IC_DATE_FORMATS__ || (window.__IC_DATE_FORMATS__ = {
  auto: "", uk: "%d/%m/%Y", us: "%m/%d/%Y"
});

/* ---------------------------
   Presets dropdown (Step 1)
---------------------------- */
async function loadWizardPresets(){
  const sel = document.getElementById('wiz_preset_select');
  if (!sel) return;
  sel.innerHTML = '<option value="">— Select preset —</option>';
  try {
    const items = await listUploadPresets();
    items.forEach(p => {
      const o = document.createElement('option');
      o.value = p.id; o.textContent = p.name;
      sel.appendChild(o);
    });
  } catch {}
}
function applyWizardPresetToWizard(p){
  wiz.mapping = p.mapping || {};
  wiz.sniff.hasHeader = !!p.header;
  wiz.sniff.delimiter = p.delimiter || ",";
  wiz.options.assignMode        = p.assign_mode || "per_row";
  wiz.options.defaultCustomerId = p.default_customer_id || null;
  wiz.options.dateFormat        = p.date_format || "";
  wiz.options.autoCreate        = !!p.create_missing_customers;
}

/* ---------------------------
   Wizard controller
---------------------------- */
// UPDATED: accept opts { customer, lockCustomer }
function openWizard(opts = {}){
    wiz.dlg = el('upload_wizard');
  if (!wiz.dlg) { alert('Upload wizard HTML not found (missing <dialog id="upload_wizard">).'); return; }
  wiz.step = 1;
  wiz.file = null;
  wiz.sniff = { delimiter: ",", hasHeader: true, headers: [], columnCount: 26 };
  wiz.mapping = {};
  wiz.options = { assignMode: "per_row", defaultCustomerId: null, dateFormat: null, autoCreate: false };
  wiz.dryRunResult = null;

  // If opened from a customer dashboard, default to single-customer mode
  if (opts.customer && opts.customer.id){
    wiz.options.assignMode = "single";
    wiz.options.defaultCustomerId = opts.customer.id;
    wiz._fixedCustomerName = opts.customer.name || '';
    wiz._lockCustomer = !!opts.lockCustomer;  // optional: prevent switching modes (see below)
  }

  renderStep();
  loadWizardPresets();

  if (typeof wiz.dlg.showModal === 'function') wiz.dlg.showModal(); else wiz.dlg.setAttribute('open','');
}

function closeWizard(){
  if (!wiz.dlg) return;
  if (typeof wiz.dlg.close === 'function') wiz.dlg.close(); else wiz.dlg.removeAttribute('open');
}
function markActiveStep(){
  const steps = [el('st1'), el('st2'), el('st3'), el('st4')];
  steps.forEach((node, idx)=>{
    if (!node) return;
    node.classList.remove('is-active','is-complete');
    if (idx + 1 < wiz.step) node.classList.add('is-complete');
    if (idx + 1 === wiz.step) node.classList.add('is-active');
  });
  el('wiz_back')?.toggleAttribute('disabled', wiz.step===1);
  const nextBtn = el('wiz_next');
  if (nextBtn) nextBtn.textContent = (wiz.step===4 ? 'Import' : 'Next');
}
function renderStep(){
  markActiveStep();
  const body = el('wiz_body');
  if (wiz.step === 1) return renderStep1(body);
  if (wiz.step === 2) return renderStep2(body);
  if (wiz.step === 3) return renderStep3(body);
  if (wiz.step === 4) return renderStep4(body);
}

/* ================= STEP 1 ================= */
function renderStep1(body){
  body.innerHTML = `
    <div class="grid grid-4">
      <div class="field">
        <label>Choose file (CSV or Excel)</label>
        <input type="file" id="wiz_file" accept=".csv,.xlsx" />
        <div class="muted" id="wiz_file_msg" style="margin-top:6px;"></div>
      </div>

      <div class="field">
        <label>Saved template</label>
        <div class="field-row">
          <select id="wiz_preset_select"><option value="">— Select preset —</option></select>
        </div>
      </div>

      <div class="field">
        <label><input type="checkbox" id="wiz_header" ${wiz.sniff.hasHeader?'checked':''}/> First row is a header</label>
      </div>

      <div class="field"></div>
    </div>

    <details class="subpanel" id="wiz_adv" style="margin-top:10px;">
      <summary style="cursor:pointer; font-weight:600;">Advanced</summary>
      <div class="grid grid-4" style="margin-top:10px;">
        <div class="field">
          <label>Delimiter (CSV only)</label>
          <input id="wiz_delim" value="${wiz.sniff.delimiter||''}" placeholder="Leave blank to auto-detect" />
        </div>
        <div class="field">
          <label>Date format</label>
          <select id="wiz_datefmt_sel">
            <option value="auto" ${!wiz.options.dateFormat?'selected':''}>Auto</option>
            <option value="uk" ${wiz.options.dateFormat===DATE_FORMATS.uk?'selected':''}>UK (dd/mm/YYYY)</option>
            <option value="us" ${wiz.options.dateFormat===DATE_FORMATS.us?'selected':''}>US (mm/dd/YYYY)</option>
          </select>
        </div>
        <div class="field"></div>
        <div class="field"></div>
      </div>
    </details>
  `;

  // events
  el('wiz_file').addEventListener('change', e=>{
    const f = e.target.files?.[0];
    wiz.file = f || null;
    el('wiz_file_msg').textContent = f ? `${f.name} — ${Math.round(f.size/1024)} KB` : '';
  });
  el('wiz_header').addEventListener('change', e=>{ wiz.sniff.hasHeader = !!e.target.checked; });
  el('wiz_delim').addEventListener('input', e=>{ wiz.sniff.delimiter = (e.target.value||'').trim(); });
  el('wiz_datefmt_sel').addEventListener('change', e=>{
    const v = e.target.value;
    wiz.options.dateFormat = (v==='uk') ? DATE_FORMATS.uk : (v==='us' ? DATE_FORMATS.us : "");
  });

  loadWizardPresets();
}

/* helpers for options builder */
const _esc = (s) => String(s).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const _isLetters = (v) => typeof v === 'string' && /^[A-Za-z]+$/.test(v);
function _lettersToIndex(val){
  let n = 0;
  for (const ch of val.toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
}

/* ================= STEP 2 (mapping) ================= */
function buildOptionsFromHeaders(headers, columnCount){
  const hasHeader = Array.isArray(headers) && headers.length > 0;
  const opts = ['<option value="">— Choose —</option>'];

  if (hasHeader){
    headers.forEach(h => opts.push(`<option value="${_esc(h)}">${_esc(h)}</option>`));
  } else {
    const count = Math.max(1, columnCount || 26);
    for (let i = 0; i < count && i < 702; i++){
      let s = '', n = i;
      do { s = String.fromCharCode(65 + (n % 26)) + s; n = Math.floor(n / 26) - 1; } while (n >= 0);
      opts.push(`<option value="${s}">${s}</option>`);
    }
  }
  return opts.join('');
}
function setSelectValue(id, preferred){
  const s = el(id); if (!s || preferred == null) return;

  const headers   = (wiz?.sniff?.headers || []);
  const hasHeader = !!(wiz?.sniff?.hasHeader && headers.length);

  let pref = String(preferred);
  if (hasHeader && _isLetters(pref)){
    const idx = _lettersToIndex(pref);
    if (idx >= 0 && idx < headers.length) pref = headers[idx];
  }
  for (const o of s.options){
    if (String(o.value).toLowerCase() === pref.toLowerCase()){
      s.value = o.value;
      return;
    }
  }
}
function renderStep2(body){
  const H = (wiz.sniff.hasHeader ? (wiz.sniff.headers || []) : []);
  const C = wiz.sniff.columnCount || 26;

  body.innerHTML = `
    <p class="muted">We matched what we could. Please map anything missing.</p>
    <div class="grid grid-4">
      <div class="field"><label>Invoice date *</label><select id="wiz_map_issue_date">${buildOptionsFromHeaders(H,C)}</select></div>
      <div class="field"><label>Invoice number *</label><select id="wiz_map_invoice_number">${buildOptionsFromHeaders(H,C)}</select></div>
      <div class="field"><label>Amount *</label><select id="wiz_map_amount_due">${buildOptionsFromHeaders(H,C)}</select></div>
      <div class="field"><label>Customer name <span class="muted">(required in Per-row)</span></label><select id="wiz_map_customer_name">${buildOptionsFromHeaders(H,C)}</select></div>
    </div>
    <div class="grid grid-4" style="margin-top:8px">
      <div class="field"><label>Due date</label><select id="wiz_map_due_date">${buildOptionsFromHeaders(H,C)}</select></div>
      <div class="field"><label>Terms type</label><select id="wiz_map_terms_type">${buildOptionsFromHeaders(H,C)}</select></div>
      <div class="field"><label>Terms days</label><select id="wiz_map_terms_days">${buildOptionsFromHeaders(H,C)}</select></div>
      <div class="field"><label>Currency</label><select id="wiz_map_currency">${buildOptionsFromHeaders(H,C)}</select></div>
    </div>
    <div class="muted" id="wiz_map_msg" style="margin-top:8px;"></div>
  `;

  const m = wiz.mapping || {};
  const findBy = (syns) => H.find(h => syns.some(s => h.toLowerCase().includes(s))) || "";
  const guessed = {
    issue_date:     findBy(['issue date','invoice date','date']),
    invoice_number: findBy(['invoice','inv no','number','reference']),
    amount_due:     findBy(['amount','total','balance']),
    customer_name:  findBy(['customer','client','account']),
    due_date:       findBy(['due']),
    terms_type:     findBy(['terms']),
    terms_days:     findBy(['days']),
    currency:       findBy(['currency','curr'])
  };
  const prefer = (k) => (m[k] || guessed[k] || "");

  setSelectValue('wiz_map_issue_date',     prefer('issue_date'));
  setSelectValue('wiz_map_invoice_number', prefer('invoice_number'));
  setSelectValue('wiz_map_amount_due',     prefer('amount_due'));
  setSelectValue('wiz_map_customer_name',  prefer('customer_name'));
  setSelectValue('wiz_map_due_date',       prefer('due_date'));
  setSelectValue('wiz_map_terms_type',     prefer('terms_type'));
  setSelectValue('wiz_map_terms_days',     prefer('terms_days'));
  setSelectValue('wiz_map_currency',       prefer('currency'));
}
function collectMappingFromStep2(){
  const get = (id) => el('wiz_map_'+id)?.value || "";
  wiz.mapping = {
    issue_date: get('issue_date'),
    invoice_number: get('invoice_number'),
    amount_due: get('amount_due'),
    customer_name: get('customer_name'),
    due_date: get('due_date'),
    terms_type: get('terms_type'),
    terms_days: get('terms_days'),
    currency: get('currency')
  };
  const missing = ['issue_date','invoice_number','amount_due'].filter(k => !wiz.mapping[k]);
  if (missing.length){
    el('wiz_map_msg').textContent = 'Please map: ' + missing.join(', ');
    return false;
  }
  return true;
}

/* ================= STEP 3 ================= */
function renderStep3(body){
  body.innerHTML = `
    <div class="subpanel">
      <div class="grid grid-4">
        <div class="field">
          <label>Assign to customers</label>
          <div style="display:flex;gap:12px;align-items:center;">
            <label><input type="radio" name="wiz_assign" value="per_row" ${wiz.options.assignMode==='per_row'?'checked':''}> Per row (use a CSV column)</label>
            <label><input type="radio" name="wiz_assign" value="single" ${wiz.options.assignMode==='single'?'checked':''}> One customer (all rows)</label>
          </div>
        </div>
        <div class="field" id="wiz_single_wrap" style="display:${wiz.options.assignMode==='single'?'block':'none'}">
          <label>Choose customer</label>
          <input id="wiz_customer_search" placeholder="Type to search…" />
          <input id="wiz_customer_id" type="hidden" value="${wiz.options.defaultCustomerId||''}" />
          <div id="wiz_customer_sugs" class="sugs" style="display:none"></div>
        </div>
        <div class="field">
          <label><input type="checkbox" id="wiz_autocreate" ${wiz.options.autoCreate?'checked':''}/> Auto-create missing customers</label>
          <div class="muted">Use carefully — we’ll create customers for names that don’t match.</div>
        </div>
      </div>
    </div>
    <div class="muted" id="wiz_opt_msg" style="margin-top:8px;"></div>
  `;
  document.querySelectorAll('input[name="wiz_assign"]').forEach(r=>{
    r.addEventListener('change', e=>{
      wiz.options.assignMode = e.target.value;
      el('wiz_single_wrap').style.display = (wiz.options.assignMode==='single'?'block':'none');
    });
  });
  el('wiz_autocreate').addEventListener('change', e=>{ wiz.options.autoCreate = !!e.target.checked; });
  el('wiz_customer_search')?.addEventListener('input', wizSearch);
}
async function wizSearch(){
  const box = el('wiz_customer_search');
  const sug = el('wiz_customer_sugs');
  const q = (box?.value || '').trim();
  if (!box) return;
  if (q.length < 2){ if(sug){sug.style.display='none'; sug.innerHTML='';} return; }
  const r = await fetch('/api/customers?q=' + encodeURIComponent(q));
  if (!r.ok){ if(sug){sug.style.display='none';} return; }
  const data = await r.json();
  if (!data.length){ if(sug){sug.style.display='block'; sug.innerHTML='<div class="muted" style="padding:6px;">No matches</div>'; } return; }
  sug.style.display='block';
  sug.innerHTML = data.map(c => `
    <div style="padding:6px;border:1px solid #e5e7eb;border-radius:8px;background:#fff;margin-bottom:6px;cursor:pointer"
         onclick="wizPickCustomer(${c.id}, '${(c.name||'').replace(/'/g, "\\'")}')">
      <div style="font-weight:600">${c.name}</div>
      <div class="muted" style="font-size:12px">${c.email||''} ${c.phone?(' • '+c.phone):''}</div>
    </div>
  `).join('');
}
window.wizPickCustomer = function(id, name){
  el('wiz_customer_id').value = String(id);
  el('wiz_customer_search').value = name;
  wiz.options.defaultCustomerId = id;
  const sug = el('wiz_customer_sugs'); if (sug){sug.style.display='none'; sug.innerHTML='';}
};

/* ================= STEP 4 (review) ================= */
function renderStep4(body){
  const r = wiz.dryRunResult || {inserted:0, skipped_duplicates:0, skipped_missing_customer:0, errors:[]};
  const hasSample = Array.isArray(r.sample) && r.sample.length;

  let sampleHtml = '';
  if (hasSample){
    const cols = Object.keys(r.sample[0]);
    const rows = r.sample.slice(0, 10);
    sampleHtml = `
      <div class="muted" style="margin-top:10px;">Sample (first ${rows.length} rows after mapping)</div>
      <div class="subpanel" style="overflow:auto">
        <table>
          <thead><tr>${cols.map(c=>`<th>${c}</th>`).join('')}</tr></thead>
          <tbody>
            ${rows.map(row => `<tr>${cols.map(c=>`<td>${row[c] ?? ''}</td>`).join('')}</tr>`).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  body.innerHTML = `
    <div class="subpanel">
      <div><strong>Ready to import</strong></div>
      <div style="margin-top:6px;">Will insert: <strong>${r.inserted}</strong></div>
      <div>Duplicates: ${r.skipped_duplicates}</div>
      <div>Missing customer: ${r.skipped_missing_customer}</div>
      ${r.unknown_customers && r.unknown_customers.length ? `<div style="margin-top:6px;">Unknown customers: ${r.unknown_customers.slice(0,8).join(', ')}${r.unknown_customers.length>8?'…':''}</div>` : ''}
      ${r.errors && r.errors.length ? `<div style="margin-top:6px;color:#b91c1c;">Errors (${r.errors.length}) — first few:<br>${r.errors.slice(0,5).map(x=>`• ${x}`).join('<br>')}</div>` : ''}
    </div>
    ${sampleHtml}
    <div class="grid grid-4" style="margin-top:10px;">
      <div class="field">
        <label><input type="checkbox" id="wiz_save_preset" /> Save these settings as a preset</label>
        <input id="wiz_preset_name" placeholder="Preset name (e.g. Xero export)"/>
      </div>
    </div>
  `;
}

/* ---------------------------
   Server inspect + navigation
---------------------------- */
async function inspectFile(){
  if (!wiz.file) throw new Error('No file');

  const fd = new FormData();
  fd.append('csv_file', wiz.file);
  fd.append('header', wiz.sniff.hasHeader ? 'true' : 'false');
  if (wiz.sniff.delimiter) fd.append('delimiter', wiz.sniff.delimiter); // ignored for XLSX

  const res = await fetch('/api/invoices/bulk-inspect', { method:'POST', body: fd });
  if (!res.ok){
    const e = await res.json().catch(()=>null);
    throw new Error(e?.detail || 'Could not read file');
  }
  const info = await res.json();

  wiz.sniff.delimiter = info.delimiter || wiz.sniff.delimiter || ',';
  wiz.sniff.hasHeader = !!info.has_header;
  wiz.sniff.headers   = Array.isArray(info.headers) ? info.headers : [];

  const previewCols = (Array.isArray(info.preview) && info.preview.length)
    ? Object.keys(info.preview[0]).length : 0;
  wiz.sniff.columnCount = wiz.sniff.headers.length || previewCols || 26;

  if (info.suggested_mapping){
    wiz.mapping = { ...(wiz.mapping || {}) };
    for (const [k, v] of Object.entries(info.suggested_mapping)){
      if (!wiz.mapping[k]) wiz.mapping[k] = v;
    }
  }

  if (!wiz.options.dateFormat && info.date_format_guess){
    wiz.options.dateFormat = info.date_format_guess;
    const sel = el('wiz_datefmt_sel');
    if (sel){
      sel.value =
        (wiz.options.dateFormat === DATE_FORMATS.us) ? 'us' :
        (wiz.options.dateFormat === DATE_FORMATS.uk) ? 'uk' : 'auto';
    }
  }
}

async function wizardNext(){
  if (wiz.step === 1){
    if (!wiz.file){
      el('wiz_file_msg').textContent = 'Please choose a file.';
      return;
    }

    // apply preset on Next (no separate Load button)
    const presetId = el('wiz_preset_select')?.value;
    if (presetId) {
      const p = await loadUploadPreset(presetId).catch(()=>null);
      if (p) applyWizardPresetToWizard(p);
    }

    // sync toggles
    wiz.sniff.hasHeader = !!el('wiz_header')?.checked;
    wiz.sniff.delimiter = (el('wiz_delim')?.value || '').trim();
    const df = el('wiz_datefmt_sel');
    if (df){
      wiz.options.dateFormat =
        df.value === 'uk' ? DATE_FORMATS.uk :
        df.value === 'us' ? DATE_FORMATS.us : "";
    }

    try {
      await inspectFile();
    } catch (err){
      el('wiz_file_msg').textContent = String(err.message || err);
      return;
    }
    wiz.step = 2; renderStep(); return;
  }

  if (wiz.step === 2){
    if (!collectMappingFromStep2()) return;
    wiz.step = 3; renderStep(); return;
  }

  if (wiz.step === 3) {
    // If opened from the customer dashboard with a fixed customer,
    // force single-customer mode.
    if (wiz._lockCustomer) {
      wiz.options.assignMode = 'single';
    }

    // Require a customer in single-customer mode
    if (wiz.options.assignMode === 'single' && !wiz.options.defaultCustomerId) {
      el('wiz_opt_msg').textContent = wiz._lockCustomer
        ? 'This import is tied to a specific customer — please choose them before continuing.'
        : 'Choose a customer for all rows.';
      return;
    }
    el('wiz_opt_msg').textContent = ''; // clear any previous error

    // Build dry-run request
    const fd = new FormData();
    fd.append('csv_file', wiz.file);
    fd.append('mapping', JSON.stringify(wiz.mapping));
    fd.append('header', wiz.sniff.hasHeader ? 'true' : 'false');
    fd.append('delimiter', wiz.sniff.delimiter || ',');
    if (wiz.options.dateFormat) fd.append('date_format', wiz.options.dateFormat);
    if (wiz.options.assignMode === 'single') {
      fd.append('default_customer_id', String(wiz.options.defaultCustomerId));
    }
    if (wiz.options.autoCreate) fd.append('create_missing_customers', 'true');
    fd.append('dry_run', 'true');

    const res = await fetch('/api/invoices/bulk-upload', { method:'POST', body: fd });
    if (!res.ok) {
      let txt = 'Validation failed';
      try { const e = await res.json(); if (e?.detail) txt = e.detail; } catch {}
      el('wiz_opt_msg').textContent = txt;
      return;
    }

    wiz.dryRunResult = await res.json();
    wiz.step = 4; renderStep(); return;
  }

  if (wiz.step === 4){
    const fd = new FormData();
    fd.append('csv_file', wiz.file);
    fd.append('mapping', JSON.stringify(wiz.mapping));
    fd.append('header', wiz.sniff.hasHeader ? 'true' : 'false');
    fd.append('delimiter', wiz.sniff.delimiter || ',');
    if (wiz.options.dateFormat) fd.append('date_format', wiz.options.dateFormat);
    if (wiz.options.assignMode === 'single' && wiz.options.defaultCustomerId){
      fd.append('default_customer_id', String(wiz.options.defaultCustomerId));
    }
    if (wiz.options.autoCreate) fd.append('create_missing_customers', 'true');

    const res = await fetch('/api/invoices/bulk-upload', { method:'POST', body: fd });
    if (!res.ok){ alert('Import failed'); return; }
    const out = await res.json();
    alert(`Imported ${out.inserted} • Duplicates ${out.skipped_duplicates} • Missing customer ${out.skipped_missing_customer}`);
    closeWizard();
    if (typeof loadInvoices === 'function') loadInvoices();

    const save = el('wiz_save_preset')?.checked;
    const name = el('wiz_preset_name')?.value?.trim();
    if (save && name){
      const body = {
        name,
        mapping: wiz.mapping,
        header: wiz.sniff.hasHeader,
        delimiter: wiz.sniff.delimiter || ',',
        date_format: wiz.options.dateFormat || null,
        default_customer_id: (wiz.options.assignMode==='single' ? wiz.options.defaultCustomerId : null),
        assign_mode: wiz.options.assignMode,
        create_missing_customers: !!wiz.options.autoCreate
      };
      await fetch('/api/invoices/upload-presets', {
        method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
      }).catch(()=>{ /* ignore */ });
    }
  }
}
function wizardBack(){ if (wiz.step>1){ wiz.step--; renderStep(); } }

/* ---------------------------
   Wire buttons
---------------------------- */

document.addEventListener('click', (e) => {
  const t = e.target.closest('button, [role="button"]');
  if (!t) return;

  if (t.id === 'btn_open_upload_wizard') {
    openWizard();
  } else if (t.id === 'wiz_next') {
    wizardNext();
  } else if (t.id === 'wiz_back') {
    wizardBack();
  }
});

// --- expose bulk-upload wizard APIs/State to other pages ---
window.wiz = window.wiz || wiz;            // same object, not a copy
window.openWizard = openWizard;
window.closeWizard = closeWizard;
window.wizardNext = wizardNext;
window.wizardBack = wizardBack;



