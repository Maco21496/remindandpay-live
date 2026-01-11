const TAB_KEY = 'ic_customer_tabs';
const ACTIVE_KEY = 'ic_active_customer';

function _readTabs(){ try{ return JSON.parse(localStorage.getItem(TAB_KEY)||'[]'); }catch{ return []; } }
function _saveTabs(tabs){ localStorage.setItem(TAB_KEY, JSON.stringify(tabs)); }
function _setActive(id){ localStorage.setItem(ACTIVE_KEY, id ? String(id) : ''); renderCustomerTabs(); }
function _escape(s){ return String(s).replace(/[&<>"']/g, m=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[m])); }

function renderCustomerTabs(){
  const mount = document.getElementById('customer-tabbar'); if(!mount) return;
  const tabs = _readTabs();
  const active = localStorage.getItem(ACTIVE_KEY) || '';

  const items = tabs.map(t => `
    <span class="tab ${String(t.id)===active?'is-active':''}">
      <a href="/customers/${t.id}" onclick="setActiveCustomerTab(${t.id})">${_escape(t.name)}</a>
      <button class="tab__close" aria-label="Close" onclick="closeCustomerTab(${t.id});return false;">×</button>
    </span>
  `).join('');

  mount.innerHTML = `
    <a class="tab ${active===''?'is-active':''}" href="/dashboard" onclick="setActiveCustomerTab('')">Dashboard</a>
    ${items}
  `;
}

function openCustomerTab(id, name){
  const tabs = _readTabs();
  if (!tabs.some(t=>t.id===id)) { tabs.push({ id, name }); _saveTabs(tabs); }
  _setActive(id);
  window.location.href = `/customers/${id}`;
}

function closeCustomerTab(id){
  const tabs = _readTabs().filter(t=>t.id!==id);
  _saveTabs(tabs);
  const active = localStorage.getItem(ACTIVE_KEY);
  if (String(id) === active){
    _setActive('');
    window.location.href = '/dashboard';
  } else {
    renderCustomerTabs();
  }
}

function setActiveCustomerTab(id){ _setActive(id); }

document.addEventListener('DOMContentLoaded', renderCustomerTabs);

// expose for other scripts
window.openCustomerTab = openCustomerTab;
window.closeCustomerTab = closeCustomerTab;
window.setActiveCustomerTab = setActiveCustomerTab;
