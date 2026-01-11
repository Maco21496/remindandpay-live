// /static/js/customer_statement_email_wizard.js
(function(){
  const dlg   = document.getElementById('stm_wizard');
  const body  = document.getElementById('stm_body');
  const steps = document.getElementById('stm_steps');

  const state = {
    step: 1,
    to: '',
    subject: '',
    message: '',
    from: '',
    toDate: '',
  };

  // Boot data (customer id/name/email) is provided by #st-boot on the page
  function getBoot(){
    const el = document.getElementById('st-boot');
    return {
      id: Number(el?.dataset?.id || 0),
      name: el?.dataset?.name || '',
      email: el?.dataset?.email || '',
    };
  }
  function getDateRange(){
    const from = document.getElementById('st_from')?.value || '';
    const to   = document.getElementById('st_to')?.value || '';
    return { from, to };
  }

  function updateStepper(){
    if (!steps) return;
    const lis = steps.querySelectorAll('li');
    lis.forEach((li, idx) => {
      li.classList.remove('is-active','is-complete');
      if (idx < state.step - 1) li.classList.add('is-complete');
      else if (idx === state.step - 1) li.classList.add('is-active');
    });
    const backBtn = document.getElementById('stm_back');
    if (backBtn) backBtn.disabled = (state.step === 1);
  }

  function openWizard(){
    const boot = getBoot();
    const { from, to } = getDateRange();
    state.step    = 1;
    state.to      = boot.email || '';
    state.subject = `Statement for ${boot.name}` + (from || to ? ` (${from || '…'} – ${to || '…'})` : '');
    state.message = `Hi,

Please find your latest statement below.

Regards,
${window.AppDate?.businessName || 'Accounts'}`;
    state.from    = from;
    state.toDate  = to;
    render();
    try { dlg.showModal(); } catch {}
  }
  function closeWizard(){ try { dlg.close(); } catch {} }

  function emailValid(s){ return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(s||'').trim()); }

  function render(){
    updateStepper();

    if (state.step === 1){
      body.innerHTML = `
        <div class="grid grid-2" style="gap:12px;">
          <div class="field">
            <label>To email *</label>
            <input id="stm_to" placeholder="customer@domain.com" style="height:36px;padding:0 8px;width:100%;" value="${state.to || ''}">
            <div id="stm_to_err" class="muted" style="color:#b00020;margin-top:4px;display:none;">Enter a valid email address</div>
          </div>
          <div class="field">
            <label>Subject *</label>
            <input id="stm_subject" style="height:36px;padding:0 8px;width:100%;" value="${state.subject || ''}">
          </div>
        </div>
        <div class="field" style="margin-top:12px;">
          <label>Message</label>
          <textarea id="stm_msg" rows="6" style="width:100%;padding:8px;">${state.message || ''}</textarea>
        </div>
      `;
      document.getElementById('stm_next').textContent = 'Next';
      return;
    }

    if (state.step === 2){
      const dateLine = (state.from || state.toDate)
        ? `<p><strong>Period:</strong> ${state.from || '–'} → ${state.toDate || '–'}</p>`
        : '';
      body.innerHTML = `
        <div class="muted" style="margin-bottom:8px;">Preview</div>
        <div class="card" style="padding:12px;">
          <div><strong>To:</strong> ${state.to}</div>
          <div><strong>Subject:</strong> ${state.subject}</div>
          <hr style="margin:10px 0;">
          <div class="email-preview">
            <p>${(state.message || '').replace(/\n/g,'<br>')}</p>
            ${dateLine}
            <p><em>This email will include your statement as a PDF attachment.</em></p>
          </div>
        </div>
      `;
      document.getElementById('stm_next').textContent = 'Send';
      return;
    }

    if (state.step === 3){
      document.getElementById('stm_next').disabled = true;
      body.innerHTML = `<div class="muted">Sending…</div>`;
    }
  }

  function next(){
    if (state.step === 1){
      const to   = document.getElementById('stm_to').value.trim();
      const subj = document.getElementById('stm_subject').value.trim();
      const msg  = document.getElementById('stm_msg').value;
      state.to = to; state.subject = subj; state.message = msg;

      const err = document.getElementById('stm_to_err');
      if (!emailValid(to)) { if (err) err.style.display = 'block'; return; }
      if (err) err.style.display = 'none';
      if (!subj) return;

      state.step = 2; render();
      return;
    }

    if (state.step === 2){
      submit();
    }
  }

  function back(){ if (state.step > 1){ state.step--; render(); } }

  async function submit(){
    state.step = 3; render();

    const boot = getBoot();
    const qs   = (state.from || state.toDate)
      ? '?' + new URLSearchParams({ date_from: state.from || '', date_to: state.toDate || '' }).toString()
      : '';

    const payload = {
      customer_id: boot.id,
      to_email: state.to,
      subject: state.subject,
      message: state.message,
      date_from: state.from || null,
      date_to: state.toDate || null,
      // link for now; we can switch to a PDF attachment in a later iteration
      statement_url: window.location.origin + `/customers/${boot.id}/statement${qs}`
    };

    try{
      const r = await fetch('/api/statement_reminders/email/statement/enqueue-one', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      if (!r.ok) {
        const txt = await r.text();
        throw new Error(`HTTP ${r.status}: ${txt}`);
      }
      closeWizard();
      alert('Statement queued to send');
      }catch(e){
        closeWizard();
        alert(`Failed to send statement email:\n${e.message}`);
      }
  }

  document.addEventListener('click', (e)=>{
    if (e.target?.id === 'btn_email_statement') openWizard();
    if (e.target?.id === 'stm_next') next();
    if (e.target?.id === 'stm_back') back();
    if (e.target?.id === 'stm_close') closeWizard();
  });
})();
