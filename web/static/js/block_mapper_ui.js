// FINAL VERSION OF static/js/block_mapper_ui.js
(function () {
  const $ = (id) => document.getElementById(id);

  // Core DOM
  const fPdf    = $('bm_pdf_file');
  const fPage   = $('bm_page');
  const btnLoad = $('bm_load_blocks');
  const msg     = $('bm_msg');

  const canvas  = $('bm_canvas');
  const overlay = $('bm_overlay');

  // Field/editor DOM
  const selField        = $('bm_field');           // hidden select kept for compatibility
  const selFilter       = $('bm_filter');
  const inpA            = $('bm_param_a');
  const inpB            = $('bm_param_b');
  const btnSet          = $('bm_add_to_template');
  const txtTpl          = $('bm_template');
  const inpTemplateName = $('bm_template_name');   // template name input
  const btnExtract      = $('bm_extract');
  const liveSpan        = $('bm_live');
  const liveLabel       = $('bm_live_label');

  // Trigger + direction
  const inpTrigger   = $('bm_trigger_text');
  const dirRight     = $('bm_dir_right');
  const dirBelow     = $('bm_dir_below');

  // Step 1 + customer-by controls
  const custByRow     = $('bm_cust_by_row');
  const custByName    = $('bm_cust_by_name');
  const custByEmail   = $('bm_cust_by_email');

  // Collapsible step bodies
  const step2Body     = $('bm_step2_body');
  const step3Body     = $('bm_step3_body');

  const sideVal = (k) => $('bm_value_' + k);
  const sideDot = (k) => $('bm_status_' + k);

  if (!overlay) return;

  // -------------------------------
  // Collapsible helpers
  // -------------------------------
  function resetStepBodies() {
    if (step2Body) step2Body.style.display = 'none';
    if (step3Body) step3Body.style.display = 'none';
  }
  function showStep2() {
    if (step2Body) step2Body.style.display = 'block';
  }
  function showStep3() {
    if (step3Body) step3Body.style.display = 'block';
  }

  // -------------------------------
  // Fixed behaviour / wizard order
  // -------------------------------
  // Required invoice fields (customer mapping is stored separately)
  const REQUIRED = new Set(['invoice_number', 'issue_date', 'amount_due']); // due_date optional

  // Automatic filters for invoice fields
  const FIXED_FILTER = {
    issue_date: { type: 'date'   },
    due_date:   { type: 'date'   },
    amount_due: { type: 'amount' }
  };

  // Wizard order for invoice fields only (customer_map handled separately)
  const FIELD_SEQUENCE = ['invoice_number', 'issue_date', 'amount_due', 'due_date'];

  // Display order for numbering (includes customer_map)
  const FIELD_ORDER_FOR_DISPLAY = ['invoice_number', 'issue_date', 'amount_due', 'customer_map', 'due_date'];

  const FIELD_LABELS = {
    invoice_number: 'Invoice number',
    issue_date:     'Invoice date',
    amount_due:     'Amount due',
    customer_map:   'Customer mapping',
    due_date:       'Due date'
  };

  let currentFieldIndex = 0;   // index into FIELD_SEQUENCE
  let maxUnlockedIndex  = 0;   // highest index for invoice fields the user is allowed to click

  // -------------------------------
  // PDF page + overlay
  // -------------------------------
  let pageViewport  = null;
  let pageData      = { width: 0, height: 0, blocks: [] };
  let selectedIds   = new Set();
  let storedPdfFile = null;    // server-backed PDF for this user/template

  function setMsg(s){ if (msg) msg.textContent = s || ''; }
  function showError(prefix, e){ console.error(prefix, e); setMsg(prefix); }
  function px(n){ return `${n}px`; }

  function clearOverlay(){ overlay.innerHTML = ''; }

  // Helpers to work with pdfplumber coordinates, used for mapped overlays
  function centerOfBlock(b) {
    const bb = (b && b.bbox) ? b.bbox : { x0: 0, y0: 0, x1: 0, y1: 0 };
    return { x: (bb.x0 + bb.x1) / 2, y: (bb.y0 + bb.y1) / 2 };
  }
  function distPoints(a, b) {
    const dx = (a.x || 0) - (b.x || 0);
    const dy = (a.y || 0) - (b.y || 0);
    return Math.sqrt(dx * dx + dy * dy);
  }

  function getCurrentPageNumber() {
    return Math.max(1, parseInt(fPage.value || '1', 10) || 1);
  }

 // FINAL VERSION OF drawOverlay() AND drawMappedFieldOverlays() IN block_mapper_ui.js
function drawOverlay() {
  clearOverlay();
  if (!pageData?.blocks?.length || !pageViewport) return;

  const scaleX = canvas.width  / pageData.width;
  const scaleY = canvas.height / pageData.height;

  // Base interactive boxes for every detected text block (used for clicking)
  for (const b of pageData.blocks) {
    const { x0, y0, x1, y1 } = b.bbox;
    const left   = x0 * scaleX;
    const top    = y0 * scaleY;
    const width  = (x1 - x0) * scaleX;
    const height = (y1 - y0) * scaleY;

    const box = document.createElement('div');
    box.className = 'bm-box';
    box.dataset.id = String(b.id);
    // Store the block text on the element so we can read it directly on click
    box.dataset.labelText = (b.text || '').trim();

    Object.assign(box.style, {
      position: 'absolute',
      left: `${left}px`,
      top: `${top}px`,
      width: `${width}px`,
      height: `${height}px`,
      border: '1.5px dashed #93c5fd',
      borderRadius: '6px',
      background: 'rgba(147,197,253,.08)',
      cursor: 'pointer'
    });

    if (selectedIds.has(b.id)) {
      box.style.borderColor = '#6366f1';
      box.style.background  = 'rgba(99,102,241,.12)';
      box.style.boxShadow   = '0 0 0 2px rgba(99,102,241,.25) inset';
    }

    const tag = document.createElement('div');
    tag.textContent = String(b.id);
    Object.assign(tag.style, {
      position: 'absolute',
      left: '-18px',
      top: '-18px',
      width: '22px',
      height: '22px',
      borderRadius: '11px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontSize: '12px',
      fontWeight: '600',
      background: '#eef2ff',
      color: '#3730a3',
      border: '1px solid #c7d2fe',
      boxShadow: '0 1px 2px rgba(0,0,0,.05)',
      pointerEvents: 'none'
    });
    box.appendChild(tag);

    overlay.appendChild(box);
  }

  // Extra overlays showing where each mapped field lives, using ONLY the field number badge.
  drawMappedFieldOverlays(scaleX, scaleY);
}

  // FINAL VERSION OF drawMappedFieldOverlays() IN block_mapper_ui.js
  function drawMappedFieldOverlays(scaleX, scaleY) {
    // Which number belongs to which field key
    const FIELD_INDEX = {
      invoice_number: 1,
      issue_date: 2,
      amount_due: 3,
      customer_map: 4,
      due_date: 5
    };

    const tpl = readTpl();
    if (!tpl || !Array.isArray(tpl.fields)) return;
    if (!pageData?.blocks?.length) return;

    const pageNum = Math.max(1, parseInt(fPage.value || '1', 10) || 1);
    const blocksOnPage = (pageData.blocks || []).filter(
      (b) => Number(b.page) === pageNum
    );
    if (!blocksOnPage.length) return;

    function centreOfBlock(b) {
      const bb = b && b.bbox ? b.bbox : { x0: 0, y0: 0, x1: 0, y1: 0 };
      return { x: (bb.x0 + bb.x1) / 2, y: (bb.y0 + bb.y1) / 2 };
    }

    function distance(a, b) {
      const dx = (a.x || 0) - (b.x || 0);
      const dy = (a.y || 0) - (b.y || 0);
      return Math.sqrt(dx * dx + dy * dy);
    }

    function findNearestBlockToPoint(blocks, pt) {
      if (!blocks.length) return null;
      let best = blocks[0];
      let bestD = distance(centreOfBlock(best), pt);
      for (let i = 1; i < blocks.length; i++) {
        const d = distance(centreOfBlock(blocks[i]), pt);
        if (d < bestD) {
          best = blocks[i];
          bestD = d;
        }
      }
      return best;
    }

    function findValueBlock(blocks, labelBlock, direction) {
      if (!labelBlock) return null;
      const bb = labelBlock.bbox || { x0: 0, y0: 0, x1: 0, y1: 0 };
      const x1 = bb.x1 || 0;
      const y1 = bb.y1 || 0;
      const lineY = typeof labelBlock.line_y === 'number'
        ? labelBlock.line_y
        : (bb.y0 || 0);

      if (direction === 'right') {
        const sameLine = blocks.filter(
          (b) => Math.abs((b.line_y || 0) - lineY) <= 2.5
        );
        const rightBlocks = sameLine.filter(
          (b) => (b.bbox && b.bbox.x0) >= x1 - 0.5
        );
        rightBlocks.sort((a, b) => (a.bbox.x0 - b.bbox.x0));
        return rightBlocks[0] || null;
      }

      const belowBlocks = blocks.filter(
        (b) => (b.bbox && b.bbox.y0) > y1 + 1.0
      );
      const labelCx = ((bb.x0 || 0) + x1) / 2;
      belowBlocks.sort((a, b) => {
        const dyA = a.bbox.y0 - y1;
        const dyB = b.bbox.y0 - y1;
        if (dyA !== dyB) return dyA - dyB;
        const cxA = (a.bbox.x0 + a.bbox.x1) / 2;
        const cxB = (b.bbox.x0 + b.bbox.x1) / 2;
        return Math.abs(cxA - labelCx) - Math.abs(cxB - labelCx);
      });
      return belowBlocks[0] || null;
    }

    (tpl.fields || []).forEach((f) => {
      if (!f || !f.anchor || !f.field_key) return;

      const fieldNo = FIELD_INDEX[f.field_key];
      if (!fieldNo) return;

      const fieldPage =
        (f.anchor.page && Number(f.anchor.page) > 0)
          ? Number(f.anchor.page)
          : (tpl.page && Number(tpl.page) > 0 ? tpl.page : 1);

      if (fieldPage !== pageNum) return;

      const anchorPoint = { x: Number(f.anchor.x) || 0, y: Number(f.anchor.y) || 0 };
      const labelBlock = findNearestBlockToPoint(blocksOnPage, anchorPoint);
      if (!labelBlock) return;

      const dir = (f.direction || 'right').toLowerCase() === 'below' ? 'below' : 'right';
      const valueBlock = findValueBlock(blocksOnPage, labelBlock, dir);

      const pieces = [];
      pieces.push({ block: labelBlock, role: 'label' });
      if (valueBlock && valueBlock !== labelBlock) {
        pieces.push({ block: valueBlock, role: 'value' });
      }

      const numText = String(fieldNo);

      pieces.forEach(({ block, role }) => {
        const bb = block.bbox || { x0: 0, y0: 0, x1: 0, y1: 0 };
        const left   = bb.x0 * scaleX;
        const top    = bb.y0 * scaleY;
        const width  = (bb.x1 - bb.x0) * scaleX;
        const height = (bb.y1 - bb.y0) * scaleY;

        const hi = document.createElement('div');
        hi.className = 'bm-mapped-box';
        Object.assign(hi.style, {
          position: 'absolute',
          left: `${left}px`,
          top: `${top}px`,
          width: `${width}px`,
          height: `${height}px`,
          border: role === 'label' ? '2px solid #facc15' : '2px solid #22c55e',
          borderRadius: '6px',
          boxShadow: '0 0 0 1px rgba(0,0,0,.06)',
          pointerEvents: 'none',
          background: 'transparent',
          zIndex: 5
        });

        const tag = document.createElement('div');
        tag.textContent = numText;
        Object.assign(tag.style, {
          position: 'absolute',
          left: '-26px',
          top: '50%',
          transform: 'translateY(-50%)',
          padding: '0 6px',
          height: '18px',
          borderRadius: '9px',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '11px',
          fontWeight: 700,
          background: role === 'label' ? '#fef9c3' : '#dcfce7',
          color: '#111827',
          border: '1px solid rgba(0,0,0,.08)',
          boxShadow: '0 1px 2px rgba(0,0,0,.08)',
          pointerEvents: 'none',
          whiteSpace: 'nowrap'
        });

        hi.appendChild(tag);
        overlay.appendChild(hi);
      });
    });
  }

// FINAL VERSION OF autoPopulateTriggerFromSelection() IN block_mapper_ui.js
function autoPopulateTriggerFromSelection(anchorId, labelTextFromBox) {
  if (!inpTrigger) return;

  let txt = (labelTextFromBox || '').trim();

  // Fallback: if for some reason we didn't get text from the box, look it up in pageData
  if (!txt && anchorId != null && pageData && Array.isArray(pageData.blocks)) {
    const blk = (pageData.blocks || []).find(b => b.id === anchorId);
    if (blk && blk.text) {
      txt = String(blk.text || '').trim();
    }
  }

  if (!txt) return;

  // Keep everything up to and including a colon, if present
  const idx = txt.indexOf(':');
  if (idx >= 0) {
    txt = txt.slice(0, idx + 1).trim();
  }

  // Truncate very long labels
  if (txt.length > 60) {
    txt = txt.slice(0, 60);
  }

  // Always update trigger text to the most recently clicked label
  inpTrigger.value = txt;
}

// FINAL VERSION OF overlay click handler IN block_mapper_ui.js
overlay.addEventListener('click', (ev) => {
  const box = ev.target.closest('.bm-box');
  if (!box) return;

  const id = parseInt(box.dataset.id, 10);
  if (Number.isNaN(id)) return;

  if (ev.shiftKey) {
    if (selectedIds.has(id)) selectedIds.delete(id);
    else selectedIds.add(id);
  } else {
    selectedIds.clear();
    selectedIds.add(id);
  }

  drawOverlay();

  // Read the label text directly from the clicked box
  const labelText = (box.dataset.labelText || '').trim();
  autoPopulateTriggerFromSelection(id, labelText);

  showStep2();       // expand Step 2 once a label block has been clicked
  previewValue();
});

  async function renderPdfToCanvas(file, pageNum){
    const arrayBuf = await file.arrayBuffer();
    const loadingTask = pdfjsLib.getDocument({ data: arrayBuf });
    const pdf = await loadingTask.promise;
    const page = await pdf.getPage(pageNum);

    const container = canvas.parentElement;
    const desiredWidth = Math.min(1000, (container && container.clientWidth) || 1000);
    const viewport1x = page.getViewport({ scale: 1.0 });
    const scale = desiredWidth / viewport1x.width;
    const vp = page.getViewport({ scale });

    canvas.width  = Math.floor(vp.width);
    canvas.height = Math.floor(vp.height);

    await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise;
    pageViewport = vp;
  }

  // UPDATED: uses getCurrentPdfFile() and uploads to /upload-pdf
  async function loadBlocks(){
    setMsg('');
    liveSpan.textContent = '';
    selectedIds.clear();
    clearOverlay();
    if (inpTrigger) inpTrigger.value = '';
    resetStepBodies();   // hide Step 2 & 3 when reloading blocks

    const pdfFile = getCurrentPdfFile();
    if (!pdfFile){
      setMsg('Choose a PDF first.');
      return;
    }

    const fromInput = !!(fPdf && fPdf.files && fPdf.files.length > 0);
    const page = getCurrentPageNumber();

    try{
      await renderPdfToCanvas(pdfFile, page);
    } catch(e){
      showError('Failed to render PDF.', e);
      return;
    }

    try{
      const fd = new FormData();
      fd.append('file', pdfFile);
      fd.append('page', String(page));
      const r = await fetch('/api/inbound/blocks/preview', { method:'POST', body: fd });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const j = await r.json();
      pageData = { width: j.width, height: j.height, blocks: j.blocks || [] };
      drawOverlay();
      setMsg('Step 1: click the label for the current item → Step 2: choose where the value is → Step 3: filter & set value.');
    } catch(e){
      showError('Error loading lines (blocks).', e);
      return;
    }

    // store the PDF for this user/template once blocks are successfully loaded
    if (fromInput) {
      await uploadCurrentPdfToServer(pdfFile);
    }
  }

  // -------------------------------
  // Filters
  // -------------------------------
  function refreshFilterInputs(){
    const t = selFilter.value;
    inpA.style.display = 'none';
    inpB.style.display = 'none';
    inpA.placeholder = '';
    inpB.placeholder = '';
    if (t === 'after_token' || t === 'before_token'){
      inpA.style.display = '';
      inpA.placeholder = 'token';
    } else if (t === 'between_tokens'){
      inpA.style.display = '';
      inpB.style.display = '';
      inpA.placeholder = 'left token';
      inpB.placeholder = 'right token';
    } else if (t === 'regex'){
      inpA.style.display = '';
      inpB.style.display = '';
      inpA.placeholder = 'regex pattern';
      inpB.placeholder = 'capture group (default 1)';
    }
  }

  function buildFilterSpec(){
    const fk = selField.value || 'invoice_number';

    // For invoice fields we enforce sensible defaults; for customer mapping we let the user choose.
    if (fk !== 'customer_map' && FIXED_FILTER[fk]) {
      return FIXED_FILTER[fk];
    }

    const t = selFilter.value;
    if (t === 'none') return { type:'none' };
    if (t === 'digits_only') return { type:'digits_only' };
    if (t === 'amount') return { type:'amount' };
    if (t === 'date') return { type:'date' };
    if (t === 'strip_parentheses') return { type:'strip_parentheses' };
    if (t === 'after_token') return { type:'after_token', token: inpA.value || '' };
    if (t === 'before_token') return { type:'before_token', token: inpA.value || '' };
    if (t === 'between_tokens') return { type:'between_tokens', left: inpA.value || '', right: inpB.value || '' };
    if (t === 'regex'){
      const g = parseInt(inpB.value || '1', 10);
      return { type:'regex', pattern: inpA.value || '', group: Number.isFinite(g) ? g : 1 };
    }
    return { type:'none' };
  }

  function shouldSendFilter(spec){
    if (!spec || !spec.type || spec.type === 'none') return false;
    if (['digits_only','amount','date','strip_parentheses'].includes(spec.type)) return true;
    if (spec.type === 'after_token' && spec.token) return true;
    if (spec.type === 'before_token' && spec.token) return true;
    if (spec.type === 'between_tokens' && spec.left && spec.right) return true;
    if (spec.type === 'regex' && spec.pattern) return true;
    return false;
  }

  function lockFilterUIFor(fieldKey){
    const fixed = FIXED_FILTER[fieldKey];
    if (fixed){
      selFilter.value = fixed.type;
      selFilter.disabled = true;
      inpA.style.display = 'none';
      inpB.style.display = 'none';
    } else {
      selFilter.disabled = false;
      refreshFilterInputs();
    }
  }

  // -------------------------------
  // Live preview (trigger-based)
  // -------------------------------
  async function previewValue(){
    liveSpan.textContent = '';

    const pdfFile = getCurrentPdfFile();
    if (!pdfFile) return;
    if (!selectedIds.size)  return;

    const trigger = (inpTrigger?.value || '').trim();
    const direction = (dirRight?.checked ? 'right' : (dirBelow?.checked ? 'below' : 'right'));

    try{
      const page = getCurrentPageNumber();
      const anchorId = Array.from(selectedIds)[0];
      const spec = buildFilterSpec();

      const fd = new FormData();
      fd.append('file', pdfFile);
      fd.append('page', String(page));
      fd.append('anchor_block_id', String(anchorId));
      fd.append('trigger_text', trigger || '');
      fd.append('direction', direction);

      if (shouldSendFilter(spec)){
        const simple = ['digits_only','amount','date','strip_parentheses'];
        if (simple.includes(spec.type)){
          fd.append('filter', spec.type);
        } else {
          const s = JSON.stringify(spec);
          fd.append('filter_json', s);
          fd.append('filter', s);
        }
      }

      const r = await fetch('/api/inbound/blocks/preview-by-trigger', { method:'POST', body: fd });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const j = await r.json();
      const value = j?.value ?? '';

      liveSpan.textContent = value;
      showStep3();     // once we have a preview, expand Step 3
    } catch(e){
      showError('Error: preview value', e);
    }
  }

  // -------------------------------
  // Template JSON helpers
  // -------------------------------
  function readTpl(){
    try { return JSON.parse(txtTpl.value || '{}'); }
    catch { return {}; }
  }

  function writeTpl(tpl){
    txtTpl.value = JSON.stringify(tpl, null, 2);
  }

  function isFieldMapped(tpl, key){
    const f = (tpl.fields || []).find(x => x.field_key === key);
    return !!(f && f.trigger_text && f.direction && f.anchor);
  }

  function fieldsComplete(tpl){
    for (const key of REQUIRED){
      if (!isFieldMapped(tpl, key)) return false;
    }
    return true;
  }

  // -------------------------------
  // UI state
  // -------------------------------
  const uiState = {
    customerBy: 'name'
  };

  // Selector element for choosing between saved templates.
  let selTemplate = null;

  function ensureTemplateSelectorElement() {
    if (selTemplate) return;
    if (!inpTemplateName) return;

    selTemplate = document.createElement('select');
    selTemplate.id = 'bm_template_selector';
    selTemplate.className = 'bm-template-selector';
    selTemplate.style.marginLeft = '0.5rem';

    const parent = inpTemplateName.parentElement || inpTemplateName;
    parent.appendChild(selTemplate);

    selTemplate.addEventListener('change', () => {
      const name = selTemplate.value || '';
      if (!name) return;
      if (inpTemplateName) inpTemplateName.value = name;
      loadTemplateFromServer(name);
    });
  }

  function setStatusDot(fieldKey, ok){
    const el = sideDot(fieldKey);
    if (!el) return;
    if (ok) el.classList.add('vm-dot--ok');
    else    el.classList.remove('vm-dot--ok');
  }

  // FINAL VERSION OF setSideValue() IN block_mapper_ui.js
  function setSideValue(fieldKey, text){
    const el = sideVal(fieldKey);
    if (!el) return;

    const idx = FIELD_ORDER_FOR_DISPLAY.indexOf(fieldKey);
    const n = (idx >= 0 ? idx + 1 : null);
    const label = FIELD_LABELS[fieldKey] || fieldKey;
    const valuePart = (text && text.trim()) ? text.trim() : '—';
    const numPart = n ? `${n}. ` : '';

    // Single condensed line, e.g. "1. Invoice number | 790"
    el.textContent = `${numPart}${label} | ${valuePart}`;
  }

  // NEW HELPER: choose PDF source (uploaded file or stored server copy)
  function getCurrentPdfFile() {
    if (fPdf && fPdf.files && fPdf.files.length > 0) {
      return fPdf.files[0];
    }
    return storedPdfFile || null;
  }

  // NEW HELPER: upload current PDF so backend stores 1 PDF per template per user
  async function uploadCurrentPdfToServer(file) {
    if (!file) return;
    try {
      const fd = new FormData();
      const tplName =
        (inpTemplateName && inpTemplateName.value) ? inpTemplateName.value : '';
      fd.append('file', file);
      fd.append('template_name', tplName);
      const r = await fetch('/api/inbound/blocks/upload-pdf', {
        method: 'POST',
        body: fd
      });
      if (!r.ok) {
        let detail = '';
        try { detail = await r.text(); } catch {}
        console.error('upload-pdf failed', r.status, detail);
      }
    } catch (e) {
      console.error('upload-pdf error', e);
    }
  }

  async function loadStoredPdfIfAvailable(hasStored, pageFromTpl) {
    if (!hasStored) return;

    const tplName = (inpTemplateName && inpTemplateName.value)
      ? inpTemplateName.value
      : '';
    if (!tplName) {
      console.warn('loadStoredPdfIfAvailable: no template name set.');
      return;
    }

    try {
      const r = await fetch(`/api/inbound/blocks/download-pdf?template_name=${encodeURIComponent(tplName)}`);
      if (!r.ok) {
        console.warn('download-pdf status', r.status);
        return;
      }
      const blob = await r.blob();
      storedPdfFile = new File([blob], 'invoice-template.pdf', { type: 'application/pdf' });

      if (fPage && pageFromTpl) {
        fPage.value = String(pageFromTpl);
      }

      await loadBlocks();
    } catch (e) {
      console.error('loadStoredPdfIfAvailable error', e);
    }
  }

  function updateFieldRadiosLockState(tpl){
    // Invoice fields follow the normal sequence/locking
    FIELD_SEQUENCE.forEach((key, idx) => {
      const radio = document.querySelector(`input[name="bm_field_choice"][value="${key}"]`);
      if (!radio) return;
      const label = radio.closest('.vm-pill');
      const locked = (idx > maxUnlockedIndex);

      radio.disabled = locked;
      if (label) {
        if (locked) {
          label.style.opacity = '0.45';
          label.style.pointerEvents = 'none';
        } else {
          label.style.opacity = '';
          label.style.pointerEvents = '';
        }
      }
    });

    // Customer mapping unlocked only once required invoice fields are done
    const custRadio = document.querySelector('input[name="bm_field_choice"][value="customer_map"]');
    if (custRadio) {
      const label = custRadio.closest('.vm-pill');
      const enabled = fieldsComplete(tpl);
      custRadio.disabled = !enabled;
      if (label) {
        if (!enabled) {
          label.style.opacity = '0.45';
          label.style.pointerEvents = 'none';
        } else {
          label.style.opacity = '';
          label.style.pointerEvents = '';
        }
      }
    }
  }

  function setCurrentFieldFromKey(fieldKey){
    const idx = FIELD_SEQUENCE.indexOf(fieldKey);
    if (idx >= 0) currentFieldIndex = idx;

    selField.value = fieldKey;

    const radio = document.querySelector(`input[name="bm_field_choice"][value="${fieldKey}"]`);
    if (radio) radio.checked = true;

    // Show / hide the customer-by controls
    if (custByRow) {
      custByRow.style.display = (fieldKey === 'customer_map') ? 'flex' : 'none';
    }

    // For invoice fields, lock filter UI where needed; for customer mapping, leave it free
    if (fieldKey === 'customer_map') {
      selFilter.disabled = false;
      refreshFilterInputs();
      liveLabel.textContent = 'Current value';
    } else {
      lockFilterUIFor(fieldKey);
      liveLabel.textContent = 'Current value';
    }

    if (inpTrigger) inpTrigger.value = '';
    selectedIds.clear();
    drawOverlay();
    resetStepBodies();   // collapse Step 2 & 3 when switching field

    const nice = FIELD_LABELS[fieldKey] || fieldKey;
    setMsg(`Currently mapping: ${nice}. Step 1) click its label on the PDF → Step 2) choose where the value is → Step 3) filter & set value.`);
  }

  function advanceAfterFieldSave(savedFieldKey, tpl){
    const savedIndex = FIELD_SEQUENCE.indexOf(savedFieldKey);
    if (savedIndex === -1) return;

    // unlock up to (savedIndex + 1) so the next field becomes available
    if (savedIndex + 1 > maxUnlockedIndex) {
      maxUnlockedIndex = Math.min(savedIndex + 1, FIELD_SEQUENCE.length - 1);
      updateFieldRadiosLockState(tpl);
    }

    // decide which invoice field to show next: first one that isn't mapped yet
    const nextKey = FIELD_SEQUENCE.find(k => !isFieldMapped(tpl, k));
    if (nextKey) {
      setCurrentFieldFromKey(nextKey);
    } else {
      setMsg('All invoice fields are mapped. You can now continue to customer mapping and then map the optional due date.');
    }
  }

  function initialiseWizardFromTemplate(tpl){
    // compute which invoice fields are already mapped
    let anyMapped = false;
    maxUnlockedIndex = 0;

    FIELD_SEQUENCE.forEach((key, idx) => {
      if (isFieldMapped(tpl, key)) {
        anyMapped = true;
        maxUnlockedIndex = Math.max(maxUnlockedIndex, idx);
      }
    });

    if (!anyMapped) {
      maxUnlockedIndex = 0; // only Invoice number unlocked
    } else if (maxUnlockedIndex < FIELD_SEQUENCE.length - 1) {
      maxUnlockedIndex += 1; // unlock the next invoice field after the last mapped one
    }

    updateFieldRadiosLockState(tpl);

    const nextKey = FIELD_SEQUENCE.find(k => !isFieldMapped(tpl, k)) || FIELD_SEQUENCE[0];
    setCurrentFieldFromKey(nextKey);
  }

  // -------------------------------
  // Persistence helpers
  // -------------------------------
  async function saveTemplateToServer(tpl) {
    if (!tpl || typeof tpl !== 'object') return;
    try {
      const fd = new FormData();
      fd.append('template_json', JSON.stringify(tpl));
      if (inpTemplateName) {
        fd.append('template_name', inpTemplateName.value || '');
      }
      const r = await fetch('/api/inbound/blocks/save-template', {
        method: 'POST',
        body: fd
      });
      if (!r.ok) {
        let txt = '';
        try { txt = await r.text(); } catch {}
        console.error('save-template failed', r.status, txt);
      } else {
        // On success, refresh the template selector so the new/updated name appears
        initialiseTemplateSelector();
      }
    } catch (e) {
      console.error('save-template error', e);
    }
  }

  async function loadTemplateFromServer(selectedName) {
    try {
      const qs = selectedName
        ? `?template_name=${encodeURIComponent(selectedName)}`
        : '';
      const r = await fetch(`/api/inbound/blocks/load-template${qs}`, {
        method: 'GET',
        cache: 'no-store'
      });
      if (!r.ok) {
        let txt = '';
        try { txt = await r.text(); } catch {}
        console.error('load-template failed', r.status, txt);
        return;
      }
      const j = await r.json();
      const tpl = (j && j.template_json) || {};
      const sampleFields =
        (j && j.sample_fields && typeof j.sample_fields === 'object')
          ? j.sample_fields
          : {};

      writeTpl(tpl);

      if (typeof tpl.page === 'number' && fPage) {
        fPage.value = String(tpl.page);
      }

      const fields = Array.isArray(tpl.fields) ? tpl.fields : [];
      const invKeys = ['invoice_number', 'issue_date', 'amount_due', 'due_date'];

      // Invoice fields: use extracted sample value if available, otherwise fall back to trigger+direction.
      for (const key of invKeys) {
        const found = fields.find(f => f.field_key === key);
        const ok = !!(found && found.trigger_text && found.direction && found.anchor);
        setStatusDot(key, ok);

        if (!ok) {
          setSideValue(key, '—');
          continue;
        }

        const sampleRaw = sampleFields[key];
        const sample = (sampleRaw !== undefined && sampleRaw !== null)
          ? String(sampleRaw).trim()
          : '';

        if (sample) {
          // e.g. "1. Invoice number | 790"
          setSideValue(key, sample);
        } else {
          const label = found.trigger_text || (FIELD_LABELS[key] || key);
          const dir = (found.direction || '').toLowerCase();
          const dirTag = dir === 'below' ? '↓' : '→';
          setSideValue(key, `${label} ${dirTag}`);
        }
      }

      // Customer map status + by mode
      if (tpl.customer_map) {
        const cm = tpl.customer_map;
        const ok = !!(cm.trigger_text && cm.direction && cm.anchor);
        setStatusDot('customer_map', ok);

        const sampleRaw = sampleFields['_customer_lookup_value'];
        const sample = (sampleRaw !== undefined && sampleRaw !== null)
          ? String(sampleRaw).trim()
          : '';

        if (ok) {
          if (sample) {
            // Show the actual extracted lookup value, e.g. "Biodock Limited"
            setSideValue('customer_map', sample);
          } else {
            const dir = (cm.direction || '').toLowerCase();
            const dirTag = dir === 'below' ? '↓' : '→';
            setSideValue(
              'customer_map',
              `${cm.trigger_text || 'Customer'} ${dirTag}`
            );
          }
        } else {
          setSideValue('customer_map', '—');
        }

        const by = cm.by === 'email' ? 'email' : 'name';
        uiState.customerBy = by;
        if (custByName)  custByName.checked  = (by === 'name');
        if (custByEmail) custByEmail.checked = (by === 'email');
      } else {
        setStatusDot('customer_map', false);
        setSideValue('customer_map', '—');
      }

      initialiseWizardFromTemplate(tpl);

      const loadedName = (j && j.template_name) || '';
      if (inpTemplateName && loadedName) {
        inpTemplateName.value = loadedName;
      }

      // Keep selector in sync with the loaded template
      if (selTemplate && loadedName) {
        let hasOption = false;
        for (const opt of selTemplate.options) {
          if (opt.value === loadedName) {
            hasOption = true;
            break;
          }
        }
        if (!hasOption) {
          const opt = document.createElement('option');
          opt.value = loadedName;
          opt.textContent = loadedName;
          selTemplate.appendChild(opt);
        }
        selTemplate.value = loadedName;
      }

      // if backend says a stored PDF exists for this template, fetch it and show it.
      const hasStoredPdf = !!(j && j.pdf_exists);
      if (hasStoredPdf) {
        const pageFromTpl = (typeof tpl.page === 'number' && tpl.page > 0) ? tpl.page : 1;
        await loadStoredPdfIfAvailable(true, pageFromTpl);
      } else {
        // No stored PDF; just clear overlay and let user choose a file manually
        clearOverlay();
      }
    } catch (e) {
      console.error('load-template error', e);
    }
  }

  async function initialiseTemplateSelector() {
    ensureTemplateSelectorElement();
    if (!selTemplate) {
      // No template-name input present; just load the latest template.
      await loadTemplateFromServer();
      return;
    }

    // Reset options
    selTemplate.innerHTML = '';
    const optPlaceholder = document.createElement('option');
    optPlaceholder.value = '';
    optPlaceholder.textContent = 'Choose template…';
    selTemplate.appendChild(optPlaceholder);

    try {
      const r = await fetch('/api/inbound/blocks/templates', {
        method: 'GET',
        cache: 'no-store'
      });
      if (!r.ok) {
        let txt = '';
        try { txt = await r.text(); } catch {}
        console.error('templates list failed', r.status, txt);
        await loadTemplateFromServer();
        return;
      }
      const j = await r.json();
      const list = Array.isArray(j.templates) ? j.templates : [];

      for (const item of list) {
        if (!item || !item.template_name) continue;
        const opt = document.createElement('option');
        opt.value = item.template_name;
        opt.textContent = item.template_name;
        selTemplate.appendChild(opt);
      }

      let initialName = '';
      if (inpTemplateName && (inpTemplateName.value || '').trim()) {
        initialName = inpTemplateName.value.trim();
      } else if (list.length > 0 && list[0].template_name) {
        initialName = list[0].template_name;
      }

      if (initialName) {
        selTemplate.value = initialName;
        if (inpTemplateName) inpTemplateName.value = initialName;
        await loadTemplateFromServer(initialName);
      } else {
        selTemplate.value = '';
        await loadTemplateFromServer();
      }
    } catch (e) {
      console.error('templates init error', e);
      await loadTemplateFromServer();
    }
  }

  // -------------------------------
  // Actions
  // -------------------------------
  async function addToTemplate(){
    if (!selectedIds.size){
      alert('Step 1: click the label text on the PDF first.');
      return;
    }

    const page = getCurrentPageNumber();
    const anchorId = Array.from(selectedIds)[0];
    const anchorBlock = (pageData.blocks || []).find(b => b.id === anchorId);
    if (!anchorBlock){
      alert('Internal: anchor block not found.');
      return;
    }

    const trigger_text = (inpTrigger?.value || '').trim();
    if (!trigger_text){
      alert('Step 2: type or confirm a trigger phrase (e.g. "Invoice Date:").');
      return;
    }

    const direction =
      (dirRight?.checked ? 'right' : (dirBelow?.checked ? 'below' : 'right'));

    const filter = buildFilterSpec();

    const bb = anchorBlock.bbox || { x0: 0, y0: 0, x1: 0, y1: 0 };
    const anchor = {
      page,
      x: (bb.x0 + bb.x1) / 2,
      y: (bb.y0 + bb.y1) / 2
    };

    const tpl = readTpl();
    if (!tpl || typeof tpl !== 'object') return;
    if (!tpl.template_id) tpl.template_id = 'user-invoice-template';
    tpl.page = page;

    const fieldKey = selField.value || 'invoice_number';

    if (fieldKey === 'customer_map') {
      // --- Customer mapping is stored in tpl.customer_map (canonical) ---
      const by = uiState.customerBy === 'email' ? 'email' : 'name';
      tpl.customer_map = {
        by,
        trigger_text,
        direction,
        anchor,
        filter
      };
    } else {
      // --- Invoice fields live in tpl.fields[] ---
      if (!Array.isArray(tpl.fields)) tpl.fields = [];
      tpl.fields = tpl.fields.filter(f => f.field_key !== fieldKey);
      tpl.fields.push({ field_key: fieldKey, trigger_text, direction, anchor, filter });
    }

    writeTpl(tpl);

    // Show the extracted sample value on the left (e.g. "1. Invoice number | 790")
    const sampleText = (liveSpan.textContent || '').trim();
    setSideValue(fieldKey, sampleText || '—');
    setStatusDot(fieldKey, true);

    advanceAfterFieldSave(fieldKey, tpl);
    drawOverlay(); // refresh mapped overlays for this field
  }

  // -------------------------------
  // Event wiring
  // -------------------------------
  document.querySelectorAll('input[name="bm_field_choice"]').forEach(r => {
    r.addEventListener('change', () => {
      const key = r.value;
      const idx = FIELD_SEQUENCE.indexOf(key);
      const tpl = readTpl();

      if (FIELD_SEQUENCE.includes(key) && idx > maxUnlockedIndex) {
        // blocked – revert to current invoice field
        const currentKey = FIELD_SEQUENCE[currentFieldIndex] || 'invoice_number';
        r.checked = false;
        const curRadio = document.querySelector(`input[name="bm_field_choice"][value="${currentKey}"]`);
        if (curRadio) curRadio.checked = true;
        alert('Please map the earlier fields first.');
        return;
      }

      setCurrentFieldFromKey(key);
    });
  });

  btnLoad?.addEventListener('click', loadBlocks);
  fPage?.addEventListener('change', () => {
    selectedIds.clear();
    if (inpTrigger) inpTrigger.value = '';
    loadBlocks();
  });

  selFilter?.addEventListener('change', () => { refreshFilterInputs(); previewValue(); });
  inpA?.addEventListener('input', previewValue);
  inpB?.addEventListener('input', previewValue);

  inpTrigger?.addEventListener('input', previewValue);
  dirRight?.addEventListener('change', previewValue);
  dirBelow?.addEventListener('change', previewValue);

  btnSet?.addEventListener('click', async () => {
    const before = readTpl();
    await addToTemplate();
    const after = readTpl();
    try {
      if (JSON.stringify(before) !== JSON.stringify(after)) {
        await saveTemplateToServer(after);
      }
    } catch (e) {
      console.error('Error auto-saving template', e);
    }
  });

  btnExtract?.addEventListener('click', async () => {
    const pdfFile = getCurrentPdfFile();
    if (!pdfFile){ alert('Choose a PDF first.'); return; }

    const tplStr = (txtTpl.value || '').trim();
    if (!tplStr){ alert('Template JSON is empty.'); return; }
    try{ JSON.parse(tplStr); }
    catch{ alert('Template JSON is not valid JSON.'); return; }

    try{
      const fd = new FormData();
      fd.append('file', pdfFile);
      fd.append('template_json', tplStr);
      const r = await fetch('/api/inbound/blocks/extract-template', { method:'POST', body: fd });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${await r.text()}`);
      const j = await r.json();
      const lines = Object.entries(j.fields || {}).map(
        ([k,v]) => `• ${k}: ${v ?? ''}`
      ).join('\n');
      alert(lines || 'No fields returned.');
    }
    catch(e){
      showError('Extract with template failed', e);
    }
  });

  // -------------------------------
  // Initial state
  // -------------------------------
  resetStepBodies();                            // start with Step 2 & 3 collapsed
  lockFilterUIFor(selField.value || 'invoice_number');
  refreshFilterInputs();
  initialiseTemplateSelector();
})();
