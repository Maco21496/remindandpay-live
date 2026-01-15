// HTML invoice import settings
(function () {
  const nameInput = document.getElementById('html_template_name');
  const selectEl = document.getElementById('html_template_selector');
  const bodyInput = document.getElementById('html_invoice_body');
  const createBtn = document.getElementById('html_template_create');
  const saveBtn = document.getElementById('html_invoice_save');
  const editBtn = document.getElementById('html_invoice_edit');
  const msgEl = document.getElementById('html_invoice_msg');
  const previewEl = document.getElementById('html_preview');
  const subjectInput = document.getElementById('html_subject_token');
  const subjectCopyBtn = document.getElementById('html_subject_copy');
  const subjectRefreshBtn = document.getElementById('html_subject_refresh');
  const sampleModeEmail = document.getElementById('html_sample_mode_email');
  const sampleModePaste = document.getElementById('html_sample_mode_paste');
  const sampleEditor = document.getElementById('html_sample_editor');
  const mapperSaveBtn = document.getElementById('html_mapper_save');
  const mapperMsg = document.getElementById('html_mapper_msg');
  const filterSelect = document.getElementById('html_filter_select');
  const mapperRaw = document.getElementById('html_mapper_raw');
  const mapperFiltered = document.getElementById('html_mapper_filtered');
  let activeTemplateName = '';
  let sampleMode = 'email';
  let lastEmailHtml = '';
  let templateJson = { fields: {} };
  let activeFieldKey = '';
  let lastSelectedEl = null;
  const fieldSamples = {};

  function setActiveTemplate(name) {
    activeTemplateName = (name || '').trim();
    if (saveBtn) saveBtn.disabled = !activeTemplateName;
  }

  function setPreview(html) {
    if (!previewEl) return;
    const content = html && html.trim()
      ? html
      : '<div style="font-size:12px; color:#6b7280;">HTML preview placeholder</div>';
    previewEl.srcdoc = content;
  }

  function setSubjectToken(token) {
    if (!subjectInput) return;
    subjectInput.value = token || '';
  }

  function collapseEditor(collapsed) {
    if (!bodyInput || !saveBtn || !editBtn || sampleMode !== 'paste') return;
    if (collapsed) {
      bodyInput.style.display = 'none';
      saveBtn.style.display = 'none';
      editBtn.style.display = 'inline-flex';
    } else {
      bodyInput.style.display = '';
      saveBtn.style.display = '';
      editBtn.style.display = 'none';
    }
  }

  function updateSampleMode(mode) {
    sampleMode = mode === 'paste' ? 'paste' : 'email';
    if (sampleMode === 'paste') {
      if (sampleEditor) sampleEditor.style.display = '';
      if (saveBtn) saveBtn.style.display = '';
      collapseEditor(!!(bodyInput?.value || '').trim());
      setPreview(bodyInput?.value || '');
    } else {
      if (sampleEditor) sampleEditor.style.display = 'none';
      if (saveBtn) saveBtn.style.display = 'none';
      if (editBtn) editBtn.style.display = 'none';
      setPreview(lastEmailHtml || '');
    }
    attachPreviewClickHandler();
  }

  function setTemplateJson(data) {
    if (data && typeof data === 'object') {
      templateJson = data;
    } else {
      templateJson = { fields: {} };
    }
    if (!templateJson.fields || typeof templateJson.fields !== 'object') {
      templateJson.fields = {};
    }
  }

  function updateFieldBadge(fieldKey, value) {
    const el = document.getElementById(`html_field_value_${fieldKey}`);
    if (!el) return;
    el.textContent = value ? `→ ${value}` : '';
  }

  function filterForField(fieldKey) {
    if (fieldKey === 'invoice_number') return { type: 'digits_only' };
    if (fieldKey === 'issue_date') return { type: 'date' };
    if (fieldKey === 'due_date') return { type: 'date' };
    if (fieldKey === 'amount_due') return { type: 'amount' };
    return null;
  }

  function applyFilter(raw, filterSpec) {
    const value = (raw || '').trim();
    if (!filterSpec || !filterSpec.type || filterSpec.type === 'none') return value;
    if (filterSpec.type === 'digits_only') return value.replace(/\D+/g, '');
    if (filterSpec.type === 'amount') {
      const match = value.match(/([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)/);
      if (!match) return value;
      const num = parseFloat(match[1].replace(/,/g, ''));
      return Number.isFinite(num) ? num.toFixed(2) : match[1];
    }
    if (filterSpec.type === 'date') {
      const match = value.match(/\b\d{1,2}\/\d{1,2}\/\d{4}\b/i)
        || value.match(/\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b/i);
      return match ? match[0] : value;
    }
    if (filterSpec.type === 'strip_parentheses') {
      return value.replace(/\s*\([^)]*\)\s*/g, ' ').trim();
    }
    return value;
  }

  function setFilterSelect(filterSpec) {
    if (!filterSelect) return;
    const value = filterSpec?.type || 'none';
    filterSelect.value = value;
  }

  function updateFilterPreview(fieldKey) {
    if (!fieldKey) return;
    const sample = fieldSamples[fieldKey] || '';
    const spec = templateJson.fields?.[fieldKey]?.filter || null;
    const filtered = applyFilter(sample, spec);
    if (mapperRaw) mapperRaw.textContent = sample || '—';
    if (mapperFiltered) mapperFiltered.textContent = filtered || '—';
  }

  function clearSelectedElementHighlight() {
    if (lastSelectedEl) {
      lastSelectedEl.style.outline = '';
      lastSelectedEl = null;
    }
  }

  function buildElementPath(element) {
    if (!element) return [];
    const path = [];
    let node = element;
    while (node && node.tagName && node.tagName.toLowerCase() !== 'body') {
      const parent = node.parentElement;
      if (!parent) break;
      const children = Array.from(parent.children || []);
      const index = children.indexOf(node);
      path.unshift({
        tag: node.tagName.toLowerCase(),
        index
      });
      node = parent;
    }
    return path;
  }

  function attachPreviewClickHandler() {
    if (!previewEl) return;
    const doc = previewEl.contentDocument;
    if (!doc) return;
    doc.removeEventListener('click', handlePreviewClick, true);
    doc.addEventListener('click', handlePreviewClick, true);
  }

  function handlePreviewClick(event) {
    const target = event.target;
    if (!target || !target.tagName) return;
    event.preventDefault();
    event.stopPropagation();
    if (!activeFieldKey) {
      if (mapperMsg) mapperMsg.textContent = 'Select a field first.';
      return;
    }
    clearSelectedElementHighlight();
    target.style.outline = '2px solid #6366f1';
    lastSelectedEl = target;
    const textValue = (target.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120);
    const path = buildElementPath(target);
    const defaultFilter = filterForField(activeFieldKey);
    templateJson.fields[activeFieldKey] = {
      type: 'dom',
      path,
      attr: 'text',
      filter: templateJson.fields?.[activeFieldKey]?.filter || defaultFilter
    };
    fieldSamples[activeFieldKey] = textValue;
    const displayValue = applyFilter(textValue, templateJson.fields[activeFieldKey].filter);
    updateFieldBadge(activeFieldKey, displayValue || textValue || '(selected)');
    setFilterSelect(templateJson.fields[activeFieldKey].filter);
    updateFilterPreview(activeFieldKey);
    if (mapperMsg) mapperMsg.textContent = `Captured ${activeFieldKey.replace('_', ' ')}.`;
  }

  async function loadTemplates(selectedName) {
    if (!selectEl) return;
    selectEl.innerHTML = '<option value="">(choose template)</option>';
    try {
      const res = await fetch('/api/inbound/html/templates', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      const list = Array.isArray(data.templates) ? data.templates : [];
      for (const item of list) {
        const opt = document.createElement('option');
        opt.value = item.template_name;
        opt.textContent = item.template_name;
        selectEl.appendChild(opt);
      }
      if (selectedName) {
        selectEl.value = selectedName;
      }
      const chosen = selectEl.value || (list[0]?.template_name || '');
      if (chosen) {
        selectEl.value = chosen;
        await loadTemplate(chosen);
      } else {
        setSubjectToken('');
        lastEmailHtml = '';
        setTemplateJson({ fields: {} });
        updateSampleMode(sampleMode);
      }
    } catch (err) {
      console.error('Failed to load HTML templates', err);
    }
  }

  async function loadTemplate(name) {
    if (!name) return;
    try {
      const res = await fetch(`/api/inbound/html/load-template?template_name=${encodeURIComponent(name)}`, { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (nameInput) nameInput.value = data.template_name || name;
      if (bodyInput) bodyInput.value = data.html_body || '';
      setSubjectToken(data.subject_token || '');
      setActiveTemplate(data.template_name || name);
      lastEmailHtml = data.html_email_body || '';
      setTemplateJson(data.template_json || {});
      Object.keys(templateJson.fields || {}).forEach((fieldKey) => {
        updateFieldBadge(fieldKey, 'mapped');
      });
      updateFilterPreview(activeFieldKey);
      updateSampleMode(sampleMode);
    } catch (err) {
      console.error('Failed to load HTML template', err);
    }
  }

  async function saveTemplate() {
    if (!msgEl) return;
    msgEl.textContent = 'Saving…';
    const templateName = (nameInput?.value || '').trim();
    if (!templateName) {
      msgEl.textContent = 'Enter a template name.';
      return;
    }
    const body = bodyInput?.value || '';
    const payload = new FormData();
    payload.append('template_name', templateName);
    payload.append('html_body', body);
    payload.append('template_json', JSON.stringify(templateJson || { fields: {} }));
    try {
      const res = await fetch('/api/inbound/html/save-template', { method: 'POST', body: payload });
      if (!res.ok) {
        msgEl.textContent = 'Save failed.';
        return;
      }
      const data = await res.json();
      msgEl.textContent = 'Saved.';
      setActiveTemplate(templateName);
      await loadTemplates(templateName);
      setSubjectToken(data.subject_token || subjectInput?.value || '');
      updateSampleMode(sampleMode);
    } catch (err) {
      msgEl.textContent = 'Save failed.';
      console.error('Failed to save HTML template', err);
    }
  }

  async function createTemplate() {
    if (!msgEl) return;
    msgEl.textContent = 'Creating…';
    const templateName = (nameInput?.value || '').trim();
    if (!templateName) {
      msgEl.textContent = 'Enter a template name.';
      return;
    }
    const payload = new FormData();
    payload.append('template_name', templateName);
    payload.append('html_body', bodyInput?.value || '');
    payload.append('template_json', JSON.stringify(templateJson || { fields: {} }));
    try {
      const res = await fetch('/api/inbound/html/save-template', { method: 'POST', body: payload });
      if (!res.ok) {
        msgEl.textContent = 'Create failed.';
        return;
      }
      const data = await res.json();
      msgEl.textContent = 'Template created.';
      setActiveTemplate(templateName);
      await loadTemplates(templateName);
      setSubjectToken(data.subject_token || subjectInput?.value || '');
      updateSampleMode(sampleMode);
    } catch (err) {
      msgEl.textContent = 'Create failed.';
      console.error('Failed to create HTML template', err);
    }
  }

  selectEl?.addEventListener('change', (e) => {
    const value = e.target.value;
    if (value) loadTemplate(value);
  });
  createBtn?.addEventListener('click', createTemplate);
  saveBtn?.addEventListener('click', saveTemplate);
  editBtn?.addEventListener('click', () => collapseEditor(false));
  subjectCopyBtn?.addEventListener('click', () => {
    if (!subjectInput || !subjectInput.value) return;
    subjectInput.select();
    document.execCommand('copy');
  });
  previewEl?.addEventListener('load', () => {
    attachPreviewClickHandler();
  });
  document.querySelectorAll('input[name="html_mapper_field"]').forEach((radio) => {
    radio.addEventListener('change', (event) => {
      activeFieldKey = event.target.value;
      const existing = templateJson.fields?.[activeFieldKey]?.filter || filterForField(activeFieldKey);
      if (!templateJson.fields[activeFieldKey]) {
        templateJson.fields[activeFieldKey] = { filter: existing };
      } else if (!templateJson.fields[activeFieldKey].filter && existing) {
        templateJson.fields[activeFieldKey].filter = existing;
      }
      setFilterSelect(templateJson.fields[activeFieldKey]?.filter || null);
      updateFilterPreview(activeFieldKey);
      if (mapperMsg) mapperMsg.textContent = 'Click a value in the HTML preview.';
    });
  });
  filterSelect?.addEventListener('change', () => {
    if (!activeFieldKey) return;
    const selected = filterSelect.value || 'none';
    templateJson.fields[activeFieldKey] = {
      ...(templateJson.fields[activeFieldKey] || {}),
      filter: { type: selected }
    };
    updateFilterPreview(activeFieldKey);
    const sample = fieldSamples[activeFieldKey] || '';
    const filtered = applyFilter(sample, templateJson.fields[activeFieldKey].filter);
    updateFieldBadge(activeFieldKey, filtered || sample || 'mapped');
  });
  mapperSaveBtn?.addEventListener('click', async () => {
    if (!activeTemplateName) return;
    if (mapperMsg) mapperMsg.textContent = 'Saving mapping…';
    try {
      await saveTemplate();
      if (mapperMsg) mapperMsg.textContent = 'Mapping saved.';
    } catch (err) {
      if (mapperMsg) mapperMsg.textContent = 'Mapping save failed.';
      console.error('Failed to save mapping', err);
    }
  });
  sampleModeEmail?.addEventListener('change', () => {
    if (sampleModeEmail.checked) updateSampleMode('email');
  });
  sampleModePaste?.addEventListener('change', () => {
    if (sampleModePaste.checked) updateSampleMode('paste');
  });
  subjectRefreshBtn?.addEventListener('click', async () => {
    if (!activeTemplateName) return;
    if (msgEl) msgEl.textContent = 'Refreshing preview…';
    try {
      const res = await fetch(`/api/inbound/html/sample?template_name=${encodeURIComponent(activeTemplateName)}`, { cache: 'no-store' });
      if (!res.ok) {
        if (msgEl) msgEl.textContent = 'No matching email found yet.';
        return;
      }
      const data = await res.json();
      lastEmailHtml = data.html_body || '';
      setSubjectToken(data.subject_token || subjectInput?.value || '');
      updateSampleMode(sampleMode);
      if (msgEl) msgEl.textContent = 'Preview updated.';
    } catch (err) {
      if (msgEl) msgEl.textContent = 'Preview refresh failed.';
      console.error('Failed to refresh HTML preview', err);
    }
  });
  setActiveTemplate('');
  setPreview('');
  setSubjectToken('');
  updateSampleMode('email');
  loadTemplates();
})();
