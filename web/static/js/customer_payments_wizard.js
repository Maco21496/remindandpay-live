// /static/js/customer_payments_wizard.js
(function(){
  const dlg   = document.getElementById('pay_wizard');
  const body  = document.getElementById('pay_body');
  const steps = document.getElementById('pay_steps');

  const state = {
    step: 1,
    details: { amount: null, method: 'bank', received_at: null },
    allocations: [] // [{invoice_id, amount}]
  };

  function getCustId(){
    const boot = document.getElementById('cust-boot');
    if (boot?.dataset?.id) return Number(boot.dataset.id);
    if (typeof window !== 'undefined' && window.__IC_CUSTOMER_ID__) return Number(window.__IC_CUSTOMER_ID__);
    return null;
  }

  function getLocale(){
    try { return (window.__APP_SETTINGS__ && __APP_SETTINGS__.date_locale) || 'en-GB'; }
    catch { return 'en-GB'; }
  }
  function altFormatFor(loc){
    const lc = String(loc || '').toLowerCase();
    return lc.includes('en-us') || lc === 'us' ? 'm/d/Y' : 'd/m/Y';
  }

  function updateStepper(){
    if (!steps) return;
    steps.querySelectorAll('li').forEach((li, idx) => {
      li.classList.remove('is-active','is-complete');
      if (idx < state.step - 1) li.classList.add('is-complete');
      else if (idx === state.step - 1) li.classList.add('is-active');
    });
  }

  function openWizard(){
    state.step = 1;
    state.details = { amount: null, method: 'bank', received_at: null };
    state.allocations = [];
    render();
    dlg.showModal();
  }
  function closeWizard(){ try { dlg.close(); } catch {} }

  const money = v => {
    try { return (window.AppCurrency && AppCurrency.format) ? AppCurrency.format(v) : ('£' + Number(v||0).toFixed(2)); }
    catch { return '£' + Number(v||0).toFixed(2); }
  };

  function numberInput(id){
    return `<input id="${id}" type="number" step="0.01" min="0" style="height:36px;padding:0 10px;width:100%;">`;
  }
  function dateInput(id){ return `<input id="${id}" type="text" style="height:36px;padding:0 10px;width:100%;">`; }

  function initDatePicker(input){
    if (!input) return;
    const loc = getLocale();
    const altFmt = altFormatFor(loc);
    if (!window.flatpickr) {
      input.type = 'date';
      input.setAttribute('lang', loc);
      input.placeholder = (altFmt === 'm/d/Y') ? 'mm/dd/yyyy' : 'dd/mm/yyyy';
      return;
    }
    window.flatpickr(input, {
      appendTo: dlg,
      dateFormat: 'Y-m-d',
      altInput: true,
      altFormat: altFmt,
      allowInput: true,
      disableMobile: true
    });
  }

  function summaryBarHTML(){
    return `
      <div id="alloc_summary" class="panel" style="margin-top:10px;padding:10px;display:flex;gap:18px;justify-content:flex-end;">
        <div><span class="muted">Payment:</span> <strong id="sum_pay">${money(state.details.amount)}</strong></div>
        <div><span class="muted">Allocated:</span> <strong id="sum_alloc">£0.00</strong></div>
        <div><span class="muted">Unallocated:</span> <strong id="sum_left">£0.00</strong></div>
      </div>
    `;
  }

  function recalcSummary(){
    const inputs = Array.from(body.querySelectorAll('input.alloc'));
    const total = inputs.reduce((s,i)=> s + Number(i.value || 0), 0);
    const left  = Number(state.details.amount || 0) - total;

    const sumAlloc = body.querySelector('#sum_alloc');
    const sumLeft  = body.querySelector('#sum_left');
    if (sumAlloc) sumAlloc.textContent = money(total);
    if (sumLeft)  sumLeft.textContent  = money(left);

    // soft validation message
    const msg = body.querySelector('#alloc_msg');
    if (msg) {
      if (left < -0.0001) {
        msg.textContent = 'Allocations exceed payment amount';
        msg.style.color = '#c62828';
      } else {
        msg.textContent = '';
      }
    }
  }

  function toggleFullForRow(checkbox){
    const row = checkbox.closest('tr');
    if (!row) return;
    const input = row.querySelector('input.alloc');
    const remain = Number(checkbox.dataset.remaining || 0);
    input.value = checkbox.checked ? remain.toFixed(2) : '';
    input.dispatchEvent(new Event('input', { bubbles:true }));
  }

  async function render(){
    updateStepper();

    if (state.step === 1){
      body.innerHTML = `
        <div class="grid grid-4">
          <div class="field">
            <label>Amount *</label>
            ${numberInput('pay_amt')}
          </div>
          <div class="field">
            <label>Method *</label>
            <select id="pay_method" style="height:36px;padding:0 8px;">
              <option value="bank">bank</option>
              <option value="card">card</option>
              <option value="cash">cash</option>
              <option value="other">other</option>
            </select>
          </div>
          <div class="field">
            <label>Date received</label>
            ${dateInput('pay_date')}
          </div>
        </div>
      `;
      initDatePicker(document.getElementById('pay_date'));
      document.getElementById('pay_next').textContent = 'Next';
      return;
    }

    if (state.step === 2){
      const custId = getCustId();
      body.innerHTML = `<div class="muted">Loading invoices…</div>`;
      try {
        const url = `/api/dashboard/customer-invoices?customer_id=${encodeURIComponent(custId)}&limit=500`;
        const r = await fetch(url);
        if (!r.ok) throw new Error(String(r.status));
        let items = await r.json();

        // Hide fully-paid (safety on the client too)
        items = items.filter(x => Number(x.amount_due || 0) > 0.0001);

        if (!items.length){
          body.innerHTML = `
            <div class="empty">No open invoices for this customer.</div>
            ${summaryBarHTML()}
            <div id="alloc_msg" class="muted" style="margin-top:6px;"></div>
          `;
        } else {
          body.innerHTML = `
            <div class="muted" style="margin:0 0 6px;">Enter amounts to allocate (leave blank = zero).</div>
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Number</th>
                  <th style="text-align:right;">Remaining</th>
                  <th>Allocate</th>
                  <th style="text-align:center;">Full</th>
                </tr>
              </thead>
              <tbody>
                ${items.map(x => `
                  <tr>
                    <td>${x.id}</td>
                    <td>${String(x.invoice_number || '')}</td>
                    <td style="text-align:right;">${money(x.amount_due)}</td>
                    <td style="width:160px;">
                      <input class="alloc" data-inv="${x.id}" type="number" step="0.01" min="0"
                             style="width:140px;height:32px;padding:0 8px;">
                    </td>
                    <td style="text-align:center;">
                      <input type="checkbox" class="alloc-full" data-inv="${x.id}" data-remaining="${Number(x.amount_due || 0)}" />
                    </td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
            ${summaryBarHTML()}
            <div id="alloc_msg" class="muted" style="margin-top:6px;"></div>
          `;
        }

        // Live total updates
        body.querySelectorAll('input.alloc').forEach(inp => {
          inp.addEventListener('input', recalcSummary);
        });
        body.querySelectorAll('input.alloc-full').forEach(cb => {
          cb.addEventListener('change', () => toggleFullForRow(cb));
        });

        recalcSummary();
      } catch {
        body.innerHTML = `<div class="empty">Failed to load invoices.</div>`;
      }
      document.getElementById('pay_next').textContent = 'Record payment';
      return;
    }

    if (state.step === 3){
      document.getElementById('pay_next').disabled = true;
      body.innerHTML = `<div class="muted">Saving…</div>`;
    }
  }

  function next(){
    if (state.step === 1){
      const amt    = Number(document.getElementById('pay_amt').value);
      const method = String(document.getElementById('pay_method').value || '');
      const date   = document.getElementById('pay_date').value || ''; // ISO (Y-m-d) from flatpickr
      if (!amt || !method){ alert('Amount and method are required'); return; }
      state.details = { amount: amt, method, received_at: (date ? date + 'T00:00:00' : null) };
      state.step = 2; render();
      return;
    }

    if (state.step === 2){
      const inputs = Array.from(body.querySelectorAll('input.alloc'));
      const allocs = inputs
        .map(i => ({ invoice_id: Number(i.dataset.inv), amount: Number(i.value || 0) }))
        .filter(x => x.amount > 0);
      const total = allocs.reduce((s,x)=>s + x.amount, 0);
      const left  = Number(state.details.amount || 0) - total;

      if (total > state.details.amount + 1e-9){
        const m = document.getElementById('alloc_msg');
        if (m) { m.textContent = 'Allocations exceed payment amount'; m.style.color = '#c62828'; }
        return;
      }

      if (left > 0.0001){
        const ok = confirm(`£${left.toFixed(2)} is still unallocated. Do you want to continue?`);
        if (!ok) return;
      }

      state.allocations = allocs;
      submit();
    }
  }

  async function submit(){
    state.step = 3; render();

    const custId = getCustId();
    const payload = {
      customer_id: custId,
      amount: state.details.amount,
      method: state.details.method,
      received_at: state.details.received_at,
      allocations: state.allocations
    };

    try {
      const r = await fetch('/api/payments/record', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!r.ok){
        const hint = (r.status === 404) ? ' (payments API not added yet)' : '';
        alert('Failed to record payment' + hint);
        closeWizard();
        return;
      }

      // refresh KPIs / lists on success
      if (typeof loadSummary === 'function') await loadSummary(custId);
      if (typeof loadCustomerInvoices === 'function') await loadCustomerInvoices(custId);
      if (typeof window.refreshCustomerTransactions === 'function') {
        await window.refreshCustomerTransactions();
      }

      closeWizard();
      alert('Payment recorded');
    } catch {
      alert('Failed to record payment');
      closeWizard();
    }
  }

  function back(){ if (state.step > 1){ state.step--; render(); } }

  document.addEventListener('click', (e)=>{
    if (e.target?.id === 'btn_add_payment') openWizard();
    if (e.target?.id === 'pay_next') next();
    if (e.target?.id === 'pay_back') back();
  });
})();
