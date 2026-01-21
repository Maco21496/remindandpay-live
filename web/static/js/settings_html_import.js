// HTML invoice import settings
(function () {
  const nameInput = document.getElementById('html_template_name');
  const templateList = document.getElementById('html_template_list');
  const createBtn = document.getElementById('html_template_create');
  const msgEl = document.getElementById('html_invoice_msg');
  const previewEl = document.getElementById('html_preview');
  const subjectInput = document.getElementById('html_subject_token');
  const subjectCopyBtn = document.getElementById('html_subject_copy');
  const subjectRefreshBtn = document.getElementById('html_subject_refresh');
  const importAddressInput = document.getElementById('html_import_address');
  const importCopyBtn = document.getElementById('html_import_copy');
  const mapperSaveBtn = document.getElementById('html_mapper_save');
  const mapperMsg = document.getElementById('html_mapper_msg');
  const filterSelect = document.getElementById('html_filter_select');
  const filterParamWrap = document.getElementById('html_filter_params');
  const filterParamA = document.getElementById('html_filter_param_a');
  const filterParamB = document.getElementById('html_filter_param_b');
  const filterApplyBtn = document.getElementById('html_filter_apply');
  const filterHint = document.getElementById('html_filter_hint');
  const mapperFiltered = document.getElementById('html_mapper_filtered');
  const step2Panel = document.getElementById('html_step2');
  const step2Body = document.getElementById('html_step2_body');
  const step2Controls = document.getElementById('html_step2_controls');
  const step1Panel = document.getElementById('html_step1_panel');
  const step1Hint = document.getElementById('html_step1_hint');
  let activeTemplateName = '';
  let lastEmailHtml = '';
  let templateJson = { fields: {} };
  let activeFieldKey = '';
  let lastSelectedEl = null;
  const fieldSamples = {};

  function setActiveTemplate(name) {
    activeTemplateName = (name || '').trim();
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

  function clearFieldBadges() {
    ['invoice_number', 'issue_date', 'due_date', 'amount_due', 'customer_name'].forEach((key) => {
      updateFieldBadge(key, '');
    });
  }

  function setPreviewFocus(active) {
    if (!previewEl) return;
    previewEl.style.boxShadow = active ? '0 0 0 3px rgba(99,102,241,0.35)' : '';
  }

  function setStep2Visible(visible) {
    if (!step2Panel || !step2Body || !step2Controls) return;
    step2Panel.style.display = visible ? '' : 'none';
    step2Body.style.display = visible ? 'none' : '';
    step2Controls.style.display = visible ? '' : 'none';
    if (step1Panel) step1Panel.style.opacity = visible ? '0.6' : '1';
    setPreviewFocus(!visible);
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
    if (filterSpec.type === 'highlight_text') return filterSpec.selected_text || '';
    if (filterSpec.type === 'digits_only') {
      const match = value.match(/([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)/);
      if (!match) return value.replace(/\D+/g, '');
      const num = parseFloat(match[1].replace(/,/g, ''));
      return Number.isFinite(num) ? num.toFixed(2) : match[1];
    }
    if (filterSpec.type === 'date') {
      const match = value.match(/\b\d{1,2}\/\d{1,2}\/\d{4}\b/i)
        || value.match(/\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b/i);
      return match ? match[0] : value;
    }
    if (filterSpec.type === 'after_token' && filterSpec.token) {
      const parts = value.split(filterSpec.token);
      return parts.length > 1 ? parts.slice(1).join(filterSpec.token).trim() : value;
    }
    if (filterSpec.type === 'before_token' && filterSpec.token) {
      const idx = value.indexOf(filterSpec.token);
      return idx !== -1 ? value.slice(0, idx).trim() : value;
    }
    if (filterSpec.type === 'between_tokens' && filterSpec.left && filterSpec.right) {
      const leftIdx = value.indexOf(filterSpec.left);
      if (leftIdx === -1) return value;
      const rightIdx = value.indexOf(filterSpec.right, leftIdx + filterSpec.left.length);
      if (rightIdx === -1) return value;
      return value.slice(leftIdx + filterSpec.left.length, rightIdx).trim();
    }
    if (filterSpec.type === 'regex' && filterSpec.pattern) {
      try {
        const rx = new RegExp(filterSpec.pattern, 'i');
        const match = rx.exec(value);
        if (!match) return value;
        const group = Number.isFinite(filterSpec.group) ? filterSpec.group : 1;
        return (match[group] || '').trim();
      } catch (err) {
        return value;
      }
    }
    return value;
  }

  function setFilterSelect(filterSpec) {
    if (!filterSelect) return;
    const value = filterSpec?.type || 'none';
    filterSelect.value = value;
    syncFilterParams(filterSpec);
  }

  function syncFilterParams(filterSpec) {
    if (!filterParamWrap || !filterParamA || !filterParamB) return;
    const type = filterSpec?.type || 'none';
    if (filterHint) {
      filterHint.style.display = type === 'highlight_text' ? 'block' : 'none';
    }
    filterParamWrap.style.display = 'none';
    filterParamA.style.display = 'none';
    filterParamB.style.display = 'none';
    if (type === 'after_token' || type === 'before_token') {
      filterParamA.placeholder = 'Token';
      filterParamA.value = filterSpec?.token || '';
      filterParamB.value = '';
    } else if (type === 'between_tokens') {
      filterParamA.placeholder = 'Left token';
      filterParamB.placeholder = 'Right token';
      filterParamA.value = filterSpec?.left || '';
      filterParamB.value = filterSpec?.right || '';
    } else if (type === 'regex') {
      filterParamA.placeholder = 'Regex pattern';
      filterParamB.placeholder = 'Capture group (default 1)';
      filterParamA.value = filterSpec?.pattern || '';
      filterParamB.value = filterSpec?.group != null ? String(filterSpec.group) : '';
    } else {
      filterParamA.value = '';
      filterParamB.value = '';
    }
  }

  function updateFilterPreview(fieldKey) {
    if (!fieldKey) return;
    const sample = fieldSamples[fieldKey] || '';
    const spec = templateJson.fields?.[fieldKey]?.filter || null;
    const filtered = spec?.type === 'highlight_text' && spec.selected_text
      ? spec.selected_text
      : applyFilter(sample, spec);
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

  function getNodeByPath(path) {
    if (!previewEl || !path || !Array.isArray(path)) return null;
    const doc = previewEl.contentDocument;
    if (!doc) return null;
    let node = doc.body;
    for (const step of path) {
      if (!node || !step || typeof step.index !== 'number') return null;
      const children = Array.from(node.children || []);
      if (step.index < 0 || step.index >= children.length) return null;
      node = children[step.index];
      if (step.tag && node.tagName && node.tagName.toLowerCase() !== step.tag) return null;
    }
    return node;
  }

  function getValueFromSpec(spec) {
    if (!spec || !spec.path) return '';
    const node = getNodeByPath(spec.path);
    if (!node) return '';
    if (spec.attr && spec.attr !== 'text') {
      return (node.getAttribute(spec.attr) || '').trim();
    }
    return (node.textContent || '').trim().replace(/\s+/g, ' ');
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
    const chosenElement = target;
    chosenElement.style.outline = '2px solid #6366f1';
    lastSelectedEl = chosenElement;
    const textValue = (chosenElement.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120);
    const path = buildElementPath(chosenElement);
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
    if (filterSelect && !filterSelect.value) filterSelect.value = 'none';
    updateFilterPreview(activeFieldKey);
    setStep2Visible(true);
    if (mapperMsg) mapperMsg.textContent = `Captured ${activeFieldKey.replace('_', ' ')}.`;
  }

  function renderTemplateList(list, selectedName) {
    if (!templateList) return;
    templateList.innerHTML = '';
    if (!list.length) {
      templateList.innerHTML = '<div class="muted" style="font-size:12px;">No templates yet.</div>';
      return;
    }
    for (const item of list) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = item.template_name;
      btn.className = 'btn btn--subtle';
      btn.style.width = '100%';
      btn.style.justifyContent = 'flex-start';
      btn.style.marginBottom = '6px';
      if (item.template_name === selectedName) {
        btn.style.background = '#eef2ff';
      }
      btn.addEventListener('click', () => loadTemplate(item.template_name));
      templateList.appendChild(btn);
    }
  }

  async function loadTemplates(selectedName) {
    try {
      const res = await fetch('/api/inbound/html/templates', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      const list = Array.isArray(data.templates) ? data.templates : [];
      const chosen = selectedName || list[0]?.template_name || '';
      renderTemplateList(list, chosen);
      if (chosen) {
        await loadTemplate(chosen);
      } else {
        setSubjectToken('');
        lastEmailHtml = '';
        setTemplateJson({ fields: {} });
        clearFieldBadges();
        setPreview('');
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
      setSubjectToken(data.subject_token || '');
      setActiveTemplate(data.template_name || name);
      lastEmailHtml = data.html_email_body || '';
      setTemplateJson(data.template_json || {});
      clearFieldBadges();
      Object.keys(templateJson.fields || {}).forEach((fieldKey) => {
        const spec = templateJson.fields[fieldKey];
        if (!spec || !spec.path) return;
        const rawValue = getValueFromSpec(spec);
        fieldSamples[fieldKey] = rawValue;
        const filteredValue = applyFilter(rawValue, spec?.filter);
        updateFieldBadge(fieldKey, filteredValue || rawValue || '');
      });
      setPreview(lastEmailHtml || '');
      previewEl?.addEventListener('load', () => {
        clearFieldBadges();
        Object.keys(templateJson.fields || {}).forEach((fieldKey) => {
          const spec = templateJson.fields[fieldKey];
          if (!spec || !spec.path) return;
          const rawValue = getValueFromSpec(spec);
          fieldSamples[fieldKey] = rawValue;
          const filteredValue = applyFilter(rawValue, spec?.filter);
          updateFieldBadge(fieldKey, filteredValue || rawValue || '');
        });
        updateFilterPreview(activeFieldKey);
      }, { once: true });
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
    const payload = new FormData();
    payload.append('template_name', templateName);
    payload.append('html_body', '');
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
      setPreview(lastEmailHtml || '');
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
    payload.append('html_body', '');
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
      setPreview(lastEmailHtml || '');
    } catch (err) {
      msgEl.textContent = 'Create failed.';
      console.error('Failed to create HTML template', err);
    }
  }

  createBtn?.addEventListener('click', createTemplate);
  subjectCopyBtn?.addEventListener('click', () => {
    if (!subjectInput || !subjectInput.value) return;
    subjectInput.select();
    document.execCommand('copy');
  });
  importCopyBtn?.addEventListener('click', () => {
    if (!importAddressInput || !importAddressInput.value) return;
    importAddressInput.select();
    document.execCommand('copy');
  });
  previewEl?.addEventListener('load', () => {
    attachPreviewClickHandler();
    const doc = previewEl.contentDocument;
    if (doc) {
      doc.addEventListener('mouseup', () => {
        if (filterSelect?.value === 'highlight_text') {
          handleHighlightFilter();
        }
      });
      doc.addEventListener('click', () => {
        if (filterSelect?.value === 'highlight_text') {
          handleHighlightFilter();
        }
      });
    }
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
      setStep2Visible(false);
      if (step1Hint) step1Hint.textContent = 'Now click the value you want to map in the preview.';
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
    if (selected === 'highlight_text') {
      if (mapperMsg) mapperMsg.textContent = 'Highlight the exact text in the preview.';
      if (filterApplyBtn) filterApplyBtn.parentElement.style.display = '';
    } else if (filterApplyBtn) {
      filterApplyBtn.parentElement.style.display = 'none';
    }
    syncFilterParams(templateJson.fields[activeFieldKey].filter);
    updateFilterPreview(activeFieldKey);
    const sample = fieldSamples[activeFieldKey] || '';
    const filtered = applyFilter(sample, templateJson.fields[activeFieldKey].filter);
    updateFieldBadge(activeFieldKey, filtered || sample || 'mapped');
  });
  const paramHandler = () => {
    if (!activeFieldKey || !templateJson.fields?.[activeFieldKey]) return;
    const filterSpec = templateJson.fields[activeFieldKey].filter || { type: 'none' };
    if (filterSpec.type === 'after_token' || filterSpec.type === 'before_token') {
      filterSpec.token = filterParamA?.value || '';
    } else if (filterSpec.type === 'between_tokens') {
      filterSpec.left = filterParamA?.value || '';
      filterSpec.right = filterParamB?.value || '';
    } else if (filterSpec.type === 'regex') {
      filterSpec.pattern = filterParamA?.value || '';
      const group = parseInt(filterParamB?.value || '1', 10);
      filterSpec.group = Number.isFinite(group) ? group : 1;
    }
    templateJson.fields[activeFieldKey].filter = filterSpec;
    updateFilterPreview(activeFieldKey);
    const sample = fieldSamples[activeFieldKey] || '';
    const filtered = applyFilter(sample, filterSpec);
    updateFieldBadge(activeFieldKey, filtered || sample || 'mapped');
  };
  filterParamA?.addEventListener('input', paramHandler);
  filterParamB?.addEventListener('input', paramHandler);

  function deriveTokenFilter(raw, selection) {
    const value = (raw || '').trim().replace(/\s+/g, ' ');
    const selected = (selection || '').trim().replace(/\s+/g, ' ');
    if (!value || !selected) return null;
    const idx = value.toLowerCase().indexOf(selected.toLowerCase());
    if (idx === -1) return null;
    const leftContext = value.slice(0, idx).trim();
    const rightContext = value.slice(idx + selected.length).trim();
    const leftToken = leftContext.slice(-20).trim();
    const rightToken = rightContext.slice(0, 20).trim();
    if (leftToken && rightToken) {
      return { type: 'between_tokens', left: leftToken, right: rightToken };
    }
    if (leftToken) {
      return { type: 'after_token', token: leftToken };
    }
    if (rightToken) {
      return { type: 'before_token', token: rightToken };
    }
    return { type: 'regex', pattern: selected };
  }

  function handleHighlightFilter() {
    if (!activeFieldKey) return;
    const doc = previewEl?.contentDocument;
    if (!doc) return;
    const selection = doc.getSelection ? doc.getSelection() : null;
    const selectedText = (selection?.toString() || '').trim();
    if (!selectedText) {
      if (mapperMsg) mapperMsg.textContent = 'Highlight text in the preview first.';
      return;
    }
    let sample = fieldSamples[activeFieldKey] || '';
    if (selection?.rangeCount) {
      const range = selection.getRangeAt(0);
      const ancestor = range.commonAncestorContainer;
      const container = ancestor.nodeType === 1 ? ancestor : ancestor.parentElement;
      if (container && container.textContent) {
        sample = container.textContent.trim().replace(/\s+/g, ' ');
      }
    }
    const derived = deriveTokenFilter(sample, selectedText);
    if (!derived) {
      if (mapperMsg) mapperMsg.textContent = 'Could not build a filter from the highlight.';
      return;
    }
    templateJson.fields[activeFieldKey] = {
      ...(templateJson.fields[activeFieldKey] || {}),
      filter: { type: 'highlight_text', selected_text: selectedText, derived }
    };
    setFilterSelect(templateJson.fields[activeFieldKey].filter);
    updateFilterPreview(activeFieldKey);
    updateFieldBadge(activeFieldKey, selectedText.trim() || sample || 'mapped');
    if (mapperMsg) mapperMsg.textContent = 'Filter created from highlight.';
  }
  filterApplyBtn?.addEventListener('click', () => {
    if (filterSelect?.value === 'highlight_text') {
      handleHighlightFilter();
    }
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
      setPreview(lastEmailHtml || '');
      if (msgEl) msgEl.textContent = 'Preview updated.';
    } catch (err) {
      if (msgEl) msgEl.textContent = 'Preview refresh failed.';
      console.error('Failed to refresh HTML preview', err);
    }
  });
  setActiveTemplate('');
  setPreview('');
  setSubjectToken('');
  async function loadImportAddress() {
    if (!importAddressInput) return;
    try {
      const res = await fetch('/api/inbound/settings', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      importAddressInput.value = data.inbound_address || '';
    } catch (err) {
      console.error('Failed to load inbound settings', err);
    }
  }

  loadImportAddress();
  loadTemplates();
})();
