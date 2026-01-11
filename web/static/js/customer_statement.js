// customer_statement.js — Statement page (uses /summary endpoint)
(() => {
  // ---------- refs ----------
  function getCustId(){
    const boot = document.getElementById('st-boot');
    const idAttr = Number(boot?.dataset?.id || 0);
    if (idAttr) return idAttr;
    const m = location.pathname.match(/\/customers\/(\d+)\/statement/);
    return m ? Number(m[1]) : 0;
  }
  const custId   = getCustId();
  const rowsEl   = document.getElementById('st_rows');
  const emptyEl  = document.getElementById('st_empty');
  const btnRun   = document.getElementById('st_run');
  const selPer   = document.getElementById('st_period');
  const inFrom   = document.getElementById('st_from');
  const inTo     = document.getElementById('st_to');
  const cbAfter  = document.getElementById('st_inc_after');
  const btnDownload = document.getElementById('btn_download_pdf');
  const $        = (sel) => document.querySelector(sel);
  const money    = (n) => {
    try { return (window.AppCurrency && AppCurrency.format) ? AppCurrency.format(n) : ('£' + Number(n||0).toFixed(2)); }
    catch { return '£' + Number(n||0).toFixed(2); }
  };

  // ---------- date helpers ----------
  const pad2 = (n) => String(n).padStart(2,'0');
  function ymd(d){ return `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}`; }
  function fmtDate(iso){
    if (!iso) return '';
    if (window.AppDate?.formatDate) return AppDate.formatDate(iso);
    const loc = (window.__APP_SETTINGS__?.date_locale) || 'en-GB';
    const d = new Date(iso + 'T00:00:00'); if (isNaN(d)) return '';
    return d.toLocaleDateString(loc);
  }
  function setEnabledDate(input, enable){
    input.disabled = !enable;
    input.readOnly = !enable;
    if (input._flatpickr?.altInput){
      input._flatpickr.altInput.disabled = !enable;
      input._flatpickr.altInput.readOnly = !enable;
    }
  }
  function setDateValue(input, iso){
    if (input._flatpickr){
      input._flatpickr.setDate(iso, true);
    } else {
      input.value = iso;
    }
  }

  // ---------- settings & letterhead ----------
  async function ensureAppSettings(){
    if (window.__APP_SETTINGS__) return window.__APP_SETTINGS__;
    try{
      const r = await fetch('/api/settings', { cache: 'no-store' });
      if (r.ok){
        const s = await r.json();
        window.__APP_SETTINGS__ = s;
        return s;
      }
    }catch{}
    return {};
  }
  function initLetterhead(){
    const s = window.__APP_SETTINGS__ || {};
    const logo = s.org_logo_url;
    const orgAdr = s.org_address;
    if (logo){ const img = $('#st_logo'); if (img){ img.src = logo; img.style.display = 'block'; } }
    if (orgAdr){ const el = $('#st_org_addr'); if (el) el.textContent = orgAdr; }

    const bootEl = document.getElementById('st-boot');
    const d = bootEl?.dataset || {};
    const lines = [];
    if (d.line1) lines.push(d.line1);
    if (d.line2) lines.push(d.line2);
    const cityRegion = [d.city, d.region].filter(Boolean).join(', ');
    if (cityRegion) lines.push(cityRegion);
    const postCountry = [d.postcode, d.country].filter(Boolean).join(' ');
    if (postCountry) lines.push(postCountry);
    const addr = lines.join('\n');

    let custBlock = (bootEl?.dataset?.name || '');
    if (addr) custBlock += (custBlock ? '\n' : '') + addr;
    const ca = $('#st_cust_addr'); if (ca) ca.textContent = custBlock;
  }

  // ---------- period selector (UI only; summary uses as-of = To) ----------
  function labelFor(y,m){ const dt = new Date(y, m, 1); return dt.toLocaleString(undefined, { month:'long', year:'numeric' }); }
  function showCustom(show){
    const f1 = inFrom.closest('.field');
    const f2 = inTo.closest('.field');
    setEnabledDate(inFrom, show);
    setEnabledDate(inTo,   show);
    if (f1) f1.style.display = show ? '' : 'none';
    if (f2) f2.style.display = show ? '' : 'none';
    if (btnRun) btnRun.style.display = show ? '' : 'none';
  }
  function populatePeriods(){
    const today = new Date();
    const opts = [];
    for (let i=0;i<12;i++){
      const d = new Date(today.getFullYear(), today.getMonth()-i, 1);
      const value = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;
      opts.push({ value, label: labelFor(d.getFullYear(), d.getMonth()) });
    }
    selPer.innerHTML = [
      ...opts.map(o => `<option value="${o.value}">${o.label}</option>`),
      `<option value="custom">Custom range…</option>`
    ].join('');
    selPer.value = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}`;
    applyPeriod();
  }
  function applyPeriod(){
    const v = selPer.value;
    if (v === 'custom'){ showCustom(true); return; }
    showCustom(false);
    const [y,m] = v.split('-').map(Number);
    const s = new Date(y, m-1, 1);
    const e = new Date(y, m, 0);
    const toISO = d => ymd(d);
    setDateValue(inFrom, toISO(s));
    setDateValue(inTo,   toISO(e));
  }
  selPer?.addEventListener('change', () => { applyPeriod(); if (selPer.value !== 'custom') run(); });

  // ---------- API ----------
  async function fetchSummary(customerId, params=''){
    const url = `/api/statements/customer/${customerId}/summary` + (params ? `?${params}` : '');
    const r = await fetch(url, { cache:'no-store' });
    if (!r.ok) throw new Error('summary');
    return r.json(); // { as_of, totals, buckets, open_invoices }
  }

  // ---------- main ----------
  async function run(){
    const id = custId;
    if (!id) return;

    const usingCustom = selPer.value === 'custom';
    const df = inFrom.value;
    const dt = inTo.value || ymd(new Date());

    const qs = new URLSearchParams();
    if (usingCustom && df) qs.set('date_from', df); // kept for parity; not used server-side
    if (dt) qs.set('date_to', dt);
    if (cbAfter.checked) qs.set('include_after_payments', 'true');

    const asofEl = $('#st_asof'); if (asofEl) asofEl.textContent = `As at ${fmtDate(dt)}`;

    rowsEl.innerHTML = `<tr><td colspan="6" class="muted">Loading…</td></tr>`;
    emptyEl.style.display = "none";

    let data;
    try {
      data = await fetchSummary(id, qs.toString());
    } catch {
      rowsEl.innerHTML = `<tr><td colspan="6" class="empty">Failed to load</td></tr>`;
      return;
    }

    // KPIs
    $('#ag_total_out').textContent = money(data.totals?.total_outstanding_gross || 0);
    $('#ag_overdue').textContent   = money(data.totals?.overdue_total || 0);
    $('#ag_od_0_30').textContent   = money(data.buckets?.overdue_0_30 || 0);
    $('#ag_od_31_60').textContent  = money(data.buckets?.overdue_31_60 || 0);
    $('#ag_od_61_90').textContent  = money(data.buckets?.overdue_61_90 || 0);
    $('#ag_od_90p').textContent    = money(data.buckets?.overdue_90p || 0);

    const open = Array.isArray(data.open_invoices) ? data.open_invoices : [];
    if (!open.length){
      rowsEl.innerHTML = "";
      emptyEl.style.display = "block";
      return;
    }

    // Table (running outstanding)
    let runOut = 0;
    rowsEl.innerHTML = open
      .sort((a,b) => (String(a.issue_date||'') < String(b.issue_date||'') ? -1 : 1))
      .map(it => {
        runOut += Number(it.outstanding || 0);
        return `
          <tr>
            <td>${fmtDate((it.issue_date||'').slice(0,10))}</td>
            <td>${it.ref || ''}</td>
            <td>${it.desc || ''}</td>
            <td style="text-align:right;">${money(it.total)}</td>
            <td style="text-align:right;">${it.paid_to_date ? money(it.paid_to_date) : ''}</td>
            <td style="text-align:right;"><strong>${money(runOut)}</strong></td>
          </tr>
        `;
      }).join('');
  }

  // wire & boot
  btnRun?.addEventListener('click', (e)=>{ e.preventDefault(); run(); });
  cbAfter?.addEventListener('change', run);
  btnDownload?.addEventListener('click', (e)=>{
    e.preventDefault();
    const usingCustom = selPer.value === 'custom';
    const df = inFrom.value;
    const dt = inTo.value || ymd(new Date());
    const qs = new URLSearchParams();
    if (usingCustom && df) qs.set('date_from', df);
    if (dt) qs.set('date_to', dt);
    if (cbAfter.checked) qs.set('include_after_payments', 'true');
    const url = `/api/statements/customer/${custId}/pdf` + (qs.toString() ? `?${qs.toString()}` : '');
    // Navigate current tab to force download (more reliable than window.open with blockers)
    window.location.href = url;
  });

  (async () => {
    await ensureAppSettings();
    initLetterhead();
    populatePeriods();
    run();
  })();
})();
