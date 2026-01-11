// /static/js/customer_editor.js
(function(){
  const dlg = document.getElementById('customer_editor');
  if (!dlg) return;

  // fields
  const f = {
    title:     document.getElementById('cust_ed_title'),
    name:      document.getElementById('cust_ed_name'),
    email:     document.getElementById('cust_ed_email'),
    phone:     document.getElementById('cust_ed_phone'),
    country:   document.getElementById('cust_ed_country'),
    line1:     document.getElementById('cust_ed_line1'),
    line2:     document.getElementById('cust_ed_line2'),
    city:      document.getElementById('cust_ed_city'),
    region:    document.getElementById('cust_ed_region'),
    post:      document.getElementById('cust_ed_postcode'),
    regionLbl: document.getElementById('cust_ed_region_lbl'),
    postLbl:   document.getElementById('cust_ed_post_lbl'),

    // terms
    termsType:     document.getElementById('cust_ed_terms_type'),
    termsDaysWrap: document.getElementById('cust_ed_terms_days_wrap'),
    termsDays:     document.getElementById('cust_ed_terms_days'),

    // recalc checkbox
    recalcWrap:    document.getElementById('cust_ed_recalc_wrap'),
    recalc:        document.getElementById('cust_ed_recalc'),

    msg:       document.getElementById('cust_ed_msg'),
    btnSave:   document.getElementById('cust_ed_save'),
    btnCancel: document.getElementById('cust_ed_cancel')
  };

  let editId = null; // null = create
  let origTerms = { type: 'net_30', days: null };

  function defaultCountry(){
    const c = (window.__APP_SETTINGS__ && __APP_SETTINGS__.default_country_code) || 'GB';
    return String(c).toUpperCase();
  }

  function applyLabels(){
    const c = (f.country.value || defaultCountry()).toUpperCase();
    if (c === 'US'){
      f.regionLbl.textContent = 'State';
      f.postLbl.textContent   = 'ZIP code';
    } else {
      f.regionLbl.textContent = 'County/Region';
      f.postLbl.textContent   = 'Postcode';
    }
  }

  function toggleTermsDays(){
    if (!f.termsType || !f.termsDaysWrap) return;
    f.termsDaysWrap.style.display = (f.termsType.value === 'custom') ? 'block' : 'none';
  }

  function showRecalc(show){
    if (f.recalcWrap) f.recalcWrap.style.display = show ? '' : 'none';
    if (!show && f.recalc) f.recalc.checked = false;
  }

  function clearForm(){
    editId = null;
    f.title.textContent = 'New customer';
    [f.name, f.email, f.phone, f.line1, f.line2, f.city, f.region, f.post].forEach(i => i.value = '');
    f.country.value = defaultCountry();
    applyLabels();
    f.msg.textContent = '';

    // reset terms to defaults
    if (f.termsType) f.termsType.value = 'net_30';
    if (f.termsDays) f.termsDays.value = '';
    toggleTermsDays();

    // recalc hidden for "create"
    showRecalc(false);

    // remember original terms baseline
    origTerms = { type: 'net_30', days: null };
  }

  async function loadForEdit(id){
    clearForm();
    editId = id;
    f.title.textContent = 'Edit customer';
    try {
      const r = await fetch(`/api/customers/${id}`);
      if (!r.ok) throw new Error(String(r.status));
      const c = await r.json();

      f.name.value   = c.name || '';
      f.email.value  = c.email || '';
      f.phone.value  = c.phone || '';
      f.country.value= (c.billing_country || defaultCountry()).toUpperCase();
      f.line1.value  = c.billing_line1 || '';
      f.line2.value  = c.billing_line2 || '';
      f.city.value   = c.billing_city  || '';
      f.region.value = c.billing_region|| '';
      f.post.value   = c.billing_postcode || '';
      applyLabels();

      // load terms
      const tType = c.terms_type || 'net_30';
      const tDays = (tType === 'custom' && c.terms_days != null) ? Number(c.terms_days) : null;
      if (f.termsType) f.termsType.value = tType;
      if (f.termsDays) f.termsDays.value = (tDays != null ? String(tDays) : '');
      toggleTermsDays();

      // remember original terms to help the user decide
      origTerms = { type: tType, days: tDays };

      // show the recalc checkbox now that we're editing
      showRecalc(true);
    } catch {
      f.msg.textContent = 'Failed to load customer.';
    }
  }

  function termsChanged(){
    const nowType = f.termsType ? f.termsType.value : 'net_30';
    const nowDays = (nowType === 'custom') ? (Number(f.termsDays?.value || 0) || null) : null;
    return (origTerms.type !== nowType) || (String(origTerms.days||'') !== String(nowDays||''));
  }

  async function save(){
    const tType = f.termsType ? (f.termsType.value || 'net_30') : 'net_30';
    const tDays = (tType === 'custom')
      ? (Number(f.termsDays?.value || 0) || null)
      : null;

    const payload = {
      name: f.name.value.trim(),
      email: f.email.value.trim() || null,
      phone: f.phone.value.trim() || null,
      billing_country: (f.country.value || defaultCountry()).toUpperCase(),
      billing_line1: f.line1.value.trim() || null,
      billing_line2: f.line2.value.trim() || null,
      billing_city:  f.city.value.trim()  || null,
      billing_region: f.region.value.trim() || null,
      billing_postcode: f.post.value.trim() || null,

      // terms snapshot
      terms_type: tType,
      terms_days: tDays,

      // optional server-side action (only relevant in edit)
      recalc_due_dates: !!(editId && f.recalc && f.recalc.checked)
    };

    if (!payload.name){ f.msg.textContent = 'Name is required.'; return; }
    if (tType === 'custom' && (tDays == null || tDays < 0)){ f.msg.textContent = 'Enter a valid number of custom days.'; return; }

    f.msg.textContent = 'Saving…';

    try {
      let r, j;
      if (editId){
        r = await fetch(`/api/customers/${editId}`, {
          method:'PUT', headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload)
        });
        j = await r.json().catch(()=> ({}));
        if (!r.ok) throw new Error(j?.detail || String(r.status));
        f.msg.textContent = 'Saved.';
        try { dlg.close(); } catch {}

        // Refresh any dashboard bits
        const boot = document.getElementById('cust-boot');
        if (boot){
          boot.dataset.name = payload.name;
          const id = Number(boot.dataset.id || 0);
          if (typeof loadSummary === 'function') loadSummary(id);
        }
      } else {
        r = await fetch('/api/customers', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload)
        });
        j = await r.json().catch(()=> ({}));
        if (!r.ok) throw new Error(j?.detail || String(r.status));
        const newId = j.id || j.customer_id;
        if (newId){
          try { dlg.close(); } catch {}
          window.location.assign(`/customers/${newId}`);
        } else {
          f.msg.textContent = 'Saved, but no ID returned.';
        }
      }
    } catch (e){
      f.msg.textContent = 'Save failed.';
    }
  }

  // openers
  function openNew(){ clearForm(); dlg.showModal(); }
  function openEdit(id){ clearForm(); loadForEdit(id).finally(()=> dlg.showModal()); }

  // wire controls
  f.country.addEventListener('change', applyLabels);
  f.termsType?.addEventListener('change', ()=>{ toggleTermsDays(); /* optional UX */ });
  f.termsDays?.addEventListener('input', ()=>{ /* optional UX */ });
  f.btnCancel.addEventListener('click', (e)=>{ e.preventDefault(); try{ dlg.close(); }catch{} });
  f.btnSave.addEventListener('click', (e)=>{ e.preventDefault(); save(); });

  // global hooks
  document.addEventListener('click', (e)=>{
    // "Add customer" anywhere
    const add = e.target.closest('#btn_add_customer');
    if (add){
      e.preventDefault();
      openNew();
      return;
    }
    // Edit on customer dashboard
    const edit = e.target.closest('#btn_edit_customer');
    if (edit){
      const boot = document.getElementById('cust-boot');
      const id = Number(boot?.dataset?.id || 0);
      if (id) openEdit(id);
    }
  });

  // expose if you want to open from elsewhere
  window.openCustomerCreate = openNew;
  window.openCustomerEdit   = openEdit;
})();
