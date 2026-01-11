// /static/js/customers.js

function fmtDate(iso){
  if (!iso) return '';
  if (window.AppDate && typeof AppDate.formatDate === 'function') {
    return AppDate.formatDate(iso);
  }
  const d = new Date(iso);
  return isNaN(d) ? '' : d.toLocaleDateString('en-GB');
}

function fmtTerms(tt, td){
  if (!tt) return '';
  if (tt === 'custom' && td) return `Custom (${td}d)`;
  if (tt === 'month_following') return 'Month following';
  if (tt === 'net_30') return 'Net 30';
  if (tt === 'net_60') return 'Net 60';
  return tt;
}

async function loadCustomers(q){
  const url = '/api/customers' + (q ? ('?q=' + encodeURIComponent(q)) : '');
  const tbody = document.getElementById('customers-rows');
  const empty = document.getElementById('customers-empty');

  tbody.innerHTML = `<tr><td colspan="6" class="muted">Loading…</td></tr>`;
  empty.style.display = 'none';

  let r, items = [];
  try {
    r = await fetch(url);
    if (!r.ok) throw new Error(String(r.status));
    items = await r.json();
  } catch {
    tbody.innerHTML = '<tr><td colspan="6">Failed to load</td></tr>';
    return;
  }

  if (!items.length){
    tbody.innerHTML = '';
    empty.style.display = 'block';
    return;
  }

  tbody.innerHTML = items.map(c => `
    <tr data-id="${c.id}">
      <td>
        <a href="/customers/${c.id}" onclick="openCustomerTab(${c.id}, ${JSON.stringify(c.name || '').replace(/"/g,'&quot;')}); return false;">
          ${c.name || ''}
        </a>
      </td>
      <td>${c.email || ''}</td>
      <td>${c.phone || ''}</td>
      <td>${fmtTerms(c.terms_type, c.terms_days)}</td>
      <td>${fmtDate(c.created_at)}</td>
      <td>
        <button class="btn btn--ghost js-edit" type="button">Edit</button>
      </td>
    </tr>
  `).join('');
}

// live search (debounced)
document.addEventListener('input', (e)=>{
  if (e.target?.id === 'cust-search'){
    const q = e.target.value.trim();
    clearTimeout(window.__cust_t);
    window.__cust_t = setTimeout(()=>loadCustomers(q), 200);
  }
});

// row actions (Edit opens the shared modal)
document.addEventListener('click', (e)=>{
  const btn = e.target.closest('.js-edit');
  if (!btn) return;
  const tr = btn.closest('tr');
  const id = Number(tr?.dataset?.id || 0);
  if (id && typeof window.openCustomerEdit === 'function') {
    window.openCustomerEdit(id);
  }
});

// boot
document.addEventListener('DOMContentLoaded', () => loadCustomers());
