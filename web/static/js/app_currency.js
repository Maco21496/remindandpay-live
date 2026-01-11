// /static/js/app_currency.js — Central currency formatter
(function(){
  const KEY = 'ic_currency';
  const valid = new Set(['GBP','USD','EUR']);

  function getCurrency(){
    try{
      const s = (window.__APP_SETTINGS__ && window.__APP_SETTINGS__.currency) || null;
      if (s && valid.has(String(s).toUpperCase())) return String(s).toUpperCase();
    }catch{}
    try{
      const ls = localStorage.getItem(KEY);
      if (ls && valid.has(String(ls).toUpperCase())) return String(ls).toUpperCase();
    }catch{}
    return 'GBP';
  }

  function format(n){
    const cur = getCurrency();
    const formatter = new Intl.NumberFormat(undefined, { style:'currency', currency: cur, minimumFractionDigits:2, maximumFractionDigits:2 });
    const num = Number(n || 0);
    return formatter.format(num);
  }

  window.AppCurrency = { format };
})();

