// web/static/js/actions_bar.js
(function () {
  // Toggle the overflow menu
  document.addEventListener('click', (e) => {
    const t = e.target;

    // Open/close the menu
    if (t.matches('[data-more-toggle]')) {
      const wrap = t.closest('.actions__more');
      const menu = wrap?.querySelector('.actions__menu');
      if (menu) menu.hidden = !menu.hidden;
      return;
    }

    // Click outside closes any open menu
    document.querySelectorAll('.actions__menu:not([hidden])').forEach(menu => {
      if (!menu.contains(t) && !menu.previousElementSibling?.contains(t)) {
        menu.hidden = true;
      }
    });
  });

  // Close all menus on Escape
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.actions__menu:not([hidden])').forEach(m => m.hidden = true);
    }
  });
})();
