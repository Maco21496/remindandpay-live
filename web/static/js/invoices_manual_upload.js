// FINAL VERSION OF invoices_manual_upload.js
// Self-contained manual-invoice wizard.
// Uses AppDate.toISODate (from uk_date.js) for all <input type="date"> values.
// Will call window.computeDueDateJs if present, but guards it.

// make this file self-contained (no reliance on other files exporting helpers)
const el = window.el || ((id) => document.getElementById(id));

// Always set <input type="date"> values as YYYY-MM-DD.
// Prefer AppDate.toISODate to stay aligned with Settings.
const toISO = (v) => {
  if (window.AppDate && typeof AppDate.toISODate === 'function') {
    const iso = AppDate.toISODate(v);
    return iso || '';
  }
  if (!v) return '';
  if (v instanceof Date) {
    // keep as date-only (UTC to avoid TZ drift)
    const y = v.getUTCFullYear();
    const m = String(v.getUTCMonth() + 1).padStart(2, '0');
    const d = String(v.getUTCDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
  }
  // assume ISO-like; trim to YYYY-MM-DD
  return String(v).slice(0, 10);
};

const HAS_COMPUTE_DUE = (typeof window.computeDueDateJs === 'function');

const mw = {
  dlg: null,
  step: 1,
  customer: null,   // {id, name, terms_type, terms_days}
  lockCustomer: false, // when true, don't let Back go to Step 1
};

function mw_markActive(){
  const s1 = el('mw_st1'), s2 = el('mw_st2');
  [s1,s2].forEach(n => n && n.classList.remove('is-active','is-complete'));
  if (mw.step === 1){ s1?.classList.add('is-active'); }
  if (mw.step === 2){ s1?.classList.add('is-complete'); s2?.classList.add('is-active'); }
  // disable Back on step 1, and also on step 2 when customer is locked
  const disableBack = (mw.step === 1) || (mw.step === 2 && mw.lockCustomer);
  el('mw_back')?.toggleAttribute('disabled', disableBack);
  const next = el('mw_next'); if (next) next.textContent = mw.step===2 ? 'Save all' : 'Next';
}

// accept opts { step, customer, lockCustomer }
function openManualWizard(opts = {}) {
  mw.dlg = el('manual_wizard');
  if (!mw.dlg) { alert('Manual wizard HTML not found (missing <dialog id="manual_wizard">).'); return; }
  mw.step = opts.step || 1;
  mw.customer = opts.customer ?? null;
  mw.lockCustomer = !!opts.lockCustomer;
  mw_render();
  if (typeof mw.dlg.showModal === 'function') mw.dlg.showModal(); else mw.dlg.setAttribute('open','');
}
function closeManualWizard(){
  if (!mw.dlg) return;
  if (typeof mw.dlg.close === 'function') mw.dlg.close(); else mw.dlg.removeAttribute('open');
}

function mw_render(){
  mw_markActive();
  const body = el('mw_body');
  if (mw.step === 1) return mw_renderStep1(body);
  if (mw.step === 2) return mw_renderStep2(body);
}

/* -------- Step 1: pick/create customer -------- */
function mw_renderStep1(body){
  body.innerHTML = el('tpl_mw_step1').innerHTML;

  // wire search box
  el('mw_customer_search')?.addEventListener('input', mw_searchCustomers);
  // new customer panel
  el('mw_btn_new_customer')?.addEventListener('click', ()=> {
    const p = el('mw_new_customer_panel');
    p.style.display = p.style.display==='block' ? 'none' : 'block';
    if (p.style.display==='block') el('mw_nc_name')?.focus();
  });
  el('mw_nc_terms')?.addEventListener('change', ()=>{
    const wrap = el('mw_nc_days_wrap');
    wrap.style.display = (el('mw_nc_terms').value === 'custom') ? 'block' : 'none';
  });
  el('mw_btn_save_customer')?.addEventListener('click', mw_createCustomer);

  // reflect selection if coming back
  if (mw.customer){
    el('mw_customer_id').value = mw.customer.id;
    el('mw_cust_selected').textContent = `Selected: ${mw.customer.name}`;
  }
}

async function mw_searchCustomers(){
  const box = el('mw_customer_search');
  const sug = el('mw_customer_sugs');
  const q = (box?.value || '').trim();
  if (!box || q.length < 2){ if (sug){sug.style.display='none'; sug.innerHTML='';} return; }
  const r = await fetch('/api/customers?q='+encodeURIComponent(q));
  if (!r.ok){ if (sug){sug.style.display='none';} return; }
  const data = await r.json();
  if (!data.length){ if (sug){sug.style.display='block'; sug.innerHTML='<div class="muted" style="padding:6px;">No matches</div>'; } return; }
  if (sug){
    sug.style.display='block';
    sug.innerHTML = data.map(c => `
      <div style="padding:6px;border:1px solid #e5e7eb;border-radius:8px;background:#fff;margin-bottom:6px;cursor:pointer"
           data-pick='${JSON.stringify({id:c.id,name:c.name,terms_type:c.terms_type,terms_days:c.terms_days}).replace(/'/g,"&apos;")}'>
        <div style="font-weight:600">${c.name}</div>
        <div class="muted" style="font-size:12px">${c.email||''} ${c.phone?(' • '+c.phone):''}
          ${c.terms_type ? (' • terms: '+c.terms_type+(c.terms_type==='custom'&&c.terms_days?(' '+c.terms_days+'d'):'') ) : ''}</div>
      </div>
    `).join('');
    // delegate: click any suggestion
    sug.querySelectorAll('[data-pick]').forEach(n => n.addEventListener('click', ()=>{
      const v = JSON.parse(n.getAttribute('data-pick').replace(/&apos;/g,"'"));
      mw.customer = v;
      el('mw_customer_id').value = v.id;
      el('mw_customer_search').value = v.name;
      sug.style.display='none'; sug.innerHTML='';
      el('mw_cust_selected').textContent = `Selected: ${v.name}`;
    }));
  }
}

async function mw_createCustomer(){
  const name  = (el('mw_nc_name')?.value || '').trim();
  const email = (el('mw_nc_email')?.value || '').trim();
  const phone = (el('mw_nc_phone')?.value || '').trim();
  const terms_type = el('mw_nc_terms')?.value || 'net_30';
  const twrap = el('mw_nc_days_wrap');
  const terms_days = (twrap && twrap.style.display==='block') ? parseInt(el('mw_nc_days')?.value || '0',10) : null;
  const msg = el('mw_nc_msg');

  if (!name || !email || !phone){ msg && (msg.textContent = 'Please enter name, email and mobile.'); return; }

  const r = await fetch('/api/customers', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name, email, phone, terms_type, terms_days})
  });
  if (!r.ok){ const e = await r.json().catch(()=>({detail:'Failed'})); msg && (msg.textContent = e.detail || 'Failed'); return; }
  const c = await r.json();
  mw.customer = { id:c.id, name:c.name, terms_type:terms_type, terms_days:terms_days };
  el('mw_customer_id').value = String(c.id);
  el('mw_cust_selected').textContent = `Selected: ${c.name}`;
  const pnl = el('mw_new_customer_panel'); if (pnl) pnl.style.display = 'none';
}

/* ---------- NEW: compute due for a single row (initial + on change) ----------
   Behavior: if the user has NOT manually edited the due date (we track via
   data-user-edited="1"), then recompute and overwrite whenever the issue date changes.
*/
function mw_computeDueForRow(tr){
  const dueEl = tr.querySelector('.mw_due');
  const issueIso = tr.querySelector('.mw_issue')?.value;
  if (!dueEl || !issueIso || !mw.customer || !HAS_COMPUTE_DUE) return;

  // Only auto-set if user has NOT manually edited this field
  if (dueEl.dataset.userEdited === "1") return;

  const ttype = mw.customer.terms_type;
  const tdays = mw.customer.terms_days;

  if (!ttype || (ttype === 'custom' && !Number(tdays))) return;

  const iso = window.computeDueDateJs(issueIso, ttype, tdays);
  if (iso) {
    dueEl.value = toISO(iso);
    // mark as not user-edited (explicit), but leave room for later manual edit
    if (!dueEl.dataset.userEdited) dueEl.dataset.userEdited = "0";
  }
}

/* -------- Step 2: multi-row entry -------- */
function mw_renderStep2(body){
  body.innerHTML = el('tpl_mw_step2').innerHTML;

  // start with one row
  mw_addRow();

  el('mw_add_row')?.addEventListener('click', mw_addRow);
}

function mw_addRow(pref = {}){
  const tbody = el('mw_rows');
  const tr = document.createElement('tr');
  const today = new Date();

  tr.innerHTML = `
    <td><input type="date" class="mw_issue" value="${toISO(today)}" /></td>
    <td><input class="mw_number" placeholder="e.g. INV-1001" /></td>
    <td><input class="mw_amount" placeholder="e.g. 120.00" /></td>
    <td><input type="date" class="mw_due" /></td>
    <td><button type="button" class="btn btn--ghost mw_remove">✕</button></td>
  `;
  tbody.appendChild(tr);

  const issueEl = tr.querySelector('.mw_issue');
  const dueEl   = tr.querySelector('.mw_due');

  // prefill (normalise to ISO if provided)
  if (pref.issue_date)     issueEl.value = toISO(pref.issue_date);
  if (pref.invoice_number) tr.querySelector('.mw_number').value = pref.invoice_number;
  if (pref.amount_due)     tr.querySelector('.mw_amount').value = pref.amount_due;

  if (pref.due_date) {
    dueEl.value = toISO(pref.due_date);
    // treat provided due_date as user decision → don't auto-overwrite
    dueEl.dataset.userEdited = "1";
  }

  // NEW: mark manual edits on due to stop auto-rewrites
  dueEl.addEventListener('input', () => { dueEl.dataset.userEdited = "1"; });
  dueEl.addEventListener('change', () => { dueEl.dataset.userEdited = "1"; });

  // NEW: auto-compute due immediately if customer has terms and the user hasn't set it
  mw_computeDueForRow(tr);

  // NEW: recompute due when issue changes (overwrites only if not user-edited)
  issueEl.addEventListener('change', ()=> {
    mw_computeDueForRow(tr);
  });

  tr.querySelector('.mw_remove').addEventListener('click', ()=> tr.remove());
}

/* -------- Navigation -------- */
async function mw_next(){
  // ---- Step 1 → Step 2 ----
  if (mw.step === 1){
    if (!mw.customer || !mw.customer.id){
      const id = el('mw_customer_id')?.value;
      if (!id) return;
    }
    mw.step = 2; mw_render();
    return;
  }

  // Inline helpers for per-row error display
  function setRowError(tr, text){
    tr.classList.add('has-error');
    // put the error under the "Invoice number" cell (2nd column)
    const td = tr.querySelector('td:nth-child(2)') || tr.lastElementChild;
    let err = td.querySelector('.mw_err');
    if (!err){
      err = document.createElement('div');
      err.className = 'mw_err';
      err.style.cssText = 'color:#b91c1c;font-size:12px;margin-top:4px;white-space:normal';
      td.appendChild(err);
    }
    err.textContent = text;

    const numInput = tr.querySelector('.mw_number');
    if (numInput){
      numInput.classList.add('mw_input_err');
      numInput.title = text;
    }
  }

  function clearRowError(tr){
    tr.classList.remove('has-error');
    tr.querySelector('.mw_err')?.remove();
    const numInput = tr.querySelector('.mw_number');
    if (numInput){
      numInput.classList.remove('mw_input_err');
      numInput.removeAttribute('title');
    }
  }

  // ---- Step 2: Save rows ----
  if (mw.step === 2){
    const msg  = el('mw_msg');
    const rows = Array.from(document.querySelectorAll('#mw_rows tr'));
    if (msg) msg.textContent = 'Saving…';

    // 1) Pre-validate + detect duplicates within this batch
    const numCounts = {};
    let hasLocalDup = false;
    for (const tr of rows){
      clearRowError(tr);
      const num = tr.querySelector('.mw_number')?.value?.trim();
      if (num){
        numCounts[num] = (numCounts[num] || 0) + 1;
      }
      const issue = tr.querySelector('.mw_issue')?.value?.trim();
      const amt   = tr.querySelector('.mw_amount')?.value?.trim();
      if (!issue || !num || !amt){
        setRowError(tr, 'Issue date, invoice number, and amount are required.');
        hasLocalDup = true; // block submit on basic missing data
      }
    }
    for (const tr of rows){
      const num = tr.querySelector('.mw_number')?.value?.trim();
      if (num && numCounts[num] > 1){
        setRowError(tr, `Duplicate in this list: "${num}". Please make each invoice number unique.`);
        hasLocalDup = true;
      }
    }
    if (hasLocalDup){
      if (msg) msg.textContent = 'Fix the highlighted rows before saving.';
      return;
    }

    // 2) Post rows (one by one; surface exact API error per row)
    let ok = 0, errs = 0;
    for (const tr of rows){
      const issue = tr.querySelector('.mw_issue')?.value?.trim();
      const num   = tr.querySelector('.mw_number')?.value?.trim();
      const amt   = tr.querySelector('.mw_amount')?.value?.trim();
      let   due   = tr.querySelector('.mw_due')?.value?.trim();

      if (!issue || !num || !amt){ errs++; continue; }

      if (!due && mw.customer && HAS_COMPUTE_DUE){
        const d = window.computeDueDateJs(
          issue,
          mw.customer.terms_type || 'net_30',
          mw.customer.terms_days || null
        );
        due = toISO(d);
      }

      try{
        const r = await fetch('/api/invoices', {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify({
            customer_id: mw.customer.id,
            invoice_number: num,
            amount_due: amt,
            issue_date: issue,
            due_date: due,
            terms_type: mw.customer.terms_type || 'net_30',
            terms_days: mw.customer.terms_type === 'custom' ? (mw.customer.terms_days || null) : null
          })
        });

        if (r.ok){
          ok++;
          clearRowError(tr);
        } else {
          errs++;
          let txt = `Row failed (${r.status})`;
          try {
            const j = await r.json();
            if (j?.detail) txt = j.detail; // e.g. 409: already in use
          } catch {}
          setRowError(tr, txt);
        }
      } catch (e){
        errs++;
        setRowError(tr, 'Network error — please retry.');
      }
    }

    if (msg) msg.textContent = `Saved ${ok} ${ok===1?'invoice':'invoices'}${errs?` • ${errs} failed — fix highlighted rows`:''}`;

    // Only close if everything succeeded; otherwise keep modal open for fixes
    if (errs === 0 && ok > 0){
      closeManualWizard();
      if (typeof loadInvoices === 'function') loadInvoices();
    }
  }
}

function mw_back(){
  // don't let the user hop back to Step 1 when locked from dashboard
  if (mw.lockCustomer && mw.step === 2) return;
  if (mw.step > 1){ mw.step--; mw_render(); }
}

/* -------- Wire open + nav buttons -------- */
document.addEventListener('DOMContentLoaded', ()=>{
  el('btn_open_manual_invoice')?.addEventListener('click', openManualWizard);
  el('mw_next')?.addEventListener('click', mw_next);
  el('mw_back')?.addEventListener('click', mw_back);
});

// --- expose manual-wizard APIs/State to other pages ---
window.mw = window.mw || mw;               // make the same object reachable via window
window.openManualWizard = openManualWizard;
window.closeManualWizard = closeManualWizard;
window.mw_render = mw_render;
