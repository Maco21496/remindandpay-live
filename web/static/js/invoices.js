// FINAL VERSION OF /static/js/invoices.js
;(function (global) {
  'use strict';

  const q  = (sel) => document.querySelector(sel);
  const qa = (sel) => Array.from(document.querySelectorAll(sel));
  const el = (id) => document.getElementById(id);

  const money = (n) => {
    try { return (window.AppCurrency && AppCurrency.format) ? AppCurrency.format(n) : ('£' + Number(n || 0).toFixed(2)); }
    catch { return '£' + Number(n || 0).toFixed(2); }
  };

  // Dates (same strategy as other pages)
  const fmtDate = (iso) => {
    if (!iso) return '';
    if (window.AppDate?.formatDate) return window.AppDate.formatDate(iso);
    try {
      const [Y, M, D] = iso.split('T')[0].split('-').map(x => parseInt(x, 10));
      const d = new Date(Y, (M - 1), D);
      const loc = window.__APP_SETTINGS__?.date_locale || 'en-GB';
      return d.toLocaleDateString(loc, { year: 'numeric', month: '2-digit', day: '2-digit' });
    } catch { return iso; }
  };
  const fmtDateTime = (iso) => {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      const loc = window.__APP_SETTINGS__?.date_locale || 'en-GB';
      return d.toLocaleString(loc, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch { return iso; }
  };

  // -------------------------------
  // Main invoice list
  // -------------------------------
  const pager = { page: 1, per: 50, pages: 1, total: 0 };
  let statusFilter = 'all'; // all|open|overdue|paid
  let searchTerm   = '';

  const rowsEl     = q('#inv_rows');
  const emptyEl    = q('#inv_empty');
  const chipsEl    = q('#inv_filters');
  const searchEl   = q('#inv_search');
  const perSel     = q('#inv_per');
  const prevBtn    = q('#inv_prev');
  const nextBtn    = q('#inv_next');
  const pageInfo   = q('#inv_page_info');
  const refreshBtn = q('#btn_refresh');

  function setPagingUI(){
    if (perSel) perSel.value = String(pager.per);
    if (pageInfo) pageInfo.textContent = `Page ${pager.page} / ${pager.pages} (${pager.total} invoices)`;
    if (prevBtn) prevBtn.disabled = pager.page <= 1;
    if (nextBtn) nextBtn.disabled = pager.page >= pager.pages;
  }

  async function loadKPIs(){
    const k1 = q('#kpi-outstanding'), k2 = q('#kpi-overdue'), k3 = q('#kpi-duesoon'), k4 = q('#kpi-paidmonth');
    try {
      const r = await fetch('/api/dashboard/summary');
      if (!r.ok) throw new Error();
      const d = await r.json();
      k1 && (k1.textContent = money(d.outstanding_total));
      k2 && (k2.textContent = money(d.overdue));
      k3 && (k3.textContent = money(d.due_soon));
      k4 && (k4.textContent = money(d.paid_this_month));
    } catch {}
  }

  async function loadInvoices(){
    if (!rowsEl) return;
    rowsEl.innerHTML = `<tr><td colspan="7" class="muted">Loading…</td></tr>`;

    const params = new URLSearchParams({
      status: statusFilter,
      page: String(pager.page),
      per_page: String(pager.per),
    });
    if (searchTerm) params.set('search', searchTerm);

    try {
      const r = await fetch(`/api/invoices/list?` + params.toString());
      if (!r.ok) throw new Error();
      const d = await r.json();

      pager.page = d.page; pager.per = d.per_page; pager.pages = d.pages; pager.total = d.total;
      setPagingUI();

      const items = d.items || [];
      if (!items.length){
        rowsEl.innerHTML = '';
        if (emptyEl) emptyEl.style.display = 'block';
        return;
      }
      if (emptyEl) emptyEl.style.display = 'none';

      rowsEl.innerHTML = items.map(x => {
        const badge =
          x.status === 'paid'    ? '<span class="pill paid">paid</span>' :
          x.status === 'overdue' ? '<span class="pill overdue">overdue</span>' :
                                   '<span class="pill open">open</span>';
        return `
          <tr>
            <td>${fmtDate(x.issue_date)}</td>
            <td>${fmtDate(x.due_date)}</td>
            <td><a href="/customers/${x.customer_id}" class="link">${x.customer_name}</a></td>
            <td>${x.invoice_number || ''}</td>
            <td style="text-align:right;">${money(x.amount)}</td>
            <td style="text-align:right;">${money(x.remaining)}</td>
            <td>${badge}</td>
          </tr>`;
      }).join('');
    } catch {
      rowsEl.innerHTML = `<tr><td colspan="7">Failed to load.</td></tr>`;
    }
  }

  // -------------------------------
  // Imported invoice queue
  // -------------------------------
  const invTabs           = q('#inv_tabs');
  const invPanelMain      = q('#inv_panel_main');
  const invPanelImported  = q('#inv_panel_imported');

  const qRowsEl    = q('#invq_rows');
  const qEmptyEl   = q('#invq_empty');
  const qMsgEl     = q('#invq_msg');
  const qRefresh   = q('#invq_refresh');
  const qClear     = q('#invq_clear');
  const qImport    = q('#invq_import');
  const qAllCb     = q('#invq_all');

  let importedLoaded = false;

  function setQueueMessage(text){ if (qMsgEl) qMsgEl.textContent = text || ''; }
  function _rowCheckbox(id){ return `<input type="checkbox" class="invq_cb" data-id="${id}">`; }
  function selectedIds(){ return qa('.invq_cb:checked').map(cb => Number(cb.dataset.id)).filter(Boolean); }

  function renderQueueRows(items){
    return items.map(item => {
      const f = item.fields || {};
      const invNo   = f.invoice_number || '';
      const issue   = f.issue_date || '';
      const due     = f.due_date || '';
      const amount  = (f.amount_due !== undefined && f.amount_due !== null) ? String(f.amount_due) : '';
      const custVal = f._customer_lookup_value || '';
      const status  = item.status || '';
      const err     = item.error_message || '';
      return `
        <tr>
          <td style="text-align:center;">${_rowCheckbox(item.id)}</td>
          <td>${fmtDateTime(item.received_at)}</td>
          <td>${item.source || ''}</td>
          <td>${item.original_filename || ''}</td>
          <td>${invNo}</td>
          <td>${issue}</td>
          <td>${due}</td>
          <td>${amount}</td>
          <td>${custVal}</td>
          <td>${status}</td>
          <td>${err}</td>
          <td><button type="button" class="btn btn--ghost invq_del" data-id="${item.id}">Delete</button></td>
        </tr>`;
    }).join('');
  }

  async function loadImportedQueue(){
    if (!qRowsEl) return;
    setQueueMessage('');
    qRowsEl.innerHTML = `<tr><td colspan="12" class="muted">Loading…</td></tr>`;
    try{
      const r = await fetch('/api/inbound/queue');
      if(!r.ok) throw new Error();
      const d = await r.json();
      importedLoaded = true;

      const items = d.items || [];
      if (!items.length){
        qRowsEl.innerHTML = '';
        if (qEmptyEl) qEmptyEl.style.display = 'block';
        if (qAllCb) qAllCb.checked = false;
        return;
      }
      if (qEmptyEl) qEmptyEl.style.display = 'none';

      qRowsEl.innerHTML = renderQueueRows(items);
      if (qAllCb) qAllCb.checked = false;
    } catch {
      qRowsEl.innerHTML = `<tr><td colspan="12">Failed to load.</td></tr>`;
      setQueueMessage('Failed to load imported invoices.');
    }
  }

  async function deleteQueueRow(id){
    try{
      const r = await fetch(`/api/inbound/queue/${id}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await loadImportedQueue();
    } catch {
      setQueueMessage('Failed to delete row.');
    }
  }

  async function clearQueue(){
    try{
      const r = await fetch('/api/inbound/queue/clear', { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await loadImportedQueue();
      setQueueMessage('All imported invoices cleared for this account.');
    } catch {
      setQueueMessage('Failed to clear imported invoices.');
    }
  }

    // auto-import toggle JS
  (async function () {
    const toggle = document.querySelector('#invq_auto_toggle');
    const note   = document.querySelector('#invq_auto_note');
    if (!toggle) return;

    async function refreshToggle() {
      try {
        const r = await fetch('/api/postmark/auto-import');
        if (!r.ok) throw new Error();
        const d = await r.json();
        toggle.checked = !!d.enabled;
        if (note) note.textContent = d.enabled
          ? 'New valid emails will be imported automatically.'
          : 'Auto-import is off.';
      } catch {
        if (note) note.textContent = 'Unable to read auto-import setting.';
      }
    }

    async function saveToggle() {
      try {
        toggle.disabled = true;
        const r = await fetch('/api/postmark/auto-import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: !!toggle.checked })
        });
        await refreshToggle();
      } catch {
        if (note) note.textContent = 'Failed to save setting.';
      } finally {
        toggle.disabled = false;
      }
    }

    toggle.addEventListener('change', saveToggle);
    await refreshToggle();
  })();


  async function promoteSelected(e){
    if (e?.preventDefault) e.preventDefault();
    if (e?.stopPropagation) e.stopPropagation();

    const msg = q('#invq_msg');
    const btn = q('#invq_import');
    const ids = selectedIds();
    const setMsg = (t) => { if (msg) msg.textContent = t || ''; };

    if (!ids.length){
      setMsg('Select at least one row.');
      return;
    }

    try {
      if (btn) { btn.disabled = true; btn.classList.add('is-loading'); }

      console.debug('[invq] promoting ids:', ids);

      const res = await fetch('/api/inbound/queue/promote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids })
      });

      const textBody = await res.text();
      let body;
      try { body = textBody ? JSON.parse(textBody) : {}; } catch { body = { raw: textBody }; }

      if (!res.ok) {
        setMsg(`Import failed (${res.status}). ${typeof body === 'object' ? JSON.stringify(body) : String(body)}`);
        console.error('[invq] promote error', res.status, body);
        return;
      }

      const imported = typeof body.imported === 'number' ? body.imported : 0;
      const failedCt = Array.isArray(body.failed) ? body.failed.length : 0;
      setMsg(`Imported ${imported}` + (failedCt ? ` — ${failedCt} failed` : ''));

      if (typeof global.loadInvoices === 'function') await global.loadInvoices();
      if (typeof global.loadImportedQueue === 'function') await global.loadImportedQueue();

    } catch (err) {
      setMsg('Import failed (JS error). See console.');
      console.error('[invq] promote exception', err);
    } finally {
      if (btn) { btn.disabled = false; btn.classList.remove('is-loading'); }
    }
  }

  // -------------------------------
  // Events
  // -------------------------------

  // Main list filters
  chipsEl?.addEventListener('click', (e) => {
    const btn = e.target.closest('.chip');
    if (!btn) return;
    qa('#inv_filters .chip').forEach(c => c.classList.remove('is-active'));
    btn.classList.add('is-active');
    statusFilter = btn.dataset.filter || 'all';
    pager.page = 1;
    loadInvoices();
  });

  // Search
  let searchTimer = null;
  searchEl?.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchTerm = searchEl.value.trim();
      pager.page = 1;
      loadInvoices();
    }, 250);
  });

  // Paging
  perSel?.addEventListener('change', () => {
    pager.per = Number(perSel.value) || 50;
    pager.page = 1;
    loadInvoices();
  });
  prevBtn?.addEventListener('click', () => { if (pager.page > 1) { pager.page--; loadInvoices(); } });
  nextBtn?.addEventListener('click', () => { if (pager.page < pager.pages) { pager.page++; loadInvoices(); } });
  refreshBtn?.addEventListener('click', () => { loadInvoices(); });

  // Tab bar: invoices vs imported
  invTabs?.addEventListener('click', (e) => {
    const btn = e.target.closest('.tab');
    if (!btn) return;
    const which = btn.dataset.tab || 'main';
    qa('#inv_tabs .tab').forEach(t => t.classList.remove('is-active'));
    btn.classList.add('is-active');

    if (which === 'imported') {
      if (invPanelMain) invPanelMain.style.display = 'none';
      if (invPanelImported) invPanelImported.style.display = 'block';
      if (!importedLoaded) loadImportedQueue();
    } else {
      if (invPanelMain) invPanelMain.style.display = 'block';
      if (invPanelImported) invPanelImported.style.display = 'none';
    }
  });

  // Imported queue: controls
  qRefresh?.addEventListener('click', () => { loadImportedQueue(); });
  qClear?.addEventListener('click', () => {
    if (!window.confirm('Clear all imported invoices for this account?')) return;
    clearQueue();
  });

  // IMPORTANT: pass the event so preventDefault() can run
  qImport?.addEventListener('click', promoteSelected, { passive: false });

  // Imported queue: master checkbox
  qAllCb?.addEventListener('change', () => {
    const on = !!qAllCb.checked;
    qa('.invq_cb').forEach(cb => { cb.checked = on; });
  });
  // Keep master checkbox in sync while clicking individual rows
  qRowsEl?.addEventListener('change', (e) => {
    const cb = e.target.closest('.invq_cb');
    if (!cb || !qAllCb) return;
    const boxes = qa('.invq_cb');
    const allChecked = boxes.length > 0 && boxes.every(x => x.checked);
    qAllCb.checked = allChecked;
  });

  // Imported queue: row delete
  qRowsEl?.addEventListener('click', (e) => {
    const btn = e.target.closest('.invq_del');
    if (!btn) return;
    const id = btn.dataset.id;
    if (!id) return;
    if (!window.confirm('Remove this imported invoice from the queue?')) return;
    deleteQueueRow(id);
  });

  // -------------------------------
  // Boot
  // -------------------------------
  document.addEventListener('DOMContentLoaded', async () => {
    setPagingUI();
    await loadKPIs();
    await loadInvoices();
  });

  // Expose helpers for cross-calls
  global.el = el;
  global.loadInvoices = loadInvoices;
  global.loadImportedQueue = loadImportedQueue;

})(window);
