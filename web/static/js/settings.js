// FINAL VERSION OF /static/js/settings.js
(function () {
  const $ = (id) => document.getElementById(id);

  // --- DOM refs ---
  const dateSel        = $('set_date');
  const timeSel        = $('set_time');
  const tzSel          = $('set_tz');
  const sendTimeInp    = $('set_send_time');
  const countrySel     = $('set_country');
  const currencySel    = $('set_currency');
  const addrBox        = $('set_org_addr');
  const msg            = $('set_msg');
  const saveBtn        = $('set_save');

  const logoFile       = $('set_logo_input');
  const logoRemove     = $('set_logo_remove');
  const logoPreview    = $('set_logo_preview');
  const logoMsg        = $('set_logo_msg');

  const themeRadios    = document.querySelectorAll('input[name="theme"]');
  const brandPicker    = $('set_brand_color');

  const restoreBtn     = $('set_restore_defaults');
  const restoreMsg     = $('set_restore_msg');

  let originalAddress  = "";
  let timezonesLoaded  = false;

  // --- helpers ---

  async function loadTimezones() {
    if (!tzSel || timezonesLoaded) return;
    try {
      const r = await fetch('/api/settings/timezones', { cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      const list = await r.json(); // array of strings

      const groups = {};
      for (const tz of list) {
        const key = tz.includes('/') ? tz.split('/')[0] : 'Other';
        (groups[key] ||= []).push(tz);
      }

      tzSel.innerHTML = '';
      for (const k of Object.keys(groups).sort()) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = k;
        for (const tz of groups[k]) {
          const opt = document.createElement('option');
          opt.value = tz;
          opt.textContent = tz.replace(/_/g, ' ');
          optgroup.appendChild(opt);
        }
        tzSel.appendChild(optgroup);
      }

      timezonesLoaded = true;
    } catch {
      tzSel.innerHTML = `
        <option value="UTC">UTC</option>
        <option value="Europe/London">Europe/London</option>
        <option value="Europe/Paris">Europe/Paris</option>
        <option value="America/New_York">America/New_York</option>
        <option value="America/Los_Angeles">America/Los_Angeles</option>
        <option value="Australia/Sydney">Australia/Sydney</option>
      `;
      timezonesLoaded = true;
    }
  }

  function applyNamedTheme(themeName) {
    // named theme (default/teal/rose/etc.) uses data-theme
    if (!themeName || themeName === 'default') {
        document.documentElement.removeAttribute('data-theme');
    } else {
        document.documentElement.setAttribute('data-theme', themeName);
    }

    // when using a named theme, custom picker is off
    if (brandPicker) {
      brandPicker.disabled = true;
    }

    // clear any custom overrides in case user used Custom before
    document.documentElement.style.removeProperty('--brand-600');
    document.documentElement.style.removeProperty('--brand-700');

    try { localStorage.setItem('ic_theme', themeName || 'default'); } catch {}
  }

  function applyCustomBrand(hex) {
    // custom brand: no data-theme, just inject vars
    document.documentElement.removeAttribute('data-theme');

    if (hex && /^#[0-9A-Fa-f]{6}$/.test(hex)) {
      // simple darken helper
      function darken(hx, amt){
        const n = parseInt(hx.slice(1),16);
        let r=(n>>16)&255, g=(n>>8)&255, b=n&255;
        r=Math.max(0, r-amt); g=Math.max(0, g-amt); b=Math.max(0, b-amt);
        return '#'+((1<<24)+(r<<16)+(g<<8)+b).toString(16).slice(1);
      }
      document.documentElement.style.setProperty('--brand-600', hex);
      document.documentElement.style.setProperty('--brand-700', darken(hex, 24));
      try { localStorage.setItem('ic_brand_color', hex); } catch {}
    }

    // picker is active in this mode
    if (brandPicker) {
      brandPicker.disabled = false;
    }

    try { localStorage.setItem('ic_theme', 'custom'); } catch {}
  }

  function currentThemeRadioValue() {
    const r = document.querySelector('input[name="theme"]:checked');
    return r ? r.value : 'default';
  }

  function syncThemeUIFromState(themeVal, brandHex) {
    // mark the radio
    const radio = document.querySelector(`input[name="theme"][value="${themeVal}"]`);
    if (radio) {
      radio.checked = true;
    }

    // set picker value
    if (brandPicker && brandHex && /^#[0-9A-Fa-f]{6}$/.test(brandHex)) {
      brandPicker.value = brandHex;
    }

    // push styles
    if (themeVal === 'custom') {
      applyCustomBrand(brandHex || brandPicker?.value || '#6366f1');
    } else {
      applyNamedTheme(themeVal);
    }
  }

  async function loadSettings() {
    try {
      await loadTimezones();

      const r = await fetch('/api/settings', { cache: 'no-store' });
      if (!r.ok) throw new Error(String(r.status));
      const s = await r.json();

      // basic fields
      if (dateSel && s.date_locale)    dateSel.value    = s.date_locale;
      if (timeSel && s.time_format)    timeSel.value    = s.time_format;
      if (countrySel && s.default_country) countrySel.value = s.default_country;
      if (currencySel && s.currency)   currencySel.value = s.currency;
      if (tzSel && s.timezone)         tzSel.value      = s.timezone;
      if (sendTimeInp && s.default_send_time) sendTimeInp.value = s.default_send_time;

      originalAddress = (s.org_address || "");
      if (addrBox) addrBox.value = originalAddress;

      window.__APP_SETTINGS__ = s;

      // logo preview
      if (logoPreview && s.org_logo_url) {
        logoPreview.src = s.org_logo_url + '?v=' + Date.now();
        logoPreview.style.display = 'block';
      } else if (logoPreview) {
        logoPreview.style.display = 'none';
        logoPreview.src = '';
      }

      // persist currency for other modules
      try { if (s.currency) localStorage.setItem('ic_currency', s.currency); } catch {}

      // theme + custom colour coming from server
      // server fields:
      //   s.theme        = one of 'default','teal',...,'custom'
      //   s.brand_color  = '#RRGGBB' or null
      const themeVal  = s.theme || 'default';
      const brandHex  = s.brand_color || '';

      syncThemeUIFromState(themeVal, brandHex);

      if (msg) msg.textContent = '';
    } catch {
      if (msg) msg.textContent = 'Failed to load settings.';
    }
  }

  async function saveSettings() {
    if (msg) msg.textContent = 'Saving…';

    try {
      const selectedTheme = currentThemeRadioValue();
      const payload = {
        date_locale:       dateSel ? dateSel.value : undefined,
        time_format:       timeSel ? timeSel.value : undefined,
        default_country:   countrySel ? countrySel.value : undefined,
        currency:          currencySel ? currencySel.value : undefined,
        timezone:          tzSel ? tzSel.value : undefined,
        default_send_time: sendTimeInp ? (sendTimeInp.value || undefined) : undefined,
        theme:             selectedTheme,
        brand_color:       (selectedTheme === 'custom' && brandPicker) ? brandPicker.value : undefined,
      };

      // only send address if changed
      if (addrBox && addrBox.value.trim() !== originalAddress.trim()) {
        payload.org_address = addrBox.value;
      }

      const r = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const t = await r.text().catch(()=> '');
        throw new Error(`Save failed ${r.status} ${t}`);
      }

      if (msg) msg.textContent = 'Saved.';

      await loadSettings();

      // ask user about reload so nav etc picks up new theme
      setTimeout(() => {
        const applyNow = confirm('Settings saved. Reload to apply everywhere now?');
        if (applyNow) window.location.reload();
      }, 200);

    } catch {
      if (msg) msg.textContent = 'Save failed.';
    }
  }

  async function uploadLogo(file) {
    if (!file) return;
    if (logoMsg) logoMsg.textContent = 'Uploading…';
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/settings/logo', { method: 'POST', body: fd });
      if (!r.ok) throw new Error(String(r.status));
      const j = await r.json();
      if (logoPreview && j.org_logo_url) {
        logoPreview.src = j.org_logo_url + '?v=' + Date.now();
        logoPreview.style.display = 'block';
      }
      if (logoMsg) logoMsg.textContent = 'Logo saved.';
    } catch {
      if (logoMsg) logoMsg.textContent = 'Logo upload failed.';
    } finally {
      if (logoFile) logoFile.value = '';
    }
  }

  async function removeLogo() {
    if (logoMsg) logoMsg.textContent = 'Removing…';
    try {
      const r = await fetch('/api/settings/logo', { method: 'DELETE' });
      if (!r.ok) throw new Error(String(r.status));
      if (logoPreview) {
        logoPreview.style.display = 'none';
        logoPreview.src = '';
      }
      if (logoMsg) logoMsg.textContent = 'Logo removed.';
      await loadSettings();
    } catch {
      if (logoMsg) logoMsg.textContent = 'Logo remove failed.';
    }
  }

  async function restoreDefaults() {
    if (!confirm('This will restore the default statement rules and message templates.\n\nContinue?')) return;
    try {
      if (restoreBtn) restoreBtn.disabled = true;
      if (restoreMsg) restoreMsg.textContent = 'Restoring…';

      const r = await fetch('/api/settings/restore_defaults', { method: 'POST' });
      if (!r.ok) {
        const t = await r.text().catch(()=> '');
        throw new Error(`Restore failed ${r.status} ${t}`);
      }

      if (restoreMsg) restoreMsg.textContent = 'Defaults restored.';
      await loadSettings();

    } catch {
      if (restoreMsg) restoreMsg.textContent = 'Restore failed.';
    } finally {
      if (restoreBtn) restoreBtn.disabled = false;
      setTimeout(() => { if (restoreMsg) restoreMsg.textContent = ''; }, 2000);
    }
  }

  // theme radio change -> update preview instantly
  function onThemeRadioChange() {
    const val = currentThemeRadioValue();
    if (val === 'custom') {
      // enable picker and apply custom brand right now
      if (brandPicker) {
        brandPicker.disabled = false;
        applyCustomBrand(brandPicker.value);
      }
    } else {
      applyNamedTheme(val);
    }
  }

  // custom picker change -> live update vars if "custom" is selected
  function onBrandPickerInput() {
    const val = currentThemeRadioValue();
    if (val === 'custom' && brandPicker) {
      applyCustomBrand(brandPicker.value);
    }
  }

  // --- listeners ---
  saveBtn?.addEventListener('click', saveSettings);
  logoFile?.addEventListener('change', (e) => uploadLogo(e.target.files?.[0]));
  logoRemove?.addEventListener('click', removeLogo);
  restoreBtn?.addEventListener('click', restoreDefaults);

  countrySel?.addEventListener('change', () => {
    if (!currencySel) return;
    const c = (countrySel.value || '').toUpperCase();
    if (c === 'GB') currencySel.value = 'GBP';
    else if (c === 'US') currencySel.value = 'USD';
    else if (c === 'EU') currencySel.value = 'EUR';
  });

  themeRadios.forEach(radio => {
    radio.addEventListener('change', onThemeRadioChange);
  });

  brandPicker?.addEventListener('input', onBrandPickerInput);

  document.addEventListener('DOMContentLoaded', loadSettings);
})();
