// FINAL VERSION OF /static/js/settings_invoice_import.js
(function () {
  // Dedicated invoice-import page bootstrap.
  // Fire the same event the old tab switcher used so existing
  // invoice_import_ui.js / block_mapper_ui.js behaviour still works.
  window.addEventListener('load', function () {
    window.dispatchEvent(new Event('invoice_import_tab_activated'));
  });
})();
