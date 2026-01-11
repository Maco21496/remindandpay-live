// FINAL VERSION OF /static/js/email_settings_ui.js
(function () {
  const $ = (id) => document.getElementById(id);

  // Common UI
  const fromNameInp  = $('em_from_name');
  const emMsg        = $('em_msg');

  const senderList   = $('sender_list');
  const senderMount  = $('sender_verified_mount');

  const testToInp    = $('email_test_to');
  const testBtn      = $('email_test_send');
  const testMsg      = $('email_test_msg');

  // Per-user server init
  const pmInitBtn    = $('btn_pm_init_server');
  const pmInitMsg    = $('pm_init_msg');

  // Domain wizard UI
  const openWizardBtn = $('btn_open_domain_wizard');
  const modal         = $('domain_wizard_modal');
  const closeBtn      = $('dw_close');
  const startBtn      = $('dw_start');
  const verifyBtn     = $('dw_verify');
  const domainInp     = $('dw_domain');
  const dwMsg         = $('dw_msg');
  const dnsBlock      = $('dw_dns_block');
  const dwStep1       = $('dw_step1');
  const dwStep2       = $('dw_step2');
  const dwVerifyMsg   = $('dw_verify_msg');
  const dwVerifiedHint= $('dw_verified_hint');
  const dwDeleteBtn   = $('dw_delete'); // optional “Remove” button in step 2

  const PLATFORM_NAME  = 'Remind & Pay';
  const PLATFORM_EMAIL = 'accounts@remindandpay.com';

  let current = null;          // /api/email/settings result
  let domains = [];            // from /api/email/domains
  let currentDomainId = null;  // wizard state
  let currentDomainName = null;

  // ---------- helpers ----------
  function debounce(fn, ms) {
    let t = null;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function emailLocalPart(email) {
    const i = (email || '').indexOf('@');
    return i > 0 ? email.slice(0, i) : '';
  }

  function makeVerifiedCard(d) {
    const wrap = document.createElement('label');
    wrap.className = 'cardlike';
    wrap.style.cssText = 'display:flex;align-items:center;gap:10px;padding:10px;border:1px solid #eee;border-radius:10px;';

    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = 'sender_choice';
    radio.value = `custom:${d.domain}`;
    radio.dataset.domain = d.domain;

    const box = document.createElement('div');

    const title = document.createElement('div');
    title.innerHTML = `<strong>${d.domain}</strong>`;

    const row = document.createElement('div');
    row.className = 'field-row';
    row.style.cssText = 'gap:8px;align-items:center;margin-top:2px;';
    const lbl = document.createElement('span');
    lbl.className = 'muted';
    lbl.textContent = 'From:';
    const local = document.createElement('input');
    local.type = 'text';
    local.placeholder = 'accounts';
    local.style.cssText = 'height:28px;padding:0 8px;width:160px;';
    local.dataset.role = 'local';
    local.dataset.domain = d.domain;

    const suffix = document.createElement('span');
    suffix.className = 'muted';
    suffix.textContent = `@${d.domain}`;

    const df = current?.default_from_email || '';
    if (df.endsWith(`@${d.domain}`)) {
      local.value = emailLocalPart(df) || 'accounts';
    } else {
      local.value = 'accounts';
    }

    row.appendChild(lbl);
    row.appendChild(local);
    row.appendChild(suffix);

    box.appendChild(title);
    box.appendChild(row);

    wrap.appendChild(radio);
    wrap.appendChild(box);

    const updateEnabled = () => {
      const checked = radio.checked;
      local.disabled = !checked;
      local.style.opacity = checked ? '1' : '.6';
    };
    radio.addEventListener('change', updateEnabled);
    updateEnabled();

    radio.addEventListener('change', () => {
      if (!radio.checked) return;
      saveSelection({
        mode: 'custom_domain',
        email: `${(local.value || 'accounts').trim()}@${d.domain}`,
      });
    });

    local.addEventListener('input', debounce(() => {
      if (!radio.checked) return;
      const email = `${(local.value || 'accounts').trim()}@${d.domain}`;
      saveSelection({ mode: 'custom_domain', email });
    }, 600));

    return wrap;
  }

  function renderApprovedList() {
    senderMount.innerHTML = '';

    const verified = (domains || []).filter(
      d => d.status === 'verified' || (d.dkim_verified && d.rp_verified)
    );

    for (const d of verified) {
      senderMount.appendChild(makeVerifiedCard(d));
    }

    const emailNow = (current?.default_from_email || '').toLowerCase();
    const wanted =
      emailNow === PLATFORM_EMAIL.toLowerCase()
        ? 'platform'
        : `custom:${(emailNow.split('@')[1] || '').toLowerCase()}`;

    const radios = senderList.querySelectorAll('input[name="sender_choice"]');
    let matched = false;
    radios.forEach(r => {
      if (r.value === 'platform' && wanted === 'platform') {
        r.checked = true; matched = true;
      } else if (r.value.startsWith('custom:') && wanted === r.value) {
        r.checked = true; matched = true;
      } else {
        r.checked = r.checked && !matched;
      }
      r.dispatchEvent(new Event('change'));
    });

    if (!matched) {
      const p = senderList.querySelector('input[name="sender_choice"][value="platform"]');
      if (p) {
        p.checked = true;
        p.dispatchEvent(new Event('change'));
      }
    }
  }

  // ---------- API wiring ----------
  async function loadAll() {
    try {
      const r1 = await fetch('/api/email/settings', { cache: 'no-store' });
      if (!r1.ok) throw new Error(`GET /api/email/settings ${r1.status}`);
      current = await r1.json();

      const r2 = await fetch('/api/email/domains', { cache: 'no-store' });
      if (!r2.ok) throw new Error(`GET /api/email/domains ${r2.status}`);
      const list = await r2.json();
      domains = list.items || [];

      fromNameInp.value = current.default_from_name || PLATFORM_NAME;
      emMsg.textContent = '';

      renderApprovedList();
    } catch (e) {
      emMsg.textContent = 'Failed to load email settings.';
      console.error(e);
    }
  }

  async function saveSelection({ mode, email }) {
    emMsg.textContent = 'Saving…';
    try {
      const r = await fetch('/api/email/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: mode === 'platform' ? 'platform' : 'custom_domain',
          default_from_name: fromNameInp?.value || PLATFORM_NAME,
          default_from_email: mode === 'platform' ? PLATFORM_EMAIL : email
        })
      });
      if (!r.ok) throw new Error(`POST /api/email/settings ${r.status}`);
      current = await r.json();
      emMsg.textContent = 'Saved.';
    } catch (e) {
      emMsg.textContent = 'Save failed.';
      console.error(e);
    }
  }

  const saveFromNameDebounced = debounce(() => {
    const selected = document.querySelector('input[name="sender_choice"]:checked');
    if (!selected) return;
    if (selected.value === 'platform') {
      saveSelection({ mode: 'platform', email: PLATFORM_EMAIL });
    } else if (selected.value.startsWith('custom:')) {
      const domain = selected.dataset.domain;
      const local = senderList.querySelector('input[data-role="local"][data-domain="' + domain + '"]');
      const email = `${(local?.value || 'accounts').trim()}@${domain}`;
      saveSelection({ mode: 'custom_domain', email });
    }
  }, 500);

  // ---------- Test send ----------
  async function sendTest() {
    if (!testToInp?.value) {
      testMsg.textContent = 'Enter a destination email first.';
      return;
    }
    testMsg.textContent = 'Sending…';
    try {
      const r = await fetch('/api/email/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to_email: testToInp.value }),
      });
      if (!r.ok) {
        let detail = '';
        try {
          const j = await r.json();
          detail = j?.detail || j?.message || '';
        } catch {
          detail = await r.text().catch(()=> '');
        }
        testMsg.textContent = detail ? `Test failed: ${detail}` : 'Test failed.';
        return;
      }
      const j = await r.json();
      testMsg.textContent = `Sent. MessageID: ${j.message_id}`;
    } catch {
      testMsg.textContent = 'Test failed (network error).';
    }
  }

  // ---------- Per-user server init ----------
  async function initServer() {
    pmInitMsg.textContent = 'Creating…';
    pmInitBtn.disabled = true;
    try {
      const r = await fetch('/api/postmark/servers/init', { method: 'POST' });
      const ok = r.ok;
      let body = {};
      try { body = await r.json(); } catch {}
      if (!ok) {
        const err = body?.detail || body?.message || 'Create failed.';
        pmInitMsg.textContent = err;
        pmInitBtn.disabled = false;
        return;
      }
      pmInitMsg.textContent = body.created ? `Server created (ID ${body.server_id}).` : `Server already exists (ID ${body.server_id}).`;
    } catch (e) {
      pmInitMsg.textContent = 'Network error.';
      pmInitBtn.disabled = false;
    }
  }

  // ---------- Wizard (verify only) ----------
  function showModal(show) {
    if (!modal) return;
    modal.style.display = show ? 'block' : 'none';
    if (!show) return;

    // reset
    currentDomainId = null;
    currentDomainName = null;
    dwMsg.textContent = '';
    dwVerifyMsg.textContent = '';
    if (dwVerifiedHint) dwVerifiedHint.style.display = 'none';
    domainInp.value = '';
    dnsBlock.innerHTML = '';
    dwStep1.style.display = 'block';
    dwStep2.style.display = 'none';

    // Auto-resume if this user already has a domain row
    (async () => {
      try {
        const r = await fetch('/api/email/domains', { cache: 'no-store' });
        if (!r.ok) return;
        const list = await r.json();
        const first = (list.items || [])[0];
        if (!first) return;
        // jump to step 2 with existing details
        currentDomainId = first.id;
        currentDomainName = first.domain;
        renderDnsRecords(first);
        dwStep1.style.display = 'none';
        dwStep2.style.display = 'block';
      } catch {}
    })();
  }

  function renderDnsRecords(d) {
    currentDomainName = d.domain || currentDomainName;

    // Trim ".<domain>" from host so it matches Postmark's "Hostname"
    function shortHost(host) {
      const dom = (currentDomainName || "").toLowerCase();
      const lower = (host || "").toLowerCase();
      if (dom && lower.endsWith("." + dom)) {
        return host.slice(0, host.length - (dom.length + 1)); // preserve original casing
      }
      return host || "";
    }

    const rows = [];
    if (d.dkim1_host && d.dkim1_target) {
      rows.push({ type: 'TXT (DKIM)', host: shortHost(d.dkim1_host), value: d.dkim1_target });
    }
    if (d.dkim2_host && d.dkim2_target) {
      rows.push({ type: 'TXT (DKIM)', host: shortHost(d.dkim2_host), value: d.dkim2_target });
    }
    if (d.return_path_host && d.return_path_target) {
      rows.push({ type: 'CNAME (Return-Path)', host: shortHost(d.return_path_host), value: d.return_path_target });
    }

    const html = [
      '<h4 style="margin:0 0 8px 0;">Add these DNS records</h4>',
      '<div class="table" style="width:100%; overflow:auto;">',
      '<table style="width:100%; border-collapse:collapse;">',
      '<thead><tr><th style="text-align:left;padding:6px;border-bottom:1px solid #eee;">Type</th><th style="text-align:left;padding:6px;border-bottom:1px solid #eee;">Host</th><th style="text-align:left;padding:6px;border-bottom:1px solid #eee;">Value</th></tr></thead><tbody>'
    ];
    for (const r of rows) {
      html.push(
        `<tr><td style="padding:6px;border-bottom:1px solid #f3f4f6;">${r.type}</td>` +
        `<td style="padding:6px;border-bottom:1px solid #f3f4f6;"><code>${r.host}</code></td>` +
        `<td style="padding:6px;border-bottom:1px solid #f3f4f6;"><code>${r.value}</code></td></tr>`
      );
    }
    html.push('</tbody></table></div>');
    dnsBlock.innerHTML = html.join('');

    // Optional verified toggle (only if these IDs exist in your HTML)
    const dwDomainSfx = $('dw_domain_sfx');
    const dwLocal     = $('dw_local');
    const dwUseBlock  = $('dw_use_block');

    const isVerified = (d.status === 'verified') || (d.dkim_verified && d.rp_verified);
    if (dwUseBlock) {
      if (isVerified && currentDomainName) {
        if (dwDomainSfx) dwDomainSfx.textContent = `@${currentDomainName}`;
        if (dwLocal && !dwLocal.value) dwLocal.value = 'accounts';
        dwUseBlock.style.display = 'block';
      } else {
        dwUseBlock.style.display = 'none';
      }
    }
  }

  async function startDomain() {
    const dom = (domainInp?.value || '').trim().toLowerCase();
    if (!dom) { dwMsg.textContent = 'Enter a domain name.'; return; }
    dwMsg.textContent = 'Creating…';

    async function loadExistingAndShow(id) {
      try {
        const r = await fetch(`/api/email/domains/${id}`, { cache: 'no-store' });
        if (!r.ok) throw new Error(`GET domain ${id}: ${r.status}`);
        const d = await r.json();
        currentDomainId = id;
        currentDomainName = d.domain || dom;
        renderDnsRecords(d);
        dwMsg.textContent = '';
        dwStep1.style.display = 'none';
        dwStep2.style.display = 'block';
      } catch (e) {
        dwMsg.textContent = 'Failed to load existing domain details.';
        console.error(e);
      }
    }

    try {
      const r = await fetch('/api/email/domains/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain: dom }),
      });

      if (r.ok) {
        const d = await r.json();
        currentDomainId = d.id;
        currentDomainName = d.domain || dom;
        renderDnsRecords(d);
        dwMsg.textContent = '';
        dwStep1.style.display = 'none';
        dwStep2.style.display = 'block';
        return;
      }

      // Non-200: surface precise server message if we have it
      let serverMsg = '';
      try {
        const j = await r.json();
        serverMsg = j?.detail || j?.message || '';
      } catch {
        serverMsg = await r.text().catch(() => '');
      }

      // Try to resume any existing row for this user
      const rList = await fetch('/api/email/domains', { cache: 'no-store' });
      if (rList.ok) {
        const list = await rList.json();
        const existing = (list.items || []).find(x => (x.domain || '').toLowerCase() === dom) || (list.items || [])[0];
        if (existing) {
          await loadExistingAndShow(existing.id);
          return;
        }
      }

      dwMsg.textContent = serverMsg || 'Failed to start domain setup.';
    } catch (e) {
      dwMsg.textContent = 'Failed to start domain setup.';
      console.error(e);
    }
  }

  async function verifyDomain() {
    if (!currentDomainId) { dwVerifyMsg.textContent = 'Nothing to verify yet.'; return; }
    dwVerifyMsg.textContent = 'Verifying…';
    try {
      const r = await fetch(`/api/email/domains/${currentDomainId}/verify`, { method: 'POST' });
      if (!r.ok) {
        const t = await r.text().catch(()=> '');
        throw new Error(`verify ${r.status}: ${t}`);
      }
      const d = await r.json();
      if (d.status === 'verified' || (d.dkim_verified && d.rp_verified)) {
        dwVerifyMsg.textContent = 'Verified ✔';
        if (dwVerifiedHint) dwVerifiedHint.style.display = 'block';
        await loadAll();
      } else {
        dwVerifyMsg.textContent = 'Still pending — DNS may take time to propagate.';
      }
    } catch (e) {
      dwVerifyMsg.textContent = 'Verify failed.';
      console.error(e);
    }
  }

  // Optional: delete current domain (requires DELETE /api/email/domains/{id})
  async function deleteDomain() {
    if (!currentDomainId) { dwMsg.textContent = 'No domain to remove.'; return; }
    const sure = confirm('Remove this domain setup? This will also delete it from Postmark.');
    if (!sure) return;
    dwMsg.textContent = 'Removing…';
    try {
      const r = await fetch(`/api/email/domains/${currentDomainId}`, { method: 'DELETE' });
      if (!r.ok) {
        let msg = '';
        try { const j = await r.json(); msg = j?.detail || j?.message || ''; } catch {}
        dwMsg.textContent = msg || 'Failed to remove domain.';
        return;
      }
      // Reset back to step 1
      currentDomainId = null;
      currentDomainName = null;
      dnsBlock.innerHTML = '';
      dwStep2.style.display = 'none';
      dwStep1.style.display = 'block';
      dwMsg.textContent = 'Domain removed. Enter a new one to start again.';
      await loadAll();
    } catch (e) {
      dwMsg.textContent = 'Failed to remove domain.';
    }
  }

  // ---------- Listeners ----------
  senderList?.addEventListener('change', (e) => {
    const r = e.target.closest('input[name="sender_choice"]');
    if (!r) return;
    if (r.value === 'platform') {
      saveSelection({ mode: 'platform', email: PLATFORM_EMAIL });
    }
  });

  fromNameInp?.addEventListener('input', saveFromNameDebounced);
  testBtn?.addEventListener('click', sendTest);

  pmInitBtn?.addEventListener('click', initServer);

  openWizardBtn?.addEventListener('click', () => showModal(true));
  closeBtn?.addEventListener('click', () => showModal(false));
  startBtn?.addEventListener('click', startDomain);
  verifyBtn?.addEventListener('click', verifyDomain);
  dwDeleteBtn?.addEventListener?.('click', deleteDomain);

  window.addEventListener('email_settings_tab_activated', loadAll);
  if (document.querySelector('#tab_email')?.style.display !== 'none') {
    loadAll();
  }
})();
