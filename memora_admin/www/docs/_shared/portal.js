/* ============================================================
   Memora Knowledge Portal — Shared JS
   Provides: accordion toggle
   Import via: <script src="../../_shared/portal.js"></script>
   ============================================================ */

document.addEventListener('DOMContentLoaded', function () {
  document.addEventListener('click', function (e) {
    var toggle = e.target.closest('[data-accordion-toggle]');
    if (!toggle) return;

    var targetId = toggle.getAttribute('data-accordion-toggle');
    var content = document.getElementById(targetId);
    if (!content) return;

    var isOpen = content.classList.contains('is-open');

    if (isOpen) {
      content.classList.remove('is-open');
      content.style.maxHeight = '0';
      toggle.classList.remove('is-open');
    } else {
      content.classList.add('is-open');
      content.style.maxHeight = content.scrollHeight + 'px';
      toggle.classList.add('is-open');
    }
  });
});
