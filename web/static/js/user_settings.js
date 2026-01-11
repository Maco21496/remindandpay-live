// /static/js/user_settings.js
(function(){
  document.addEventListener('DOMContentLoaded', () => {
    const btn  = document.getElementById('user_btn');
    const menu = document.getElementById('user_menu');
    if (!btn || !menu) return;

    const close = () => { menu.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
    const open  = () => { menu.hidden = false; btn.setAttribute('aria-expanded', 'true'); };

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      menu.hidden ? open() : close();
    });

    // click-away + Esc to close
    document.addEventListener('click', (e) => {
      if (!menu.hidden && !menu.contains(e.target) && !btn.contains(e.target)) close();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });

    // (Optional) derive initials from a name/email you render server-side:
    // const n = (document.body.dataset.userName || document.body.dataset.userEmail || '').trim();
    // if (n) {
    //   const ini = n.split(/\s+/).map(p => p[0]).slice(0,2).join('').toUpperCase();
    //   const el = document.getElementById('user_initials');
    //   if (el && ini) el.textContent = ini;
    // }
  });
})();
