// HTML invoice import settings
(function () {
  const nameInput = document.getElementById('html_template_name');
  const selectEl = document.getElementById('html_template_selector');
  const bodyInput = document.getElementById('html_invoice_body');
  const createBtn = document.getElementById('html_template_create');
  const saveBtn = document.getElementById('html_invoice_save');
  const msgEl = document.getElementById('html_invoice_msg');
  let activeTemplateName = '';

  function setActiveTemplate(name) {
    activeTemplateName = (name || '').trim();
    if (saveBtn) saveBtn.disabled = !activeTemplateName;
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
      if (selectedName) selectEl.value = selectedName;
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
      setActiveTemplate(data.template_name || name);
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
    payload.append('template_json', JSON.stringify({ fields: {} }));
    try {
      const res = await fetch('/api/inbound/html/save-template', { method: 'POST', body: payload });
      if (!res.ok) {
        msgEl.textContent = 'Save failed.';
        return;
      }
      msgEl.textContent = 'Saved.';
      setActiveTemplate(templateName);
      await loadTemplates(templateName);
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
    payload.append('template_json', JSON.stringify({ fields: {} }));
    try {
      const res = await fetch('/api/inbound/html/save-template', { method: 'POST', body: payload });
      if (!res.ok) {
        msgEl.textContent = 'Create failed.';
        return;
      }
      msgEl.textContent = 'Template created.';
      setActiveTemplate(templateName);
      await loadTemplates(templateName);
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
  setActiveTemplate('');
  loadTemplates();
})();
