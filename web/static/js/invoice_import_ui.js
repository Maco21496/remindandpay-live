// FINAL VERSION OF /static/js/invoice_import_ui.js
// Adds Visual Mapper + line-region extractor + active template wiring.

(function(){
  const $ = (id) => document.getElementById(id);

  // ---------- Settings + preview wiring ----------
  const iiStatus   = $('ii_status');
  const iiLastSeen = $('ii_last_seen');
  const iiGenerate = $('ii_generate');
  const iiAddress  = $('ii_address');
  const iiCopy     = $('ii_copy');
  const iiActive   = $('ii_active');
  const iiMsg      = $('ii_msg');

  // Active template select (bottom of page)
  const iiBlockTemplate = $('ii_block_template_name');

  // PDF preview controls
  const iiPdfFile   = $('ii_pdf_file');
  const iiPdfBtn    = $('ii_pdf_preview');
  const iiPdfMsg    = $('ii_pdf_msg');
  const iiPdfResult = $('ii_pdf_result');

  // Line-region extractor (textarea runner)
  const iiLineTpl    = $('ii_line_tpl');
  const iiLineBtn    = $('ii_line_extract');
  const iiLineMsg    = $('ii_line_msg');
  const iiLineResult = $('ii_line_result');

  // Options
  const caseIns = $('ii_case_ins');

  let readerInputs = null;

  function setStatus(s) {
    if (iiStatus) iiStatus.textContent = s || '';
  }

  function setLastSeen(ts) {
    if (iiLastSeen) {
      iiLastSeen.textContent = ts ? `Last inbound activity: ${ts}` : '';
    }
  }

  // ---------- Active template dropdown helper ----------

  function getTemplateEndpoint(reader) {
    if (reader === 'html') return '/api/inbound/html/templates';
    return '/api/inbound/blocks/templates';
  }

  async function loadActiveTemplates(selectedName, reader) {
    if (!iiBlockTemplate) return;

    // Reset options
    iiBlockTemplate.innerHTML = '';
    const optNone = document.createElement('option');
    optNone.value = '';
    optNone.textContent = '(none)';
    iiBlockTemplate.appendChild(optNone);

    try {
      const endpoint = getTemplateEndpoint(reader);
      const r = await fetch(endpoint, {
        method: 'GET',
        cache: 'no-store'
      });
      if (!r.ok) {
        let detail = '';
        try { detail = await r.text(); } catch {}
        console.error('GET templates failed', r.status, detail);
        return;
      }
      const j = await r.json();
      const list = Array.isArray(j.templates) ? j.templates : [];

      for (const item of list) {
        if (!item || !item.template_name) continue;
        const opt = document.createElement('option');
        opt.value = item.template_name;
        opt.textContent = item.template_name;
        iiBlockTemplate.appendChild(opt);
      }

      // Try to select the saved active template name (if any)
      if (selectedName) {
        let found = false;
        for (const opt of iiBlockTemplate.options) {
          if (opt.value === selectedName) {
            found = true;
            break;
          }
        }
        if (found) {
          iiBlockTemplate.value = selectedName;
        }
      }
    } catch (e) {
      console.error('Error loading active templates', e);
    }
  }

  // ---------- Load / save settings ----------

  async function load() {
    try {
      const r = await fetch('/api/inbound/settings', { cache: 'no-store' });
      if (!r.ok) throw new Error(`GET /api/inbound/settings ${r.status}`);
      const j = await r.json();

      if (iiAddress) iiAddress.value = j.inbound_address || '';
      setStatus(
        j.inbound_token
          ? (j.inbound_active ? 'Active' : 'Not active')
          : 'Not configured'
      );
      setLastSeen(j.inbound_last_seen_at);

      readerInputs = document.querySelectorAll('input[name="ii_reader"]');
      if (readerInputs && j.inbound_reader) {
        [...readerInputs].forEach(radio => {
          radio.checked = (radio.value === j.inbound_reader);
        });
      }

      const savedTemplateName =
        (typeof j.inbound_block_template_name === 'string'
          ? j.inbound_block_template_name
          : '').trim();

      // Populate dropdown from the correct templates endpoint and select the saved name
      await loadActiveTemplates(savedTemplateName, (j.inbound_reader || 'pdf'));

      if (iiActive) iiActive.checked = !!j.inbound_active;
      if (iiMsg) iiMsg.textContent = '';
    } catch (e) {
      if (iiMsg) iiMsg.textContent = 'Failed to load.';
      console.error(e);
    }
  }

  async function generate() {
    if (iiMsg) iiMsg.textContent = 'Generating…';
    try {
      const r = await fetch('/api/inbound/generate', { method: 'POST' });
      if (!r.ok) throw new Error(`POST /api/inbound/generate ${r.status}`);
      await load();
      if (iiMsg) iiMsg.textContent = 'Address generated.';
    } catch (e) {
      if (iiMsg) iiMsg.textContent = 'Generate failed.';
      console.error(e);
    }
  }

  async function save() {
    if (iiMsg) iiMsg.textContent = 'Saving…';
    try {
      const reader =
        [...document.querySelectorAll('input[name="ii_reader"]')]
          .find(r => r.checked)?.value || null;
      const active = !!(iiActive && iiActive.checked);

      const blockTemplateName =
        iiBlockTemplate && typeof iiBlockTemplate.value === 'string'
          ? iiBlockTemplate.value
          : '';

      const body = {
        inbound_active: active,
        inbound_reader: reader || 'pdf',
        inbound_mapping_json: {},            // mapping JSON is validated/saved separately
        inbound_block_template_name: blockTemplateName || null
      };

      const r = await fetch('/api/inbound/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      if (!r.ok) {
        let detail = '';
        try {
          const j = await r.json();
          detail = j?.detail ? JSON.stringify(j.detail) : '';
        } catch {}
        throw new Error(detail || `POST /api/inbound/save ${r.status}`);
      }
      await load();
      if (iiMsg) iiMsg.textContent = 'Saved.';
    } catch (e) {
      if (iiMsg) iiMsg.textContent = 'Save failed.';
      console.error(e);
    }
  }

  function copyAddr() {
    if (!iiAddress || !iiAddress.value) return;
    iiAddress.select();
    document.execCommand('copy');
  }

  function handleReaderChange() {
    const reader =
      [...document.querySelectorAll('input[name="ii_reader"]')]
        .find(r => r.checked)?.value || 'pdf';
    loadActiveTemplates(iiBlockTemplate?.value || '', reader);
  }

  // ---------- Inline manual editor helpers (existing preview UI) ----------

  const FIELD_KEYS = ['customer_name','invoice_number','issue_date','due_date','amount_due'];

  function collectManualEditors() {
    const out = {};
    for (const f of FIELD_KEYS) {
      const use  = document.getElementById(`ii_use_${f}`);
      const anch = document.getElementById(`ii_m_${f}`);
      const mode = document.getElementById(`ii_mode_${f}`);
      if (use || anch || mode) {
        out[f] = {
          use: !!(use && use.checked),
          anchor: (anch && anch.value) ? anch.value : '',
          mode: (mode && mode.value === 'next') ? 'next' : 'same'
        };
      }
    }
    return out;
  }

  function restoreManualEditors(saved) {
    if (!saved) return;
    for (const f of FIELD_KEYS) {
      const entry = saved[f];
      if (!entry) continue;
      const use  = document.getElementById(`ii_use_${f}`);
      const anch = document.getElementById(`ii_m_${f}`);
      const mode = document.getElementById(`ii_mode_${f}`);
      if (use)  use.checked = !!entry.use;
      if (anch) anch.value  = entry.anchor || '';
      if (mode) mode.value  = (entry.mode === 'next') ? 'next' : 'same';
    }
  }

  function mkManualEditorRow(fieldKey, label, value, manualUsed) {
    const val = value ? String(value) : '';
    const used = !!manualUsed;
    const idAnchor = `ii_m_${fieldKey}`;
    const idMode   = `ii_mode_${fieldKey}`;
    const idUse    = `ii_use_${fieldKey}`;
    const tag = used
      ? '<span class="chip" style="background:#ecfdf5;color:#065f46;border:1px solid #a7f3d0;">manual</span>'
      : '';

    return `
      <tr>
        <td style="padding:6px;border-bottom:1px solid #f3f4f6;white-space:nowrap;"><strong>${label}</strong></td>
        <td style="padding:6px;border-bottom:1px solid #f3f4f6;">
          <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
            <div>${val || '<span class="muted">not detected</span>'}</div>
            ${tag}
          </div>
          <div class="muted" style="margin-top:6px;">
            <label style="display:inline-flex;align-items:center;gap:6px;">
              <input type="checkbox" id="${idUse}"> Use manual
            </label>
            <input id="${idAnchor}" placeholder="Anchor text (e.g. Bill To:)" style="height:30px;padding:0 8px;margin-left:8px;max-width:260px;">
            <select id="${idMode}" style="height:30px;padding:0 8px;margin-left:8px;">
              <option value="same">Same line</option>
              <option value="next">Next line</option>
            </select>
          </div>
        </td>
        <td style="padding:6px;border-bottom:1px solid #f3f4f6;text-align:right;">
          <span style="${val ? 'color:#059669' : 'color:#6b7280'}">${val ? '✔' : '—'}</span>
        </td>
      </tr>
    `;
  }

  function renderPreview(result) {
    if (!iiPdfResult) return;
    const c = result?.candidates || {};
    const used = result?.manual_used || {};

    const html = `
      <div style="border:1px solid #eee;border-radius:8px;overflow:hidden;">
        <div style="padding:10px;border-bottom:1px solid #eee;background:#fafafa;">
          <strong>Detected fields</strong>
          <span class="muted" style="margin-left:8px;">(${result.text_chars} chars parsed)</span>
        </div>
        <div style="padding:10px;">
          <div style="overflow:auto;">
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
              <thead>
                <tr>
                  <th style="text-align:left;padding:6px;border-bottom:1px solid #eee;">Field</th>
                  <th style="text-align:left;padding:6px;border-bottom:1px solid #eee;">Value & manual override</th>
                  <th style="text-align:right;padding:6px;border-bottom:1px solid #eee;">Status</th>
                </tr>
              </thead>
              <tbody>
                ${mkManualEditorRow('customer_name',  'Customer name',  c.customer_name,  used.customer_name)}
                ${mkManualEditorRow('invoice_number', 'Invoice number', c.invoice_number, used.invoice_number)}
                ${mkManualEditorRow('issue_date',     'Issue date',     c.issue_date,     used.issue_date)}
                ${mkManualEditorRow('due_date',       'Due date',       c.due_date,       used.due_date)}
                ${mkManualEditorRow('amount_due',     'Amount due',     c.amount_due,     used.amount_due)}
                <tr>
                  <td style="padding:6px;border-bottom:1px solid #f3f4f6;"><strong>Currency</strong></td>
                  <td style="padding:6px;border-bottom:1px solid #f3f4f6;">${c.currency || '<span class="muted">not detected</span>'}</td>
                  <td style="padding:6px;border-bottom:1px solid #f3f4f6;text-align:right;"><span style="${c.currency ? 'color:#059669' : 'color:#6b7280'}">${c.currency ? '✔' : '—'}</span></td>
                </tr>
              </tbody>
            </table>
          </div>
          ${Array.isArray(result.notes) && result.notes.length
            ? `<div style="margin-top:10px;"><strong>Notes</strong><ul class="muted" style="margin:6px 0 0 16px;">${result.notes.map(n=>`<li>${n}</li>`).join('')}</ul></div>`
            : ''}
          <details style="margin-top:10px;">
            <summary class="muted">View text sample</summary>
            <pre style="white-space:pre-wrap;font-size:12px;background:#f9fafb;border:1px solid #eee;padding:8px;border-radius:6px;margin-top:6px;">${(result.text_sample || '').replace(/[<&>]/g, s => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[s]))}</pre>
          </details>
        </div>
      </div>
    `;

    iiPdfResult.innerHTML = html;
    iiPdfResult.style.display = 'block';
    iiPdfResult.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  window.renderInvoicePreview = renderPreview;

  async function previewPdf() {
    if (!iiPdfFile?.files?.length) {
      if (iiPdfMsg) iiPdfMsg.textContent = 'Choose a PDF first.';
      return;
    }

    const savedEditors = collectManualEditors();
    if (iiPdfMsg) iiPdfMsg.textContent = 'Reading…';

    const fd = new FormData();
    fd.append('file', iiPdfFile.files[0]);

    for (const f of FIELD_KEYS){
      const entry = savedEditors[f];
      if (entry && entry.use && entry.anchor.trim()){
        fd.append(`manual_${f}`, entry.anchor.trim());
        fd.append(`manual_mode_${f}`, entry.mode === 'next' ? 'next' : 'same');
      }
    }
    fd.append('manual_case_insensitive', caseIns?.checked ? 'true' : 'false');

    if (iiPdfResult) iiPdfResult.style.display = 'none';

    try {
      const r = await fetch('/api/inbound/pdf/preview', { method: 'POST', body: fd });
      if (!r.ok) {
        let detail = '';
        try { const j = await r.json(); detail = j?.detail || ''; } catch {}
        throw new Error(detail || `Preview failed (${r.status})`);
      }
      const result = await r.json();

      renderPreview(result);
      restoreManualEditors(savedEditors);
      if (iiPdfMsg) iiPdfMsg.textContent = 'Preview ready.';
    } catch (e) {
      console.error(e);
      if (iiPdfMsg) iiPdfMsg.textContent = e?.message || 'Preview failed.';
    }
  }

  // ------- Line-region extractor -------

  function renderLineFields(fieldsObj) {
    if (!iiLineResult) return;
    const entries = Object.entries(fieldsObj || {});
    if (!entries.length) {
      iiLineResult.innerHTML = '<div class="muted">No fields returned.</div>';
      iiLineResult.style.display = 'block';
      return;
    }
    const rows = entries.map(([k,v]) => `
      <tr>
        <td style="padding:6px;border-bottom:1px solid #f3f4f6;white-space:nowrap;"><strong>${k}</strong></td>
        <td style="padding:6px;border-bottom:1px solid #f3f4f6;">${(v ?? '').toString().replace(/[<&>]/g, s => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[s]))}</td>
      </tr>
    `).join('');

    iiLineResult.innerHTML = `
      <div style="border:1px solid #eee;border-radius:8px;overflow:hidden;">
        <div style="padding:10px;border-bottom:1px solid #eee;background:#fafafa;">
          <strong>Line-region fields</strong>
        </div>
        <div style="padding:10px; overflow:auto;">
          <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    `;
    iiLineResult.style.display = 'block';
    iiLineResult.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function lineExtract() {
    if (iiLineMsg) iiLineMsg.textContent = '';
    if (iiLineResult) iiLineResult.style.display = 'none';

    if (!iiPdfFile?.files?.length) {
      if (iiLineMsg) iiLineMsg.textContent = 'Choose a PDF first.';
      return;
    }

    const raw = iiLineTpl?.value || '';
    let tpl;
    try {
      tpl = JSON.parse(raw);
      if (!tpl || typeof tpl !== 'object') throw new Error('Template must be an object.');
    } catch {
      if (iiLineMsg) iiLineMsg.textContent = 'Template JSON is invalid.';
      return;
    }

    if (iiLineMsg) iiLineMsg.textContent = 'Extracting…';
    const fd = new FormData();
    fd.append('file', iiPdfFile.files[0]);
    fd.append('template_json', JSON.stringify(tpl));

    try {
      const r = await fetch('/api/inbound/lines/extract', {
        method: 'POST',
        body: fd,
        credentials: 'include'
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || r.statusText);

      const fields = j.fields || {};
      renderLineFields(fields);
      if (iiLineMsg) iiLineMsg.textContent = 'Done.';
    } catch (e) {
      console.error(e);
      if (iiLineMsg) iiLineMsg.textContent = e?.message || 'Extract failed.';
    }
  }

  // ---------- Visual Mapper (row band) ----------
  // (unchanged – uses vm_* IDs and writes into iiLineTpl)

  const vmRows   = $('vm_rows');
  const vmBand   = $('vm_band');
  const vmFill   = $('vm_band_fill');
  const vmHL     = $('vm_handle_l');
  const vmHR     = $('vm_handle_r');
  const vmLoad   = $('vm_load');
  const vmMsg    = $('vm_msg');
  const vmField  = $('vm_field');
  const vmPost   = $('vm_post');
  const vmAdd    = $('vm_add');
  const vmLive   = $('vm_live_val');
  const vmTplTA  = iiLineTpl;

  const VM_ENABLED = !!(vmRows && vmBand && vmHL && vmHR && vmTplTA);

  let vmLines = [];
  let selStart = null;
  let selEnd   = null;
  let xs = 25;
  let xe = 75;
  let tplState = { template_id: 'user-layout', fields: [] };

  function renderBand(){
    if (!VM_ENABLED) return;
    const w = vmBand.clientWidth || 1;
    const lpx = Math.round((xs/100)*w);
    const rpx = Math.round((xe/100)*w);
    vmHL.style.left  = `${Math.max(0, lpx - 5)}px`;
    vmHR.style.right = `${Math.max(0, (w - rpx) - 5)}px`;
    vmFill.style.left  = `${Math.min(lpx, rpx)}px`;
    vmFill.style.right = `${Math.max(0, w - Math.max(lpx, rpx))}px`;
  }

  function rowHTML(ln){
    const active = (selStart !== null && selEnd !== null &&
      ln.index >= Math.min(selStart, selEnd) &&
      ln.index <= Math.max(selStart, selEnd));
    return `
      <div class="vm_row ${active?'is-active':''}"
           data-idx="${ln.index}"
           style="display:grid; grid-template-columns:56px 1fr; gap:8px; padding:6px 8px; border-bottom:1px solid #f1f5f9; cursor:pointer;">
        <div class="muted" style="white-space:nowrap;">#${ln.index}</div>
        <div>${(ln.text || '').replace(/&/g,'&amp;').replace(/</g,'&lt;')}</div>
      </div>
    `;
  }

  function renderRows(){
    if (!VM_ENABLED) return;
    vmRows.innerHTML = (vmLines.length
      ? vmLines.map(rowHTML).join('')
      : '<div class="muted" style="padding:8px;">No rows</div>');
  }

  function normalizeSel(){
    if (selStart === null && selEnd === null) return null;
    const a = Math.max(1, Math.min(selStart||1, selEnd||1));
    const b = Math.max(1, Math.max(selStart||1, selEnd||1));
    return {row_start:a, row_end:b};
  }

  async function livePreview(){
    if (!VM_ENABLED) return;
    const block = normalizeSel();
    if (!block || !iiPdfFile?.files?.length) {
      if (vmLive) vmLive.textContent = '';
      return;
    }

    const tempFieldKey = '__vm_live__';
    const tempTpl = {
      template_id: 'vm-live',
      fields: [{
        field_key: tempFieldKey,
        page: 1,
        row_start: block.row_start,
        row_end: block.row_end,
        x_start_pct: xs,
        x_end_pct: xe,
        join_rows_mode: 'space',
        postprocess: { type: (vmPost?.value || 'text') },
        margin_pct: 1.0
      }]
    };

    const fd = new FormData();
    fd.append('file', iiPdfFile.files[0]);
    fd.append('template_json', JSON.stringify(tempTpl));

    try{
      const r = await fetch('/api/inbound/lines/extract', { method:'POST', body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j?.detail || `HTTP ${r.status}`);
      const val = (j.fields && j.fields[tempFieldKey]) || '';
      if (vmLive) vmLive.textContent = `Current value → ${val}`;
    }catch(err){
      if (vmLive) vmLive.textContent = 'Preview failed';
      console.error(err);
    }
  }

  if (VM_ENABLED){
    vmLoad?.addEventListener('click', async ()=>{
      if (!iiPdfFile?.files?.length){
        if (vmMsg) vmMsg.textContent = 'Choose a PDF first.';
        return;
      }
      if (vmMsg) vmMsg.textContent = 'Loading…';
      const fd = new FormData();
      fd.append('file', iiPdfFile.files[0]);
      try{
        const r = await fetch('/api/inbound/lines/preview', { method:'POST', body: fd });
        const j = await r.json();
        if (!r.ok) throw new Error(j?.detail || `HTTP ${r.status}`);
        const arr = Array.isArray(j.lines) ? j.lines : [];
        vmLines = arr.map((ln, i) => ({
          index: i + 1,
          text: ln.text || '',
          x0: ln.x0, x1: ln.x1, top: ln.top, bottom: ln.bottom
        }));
        selStart = selEnd = null;
        renderRows();
        renderBand();
        if (vmMsg) vmMsg.textContent = 'Done.';
        if (vmLive) vmLive.textContent = '';
      }catch(err){
        if (vmMsg) vmMsg.textContent = 'Failed.';
        console.error(err);
      }
    });

    vmRows?.addEventListener('click', (e)=>{
      const row = e.target.closest('.vm_row');
      if (!row) return;
      const idx = parseInt(row.dataset.idx, 10);
      if (Number.isNaN(idx)) return;

      if (selStart === null) {
        selStart = selEnd = idx;
      } else {
        selEnd = idx;
      }
      renderRows();
      livePreview();
    });

    function attachDrag(handle, which){
      let down = false;
      handle.addEventListener('mousedown', (ev)=>{ down = true; ev.preventDefault(); });
      window.addEventListener('mouseup', ()=>{ down = false; });
      window.addEventListener('mousemove', (ev)=>{
        if (!down) return;
        const rect = vmBand.getBoundingClientRect();
        const pct = Math.min(100, Math.max(0, ((ev.clientX - rect.left) / Math.max(1, rect.width))*100 ));
        if (which === 'l'){
          xs = Math.min(pct, xe-1);
        } else {
          xe = Math.max(pct, xs+1);
        }
        renderBand();
        livePreview();
      });
    }
    attachDrag(vmHL, 'l');
    attachDrag(vmHR, 'r');
    window.addEventListener('resize', renderBand);

    vmPost?.addEventListener('change', livePreview);

    vmAdd?.addEventListener('click', ()=>{
      const block = normalizeSel();
      if (!block){
        alert('Click one or more rows to select them first.');
        return;
      }
      const field_key = (vmField?.value || 'customer_name');

      try {
        if (iiLineTpl?.value?.trim()){
          const parsed = JSON.parse(iiLineTpl.value);
          if (parsed && typeof parsed === 'object') tplState = parsed;
        }
      } catch { /* keep current tplState */ }

      tplState.template_id = tplState.template_id || 'user-layout';
      tplState.fields = Array.isArray(tplState.fields) ? tplState.fields : [];

      tplState.fields = tplState.fields.filter(f => f.field_key !== field_key);
      tplState.fields.push({
        field_key,
        page: 1,
        row_start: block.row_start,
        row_end: block.row_end,
        x_start_pct: xs,
        x_end_pct: xe,
        join_rows_mode: 'space',
        postprocess: { type: (vmPost?.value || 'text') },
        margin_pct: 1.0
      });

      if (iiLineTpl) iiLineTpl.value = JSON.stringify(tplState, null, 2);
      alert(`Mapped "${field_key}" → rows ${block.row_start}-${block.row_end}, band ${xs.toFixed(1)}%–${xe.toFixed(1)}%.`);
    });
  }

  // ------- Listeners / init -------

  iiGenerate?.addEventListener('click', generate);
  iiCopy?.addEventListener('click', copyAddr);
  iiActive?.addEventListener('change', save);
  document.querySelectorAll('input[name="ii_reader"]').forEach(r => r.addEventListener('change', () => {
    handleReaderChange();
    save();
  }));
  iiBlockTemplate?.addEventListener('change', save);
  iiPdfBtn?.addEventListener('click', previewPdf);
  iiLineBtn?.addEventListener('click', lineExtract);

  // lazy-init when tab is opened
  window.addEventListener('invoice_import_tab_activated', load);
  if (document.querySelector('#tab_import')?.style.display !== 'none') load();
})();
